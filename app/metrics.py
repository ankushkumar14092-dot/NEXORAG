from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


class MetricsRegistry:
    """In-process ops metrics (desk-scale monitoring)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.started_at = time.time()
        self.requests_total = 0
        self.errors_total = 0
        self.status_counts: dict[str, int] = defaultdict(int)
        self.path_counts: dict[str, int] = defaultdict(int)
        self.latency_ms_sum = 0.0
        self.latency_ms_max = 0.0
        self.query_total = 0
        self.query_cache_hits = 0
        self.alerts_sent = 0

    def record_request(self, path: str, status: int, latency_ms: float) -> None:
        with self._lock:
            self.requests_total += 1
            self.status_counts[str(status)] += 1
            key = path.split("?")[0]
            if key.startswith("/api/"):
                self.path_counts[key] += 1
            self.latency_ms_sum += latency_ms
            if latency_ms > self.latency_ms_max:
                self.latency_ms_max = latency_ms
            if status >= 500:
                self.errors_total += 1

    def record_query(self, *, cache_hit: bool) -> None:
        with self._lock:
            self.query_total += 1
            if cache_hit:
                self.query_cache_hits += 1

    def record_alert(self) -> None:
        with self._lock:
            self.alerts_sent += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            avg = (
                self.latency_ms_sum / self.requests_total
                if self.requests_total
                else 0.0
            )
            return {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "requests_total": self.requests_total,
                "errors_total": self.errors_total,
                "status_counts": dict(self.status_counts),
                "path_counts": dict(sorted(self.path_counts.items(), key=lambda x: -x[1])[:30]),
                "latency_ms_avg": round(avg, 2),
                "latency_ms_max": round(self.latency_ms_max, 2),
                "query_total": self.query_total,
                "query_cache_hits": self.query_cache_hits,
                "query_cache_hit_rate": round(
                    (self.query_cache_hits / self.query_total) if self.query_total else 0.0,
                    4,
                ),
                "alerts_sent": self.alerts_sent,
            }


metrics = MetricsRegistry()
