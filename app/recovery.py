from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.models import store
from app.rag import retriever
from app.hybrid import hybrid_retriever
from app.memory import query_cache
from app.security import ensure_under_data_dir


def backup_store() -> dict:
    """Zip durable store JSON into data/backups for recovery."""
    backup_dir = settings.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = backup_dir / f"store-{stamp}.zip"
    store_dir = settings.data_dir / "store"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(store_dir.glob("*.json")):
            zf.write(path, arcname=path.name)
        manifest = {
            "created_at": stamp,
            "revision": store.revision,
            "files": [p.name for p in store_dir.glob("*.json")],
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    return {
        "backup": str(out),
        "bytes": out.stat().st_size,
        "files": len(manifest["files"]),
        "revision": store.revision,
    }


def restore_store(backup_path: str | Path) -> dict:
    """Restore store from a backup zip under data/backups."""
    path = ensure_under_data_dir(Path(backup_path))
    if not path.is_file() or path.suffix.lower() != ".zip":
        raise ValueError("Backup must be a .zip under data/")
    if "backups" not in path.parts:
        raise ValueError("Only backups under data/backups are allowed")

    store_dir = settings.data_dir / "store"
    tmp = settings.data_dir / "store_restore_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    with zipfile.ZipFile(path, "r") as zf:
        zf.extractall(tmp)

    # Replace store json files
    for old in store_dir.glob("*.json"):
        old.unlink()
    for item in tmp.glob("*.json"):
        if item.name == "manifest.json":
            continue
        shutil.move(str(item), str(store_dir / item.name))
    shutil.rmtree(tmp, ignore_errors=True)

    store.reload()
    retriever.invalidate()
    hybrid_retriever.invalidate()
    query_cache.clear()
    return {
        "restored_from": str(path),
        "revision": store.revision,
        "sources": len(store.list()),
    }


def list_backups() -> list[dict]:
    backup_dir = settings.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in sorted(backup_dir.glob("store-*.zip"), reverse=True):
        rows.append(
            {
                "path": str(p),
                "name": p.name,
                "bytes": p.stat().st_size,
                "mtime": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
            }
        )
    return rows
