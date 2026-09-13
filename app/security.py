from __future__ import annotations

import re
import secrets
from pathlib import Path

from app.config import settings

_ID_RE = re.compile(r"^(vid|doc)_[a-f0-9]{12}$")
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._\- ]+")


class SecurityError(ValueError):
    pass


def validate_source_id(source_id: str) -> str:
    sid = (source_id or "").strip()
    if not _ID_RE.match(sid):
        raise SecurityError("Invalid source id")
    return sid


def sanitize_filename(name: str, max_len: int = 120) -> str:
    base = Path(name or "upload").name
    cleaned = _UNSAFE_NAME.sub("_", base).strip(" ._")
    if not cleaned:
        cleaned = f"upload_{secrets.token_hex(4)}"
    if len(cleaned) > max_len:
        stem = Path(cleaned).stem[: max_len - 20]
        suffix = Path(cleaned).suffix[:12]
        cleaned = f"{stem}{suffix}"
    return cleaned


def data_root() -> Path:
    return settings.data_dir.resolve()


def ensure_under_data_dir(path: Path) -> Path:
    """Prevent path traversal — only allow files inside data_dir."""
    root = data_root()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SecurityError("Path escapes data directory") from exc
    return resolved


def safe_unlink(path: Path | str | None) -> bool:
    if not path:
        return False
    try:
        p = ensure_under_data_dir(Path(path))
    except SecurityError:
        return False
    if p.is_file():
        p.unlink(missing_ok=True)
        return True
    return False


def safe_rmtree(path: Path | str | None) -> bool:
    if not path:
        return False
    try:
        p = ensure_under_data_dir(Path(path))
    except SecurityError:
        return False
    if not p.exists() or not p.is_dir():
        return False
    # Only allow known subdirs
    rel = str(p.relative_to(data_root()))
    if not (rel.startswith("audio/") or rel.startswith("uploads/")):
        return False
    import shutil

    shutil.rmtree(p, ignore_errors=True)
    return True
