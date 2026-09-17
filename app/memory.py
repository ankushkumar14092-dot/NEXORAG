from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any

from app.config import settings


def _estimate_bytes(value: Any) -> int:
    """Prefer accurate JSON size so LRU byte budgets actually work."""
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str)
        return max(64, len(raw.encode("utf-8")) + 64)
    except Exception:
        return max(64, len(str(value)) + 128)


class TempRAMCache:
    """
    Process-local temporary RAM store (not durable).

    Features:
    - TTL expiry
    - LRU eviction when entry/byte budget exceeded
    - Skip caching oversized single payloads
    - Thread-safe

    Used for query briefing cache and short-lived index scratch data.
    """

    def __init__(
        self,
        max_entries: int | None = None,
        max_bytes: int | None = None,
        default_ttl_seconds: float | None = None,
        max_entry_bytes: int | None = None,
    ) -> None:
        self.max_entries = max_entries or settings.ram_cache_max_entries
        self.max_bytes = max_bytes or settings.ram_cache_max_mb * 1024 * 1024
        self.default_ttl = default_ttl_seconds or settings.ram_cache_ttl_seconds
        # Single entry hard cap (default 2MB) — prevents one briefing from blowing the budget
        self.max_entry_bytes = max_entry_bytes or min(2 * 1024 * 1024, self.max_bytes)
        self._lock = threading.RLock()
        self._data: OrderedDict[str, tuple[float, int, Any]] = OrderedDict()
        # key -> source_ids referenced by the cached payload (keys are hashes)
        self._key_sources: dict[str, set[str]] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._skipped_oversized = 0
        self._bytes = 0

    @staticmethod
    def make_key(*parts: Any) -> str:
        raw = "||".join("" if p is None else str(p) for p in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self._misses += 1
                return None
            expires, _size, value = item
            if expires < now:
                self._remove(key)
                self._misses += 1
                return None
            self._data.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        ttl = self.default_ttl if ttl_seconds is None else ttl_seconds
        expires = time.monotonic() + max(1.0, float(ttl))
        size = _estimate_bytes(value)
        with self._lock:
            if size > self.max_entry_bytes:
                self._skipped_oversized += 1
                return
            if key in self._data:
                self._remove(key)
            self._data[key] = (expires, size, value)
            self._bytes += size
            self._key_sources[key] = self._source_ids_from_value(value)
            self._trim()

    @staticmethod
    def _source_ids_from_value(value: Any) -> set[str]:
        found: set[str] = set()
        if not isinstance(value, dict):
            return found
        sid = value.get("source_id")
        if isinstance(sid, str) and sid:
            found.add(sid)
        for item in value.get("source_ids") or []:
            if isinstance(item, str) and item:
                found.add(item)
        for ev in value.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            for field in ("source_id", "video_id"):
                v = ev.get(field)
                if isinstance(v, str) and v:
                    found.add(v)
        return found

    def invalidate_source(self, source_id: str) -> int:
        removed = 0
        with self._lock:
            drop: list[str] = []
            for k, sids in self._key_sources.items():
                if source_id in sids:
                    drop.append(k)
            for k, (_e, _s, v) in self._data.items():
                if k in drop:
                    continue
                # Legacy payloads without index metadata
                if source_id in self._source_ids_from_value(v):
                    drop.append(k)
            for k in drop:
                self._remove(k)
                removed += 1
                self._evictions += 1
        return removed

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._key_sources.clear()
            self._bytes = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._expire()
            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "skipped_oversized": self._skipped_oversized,
                "entries": len(self._data),
                "bytes_used": self._bytes,
                "max_entries": self.max_entries,
                "max_bytes": self.max_bytes,
                "max_entry_bytes": self.max_entry_bytes,
                "ttl_seconds": self.default_ttl,
            }

    def _remove(self, key: str) -> None:
        item = self._data.pop(key, None)
        self._key_sources.pop(key, None)
        if item:
            self._bytes = max(0, self._bytes - item[1])

    def _expire(self) -> None:
        now = time.monotonic()
        for k in [k for k, (exp, _, _) in self._data.items() if exp < now]:
            self._remove(k)
            self._evictions += 1

    def _trim(self) -> None:
        self._expire()
        while self._data and (
            len(self._data) > self.max_entries or self._bytes > self.max_bytes
        ):
            k = next(iter(self._data))
            self._remove(k)
            self._evictions += 1


query_cache = TempRAMCache()
