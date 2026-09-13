from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.config import settings
from app.llm import generate_answer
from app.models import Chunk, SourceRecord, store


@dataclass
class Hit:
    chunk: Chunk
    score: float
    source_title: str


class Retriever:
    def __init__(self) -> None:
        self.vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            max_features=50000,
            min_df=1,
        )
        self.matrix = None
        self.chunks: list[Chunk] = []
        self.titles: dict[str, str] = {}
        self._built_revision = -1
        self._rebuild()

    def _ensure_fresh(self) -> None:
        if self._built_revision != store.revision:
            self._rebuild()

    def _rebuild(self) -> None:
        self.chunks = []
        self.titles = {}
        for item in store.list():
            if item.status != "ready":
                continue
            self.titles[item.id] = item.title
            self.chunks.extend(item.chunks)

        if not self.chunks:
            self.matrix = None
            self._built_revision = store.revision
            return

        texts = [c.text for c in self.chunks]
        self.matrix = self.vectorizer.fit_transform(texts)
        self._built_revision = store.revision

    def refresh(self) -> None:
        self._rebuild()

    def invalidate(self) -> None:
        """Force rebuild on next search (after delete/ingest)."""
        self._built_revision = -1
        self.matrix = None
        self.chunks = []
        self.titles = {}

    def search(
        self,
        query: str,
        source_id: str | None = None,
        kind: str | None = None,
        top_k: int | None = None,
    ) -> list[Hit]:
        top_k = top_k or settings.top_k
        self._ensure_fresh()
        if self.matrix is None or not self.chunks:
            return []

        allowed_ids = None
        if kind:
            allowed_ids = {i.id for i in store.list(kind=kind) if i.status == "ready"}

        q = self.vectorizer.transform([query])
        scores = cosine_similarity(q, self.matrix).ravel()
        order = np.argsort(scores)[::-1]

        hits: list[Hit] = []
        for idx in order:
            chunk = self.chunks[idx]
            sid = chunk.source_id or chunk.video_id
            if source_id and sid != source_id:
                continue
            if allowed_ids is not None and sid not in allowed_ids:
                continue
            score = float(scores[idx])
            hits.append(
                Hit(
                    chunk=chunk,
                    score=score,
                    source_title=self.titles.get(sid, sid),
                )
            )
            if len(hits) >= top_k:
                break

        if source_id and (not hits or all(h.score <= 0 for h in hits)):
            scoped = [
                c for c in self.chunks if (c.source_id or c.video_id) == source_id
            ][:top_k]
            hits = [
                Hit(
                    chunk=c,
                    score=0.01,
                    source_title=self.titles.get(c.source_id or c.video_id, c.source_id),
                )
                for c in scoped
            ]
        elif hits and all(h.score <= 0 for h in hits):
            hits = hits[: min(3, len(hits))]
            for h in hits:
                h.score = 0.01

        return [h for h in hits if h.score > 0]


retriever = Retriever()


def answer_with_context(question: str, hits: list[Hit]) -> str:
    if not hits:
        return (
            "No relevant evidence found. Upload a document or video first, "
            "or try another question."
        )

    context_blocks = []
    for i, hit in enumerate(hits, start=1):
        c = hit.chunk
        loc = c.location_label or f"{c.start_label}-{c.end_label}"
        kind = c.kind or "source"
        context_blocks.append(
            f"[{i}] ({hit.source_title}) [{kind}] {loc}\n{c.text}"
        )
    context = "\n\n".join(context_blocks)

    def extractive() -> str:
        lines = ["Based on retrieved evidence:\n"]
        for i, hit in enumerate(hits[:4], start=1):
            c = hit.chunk
            loc = c.location_label or f"{c.start_label}-{c.end_label}"
            lines.append(f"{i}. [{loc}] {c.text[:500]}")
        return "\n".join(lines)

    system = (
        "You are an evidence briefing writer. Answer ONLY from the provided evidence. "
        "Never invent facts. Cite evidence numbers like [1], [2] inline. "
        "If evidence is insufficient, say so clearly under Limits."
    )
    user = (
        f"Question: {question}\n\n"
        f"Evidence:\n{context}\n\n"
        "Write a readable briefing in exactly this Markdown structure:\n\n"
        "# Finding\n"
        "One clear paragraph answering the question.\n\n"
        "## Key points\n"
        "- 3 to 6 short bullet points with inline citations like [1]\n\n"
        "## Evidence notes\n"
        "- For each used citation: [n] what it supports + location\n\n"
        "## Limits\n"
        "- What is missing, uncertain, or unsupported\n"
    )

    try:
        return generate_answer(system, user)
    except Exception as exc:
        return f"(LLM unavailable: {exc})\n\n{extractive()}"


def get_source_or_raise(source_id: str) -> SourceRecord:
    item = store.get(source_id)
    if not item:
        raise KeyError(source_id)
    return item
