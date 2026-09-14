"""Detect whether /app/data (or DATA_DIR) looks like a durable disk mount."""

from __future__ import annotations

import os
from pathlib import Path

from app.config import settings


def _is_mount(path: Path) -> bool:
    """Best-effort: path is its own mountpoint (Render disk, Docker volume)."""
    try:
        path = path.resolve()
        if not path.exists():
            return False
        # Linux: compare device ids with parent
        parent = path.parent
        if path.stat().st_dev != parent.stat().st_dev:
            return True
        # Also check /proc/mounts when available
        mounts = Path("/proc/mounts")
        if mounts.exists():
            text = mounts.read_text(encoding="utf-8", errors="ignore")
            target = str(path)
            for line in text.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1].rstrip("/") == target.rstrip("/"):
                    return True
    except OSError:
        return False
    return False


def storage_status() -> dict:
    data_dir = Path(settings.data_dir)
    marker = data_dir / ".nexora_storage_marker"
    writable = False
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text("ok", encoding="utf-8")
        writable = marker.exists()
    except OSError:
        writable = False

    mounted = _is_mount(data_dir)
    # Render sets this when a disk is attached in some setups
    render_disk = bool(os.getenv("RENDER_DISK_MOUNT_PATH") or os.getenv("NEXORA_REQUIRE_DISK"))
    # Explicit override for ops
    force = (os.getenv("NEXORA_PERSISTENT_STORAGE") or "").strip().lower()
    if force in {"1", "true", "yes"}:
        persistent = True
    elif force in {"0", "false", "no"}:
        persistent = False
    else:
        persistent = bool(mounted or render_disk)

    return {
        "data_dir": str(data_dir),
        "writable": writable,
        "mounted": mounted,
        "persistent": persistent,
        "warning": (
            None
            if persistent
            else (
                "Ephemeral storage: uploads/index wipe on every Render redeploy. "
                "Attach a disk at /app/data (10GB+) in the Render dashboard."
            )
        ),
    }
