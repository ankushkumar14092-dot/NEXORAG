from __future__ import annotations

from pathlib import Path

import aiofiles
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import ApiKeyMiddleware, api_key_configured
from app.capabilities import capabilities_payload
from app.config import settings
from app.docs_parse import DOC_EXTENSIONS
from app.llm import llm_status
from app.memory import query_cache
from app.metrics import metrics
from app.models import extract_youtube_id, is_youtube_host, store
from app.observability import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    configure_logging,
    logger,
)
from app.modality import detect_file, detect_url
from app.pipeline import (
    create_document_record,
    create_upload_record,
    create_url_record,
    create_web_record,
    process_document,
    process_prefetched_captions,
    process_video,
)
from app.recovery import backup_store, list_backups, restore_store
from app.security import SecurityError, sanitize_filename
from app.services import build_upload_path, query_service, source_service
from app.desk_snapshot import export_desk_snapshot, import_desk_snapshot
from app.footprint import desk_footprint
from app.storage_status import storage_status
from app.youtube_auth import youtube_auth_status

configure_logging()

app = FastAPI(title="Nexora Evidence Desk", version="0.6.0")
app.add_middleware(RateLimitMiddleware)
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(RequestContextMiddleware)

_cors = [o.strip() for o in (settings.cors_origins or "*").split(",") if o.strip()]
if _cors == ["*"]:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".flac",
}


class UrlIngestRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000)


class YoutubeCookiesInstallRequest(BaseModel):
    cookies: str = Field(..., min_length=20, max_length=500_000)
    setup_key: str = Field(..., min_length=8, max_length=200)


class DeskSnapshotImportRequest(BaseModel):
    snapshot: dict = Field(...)
    replace: bool = True
    setup_key: str = Field(default="", max_length=200)


class CaptionSegIn(BaseModel):
    start: float = Field(..., ge=0)
    end: float = Field(..., ge=0)
    text: str = Field(..., min_length=1, max_length=2000)


class CaptionsIngestRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000)
    title: str | None = Field(default=None, max_length=300)
    segments: list[CaptionSegIn] = Field(..., min_length=1, max_length=20000)
    method: str = Field(default="youtube_captions_proxy", max_length=64)


class WebIngestRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000)


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=4000)
    source_id: str | None = None
    video_id: str | None = None
    kind: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)


class RestoreRequest(BaseModel):
    backup_path: str = Field(..., min_length=3, max_length=1000)


async def _save_upload(file: UploadFile, dest: Path) -> None:
    size = 0
    max_bytes = settings.max_upload_mb * 1024 * 1024
    async with aiofiles.open(dest, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                await out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    400, f"File exceeds {settings.max_upload_mb}MB limit"
                )
            await out.write(chunk)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/config.js")
async def config_js() -> FileResponse:
    return FileResponse(STATIC_DIR / "config.js", media_type="application/javascript")


@app.get("/api/health")
async def health() -> dict:
    """Liveness: process is up."""
    return {
        "ok": True,
        "status": "live",
        "version": app.version,
        "architecture": "modular_monolith",
        "scale_mode": "vertical_single_process",
        "auth_required": api_key_configured(),
        **llm_status(),
        "whisper_model": settings.whisper_model,
        "videos": len(store.list(kind="video")),
        "documents": len(store.list(kind="document")),
        "store_revision": store.revision,
        "ram_cache": query_cache.stats(),
        "supported_docs": sorted(DOC_EXTENSIONS),
        "youtube": youtube_auth_status(),
        "storage": storage_status(),
        "memory": desk_footprint(),
    }


@app.get("/api/metrics")
async def api_metrics() -> dict:
    """Ops monitoring snapshot."""
    return {"ok": True, "metrics": metrics.snapshot()}


@app.get("/api/capabilities")
async def capabilities() -> dict:
    """Honest OmniRAG coverage map — live vs deferred."""
    return capabilities_payload()


@app.get("/api/ready")
async def ready() -> dict:
    """Readiness: store + data dirs usable."""
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _ = store.revision
        backup_dir = settings.data_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        return {
            "ok": True,
            "status": "ready",
            "store_revision": store.revision,
            "data_dir": str(settings.data_dir),
            "backups_dir": str(backup_dir),
            "auth_required": api_key_configured(),
            "storage": storage_status(),
        }
    except Exception as exc:
        logger.exception("readiness_failed")
        raise HTTPException(503, f"Not ready: {exc}") from exc


