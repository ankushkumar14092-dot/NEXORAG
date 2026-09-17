"""
Hybrid retrieval + RRF rerank (desk-scale, in-process).

Channels:
  - keyword  → BM25
  - vector   → sparse TF-IDF cosine (local sparse vectors — not a cloud vector DB)
  - graph    → same-source / title-boost from evidence neighborhood

Fusion: Reciprocal Rank Fusion → coverage rerank.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.config import settings
from app.modality import location_type_for_chunk
from app.rag import Hit, Retriever, _tokenize, retriever


@dataclass
class RankedHit(Hit):
    channels: dict[str, float] | None = None
    location_type: str = "passage"


def rrf_fuse(
    ranked_lists: list[list[int]],
    *,
    k: int = 60,
) -> list[tuple[int, float]]:
    scores: dict[int, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, idx in enumerate(ranked, start=1):
            scores[idx] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


class HybridRetriever:
    def __init__(self, base: Retriever | None = None) -> None:
        self.base = base or retriever
        self._tfidf = TfidfVectorizer(ngram_range=(1, 2), max_features=40000, min_df=1)
        self._matrix = None
        self._built_revision = -1

    def _ensure(self) -> None:
        self.base._ensure_fresh()
        if self._built_revision == self.base._built_revision and self._matrix is not None:
            return
        if not self.base.chunks:
            self._matrix = None
            self._built_revision = self.base._built_revision
            return
        texts = [c.text for c in self.base.chunks]
        self._matrix = self._tfidf.fit_transform(texts)
        self._built_revision = self.base._built_revision

    def invalidate(self) -> None:
        self._matrix = None
        self._built_revision = -1
        # Drop prior vocabulary so deleted sources cannot linger in RAM
        self._tfidf = TfidfVectorizer(ngram_range=(1, 2), max_features=40000, min_df=1)

    def search(
        self,
        query: str,
        source_id: str | None = None,
        kind: str | None = None,
        top_k: int | None = None,
    ) -> list[RankedHit]:
        top_k = top_k or settings.top_k
        self._ensure()
        if not self.base.chunks or self.base.bm25 is None:
            return []

        allowed = None
        if kind:
            from app.models import store

            allowed = {i.id for i in store.list(kind=kind) if i.status == "ready"}

        def allowed_idx(i: int) -> bool:
            c = self.base.chunks[i]
            sid = c.source_id or c.video_id
            if source_id and sid != source_id:
                return False
            if allowed is not None and sid not in allowed:
                return False
            return True

        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        # --- keyword channel (BM25) ---
        bm25_scores = self.base.bm25.get_scores(q_tokens)
        bm25_order = [
            i
            for i in sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)
            if allowed_idx(i) and bm25_scores[i] >= 0.15
        ][: max(40, top_k * 5)]

        # --- sparse vector channel (TF-IDF cosine) ---
        vec_order: list[int] = []
        vec_scores = np.zeros(len(self.base.chunks))
        if self._matrix is not None:
            qv = self._tfidf.transform([query])
            vec_scores = cosine_similarity(qv, self._matrix).ravel()
            vec_order = [
                i
                for i in sorted(range(len(vec_scores)), key=lambda i: vec_scores[i], reverse=True)
                if allowed_idx(i) and vec_scores[i] >= 0.04
            ][: max(40, top_k * 5)]

        # --- graph / structure channel: title + location token overlap ---
        graph_scores = np.zeros(len(self.base.chunks))
        qset = set(q_tokens)
        for i, chunk in enumerate(self.base.chunks):
            if not allowed_idx(i):
                continue
            sid = chunk.source_id or chunk.video_id
            title = self.base.titles.get(sid, "")
            loc = chunk.location_label or ""
            bag = set(_tokenize(f"{title} {loc}"))
            overlap = len(qset & bag)
            if overlap:
                graph_scores[i] = float(overlap)
        graph_order = [
            i
            for i in sorted(range(len(graph_scores)), key=lambda i: graph_scores[i], reverse=True)
            if graph_scores[i] > 0
        ][: max(40, top_k * 5)]

        fused = rrf_fuse([bm25_order, vec_order, graph_order])

        # coverage rerank: boost passages containing more query terms
        reranked: list[tuple[int, float, dict[str, float]]] = []
        for idx, rrf_score in fused[: max(30, top_k * 4)]:
            toks = set(self.base.tokenized[idx] if self.base.tokenized else [])
            coverage = len(qset & toks) / max(1, len(qset))
            phrase = 1.0 if query.strip().lower() in (self.base.chunks[idx].text or "").lower() else 0.0
            final = rrf_score + 0.15 * coverage + 0.1 * phrase
            channels = {
                "keyword": float(bm25_scores[idx]),
                "vector": float(vec_scores[idx]),
                "graph": float(graph_scores[idx]),
                "rrf": float(rrf_score),
                "rerank": float(final),
            }
            reranked.append((idx, final, channels))
        reranked.sort(key=lambda x: x[1], reverse=True)

        hits: list[RankedHit] = []
        for idx, score, channels in reranked[:top_k]:
            chunk = self.base.chunks[idx]
            sid = chunk.source_id or chunk.video_id
            loc = chunk.location_label or f"{chunk.start_label}-{chunk.end_label}"
            hits.append(
                RankedHit(
                    chunk=chunk,
                    score=score,
                    source_title=self.base.titles.get(sid, sid),
                    channels=channels,
                    location_type=location_type_for_chunk(loc, chunk.kind),
                )
            )

        # No lexical/vector match for a scoped source → empty evidence
        # (do not invent floor-score chunks; that forced useless LLM calls).
        return hits


hybrid_retriever = HybridRetriever()
