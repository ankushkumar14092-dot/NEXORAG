from __future__ import annotations

from app.models import Chunk, SourceRecord, store
from app.services import (
    QueryService,
    _SUMMARY_RE,
    _chunk_in_region,
    _detect_time_region,
)


def test_detect_time_region_phrases():
    assert _detect_time_region("middle last mein kya bola") == "middle_end"
    assert _detect_time_region("end mein kya bataya") == "end"
    assert _detect_time_region("beech mein relationships") == "middle"
    assert _detect_time_region("start se intro") == "start"
    # bare "last" / "last week" must NOT force end-of-video
    assert _detect_time_region("last week they mentioned fame") is None
    assert _detect_time_region("what was the last point about money") is None


def test_summary_regex_not_overbroad():
    assert _SUMMARY_RE.search("summarize the video")
    assert _SUMMARY_RE.search("is video mein kya bataya")
    assert _SUMMARY_RE.search("what is this video about")
    # topical Hinglish must stay retrieval, not forced summary sampling
    assert not _SUMMARY_RE.search("cheating ke baare mein kya bataya")
    assert not _SUMMARY_RE.search("what was the last point about money")
    assert not _SUMMARY_RE.search("relationships money fame")


def test_chunk_region_bounds():
    assert _chunk_in_region(100, 1000, "start")
    assert not _chunk_in_region(500, 1000, "start")
    assert _chunk_in_region(500, 1000, "middle")
    assert _chunk_in_region(900, 1000, "end")
    assert _chunk_in_region(500, 1000, "middle_end")
    assert not _chunk_in_region(100, 1000, "middle_end")


def test_query_middle_last_uses_latter_half(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    store._items.clear()
    store._revision = 0

    chunks = []
    for i, start in enumerate(range(0, 1000, 50)):
        chunks.append(
            Chunk(
                id=f"c{i}",
                source_id="vid_testregion01",
                video_id="vid_testregion01",
                start=float(start),
                end=float(start + 40),
                text=f"chunk at {start} seconds about topic {i}",
                start_label=f"{start // 60:02d}:{start % 60:02d}",
                end_label=f"{(start + 40) // 60:02d}:{(start + 40) % 60:02d}",
                location_label=(
                    f"{start // 60:02d}:{start % 60:02d}-"
                    f"{(start + 40) // 60:02d}:{(start + 40) % 60:02d}"
                ),
                kind="video",
            )
        )
    rec = SourceRecord(
        id="vid_testregion01",
        title="Test Video",
        kind="video",
        source_type="url",
        source="https://youtu.be/dQw4w9WgXcQ",
        status="ready",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        duration=1000.0,
        chunks=chunks,
        transcript_method="test",
    )
    store.save(rec)

    monkeypatch.setattr(
        "app.services.answer_with_context",
        lambda q, hits: f"hits={len(hits)}",
    )
    svc = QueryService()
    out = svc.run("middle last part summary", source_id=rec.id, top_k=4)
    starts = [e["start"] for e in out["evidence"]]
    assert starts, "expected evidence"
    assert min(starts) >= 400.0, starts
    assert all(s >= 400.0 for s in starts)