@app.get("/api/sources")
async def list_sources(kind: str | None = None) -> dict:
    if kind and kind not in {"video", "document"}:
        raise HTTPException(400, "kind must be video or document")
    return {"sources": source_service.list(kind=kind)}


@app.get("/api/youtube/captions")
async def youtube_captions_preview(url: str = "", v: str = "") -> dict:
    """Fetch captions now (used by cloud UI via local bridge, or ops checks)."""
    from app.ingest import fetch_youtube_captions, resolve_title_from_url

    raw = (url or "").strip() or (
        f"https://www.youtube.com/watch?v={v.strip()}" if v.strip() else ""
    )
    if not raw:
        raise HTTPException(400, "Provide url= or v=")
    try:
        video_id, segments, duration = fetch_youtube_captions(raw)
    except Exception as exc:
        raise HTTPException(502, f"Caption fetch failed: {exc}") from exc
    title = resolve_title_from_url(raw)
    return {
        "ok": True,
        "video_id": video_id,
        "title": title,
        "duration": duration,
        "segment_count": len(segments),
        "segments": [
            {"start": s.start, "end": s.end, "text": s.text} for s in segments
        ],
        "method": "youtube_captions",
    }


@app.post("/api/admin/youtube-cookies")
async def install_youtube_cookies_route(body: YoutubeCookiesInstallRequest) -> dict:
    """Install Netscape cookies on the server disk (Render).

    Auth: setup_key must match FIREWORKS_API_KEY already configured on the service.
    """
    from app.youtube_auth import install_youtube_cookies, youtube_auth_status

    expected = (settings.fireworks_api_key or "").strip()
    if not expected or body.setup_key.strip() != expected:
        raise HTTPException(401, "Invalid setup_key")
    try:
        path = install_youtube_cookies(body.cookies)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "path": str(path), "youtube": youtube_auth_status()}


@app.get("/api/videos")
async def list_videos() -> dict:
    return {"videos": source_service.list(kind="video")}


@app.get("/api/documents")
async def list_documents() -> dict:
    return {"documents": source_service.list(kind="document")}


@app.get("/api/videos/{video_id}")
async def get_video(video_id: str) -> dict:
    try:
        video = source_service.get(video_id)
    except (KeyError, SecurityError):
        raise HTTPException(404, "Video not found") from None
    if video.kind != "video":
        raise HTTPException(404, "Video not found")
    payload = video.public_dict()
    payload["segments"] = [
        {"start": s.start, "end": s.end, "text": s.text} for s in video.segments[:200]
    ]
    return payload


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: str) -> dict:
    try:
        doc = source_service.get(doc_id)
    except (KeyError, SecurityError):
        raise HTTPException(404, "Document not found") from None
    if doc.kind != "document":
        raise HTTPException(404, "Document not found")
    payload = doc.public_dict()
    payload["chunks"] = [
        {"id": c.id, "location": c.location_label, "text": c.text[:500]}
        for c in doc.chunks[:100]
    ]
    return payload


@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: str) -> dict:
    try:
        return source_service.delete(source_id)
    except SecurityError:
        raise HTTPException(400, "Invalid source id") from None
    except KeyError:
        raise HTTPException(404, "Source not found") from None


@app.delete("/api/videos/{video_id}")
async def delete_video(video_id: str) -> dict:
    return await delete_source(video_id)


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str) -> dict:
    return await delete_source(doc_id)


