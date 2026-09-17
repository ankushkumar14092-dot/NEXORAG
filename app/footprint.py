"""Process / desk memory footprint helpers for ops health."""

from __future__ import annotations

from typing import Any

from app.memory import query_cache
from app.models import store


def desk_footprint() -> dict[str, Any]:
    sources = store.list()
    ready = [s for s in sources if s.status == "ready"]
    chunks = sum(len(s.chunks) for s in ready)
    segments = sum(len(s.segments) for s in ready)
    approx_chars = 0
    for s in ready:
        for c in s.chunks:
            approx_chars += len(c.text or "")
        for seg in s.segments:
            approx_chars += len(seg.text or "")
    rss_mb = None
    try:
        import platform
        import resource
        import sys

        rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # Linux: KB; macOS: bytes
        if sys.platform == "darwin":
            rss_mb = round(rss / (1024.0 * 1024.0), 1)
        else:
            rss_mb = round(rss / 1024.0, 1)
        _ = platform.system()
    except Exception:
        rss_mb = None

    return {
        "sources": len(sources),
        "ready_sources": len(ready),
        "chunks": chunks,
        "segments": segments,
        "approx_text_mb": round(approx_chars / (1024 * 1024), 3),
        "query_cache": query_cache.stats(),
        "rss_mb": rss_mb,
        "note": (
            "Segments are dropped after chunking to save RAM; "
            "retrieval uses chunks only."
        ),
    }
