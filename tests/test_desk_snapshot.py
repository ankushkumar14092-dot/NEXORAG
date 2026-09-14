from __future__ import annotations

from app.desk_snapshot import export_desk_snapshot, import_desk_snapshot
from app.models import Chunk, SourceRecord, store
from app.storage_status import storage_status


def test_storage_status_shape(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    monkeypatch.setattr("app.storage_status.settings.data_dir", tmp_path)
    st = storage_status()
    assert st["writable"] is True
    assert "persistent" in st
    assert "data_dir" in st


def test_desk_snapshot_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    store.root = tmp_path / "store"
    store.root.mkdir(parents=True, exist_ok=True)
    store._items.clear()
    store._revision = 0

    chunks = [
        Chunk(
            id="c1",
            text="alpha relationship fame",
            source_id="vid_snaptest0001",
            location_label="00:00-00:20",
            kind="video",
            start=0.0,
            end=20.0,
            start_label="00:00",
            end_label="00:20",
        )
    ]
    rec = SourceRecord(
        id="vid_snaptest0001",
        title="Snap Test",
        kind="video",
        source_type="url",
        source="https://youtu.be/AAAAAAAAAAA",
        status="ready",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        duration=20.0,
        chunks=chunks,
        transcript_method="test",
    )
    store.save(rec)
    snap = export_desk_snapshot(include_segments=False)
    assert snap["source_count"] == 1

    store.delete(rec.id)
    assert store.list() == []

    out = import_desk_snapshot(snap, replace=True)
    assert out["imported"] == 1
    got = store.get("vid_snaptest0001")
    assert got is not None
    assert got.chunks and got.chunks[0].text.startswith("alpha")
