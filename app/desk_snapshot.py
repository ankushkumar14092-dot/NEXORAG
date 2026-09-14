"""Export/import desk sources so the browser can survive ephemeral Render disks."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from app.hybrid import hybrid_retriever
from app.memory import query_cache
from app.models import Segment, SourceRecord, chunk_from_dict, store
from app.rag import retriever

SNAPSHOT_VERSION = 1


def export_desk_snapshot(*, include_segments: bool = False) -> dict[str, Any]:
    items = []
    for rec in store.list():
        payload = asdict(rec)
        if not include_segments:
            payload["segments"] = []
        items.append(payload)
    return {
        "version": SNAPSHOT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "revision": store.revision,
        "source_count": len(items),
        "sources": items,
    }


def import_desk_snapshot(data: dict[str, Any], *, replace: bool = True) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Snapshot must be a JSON object")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Snapshot has no sources")
    if replace:
        for old in list(store.list()):
            store.delete(old.id)

    loaded = 0
    for raw in sources:
        if not isinstance(raw, dict) or "id" not in raw:
            continue
        segments = [Segment(**s) for s in (raw.get("segments") or []) if isinstance(s, dict)]
        chunks = [chunk_from_dict(c) for c in (raw.get("chunks") or []) if isinstance(c, dict)]
        # Drop incomplete rows
        if raw.get("status") != "ready" or not chunks:
            continue
        kind = raw.get("kind") or (
            "document" if str(raw.get("id", "")).startswith("doc_") else "video"
        )
        record = SourceRecord(
            id=str(raw["id"]),
            title=str(raw.get("title") or raw["id"])[:300],
            kind=kind,
            source_type=str(raw.get("source_type") or "url"),
            source=str(raw.get("source") or ""),
            status="ready",
            created_at=str(raw.get("created_at") or datetime.now(timezone.utc).isoformat()),
            updated_at=datetime.now(timezone.utc).isoformat(),
            error=None,
            duration=raw.get("duration"),
            transcript_method=raw.get("transcript_method") or "snapshot_restore",
            doc_type=raw.get("doc_type"),
            unit_count=int(raw.get("unit_count") or 0),
            chunk_count=len(chunks),
            segments=segments,
            chunks=chunks,
        )
        store.save(record)
        loaded += 1

    if loaded == 0:
        raise ValueError("No ready sources with chunks found in snapshot")

    retriever.invalidate()
    hybrid_retriever.invalidate()
    query_cache.clear()
    return {
        "ok": True,
        "imported": loaded,
        "revision": store.revision,
        "sources": len(store.list()),
    }
