from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from app.config import settings
from app.evidence_graph import build_evidence_graph
from app.hybrid import RankedHit, hybrid_retriever
from app.language import detect_locale, search_query_variants
from app.memory import TempRAMCache, query_cache
from app.metrics import metrics
from app.models import SourceRecord, store
from app.modality import location_type_for_chunk
from app.query_fast import correct_query_typos
from app.rag import answer_with_context, retriever
from app.security import safe_rmtree, safe_unlink, sanitize_filename, validate_source_id

_SUMMARY_RE = re.compile(
    r"(?i)\b(?:"
    r"summarize|summary|overview|tl;?dr|"
    r"main\s+points?|key\s+takeaways?|overall\s+khulasa|"
    r"what(?:'?s|\s+is)\s+(?:this|the)\s+(?:video|doc|document|page|episode)(?:\s+about)?|"
    r"(?:is\s+)?(?:this\s+)?video\s+mein\s+kya(?:\s+bataya)?|"
    r"ismein\s+kya\s+bataya|isme\s+mein\s+kya\s+bataya"
    r")\b"
)
_REGION_END_RE = re.compile(
    r"(?i)\b(?:"
    r"end(?:ing)?|finale|conclusion|closing|akhir|aakhir|ant|"
    r"last\s+part|last\s+section|last\s+bit|last\s+half|"
    r"second\s+half|latter\s+half|middle\s+last|"
    r"baad\s*(?:mein|me)|end\s*(?:mein|me)|"
    r"towards?\s+the\s+end|near\s+the\s+end|at\s+the\s+end"
    r")\b"
)
_REGION_MID_RE = re.compile(
    r"(?i)\b(?:middle|midway|halfway|mid\s*section|"
    r"beech(?:\s*(?:mein|me))?|center|centre)\b"
)
_REGION_START_RE = re.compile(
    r"(?i)\b(?:start|beginning|opening|intro|shuru|shuruaat|"
    r"first\s+part|beginning\s+mein|start\s*(?:mein|me))\b"
)


def _detect_time_region(question: str) -> str | None:
    """Return start|middle|end|middle_end when user asks about a video section."""
    q = question or ""
    has_end = bool(_REGION_END_RE.search(q))
    has_mid = bool(_REGION_MID_RE.search(q))
    has_start = bool(_REGION_START_RE.search(q))
    # "middle last" / both middle+end => latter half
    if has_mid and has_end:
        return "middle_end"
    if has_end:
        return "end"
    if has_mid:
        return "middle"
    if has_start:
        return "start"
    return None


def _chunk_in_region(start: float | None, duration: float | None, region: str) -> bool:
    if start is None or duration is None or duration <= 0:
        return True
    t = float(start) / float(duration)
    if region == "start":
        return t < 0.28
    if region == "middle":
        return 0.28 <= t < 0.72
    if region == "end":
        return t >= 0.55
    if region == "middle_end":
        return t >= 0.40
    return True

