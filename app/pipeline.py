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

    caption_error: Exception | None = None
    if yt_id:
        try:
            _, segments, duration = fetch_youtube_captions(url)
            method = "youtube_captions"
        except Exception as exc:
            caption_error = exc
            segments = []

    if not segments:
        # Render/cloud IPs are bot-blocked unless cookies or residential proxy are set.
        from app.youtube_auth import ensure_youtube_cookie_file, youtube_proxy_url

        allow_download = bool(settings.youtube_download_fallback) or bool(
            ensure_youtube_cookie_file() or youtube_proxy_url()
        )
        if yt_id and not allow_download:
            _ = caption_error
            raise RuntimeError(
                "YouTube blocked this cloud server (HTTP 403). "
                "Fix: set YOUTUBE_COOKIES on Render (browser cookies.txt), "
                "or upload the video file, "
                "or run locally: python scripts/ingest_youtube_remote.py <URL>"
            )
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

def apply_caption_segments(
    record: SourceRecord,
    segments: list,
    *,
    method: str = "youtube_captions",
    title: str | None = None,
) -> None:
    """Index pre-fetched caption segments onto a video record."""
    from app.models import Segment

    typed: list[Segment] = []
    for item in segments:
        if isinstance(item, Segment):
            typed.append(item)
            continue
        text = str(getattr(item, "text", None) or item.get("text") or "").strip()
        if not text:
            continue
        start = float(getattr(item, "start", None) or item.get("start") or 0)
        end = float(getattr(item, "end", None) or item.get("end") or start + 2)
        typed.append(Segment(start=start, end=end, text=text))
    if not typed:
        raise RuntimeError("No caption segments to index")
    if title:
        record.title = title[:200]
    record.segments = typed
    record.duration = typed[-1].end
    record.transcript_method = method
    record.chunks = segments_to_chunks(record.id, typed)
    record.status = "ready"
    record.error = None
    record.updated_at = utc_now()
    store.save(record)
    retriever.refresh()


def process_prefetched_captions(
    video_id: str,
    segments: list,
    *,
    method: str = "youtube_captions_proxy",
    title: str | None = None,
) -> None:
    record = store.get(video_id)
    if not record:
        return
    record.status = "processing"
    record.error = None
    store.save(record)
    try:
        apply_caption_segments(record, segments, method=method, title=title)
    except Exception as exc:
        record.status = "error"
        record.error = str(exc)
        store.save(record)
        traceback.print_exc()


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
