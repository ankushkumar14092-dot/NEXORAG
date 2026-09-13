from __future__ import annotations

import threading
import time
from pathlib import Path

from app.config import settings
from app.memory import TempRAMCache, query_cache
from app.models import SourceRecord, store
from app.rag import answer_with_context, retriever
from app.security import safe_rmtree, safe_unlink, sanitize_filename, validate_source_id

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
            return out

        hits = retriever.search(
            question,
            source_id=source_id,
            kind=kind,
            top_k=top_k,
        )
        answer = answer_with_context(question, hits)
        evidence = [
            {
                "source_id": h.chunk.source_id or h.chunk.video_id,
                "source_title": h.source_title,
                "kind": h.chunk.kind,
                "location": h.chunk.location_label
                or f"{h.chunk.start_label}-{h.chunk.end_label}",
                "start": h.chunk.start,
                "end": h.chunk.end,
                "start_label": h.chunk.start_label,
                "end_label": h.chunk.end_label,
                "text": h.chunk.text,
                "score": round(h.score, 4),
                "video_id": h.chunk.source_id or h.chunk.video_id,
                "video_title": h.source_title,
            }
            for h in hits
        ]
        source_ids = sorted({e["source_id"] for e in evidence if e.get("source_id")})
        payload = {
            "answer": answer,
            "evidence": evidence,
            "source_ids": source_ids,
            "cache": "miss",
        }
        query_cache.set(cache_key, payload)
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
