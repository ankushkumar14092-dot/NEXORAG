from pathlib import Path

import pytest

from app.memory import TempRAMCache
from app.security import SecurityError, ensure_under_data_dir, sanitize_filename, validate_source_id


def test_validate_source_id_ok():
    assert validate_source_id("vid_abcdef123456") == "vid_abcdef123456"
    assert validate_source_id("doc_ffffffffffff") == "doc_ffffffffffff"


def test_validate_source_id_rejects_traversal():
    with pytest.raises(SecurityError):
        validate_source_id("../etc/passwd")
    with pytest.raises(SecurityError):
        validate_source_id("vid_short")


def test_sanitize_filename_strips_paths():
    name = sanitize_filename("../../evil.pdf")
    assert "/" not in name
    assert ".." not in name
    assert name.endswith(".pdf")


def test_ensure_under_data_dir_blocks_escape(tmp_path, monkeypatch):
    from app import security

    monkeypatch.setattr(security, "data_root", lambda: tmp_path.resolve())
    ok = ensure_under_data_dir(tmp_path / "uploads" / "a.txt")
    assert ok == (tmp_path / "uploads" / "a.txt").resolve()
    with pytest.raises(SecurityError):
        ensure_under_data_dir(Path("/tmp/outside.txt"))


def test_ram_cache_ttl_and_lru():
    cache = TempRAMCache(max_entries=2, max_bytes=10_000_000, default_ttl_seconds=60)
    cache.set("a", {"v": 1})
    cache.set("b", {"v": 2})
    assert cache.get("a")["v"] == 1
    cache.set("c", {"v": 3})  # evicts oldest unused (b if a was touched)
    assert cache.get("b") is None
    assert cache.get("c")["v"] == 3
    stats = cache.stats()
    assert stats["entries"] <= 2
    assert stats["hits"] >= 1


def test_ram_cache_byte_budget_and_oversized_skip():
    cache = TempRAMCache(
        max_entries=50,
        max_bytes=2_000,
        default_ttl_seconds=60,
        max_entry_bytes=1_500,
    )
    cache.set("small", {"text": "hi"})
    assert cache.get("small") is not None
    cache.set("huge", {"text": "x" * 5000})
    assert cache.get("huge") is None
    assert cache.stats()["skipped_oversized"] >= 1
    # Fill past byte budget with many medium entries
    for i in range(20):
        cache.set(f"k{i}", {"text": "y" * 200})
    assert cache.stats()["bytes_used"] <= cache.max_bytes
    assert cache.stats()["entries"] <= cache.max_entries


def test_ram_cache_invalidate_source_by_payload():
    cache = TempRAMCache(max_entries=10, max_bytes=10_000_000, default_ttl_seconds=60)
    k1 = TempRAMCache.make_key("q", "vid_aaa", "hello")
    k2 = TempRAMCache.make_key("q", "vid_bbb", "hello")
    cache.set(
        k1,
        {
            "answer": "a",
            "source_ids": ["vid_aaa111111"],
            "evidence": [{"source_id": "vid_aaa111111", "text": "x"}],
        },
    )
    cache.set(
        k2,
        {
            "answer": "b",
            "source_ids": ["vid_bbb222222"],
            "evidence": [{"source_id": "vid_bbb222222", "text": "y"}],
        },
    )
    removed = cache.invalidate_source("vid_aaa111111")
    assert removed == 1
    assert cache.get(k1) is None
    assert cache.get(k2) is not None
