# Nexora Evidence Desk

Citation-first **Video + Document RAG** for investigation workflows.  
Upload a file or paste a YouTube URL → indexed passages with locations → ask questions → get a grounded briefing with evidence you can jump to.

**Stack:** FastAPI · Fireworks LLM · faster-whisper · BM25 retrieval · Docker  

**Full guide:** see **[DOCUMENTATION.md](./DOCUMENTATION.md)** (setup, every feature, API, config, ops, troubleshooting).

---

## Why this exists

Most “chat with your PDF/video” tools hide the trail. Nexora is built as an **evidence desk**:

- Answers cite **timestamps** (video) or **locations** (page / sheet / slide)
- Sources can be **deleted** with index + disk cleanup
- Temporary answers live in a **RAM TTL/LRU cache** (not durable)
- Architecture stays a **modular monolith** — one deployable app, clean layers

---

## Features

| Area | What you get |
|------|----------------|
| **Video RAG** | YouTube URL (captions first, Whisper fallback) or local video/audio upload |
| **Document RAG** | PDF (incl. scanned OCR), DOCX, TXT/MD, CSV/TSV, XLSX, PPTX, JSON, XML, HTML, **EPUB**, **images (OCR)** |
| **Web page** | Single URL via `/api/ingest/web` |
| **Ask** | Scoped by mode (video/doc) or a single source |
| **Delete** | Removes store record, upload/audio files, cache entries, retriever index |
| **Retrieval** | **Hybrid**: BM25 keyword + TF-IDF sparse vector + structure/graph boost → RRF rerank |
| **Evidence** | Evidence graph + claim/conflict briefing; locations: page / cell / timestamp / region / line / URL |
| **Ops** | `/api/health` (live), `/api/ready`, `/api/capabilities`, request IDs, rate limit |
| **UI** | Editorial “Evidence Desk” UI — Archivo / Newsreader / IBM Plex Mono |

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│  UI (static)  ·  FastAPI edge                               │
│  Request ID · Rate limit · Live / Ready                     │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  Services                                                   │
│  SourceService (list/get/delete) · QueryService (+ RAM cache)│
└───────┬───────────────────────────────┬─────────────────────┘
        │                               │
┌───────▼──────────┐          ┌─────────▼──────────┐
│  Pipeline        │          │  RAG               │
│  ingest · parse  │          │  BM25 · LLM        │
│  whisper / ytdlp │          │  Fireworks → fallback│
└───────┬──────────┘          └─────────┬──────────┘
        │                               │
┌───────▼───────────────────────────────▼──────────┐
│  Store (JSON on disk + revision)                 │
│  TempRAMCache (TTL + LRU) · path sandbox security│
└──────────────────────────────────────────────────┘
```

| Layer | Modules |
|-------|---------|
| Edge / API | `app/main.py`, `app/observability.py` |
| Application | `app/services.py`, `app/pipeline.py` |
| Domain | `app/models.py`, `app/rag.py`, `app/llm.py`, `app/chunking.py` |
| Ingest | `app/ingest.py`, `app/docs_parse.py` |
| Platform | `app/config.py`, `app/security.py`, `app/memory.py` |

---

## Quick start

### Prerequisites

- Python **3.12+** (3.13 works locally)
- **ffmpeg** on `PATH` (Whisper / audio extract)
- A **Fireworks** API key ([fireworks.ai](https://fireworks.ai))

### 1. Setup

```bash
cd "vedieo transcription"
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env → set FIREWORKS_API_KEY=fw_...
```

### 2. Run (dev)

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

### 3. Run (Docker — recommended production shape)

```bash
docker compose up --build -d
# → http://127.0.0.1:8000
docker compose logs -f nexora
docker compose down
```

Data persists in the `nexora_data` volume at `/app/data` inside the container.

---

## Configuration

Copy `.env.example` → `.env`. Never commit `.env`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `FIREWORKS_API_KEY` | — | Required for LLM briefings |
| `FIREWORKS_MODEL` | `accounts/fireworks/models/deepseek-v4-pro-0813` | Chat model id |
| `FIREWORKS_BASE_URL` | `https://api.fireworks.ai/inference/v1` | OpenAI-compatible base |
| `WHISPER_MODEL` | `base` | `tiny` / `base` / `small` … (CPU cost ↑) |
| `MAX_UPLOAD_MB` | `500` | Upload size cap |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per-client sliding window |
| `RAM_CACHE_TTL_SECONDS` | `600` | Query cache TTL |
| `RAM_CACHE_MAX_ENTRIES` | `256` | Cache entry budget |
| `RAM_CACHE_MAX_MB` | `128` | Soft byte budget |

