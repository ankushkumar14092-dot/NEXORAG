from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Any

from app.config import settings


class TempRAMCache:
    """
    Process-local temporary RAM store (not durable).

    Features:
    - TTL expiry
    - LRU eviction when entry/byte budget exceeded
    - Thread-safe

    Used for query briefing cache and short-lived index scratch data.
    """

    def __init__(
        self,
        max_entries: int | None = None,
        max_bytes: int | None = None,
        default_ttl_seconds: float | None = None,
    ) -> None:
        self.max_entries = max_entries or settings.ram_cache_max_entries
        self.max_bytes = max_bytes or settings.ram_cache_max_mb * 1024 * 1024
        self.default_ttl = default_ttl_seconds or settings.ram_cache_ttl_seconds
        self._lock = threading.RLock()
        self._data: OrderedDict[str, tuple[float, int, Any]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
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
        size = max(64, len(str(value)) + 128)
        with self._lock:
            if key in self._data:
                self._remove(key)
            self._data[key] = (expires, size, value)
            self._bytes += size
            self._trim()

    def invalidate_source(self, source_id: str) -> int:
        removed = 0
        with self._lock:
            drop: list[str] = []
            for k, (_e, _s, v) in self._data.items():
                if source_id in k:
                    drop.append(k)
                    continue
                if isinstance(v, dict):
                    if v.get("source_id") == source_id:
                        drop.append(k)
                        continue
                    sids = v.get("source_ids") or []
                    if source_id in sids:
                        drop.append(k)
            for k in drop:
                self._remove(k)
                removed += 1
                self._evictions += 1
        return removed

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._bytes = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._expire()
            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "entries": len(self._data),
                "bytes_used": self._bytes,
                "max_entries": self.max_entries,
                "max_bytes": self.max_bytes,
                "ttl_seconds": self.default_ttl,
            }

    def _remove(self, key: str) -> None:
        item = self._data.pop(key, None)
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
index_cache = TempRAMCache(
    max_entries=32,
    max_bytes=64 * 1024 * 1024,
    default_ttl_seconds=300,
)
