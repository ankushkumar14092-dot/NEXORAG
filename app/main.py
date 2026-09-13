from __future__ import annotations

from pathlib import Path

import aiofiles
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings
from app.docs_parse import DOC_EXTENSIONS
from app.llm import llm_status
from app.memory import query_cache
from app.models import store
from app.observability import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    configure_logging,
    logger,
)
from app.pipeline import (
    create_document_record,
    create_upload_record,
    create_url_record,
    process_document,
    process_video,
)
from app.security import SecurityError, sanitize_filename
from app.services import build_upload_path, query_service, source_service

configure_logging()

app = FastAPI(title="Nexora Evidence Desk", version="0.4.0")
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)

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


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=4000)
    source_id: str | None = None
    video_id: str | None = None
    kind: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)


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


@app.get("/api/health")
async def health() -> dict:
    """Liveness: process is up."""
    return {
        "ok": True,
        "status": "live",
        **llm_status(),
        "whisper_model": settings.whisper_model,
        "videos": len(store.list(kind="video")),
        "documents": len(store.list(kind="document")),
        "store_revision": store.revision,
        "ram_cache": query_cache.stats(),
        "supported_docs": sorted(DOC_EXTENSIONS),
    }


@app.get("/api/ready")
async def ready() -> dict:
    """Readiness: store + data dirs usable."""
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _ = store.revision
        return {
            "ok": True,
            "status": "ready",
            "store_revision": store.revision,
            "data_dir": str(settings.data_dir),
        }
    except Exception as exc:
        logger.exception("readiness_failed")
        raise HTTPException(503, f"Not ready: {exc}") from exc


@app.get("/api/sources")
async def list_sources(kind: str | None = None) -> dict:
    if kind and kind not in {"video", "document"}:
        raise HTTPException(400, "kind must be video or document")
    return {"sources": source_service.list(kind=kind)}


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
    record = create_url_record(url)
    background_tasks.add_task(process_video, record.id)
    return {"video": record.public_dict(), "source": record.public_dict()}


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