Optional: `ANTHROPIC_API_KEY` as LLM fallback if Fireworks is unset.

---

## How to use

1. Choose **Video** or **Documents** in the UI.
2. **Ingest** — paste a YouTube URL, upload media, or upload a document.
3. Wait until status is **ready** (polling is automatic).
4. Ask a question — optionally scope to one source.
5. Open citations / evidence passages; **Delete** when done.

**Video path:** captions → else download audio (yt-dlp) → Whisper → timed chunks.  
**Doc path:** structure-aware parse → location-labeled chunks → same query stack.

---

## API

Base URL: `http://127.0.0.1:8000`  
Interactive docs: `/docs` (FastAPI Swagger)

### Health & readiness

```http
GET /api/health   # liveness + LLM/cache stats
GET /api/ready    # store + data dir usable
```

Responses include:

- `X-Request-Id`
- `X-Response-Time-Ms`

### Sources

```http
GET    /api/sources?kind=video|document
GET    /api/videos
GET    /api/documents
GET    /api/videos/{id}
GET    /api/documents/{id}
DELETE /api/sources/{id}
DELETE /api/videos/{id}
DELETE /api/documents/{id}
```

### Ingest

```http
POST /api/ingest/url          { "url": "https://..." }
POST /api/ingest/upload       multipart file (video/audio)
POST /api/ingest/document     multipart file (document)
```

Ingest returns immediately with `status: queued`; processing runs in the background.

### Query

```http
POST /api/query
Content-Type: application/json

{
  "question": "What claims are made about revenue?",
  "kind": "document",
  "source_id": null,
  "top_k": 6
}
```

```json
{
  "answer": "# Finding\n...",
  "evidence": [ { "location": "Page 3", "text": "...", "score": 0.42 } ],
  "source_ids": ["doc_..."],
  "cache": "miss"
}
```

---

## Project layout

```text
.
├── app/
│   ├── main.py            # HTTP edge
│   ├── services.py        # Source + Query services
│   ├── pipeline.py        # Background ingest
│   ├── rag.py             # Retriever + answer orchestration
│   ├── llm.py             # Fireworks / Anthropic
│   ├── memory.py          # TempRAMCache (TTL + LRU)
│   ├── security.py        # IDs, filenames, path sandbox
│   ├── observability.py   # Logging, rate limit, request context
│   ├── models.py          # SourceRecord + JSON store
│   ├── ingest.py          # YouTube / Whisper / ffmpeg
│   ├── docs_parse.py      # Document extractors
│   ├── chunking.py
│   ├── config.py
│   └── static/index.html  # Evidence Desk UI
├── tests/
├── data/                  # local runtime (gitignored)
├── Dockerfile
├── docker-compose.yml
├── render.yaml
├── DEPLOY.md
├── requirements.txt
└── .env.example
```

---

## Security notes

- Source IDs validated (`vid_` / `doc_` + hex)
- Filenames sanitized; deletes **sandboxed** under `data/`
- Upload size enforced
- In-process rate limiting (single-node)
- Secrets via environment only — **not** baked into the image

Not included yet (by design for desk MVP): multi-tenant auth, SSO, audit log export. Add API keys / auth before public internet exposure.

---

## Tests

```bash
source .venv/bin/activate
PYTHONPATH=. pytest -q
```

Covers ID validation, path sandbox, and RAM cache TTL/LRU behavior.

---

## Deploy

See **[DEPLOY.md](./DEPLOY.md)** for Docker, Render Blueprint (`render.yaml`), and Fly.io notes.

Short version:

```bash
docker compose up --build -d
```

Cloud: push to GitHub → Render Blueprint or Fly → set `FIREWORKS_API_KEY` as a secret → attach a disk at `/app/data`.

> Whisper + large uploads need real RAM/disk. Free PaaS tiers often OOM.

---

## Design stance

- **Modular monolith** over microservices until a hard scale or team boundary appears  
- **Durable store** = JSON files under `data/store`  
- **Temporary memory** = process RAM cache (ephemeral by intent)  
- **Evidence first** — answers without passages are a product failure  

---

## License

Private / unlicensed unless you add one. Treat API keys and uploaded evidence as confidential.
