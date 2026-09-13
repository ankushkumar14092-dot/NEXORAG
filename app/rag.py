from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from app.config import settings
from app.language import answer_language_rules, no_evidence_reply
from app.llm import generate_answer
from app.models import Chunk, SourceRecord, store

# Split on whitespace / common punctuation so Devanagari words stay intact.
_TOKEN_RE = re.compile(r"[\s.,;:!?\[\](){}/\\\\|\"\']+")


def _tokenize(text: str) -> list[str]:
    """Unicode-aware tokens for multilingual BM25 (Hindi, etc.)."""
    norm = unicodedata.normalize("NFC", text or "")
    return [tok.lower() for tok in _TOKEN_RE.split(norm) if tok]

@dataclass
class Hit:
    chunk: Chunk
    score: float
    source_title: str


class Retriever:
    """
    In-process BM25 retrieval (vectorless sparse RAG).

    Faster and better-ranked than TF-IDF for keyword/evidence search
    at desk scale — no vector database required.
    """

    def __init__(self) -> None:
        self.bm25: BM25Okapi | None = None
        self.tokenized: list[list[str]] = []
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
        self.tokenized = []
        self.bm25 = None

        for item in store.list():
            if item.status != "ready":
                continue
            self.titles[item.id] = item.title
            self.chunks.extend(item.chunks)

        if not self.chunks:
            self._built_revision = store.revision
            return

        self.tokenized = [_tokenize(c.text) for c in self.chunks]
        # BM25Okapi needs at least one doc; empty-token docs get a placeholder
        corpus = [toks if toks else ["_empty_"] for toks in self.tokenized]
        self.bm25 = BM25Okapi(corpus)
        self._built_revision = store.revision

    def refresh(self) -> None:
        self._rebuild()

    def invalidate(self) -> None:
        """Force rebuild on next search (after delete/ingest)."""
        self._built_revision = -1
        self.bm25 = None
        self.tokenized = []
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
        if self.bm25 is None or not self.chunks:
            return []

        allowed_ids = None
        if kind:
            allowed_ids = {i.id for i in store.list(kind=kind) if i.status == "ready"}

        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        scores = self.bm25.get_scores(q_tokens)
        # Rank high → low without building a full sorted copy of all chunks
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

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

        # Drop non-positive scores; do not fabricate floor matches for a source.
        return [h for h in hits if h.score > 0]


retriever = Retriever()


def answer_with_context(question: str, hits: list[Hit]) -> str:
    if not hits:
        return no_evidence_reply(question)

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
        "You are an evidence briefing writer for an investigation desk. "
        "Answer ONLY from the provided evidence. Never invent facts. "
        "Cite evidence numbers like [1], [2] inline. "
        "If passages conflict, state both sides under Conflicts. "
        "If evidence is insufficient, say so clearly under Limits. "
        "Your response must start with the Finding heading — never with analysis about the user. "
        + answer_language_rules(question)
    )
    user = (
        f"Question: {question}\n\n"
        f"Evidence (may be in another language — translate findings into the question language):\n{context}\n\n"
        "Write a readable briefing in exactly this Markdown structure "
        "(section titles may be translated to match the question language):\n\n"
        "# Finding\n"
        "One clear paragraph answering the question.\n\n"
        "## Claims\n"
        "- Claim statements grounded in evidence, each with citation like [1] and support: strong|moderate|weak\n\n"
        "## Key points\n"
        "- 3 to 6 short bullet points with inline citations like [1]\n\n"
        "## Evidence notes\n"
        "- For each used citation: [n] what it supports + location\n\n"
        "## Conflicts\n"
        "- Contradictions or tensions between passages (or 'None detected')\n\n"
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