@app.post("/api/ingest/url")
async def ingest_url(
    body: UrlIngestRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http:// or https://")
    if is_youtube_host(url) and not extract_youtube_id(url):
        raise HTTPException(
            400,
            "Invalid or incomplete YouTube video id (expected 11 characters)",
        )
    record = create_url_record(url)
    background_tasks.add_task(process_video, record.id)
    return {"video": record.public_dict(), "source": record.public_dict()}


@app.post("/api/ingest/captions")
async def ingest_captions(
    body: CaptionsIngestRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """Index YouTube captions already fetched by the Vercel caption proxy."""
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http:// or https://")
    if not extract_youtube_id(url):
        raise HTTPException(400, "url must be a valid YouTube watch/shorts link")
    record = create_url_record(url)
    if body.title:
        record.title = body.title[:200]
        store.save(record)
    segs = [s.model_dump() for s in body.segments]
    background_tasks.add_task(
        process_prefetched_captions,
        record.id,
        segs,
        method=body.method or "youtube_captions_proxy",
        title=body.title,
    )
    return {"video": record.public_dict(), "source": record.public_dict()}


@app.post("/api/ingest/web")
async def ingest_web(
    body: WebIngestRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http:// or https://")
    record = create_web_record(url)
    background_tasks.add_task(process_document, record.id)
    return {"document": record.public_dict(), "source": record.public_dict()}


@app.post("/api/ingest/upload")
async def ingest_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> dict:
    if not file.filename:
        raise HTTPException(400, "Missing filename")

    safe_name = sanitize_filename(file.filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise HTTPException(400, f"Unsupported video/audio type: {suffix}")

    dest = build_upload_path(safe_name, docs=False)
    await _save_upload(file, dest)

    record = create_upload_record(safe_name, dest)
    background_tasks.add_task(process_video, record.id)
    return {"video": record.public_dict(), "source": record.public_dict()}


@app.post("/api/ingest/document")
async def ingest_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> dict:
    if not file.filename:
        raise HTTPException(400, "Missing filename")

    safe_name = sanitize_filename(file.filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in DOC_EXTENSIONS:
        raise HTTPException(
            400,
            f"Unsupported document type: {suffix}. Supported: {', '.join(sorted(DOC_EXTENSIONS))}",
        )

    dest = build_upload_path(safe_name, docs=True)
    await _save_upload(file, dest)

    record = create_document_record(safe_name, dest)
    background_tasks.add_task(process_document, record.id)
    return {"document": record.public_dict(), "source": record.public_dict()}


@app.post("/api/ingest/auto")
async def ingest_auto(
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(None),
    url: str | None = Form(None),
) -> dict:
    """Anything ingest: detect modality → route to video/doc/web pipeline."""
    if url and url.strip():
        clean = url.strip()
        decision = detect_url(clean)
        if decision.pipeline == "video":
            if is_youtube_host(clean) and not extract_youtube_id(clean):
                raise HTTPException(
                    400,
                    "Invalid or incomplete YouTube video id (expected 11 characters)",
                )
            record = create_url_record(clean)
            background_tasks.add_task(process_video, record.id)
        else:
            record = create_web_record(clean)
            background_tasks.add_task(process_document, record.id)
        return {
            "route": decision.__dict__,
            "source": record.public_dict(),
        }

    if not file or not file.filename:
        raise HTTPException(400, "Provide file or url")

    safe_name = sanitize_filename(file.filename)
    try:
        decision = detect_file(safe_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    if decision.pipeline == "video":
        dest = build_upload_path(safe_name, docs=False)
        await _save_upload(file, dest)
        record = create_upload_record(safe_name, dest)
        background_tasks.add_task(process_video, record.id)
    else:
        dest = build_upload_path(safe_name, docs=True)
        await _save_upload(file, dest)
        record = create_document_record(safe_name, dest)
        background_tasks.add_task(process_document, record.id)

    return {"route": decision.__dict__, "source": record.public_dict()}


@app.post("/api/admin/backup")
async def admin_backup() -> dict:
    return backup_store()


@app.get("/api/admin/backups")
async def admin_list_backups() -> dict:
    return {"backups": list_backups()}


@app.post("/api/admin/restore")
async def admin_restore(body: RestoreRequest) -> dict:
    try:
        return restore_store(body.backup_path)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from None


@app.get("/api/export/desk")
async def export_desk(include_segments: bool = False) -> dict:
    """Browser-friendly snapshot for surviving ephemeral Render disks."""
    return export_desk_snapshot(include_segments=include_segments)


@app.post("/api/import/desk")
async def import_desk(body: DeskSnapshotImportRequest) -> dict:
    """Restore sources/chunks from a previously exported snapshot."""
    expected = (settings.fireworks_api_key or "").strip()
    # If Fireworks key is configured, require it as setup_key to avoid public overwrite.
    if expected and body.setup_key.strip() != expected:
        # Allow unauthenticated import only when desk is empty (first boot recovery).
        if store.list():
            raise HTTPException(401, "setup_key required to replace a non-empty desk")
    try:
        return import_desk_snapshot(body.snapshot, replace=body.replace)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/query")
async def query(body: QueryRequest) -> dict:
    source_id = body.source_id or body.video_id
    if source_id:
        try:
            item = source_service.get(source_id)
        except SecurityError:
            raise HTTPException(400, "Invalid source id") from None
        except KeyError:
            raise HTTPException(404, "Source not found") from None
        if item.status != "ready":
            raise HTTPException(400, f"Source status is {item.status}")

    if body.kind and body.kind not in {"video", "document"}:
        raise HTTPException(400, "kind must be video or document")

    return query_service.run(
        body.question,
        source_id=source_id,
        kind=body.kind,
        top_k=body.top_k,
    )
