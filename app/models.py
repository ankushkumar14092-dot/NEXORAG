from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def format_ts(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def extract_youtube_id(url: str) -> str | None:
    patterns = [
        r"(?:v=|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:embed/)([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def is_youtube_host(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return "youtube.com" in host or "youtu.be" in host


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Chunk:
    id: str
    text: str
    source_id: str
    location_label: str
    kind: str = "video"  # video | document
    start: float = 0.0
    end: float = 0.0
    start_label: str = ""
    end_label: str = ""
    # legacy alias used by older video JSON
    video_id: str = ""

    def __post_init__(self) -> None:
        if not self.source_id and self.video_id:
            self.source_id = self.video_id
        if not self.video_id and self.source_id:
            self.video_id = self.source_id
        if not self.location_label and (self.start_label or self.end_label):
            self.location_label = f"{self.start_label}-{self.end_label}".strip("-")


@dataclass
class SourceRecord:
    id: str
    title: str
    kind: str  # video | document
    source_type: str  # upload | url
    source: str
    status: str  # queued | processing | ready | error
    created_at: str
    updated_at: str
    error: str | None = None
    duration: float | None = None
    transcript_method: str | None = None
    doc_type: str | None = None
    unit_count: int = 0
    chunk_count: int = 0
    segments: list[Segment] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "source_type": self.source_type,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "duration": self.duration,
            "transcript_method": self.transcript_method,
            "doc_type": self.doc_type,
            "unit_count": self.unit_count,
            "segment_count": len(self.segments),
            "chunk_count": self.chunk_count,
        }


# Back-compat alias
VideoRecord = SourceRecord


def chunk_from_dict(data: dict[str, Any]) -> Chunk:
    source_id = data.get("source_id") or data.get("video_id") or ""
    location = data.get("location_label") or ""
    if not location:
        start_label = data.get("start_label", "")
        end_label = data.get("end_label", "")
        if start_label or end_label:
            location = f"{start_label}-{end_label}".strip("-")
    return Chunk(
        id=data["id"],
        text=data.get("text", ""),
        source_id=source_id,
        location_label=location,
        kind=data.get("kind", "video"),
        start=float(data.get("start", 0) or 0),
        end=float(data.get("end", 0) or 0),
        start_label=data.get("start_label", ""),
        end_label=data.get("end_label", ""),
        video_id=data.get("video_id") or source_id,
    )


class Store:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (settings.data_dir / "store")
        self.root.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, SourceRecord] = {}
        self._lock = __import__("threading").RLock()
        self._revision = 0
        self._load_all()

    @property
    def revision(self) -> int:
        return self._revision

    def _bump(self) -> None:
        self._revision += 1

    def _path(self, item_id: str) -> Path:
        # Harden: only allow simple ids in filenames
        if "/" in item_id or "\\" in item_id or ".." in item_id:
            raise ValueError("Invalid id")
        return self.root / f"{item_id}.json"

    def _load_all(self) -> None:
        self._items = {}
        for path in self.root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                segments = [Segment(**s) for s in data.get("segments", [])]
                chunks = [chunk_from_dict(c) for c in data.get("chunks", [])]
                kind = data.get("kind")
                if not kind:
                    kind = "document" if str(data.get("id", "")).startswith("doc_") else "video"
                record = SourceRecord(
                    id=data["id"],
                    title=data["title"],
                    kind=kind,
                    source_type=data["source_type"],
                    source=data["source"],
                    status=data["status"],
                    created_at=data["created_at"],
                    updated_at=data["updated_at"],
                    error=data.get("error"),
                    duration=data.get("duration"),
                    transcript_method=data.get("transcript_method"),
                    doc_type=data.get("doc_type"),
                    unit_count=data.get("unit_count", 0),
                    chunk_count=data.get("chunk_count", len(chunks)),
                    segments=segments,
                    chunks=chunks,
                )
                self._items[record.id] = record
            except Exception:
                continue

    def reload(self) -> None:
        with self._lock:
            self._load_all()

    def save(self, record: SourceRecord) -> None:
        with self._lock:
            record.updated_at = utc_now()
            record.chunk_count = len(record.chunks)
            if record.kind == "document":
                record.unit_count = record.unit_count or len(record.chunks)
            payload = asdict(record)
            self._path(record.id).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._items[record.id] = record
            self._bump()

    def get(self, item_id: str) -> SourceRecord | None:
        with self._lock:
            return self._items.get(item_id)

    def list(self, kind: str | None = None) -> list[SourceRecord]:
        with self._lock:
            items = list(self._items.values())
        if kind:
            items = [i for i in items if i.kind == kind]
        return sorted(items, key=lambda v: v.created_at, reverse=True)

    def delete(self, item_id: str) -> bool:
        with self._lock:
            path = self._path(item_id)
            existed = item_id in self._items or path.exists()
            if item_id in self._items:
                del self._items[item_id]
            if path.exists():
                path.unlink()
            if existed:
                self._bump()
            return existed


store = Store()
