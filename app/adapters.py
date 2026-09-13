"""
Source adapter contract — extend Nexora without rewriting RAG core.

New modalities plug in as adapters that emit normalized Chunk units
with stable location labels. Core BM25 index stays unchanged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ExtractedUnit:
    text: str
    location: str
    kind: str  # document | video | audio | image | web | code | ...
    meta: dict[str, Any] | None = None


class SourceAdapter(ABC):
    """One ingest path → normalized evidence units."""

    id: str
    name: str
    status: str  # live | partial | next | later | out_of_scope

    @abstractmethod
    def can_handle(self, *, filename: str | None = None, url: str | None = None) -> bool:
        raise NotImplementedError

    @abstractmethod
    def extract(self, *, path: str | None = None, url: str | None = None) -> list[ExtractedUnit]:
        raise NotImplementedError


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, SourceAdapter] = {}

    def register(self, adapter: SourceAdapter) -> None:
        self._adapters[adapter.id] = adapter

    def get(self, adapter_id: str) -> SourceAdapter | None:
        return self._adapters.get(adapter_id)

    def list(self) -> list[dict[str, str]]:
        return [
            {"id": a.id, "name": a.name, "status": a.status}
            for a in self._adapters.values()
        ]

    def resolve(
        self, *, filename: str | None = None, url: str | None = None
    ) -> SourceAdapter | None:
        for adapter in self._adapters.values():
            if adapter.status in {"later", "out_of_scope"}:
                continue
            if adapter.can_handle(filename=filename, url=url):
                return adapter
        return None


registry = AdapterRegistry()
