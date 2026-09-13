"""
Source detection + modality router.

Routes ANYTHING → TEXT | VISUAL | STRUCTURED for the Evidence Desk.
Does not invent connectors; it classifies and selects the right pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.ocr import IMAGE_EXTENSIONS

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".flv",
    ".wmv",
}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma"}
CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".kt",
    ".swift",
    ".scala",
    ".r",
    ".sql",
    ".sh",
    ".bash",
    ".ps1",
    ".yaml",
    ".yml",
    ".toml",
    ".json",  # also doc — treated as structured/text
}
DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".txt",
    ".md",
    ".markdown",
    ".pptx",
    ".html",
    ".htm",
    ".epub",
    ".xml",
}
STRUCTURED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv", ".json", ".xml"}


@dataclass
class RouteDecision:
    modality: str  # text | visual | structured
    pipeline: str  # document | video | web | code | image
    kind: str  # document | video
    reason: str
    extension: str | None = None


def detect_url(url: str) -> RouteDecision:
    u = (url or "").strip().lower()
    host = urlparse(u).netloc
    if "youtube.com" in host or "youtu.be" in host:
        return RouteDecision(
            modality="visual",
            pipeline="video",
            kind="video",
            reason="youtube_url",
        )
    return RouteDecision(
        modality="text",
        pipeline="web",
        kind="document",
        reason="web_url",
    )


def detect_file(filename: str) -> RouteDecision:
    suffix = Path(filename or "").suffix.lower()
    if suffix in VIDEO_EXTENSIONS or suffix in AUDIO_EXTENSIONS:
        return RouteDecision(
            modality="visual",
            pipeline="video",
            kind="video",
            reason="av_file",
            extension=suffix,
        )
    if suffix in IMAGE_EXTENSIONS:
        return RouteDecision(
            modality="visual",
            pipeline="image",
            kind="document",
            reason="image_ocr",
            extension=suffix,
        )
    if suffix in STRUCTURED_EXTENSIONS:
        return RouteDecision(
            modality="structured",
            pipeline="document",
            kind="document",
            reason="tabular_or_structured",
            extension=suffix,
        )
    if suffix in CODE_EXTENSIONS and suffix not in {".json"}:
        return RouteDecision(
            modality="text",
            pipeline="code",
            kind="document",
            reason="source_code",
            extension=suffix,
        )
    if suffix in DOCUMENT_EXTENSIONS or suffix in CODE_EXTENSIONS or suffix in IMAGE_EXTENSIONS:
        return RouteDecision(
            modality="text" if suffix not in IMAGE_EXTENSIONS else "visual",
            pipeline="document",
            kind="document",
            reason="document_file",
            extension=suffix,
        )
    raise ValueError(f"Unsupported source type: {suffix or filename}")


def location_type_for_chunk(location: str, kind: str) -> str:
    loc = (location or "").lower()
    if kind == "video" or ("-" in loc and ":" in loc and "page" not in loc):
        if ":" in loc:
            return "timestamp"
    if loc.startswith("page"):
        return "page"
    if "sheet" in loc or "!" in loc:
        return "cell"
    if loc.startswith("slide"):
        return "slide"
    if "ocr" in loc or loc.startswith("image"):
        return "region"
    if loc.startswith("url"):
        return "url"
    if "line" in loc or loc.startswith("file"):
        return "line"
    if loc.startswith("chapter"):
        return "chapter"
    return "passage"