class SourceService:
    """Application service for source lifecycle (list/get/delete)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def list(self, kind: str | None = None) -> list[dict]:
        return [s.public_dict() for s in store.list(kind=kind)]

    def get(self, source_id: str) -> SourceRecord:
        sid = validate_source_id(source_id)
        item = store.get(sid)
        if not item:
            raise KeyError(sid)
        return item

    def delete(self, source_id: str) -> dict:
        sid = validate_source_id(source_id)
        with self._lock:
            record = store.get(sid)
            if not record:
                raise KeyError(sid)

            store.delete(sid)

            removed_files: list[str] = []
            if record.source_type == "upload" and record.source:
                if safe_unlink(record.source):
                    removed_files.append(str(record.source))

            audio_dir = settings.data_dir / "audio" / sid
            if safe_rmtree(audio_dir):
                removed_files.append(str(audio_dir))

            query_cache.invalidate_source(sid)
            retriever.invalidate()
            hybrid_retriever.invalidate()

            return {
                "deleted": sid,
                "kind": record.kind,
                "title": record.title,
                "cleaned_paths": removed_files,
            }

class QueryService:
    """Query orchestration with temporary RAM answer cache."""

    def run(
        self,
        question: str,
        *,
        source_id: str | None = None,
        kind: str | None = None,
        top_k: int | None = None,
    ) -> dict:
        cache_key = TempRAMCache.make_key(
            "q",
            "ml5",
            store.revision,
            question.strip().lower(),
            source_id or "",
            kind or "",
            top_k or settings.top_k,
        )
        cached = query_cache.get(cache_key)
        if cached is not None:
            out = dict(cached)
            out["cache"] = "hit"
            metrics.record_query(cache_hit=True)
            return out

        corrected = correct_query_typos(question)
        # Fast variants first (no LLM) — keeps latency low on happy path
        variants = search_query_variants(corrected, allow_llm=False)
        if corrected not in variants:
            variants.insert(0, corrected)

        def _search_all(qs: list[str]) -> list[RankedHit]:
            merged_local: dict[str, RankedHit] = {}
            for vq in qs:
                for h in hybrid_retriever.search(
                    vq,
                    source_id=source_id,
                    kind=kind,
                    top_k=(top_k or settings.top_k),
                ):
                    cid = h.chunk.id
                    prev = merged_local.get(cid)
                    if prev is None or h.score > prev.score:
                        merged_local[cid] = h
            return sorted(merged_local.values(), key=lambda h: h.score, reverse=True)[
                : (top_k or settings.top_k)
            ]

        def _hits_from_chunks(record: SourceRecord, chunks: list) -> list[RankedHit]:
            out: list[RankedHit] = []
            for chunk in chunks:
                loc = chunk.location_label or f"{chunk.start_label}-{chunk.end_label}"
                out.append(
                    RankedHit(
                        chunk=chunk,
                        score=1.0,
                        source_title=record.title,
                        channels={
                            "keyword": 0.0,
                            "vector": 0.0,
                            "graph": 0.0,
                            "rrf": 1.0,
                            "rerank": 1.0,
                        },
                        location_type=location_type_for_chunk(loc, chunk.kind),
                    )
                )
            return out

        def _section_source_hits(region: str | None) -> list[RankedHit]:
            if not source_id:
                return []
            record = store.get(source_id)
            if not record or record.status != "ready" or not record.chunks:
                return []
            ordered = sorted(
                record.chunks,
                key=lambda c: (c.start is None, c.start or 0.0, c.id),
            )
            k = top_k or settings.top_k
            duration = record.duration
            if duration is None:
                ends = [c.end for c in ordered if c.end is not None]
                duration = max(ends) if ends else None

            if region in {"start", "middle", "end", "middle_end"}:
                in_region = [
                    c
                    for c in ordered
                    if _chunk_in_region(c.start, duration, region)
                ]
                if not in_region:
                    in_region = ordered
                if region == "end":
                    picked = in_region[-k:]
                elif region == "middle_end":
                    # Spread across latter half (not only absolute ending clips).
                    if len(in_region) <= k:
                        picked = in_region
                    else:
                        n = len(in_region)
                        idxs = sorted(
                            {
                                0,
                                max(0, n // 4),
                                max(0, n // 2),
                                max(0, (3 * n) // 4),
                                n - 1,
                            }
                        )
                        picked = []
                        for i in idxs:
                            if in_region[i] not in picked:
                                picked.append(in_region[i])
                            if len(picked) >= k:
                                break
                        for c in in_region:
                            if len(picked) >= k:
                                break
                            if c not in picked:
                                picked.append(c)
                        picked = sorted(
                            picked[:k],
                            key=lambda c: (c.start is None, c.start or 0.0, c.id),
                        )
                elif region == "middle":
                    if len(in_region) <= k:
                        picked = in_region
                    else:
                        mid = len(in_region) // 2
                        half = max(1, k // 2)
                        lo = max(0, mid - half)
                        picked = in_region[lo : lo + k]
                else:
                    picked = in_region[:k]
                return _hits_from_chunks(record, picked)

            # Whole-video summarize: spread start + middle + end (not only 00:00).
            if len(ordered) <= k:
                return _hits_from_chunks(record, ordered)
            picks = []
            n = len(ordered)
            idxs = sorted(
                {
                    0,
                    max(0, n // 4),
                    max(0, n // 2),
                    max(0, (3 * n) // 4),
                    n - 1,
                }
            )
            for i in idxs:
                if ordered[i] not in picks:
                    picks.append(ordered[i])
                if len(picks) >= k:
                    break
            # fill gaps chronologically if still short
            for c in ordered:
                if len(picks) >= k:
                    break
                if c not in picks:
                    picks.append(c)
            picks.sort(key=lambda c: (c.start is None, c.start or 0.0, c.id))
            return _hits_from_chunks(record, picks[:k])

        hits = _search_all(variants)
        # Lazy LLM expansion only on miss (Hinglish/Hindi/cross-lingual)
        if not hits and detect_locale(corrected) != "english":
            variants = search_query_variants(corrected, allow_llm=True)
            hits = _search_all(variants)

        region = _detect_time_region(corrected)
        is_summary = bool(_SUMMARY_RE.search(corrected))

        # If user asks about middle/last, keep only evidence from that section.
        if source_id and region:
            record = store.get(source_id)
            duration = record.duration if record else None
            if duration is None and record and record.chunks:
                ends = [c.end for c in record.chunks if c.end is not None]
                duration = max(ends) if ends else None
            regional = [
                h
                for h in hits
                if _chunk_in_region(h.chunk.start, duration, region)
            ]
            hits = regional or _section_source_hits(region)

        # Generic summarize with no section hint: spread across the video.
        elif source_id and is_summary:
            hits = _section_source_hits(None) or hits

        answer = answer_with_context(question, hits)
        evidence = [
            {
                "source_id": h.chunk.source_id or h.chunk.video_id,
                "source_title": h.source_title,
                "kind": h.chunk.kind,
                "location": h.chunk.location_label
                or f"{h.chunk.start_label}-{h.chunk.end_label}",
                "location_type": getattr(h, "location_type", "passage"),
                "start": h.chunk.start,
                "end": h.chunk.end,
                "start_label": h.chunk.start_label,
                "end_label": h.chunk.end_label,
                "text": h.chunk.text,
                "score": round(h.score, 4),
                "channels": getattr(h, "channels", None),
                "video_id": h.chunk.source_id or h.chunk.video_id,
                "video_title": h.source_title,
            }
            for h in hits
        ]
        source_ids = sorted({e["source_id"] for e in evidence if e.get("source_id")})
        graph = build_evidence_graph(question, hits)
        payload = {
            "answer": answer,
            "evidence": evidence,
            "source_ids": source_ids,
            "evidence_graph": graph,
            "retrieval": {
                "mode": "hybrid",
                "channels": ["keyword_bm25", "vector_tfidf", "graph_structure"],
                "rerank": "rrf_plus_coverage",
                "query_variants": variants,
                "query_corrected": corrected,
                "multilingual": True,
            },
            "cache": "miss",
        }
        query_cache.set(cache_key, payload)
        metrics.record_query(cache_hit=False)
        return payload

source_service = SourceService()
query_service = QueryService()

def build_upload_path(filename: str, *, docs: bool = False) -> Path:
    safe = sanitize_filename(filename)
    stem = Path(safe).stem
    suffix = Path(safe).suffix.lower()
    token = str(int(time.time()))
    folder = settings.data_dir / "uploads"
    if docs:
        folder = folder / "docs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{stem}_{token}{suffix}"
