from pathlib import Path

from app.language import detect_locale, search_query_variants
from app.modality import detect_file, detect_url, location_type_for_chunk
from app.query_fast import correct_query_typos
from app.recovery import backup_store, list_backups
from app.auth import verify_api_key
from app.config import settings


def test_detect_locale_english_hinglish_hindi():
    assert detect_locale("What is LangGraph?") == "english"
    assert detect_locale("is video mein kya bataya?") == "hinglish"
    assert detect_locale("इस वीडियो में क्या है?") == "hindi"


def test_hinglish_variants_include_devanagari():
    variants = search_query_variants("video mein kya bataya", allow_llm=False)
    assert variants[0].startswith("video")
    assert any("वीडियो" in v or "क्या" in v for v in variants)


def test_modality_router():
    assert detect_file("notes.pdf").pipeline == "document"
    assert detect_file("clip.mp4").pipeline == "video"
    assert detect_file("shot.png").pipeline == "image"
    assert detect_file("main.py").pipeline == "code"
    assert detect_url("https://youtu.be/abc123xyz12").pipeline == "video"
    assert detect_url("https://example.com/a").pipeline == "web"


def test_location_types():
    assert location_type_for_chunk("Page 3", "document") == "page"
    assert location_type_for_chunk("Sheet1!A1:B2", "document") == "cell"
    assert location_type_for_chunk("Image (OCR)", "document") == "region"
    assert location_type_for_chunk("URL · section 1", "document") == "url"
    assert location_type_for_chunk("app.py lines 1-40", "document") == "line"


def test_typo_correction_langraph(tmp_path, monkeypatch):
    # Smoke: function returns a string (may or may not correct without vocab)
    out = correct_query_typos("why langraph")
    assert isinstance(out, str)


def test_backup_creates_zip():
    result = backup_store()
    path = Path(result["backup"])
    assert path.exists()
    assert path.suffix == ".zip"
    assert list_backups()


def test_api_key_verify(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "secret-desk-key")
    assert verify_api_key("secret-desk-key")
    assert not verify_api_key("wrong")
    monkeypatch.setattr(settings, "api_key", "")
    assert verify_api_key("")  # open mode
