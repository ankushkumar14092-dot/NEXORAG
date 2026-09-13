"""Fast query normalization — typos + weak-hit expansion without slowing happy path."""

from __future__ import annotations

import re
from functools import lru_cache

from app.rag import _tokenize, retriever


def _edit_distance_le(a: str, b: str, max_dist: int = 2) -> bool:
    if abs(len(a) - len(b)) > max_dist:
        return False
    # classic DP early-exit
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        row_min = i
        for j, cb in enumerate(b, start=1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (ca != cb)
            val = min(ins, delete, sub)
            cur.append(val)
            if val < row_min:
                row_min = val
        if row_min > max_dist:
            return False
        prev = cur
    return prev[-1] <= max_dist


@lru_cache(maxsize=8)
def _vocab_for_revision(revision: int) -> frozenset[str]:
    retriever._ensure_fresh()
    vocab: set[str] = set()
    for toks in retriever.tokenized:
        vocab.update(toks)
    # also title tokens
    for title in retriever.titles.values():
        vocab.update(_tokenize(title))
    return frozenset(vocab)


def correct_query_typos(question: str) -> str:
    """
    Replace OOV Latin tokens with nearest corpus token (edit distance ≤1 for short,
    ≤2 for longer). Keeps latency tiny — no LLM.
    """
    retriever._ensure_fresh()
    vocab = _vocab_for_revision(retriever._built_revision)
    if not vocab:
        return question

    parts = re.split(r"(\s+)", question or "")
    out: list[str] = []
    for part in parts:
        if not part or part.isspace():
            out.append(part)
            continue
        # only correct simple latin tokens
        m = re.fullmatch(r"[A-Za-z][A-Za-z0-9']{2,}", part)
        if not m:
            out.append(part)
            continue
        low = part.lower()
        if low in vocab:
            out.append(part)
            continue
        max_d = 1 if len(low) <= 6 else 2
        best = None
        for v in vocab:
            if abs(len(v) - len(low)) > max_d:
                continue
            if not re.fullmatch(r"[a-z0-9']+", v):
                continue
            if _edit_distance_le(low, v, max_d):
                # prefer similar length
                if best is None or abs(len(v) - len(low)) < abs(len(best) - len(low)):
                    best = v
        out.append(best if best else part)
    return "".join(out)
