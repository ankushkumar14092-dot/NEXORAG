from __future__ import annotations

import shutil
import traceback
from pathlib import Path

from app.chunking import doc_units_to_chunks, segments_to_chunks
from app.config import settings
from app.docs_parse import DOC_EXTENSIONS, parse_document
from app.ingest import (
    download_audio_from_url,
    extract_audio_from_video,
    fetch_youtube_captions,
    resolve_title_from_url,
    transcribe_audio,
)
from app.models import SourceRecord, extract_youtube_id, is_youtube_host, new_id, store, utc_now
from app.rag import retriever

def create_upload_record(filename: str, saved_path: Path) -> SourceRecord:
    record = SourceRecord(
        id=new_id("vid"),
        title=filename,
        kind="video",
        source_type="upload",
        source=str(saved_path),
        status="queued",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    store.save(record)
    return record

def create_url_record(url: str) -> SourceRecord:
    title = resolve_title_from_url(url)
    record = SourceRecord(
        id=new_id("vid"),
        title=title,
        kind="video",
        source_type="url",
        source=url,
        status="queued",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    store.save(record)
    return record

def create_document_record(filename: str, saved_path: Path) -> SourceRecord:
    record = SourceRecord(
        id=new_id("doc"),
        title=filename,
        kind="document",
        source_type="upload",
        source=str(saved_path),
        status="queued",
        created_at=utc_now(),
        updated_at=utc_now(),
        doc_type=saved_path.suffix.lower().lstrip(".") or "unknown",
    )
    store.save(record)
    return record


def create_web_record(url: str, title: str | None = None) -> SourceRecord:
    record = SourceRecord(
        id=new_id("doc"),
        title=(title or url)[:200],
        kind="document",
        source_type="url",
        source=url,
        status="queued",
        created_at=utc_now(),
        updated_at=utc_now(),
        doc_type="web",
    )
    store.save(record)
    return record

def process_video(video_id: str) -> None:
    record = store.get(video_id)
    if not record:
        return

    record.status = "processing"
    record.error = None
    store.save(record)

    try:
        if record.source_type == "url":
            _process_url(record)
        else:
            _process_upload(record)
        record.status = "ready"
        store.save(record)
        retriever.refresh()
    except Exception as exc:
        record.status = "error"
        record.error = str(exc)
        store.save(record)
        traceback.print_exc()

def process_document(doc_id: str) -> None:
    record = store.get(doc_id)
    if not record:
        return

    record.status = "processing"
    record.error = None
    store.save(record)

    try:
        if record.source_type == "url" and record.doc_type == "web":
            from app.web_fetch import fetch_web_page

            title, units = fetch_web_page(record.source)
            if title:
                record.title = title[:200]
            record.transcript_method = "web:fetch"
            record.doc_type = "web"
        else:
            path = Path(record.source)
            if not path.exists():
                raise FileNotFoundError(f"Document missing: {path}")
            if path.suffix.lower() not in DOC_EXTENSIONS:
                raise ValueError(f"Unsupported document type: {path.suffix}")

            units = parse_document(path)
            record.doc_type = path.suffix.lower().lstrip(".")
            record.transcript_method = f"parser:{record.doc_type}"

        record.chunks = doc_units_to_chunks(record.id, units)
        record.unit_count = len(units)
        if not record.chunks:
            raise RuntimeError("No chunks created from document")
        record.status = "ready"
        store.save(record)
        retriever.refresh()
    except Exception as exc:
        record.status = "error"
        record.error = str(exc)
        store.save(record)
        traceback.print_exc()

def _process_url(record: SourceRecord) -> None:
    url = record.source
    segments = []
    duration = None
    method = None

    yt_id = extract_youtube_id(url)

    if is_youtube_host(url) and not yt_id:
        raise ValueError(
            "Invalid or incomplete YouTube video id (expected 11 characters)"
        )

    if yt_id:
        try:
            _, segments, duration = fetch_youtube_captions(url)
            method = "youtube_captions"
        except Exception:
            segments = []

    if not segments:
        audio_dir = settings.data_dir / "audio" / record.id
        audio_path, _ = download_audio_from_url(url, audio_dir)
        segments, duration = transcribe_audio(audio_path)
        method = "whisper"

    if not segments:
        raise RuntimeError("No transcript segments produced")

    record.segments = segments
    record.duration = duration
    record.transcript_method = method
    record.chunks = segments_to_chunks(record.id, segments)

def _process_upload(record: SourceRecord) -> None:
    video_path = Path(record.source)
    if not video_path.exists():
        raise FileNotFoundError(f"Upload missing: {video_path}")

    audio_dir = settings.data_dir / "audio" / record.id
    suffix = video_path.suffix.lower()
    if suffix in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}:
        audio_path = audio_dir / video_path.name
        audio_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video_path, audio_path)
    else:
        audio_path = extract_audio_from_video(video_path, audio_dir)

    segments, duration = transcribe_audio(audio_path)
    if not segments:
        raise RuntimeError("Whisper produced empty transcript")

    record.segments = segments
    record.duration = duration
    record.transcript_method = "whisper"
    record.chunks = segments_to_chunks(record.id, segments)
