# Nexora Evidence Desk — Complete Documentation

**Product:** Citation-first Video + Document evidence RAG  
**Version:** 0.6.0  
**Repo:** https://github.com/ankushkumar14092-dot/NEXORAG  
**Local URL:** http://127.0.0.1:8000  
**API docs (Swagger):** http://127.0.0.1:8000/docs  

This file is the full project guide: product, architecture, features, setup, usage, API, config, security, ops, limits, and troubleshooting. Nothing important is left only in chat history.

---

## 1. What this project is

Nexora Evidence Desk is a **modular monolith** app for investigation-style Q&A:

1. Ingest a **video**, **audio**, **document**, **image**, **EPUB**, **code file**, or **web page**
2. Chunk content with **stable locations** (page, cell, timestamp, region, line, URL)
3. Ask in **English** or **Hinglish** (and Devanagari Hindi)
4. Get a **briefing** with Finding / Claims / Key points / Evidence notes / Conflicts / Limits
5. Jump from citations to exact source passages

It is **not** a full “OmniRAG / connect every SaaS” platform. Those categories are mapped in `/api/capabilities` with honest status (`live` / `partial` / `next` / `later` / `out_of_scope`).

---

## 2. Feature list (what works today)

### 2.1 Ingest — Video / Audio
| Input | How | Output locations |
|-------|-----|------------------|
| YouTube URL | Captions first, else yt-dlp + Whisper | `mm:ss–mm:ss` timestamps |
| Video file | Upload → audio extract → Whisper | timestamps |
| Audio file | Upload → Whisper | timestamps |

Supported media extensions: `.mp4` `.mov` `.mkv` `.webm` `.avi` `.mp3` `.wav` `.m4a` `.aac` `.ogg` `.flac`

### 2.2 Ingest — Documents
| Type | Extensions | Location labels |
|------|------------|-----------------|
| PDF | `.pdf` | Page N (OCR fallback for scanned) |
| Word | `.docx` | Paragraph / table |
| Text / Markdown | `.txt` `.md` `.markdown` | Section |
| Spreadsheet | `.xlsx` `.xls` `.csv` `.tsv` | Sheet / cell range |
| Presentation | `.pptx` | Slide N |
| Data | `.json` `.xml` | Path / XML section |
| HTML file | `.html` `.htm` | HTML section |
| EPUB | `.epub` | Chapter |
| Images (OCR) | `.jpg` `.jpeg` `.png` `.webp` `.bmp` `.tif` `.tiff` `.gif` | Image (OCR) / region |
| Code | `.py` `.js` `.ts` `.java` `.go` `.rs` `.sql` `.sh` … | `file lines A–B` |

### 2.3 Ingest — Web
| Input | Endpoint | Notes |
|-------|----------|-------|
| Single web page URL | `POST /api/ingest/web` | Fetches page, extracts readable text, cites URL sections |
| Auto route | `POST /api/ingest/auto` | Detects YouTube vs web vs file modality |

### 2.4 Ask / RAG
- Hybrid retrieval:
  - **Keyword:** BM25
  - **Vector (sparse):** TF-IDF cosine
  - **Graph/structure:** title + location overlap
  - **Rerank:** Reciprocal Rank Fusion + term coverage
- Typo correction (e.g. `langraph` → `langgraph`) against corpus vocabulary
- Multilingual:
  - English → English answer
  - Hinglish → Hinglish answer + Devanagari search variants
  - Devanagari Hindi → Hindi answer (+ English search expansion when needed)
- Query RAM cache (TTL + LRU)
- Evidence graph in API response (lite, in-process — not Neo4j)
- Briefing sections include **Claims** and **Conflicts**

### 2.5 Source management
- List / get videos & documents
- **Delete** source (UI + API): removes JSON index, upload/audio files (sandboxed), invalidates cache + retriever

### 2.6 Operations / enterprise desk features
| Feature | Endpoint / config |
|---------|-------------------|
| Liveness | `GET /api/health` |
| Readiness | `GET /api/ready` |
| Metrics | `GET /api/metrics` |
| Capability map | `GET /api/capabilities` |
| Backup store | `POST /api/admin/backup` |
| List backups | `GET /api/admin/backups` |
| Restore backup | `POST /api/admin/restore` |
| API key lock | `API_KEY` + header `X-API-Key` |
| Rate limit | `RATE_LIMIT_PER_MINUTE` |
| 5xx alerts | `ALERT_WEBHOOK_URL` |
| Request tracing | `X-Request-Id`, `X-Response-Time-Ms` |

---

## 3. Architecture (how it works)

```text
User (UI or API)
    │
    ▼
FastAPI edge
  · ApiKeyMiddleware (optional)
  · RateLimitMiddleware
  · RequestContextMiddleware (logs + metrics + security headers)
    │
    ▼
Modality router (detect file/URL → text | visual | structured)
    │
    ▼
Pipeline ingest (background)
  video/audio → captions/Whisper
  docs/images/epub/code → parsers / OCR
  web URL → fetch + extract
    │
    ▼
JSON durable store (data/store/*.json) + revision
    │
    ▼
Hybrid retriever (BM25 + TF-IDF + structure) → RRF rerank
    │
    ▼
Evidence graph (query-time) + Fireworks LLM briefing
    │
    ▼
Trusted answer + evidence locations
```

### 3.1 Important modules

| Module | Role |
|--------|------|
| `app/main.py` | HTTP API + static UI |
| `app/services.py` | SourceService, QueryService |
| `app/pipeline.py` | Background ingest jobs |
| `app/modality.py` | Source detection + router |
| `app/docs_parse.py` | Document/image/epub/code parsers |
| `app/ocr.py` | Tesseract OCR + scanned PDF OCR |
| `app/web_fetch.py` | Single-page web ingest |
| `app/ingest.py` | YouTube / Whisper / ffmpeg helpers |
| `app/rag.py` | BM25 base + answer prompting |
| `app/hybrid.py` | Hybrid channels + RRF |
| `app/language.py` | English / Hinglish / Hindi |
| `app/query_fast.py` | Typo correction |
| `app/evidence_graph.py` | Lite evidence graph |
| `app/memory.py` | TempRAMCache TTL/LRU |
| `app/security.py` | IDs, filenames, path sandbox |
| `app/auth.py` | Optional API key |
| `app/observability.py` | Logging, rate limit, alerts |
| `app/metrics.py` | In-process metrics |
| `app/recovery.py` | Backup / restore |
| `app/capabilities.py` | Honest feature catalog |
| `app/static/index.html` | Evidence Desk UI |

### 3.2 Data on disk

```text
data/
  store/          # durable source JSON (survives restart)
  uploads/        # uploaded videos
  uploads/docs/   # uploaded documents/images
  audio/          # extracted/downloaded audio per source
  backups/        # zip backups from /api/admin/backup
```

`.env`, `.venv/`, and `data/` are gitignored. Never commit secrets.

---

## 4. Prerequisites

- **Python** 3.12+ (3.13 OK)
- **ffmpeg** on PATH (video/audio)
- **Tesseract OCR** (images + scanned PDF)
- **Poppler** (`pdftoppm`) for scanned PDF OCR
- **Fireworks API key** (primary LLM)
- Optional: Anthropic key as fallback
- Optional: Docker Desktop (compose deploy)

macOS examples:

```bash
brew install ffmpeg tesseract poppler
```

---

## 5. Setup (local Python)

```bash
cd "vedieo transcription"   # or your clone of NEXORAG
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env — set FIREWORKS_API_KEY=fw_...
```

### 5.1 Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

- UI: http://127.0.0.1:8000  
- Swagger: http://127.0.0.1:8000/docs  

### 5.2 Tests

```bash
PYTHONPATH=. pytest -q
```

---

## 6. Setup (Docker)

```bash
# .env must exist with FIREWORKS_API_KEY
docker compose up --build -d
open http://127.0.0.1:8000
docker compose logs -f nexora
docker compose down
```

Docker image includes: Python, ffmpeg, tesseract, poppler.  
Persist volume mounts `/app/data`.

See also `DEPLOY.md` for Render / Fly notes.

---

## 7. Configuration reference (`.env`)

| Variable | Default | Meaning |
|----------|---------|---------|
| `FIREWORKS_API_KEY` | — | Required for LLM briefings |
| `FIREWORKS_MODEL` | `.../deepseek-v4-pro-0813` | Primary quality model |
| `FIREWORKS_FAST_MODEL` | `.../deepseek-v4p1-flash` | Faster model |
| `PREFER_FAST_MODEL` | `false` | Prefer fast model first (lower latency) |
| `ANSWER_MAX_TOKENS` | `700` | Max answer tokens |
| `FIREWORKS_BASE_URL` | Fireworks OpenAI-compatible URL | |
| `ANTHROPIC_API_KEY` | — | Optional fallback if Fireworks unset |
| `WHISPER_MODEL` | `base` | `tiny`/`base`/`small`… |
| `MAX_UPLOAD_MB` | `500` | Upload size limit |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per-client sliding window |
| `RAM_CACHE_TTL_SECONDS` | `600` | Query cache TTL |
| `RAM_CACHE_MAX_ENTRIES` | `256` | Cache entry cap |
| `RAM_CACHE_MAX_MB` | `128` | Soft byte budget |
| `API_KEY` | empty | If set, require `X-API-Key` on most `/api/*` |
| `ALERT_WEBHOOK_URL` | empty | POST JSON on HTTP 5xx / exceptions |

After changing `.env`, **restart** the server.

---

## 8. How to use the UI (step-by-step)

### 8.1 Documents mode
1. Open http://127.0.0.1:8000  
2. Ensure **Documents** tab is active  
3. Either:
   - Paste a **web page URL** → **Index page**  
   - Or **Upload** PDF/DOCX/Excel/EPUB/image/code/…  
4. Wait until source status is **ready** (auto-poll)  
5. Optionally set **Scope** to one source  
6. Type question (English or Hinglish) → **Ask**  
7. Read Folio briefing; use evidence list / citations  
8. **Delete** removes source + files  

### 8.2 Video mode
1. Switch to **Video**  
2. Paste YouTube URL → **Process**  
   or upload video/audio → **Upload**  
3. Wait for **ready** (`youtube_captions` or Whisper)  
4. Ask questions scoped to that reel if needed  

### 8.3 Languages
| You type | Answer style | Retrieval help |
|----------|--------------|----------------|
| English | English | Typo fix + hybrid |
| Hinglish (`is video mein kya…`) | Hinglish | Roman→Devanagari map (+ optional LLM expand on miss) |
| Devanagari Hindi | Hindi | Unicode BM25 + English expand when needed |

### 8.4 Optional API key in browser
If `API_KEY` is set in `.env`:

```js
localStorage.setItem("nexora_api_key", "your-secret")
```

All UI `fetch` calls send `X-API-Key`.

---

## 9. API guide

Base: `http://127.0.0.1:8000`  
Auth (if configured): header `X-API-Key: <key>` or `Authorization: Bearer <key>`  
Public without key: `/`, `/api/health`, `/api/ready`, `/api/metrics`, `/docs`

### 9.1 Health & ops

```bash
curl -s http://127.0.0.1:8000/api/health | jq
curl -s http://127.0.0.1:8000/api/ready | jq
curl -s http://127.0.0.1:8000/api/metrics | jq
curl -s http://127.0.0.1:8000/api/capabilities | jq
```

### 9.2 List sources

```bash
curl -s "http://127.0.0.1:8000/api/sources?kind=document" | jq
curl -s "http://127.0.0.1:8000/api/sources?kind=video" | jq
curl -s http://127.0.0.1:8000/api/videos | jq
curl -s http://127.0.0.1:8000/api/documents | jq
```

### 9.3 Ingest

**YouTube / video URL**

```bash
curl -s -X POST http://127.0.0.1:8000/api/ingest/url \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID"}' | jq
```

**Web page**

```bash
curl -s -X POST http://127.0.0.1:8000/api/ingest/web \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com"}' | jq
```

**Video/audio upload**

```bash
curl -s -F "file=@./sample.mp4" http://127.0.0.1:8000/api/ingest/upload | jq
```

**Document / image / epub / code**

```bash
curl -s -F "file=@./report.pdf" http://127.0.0.1:8000/api/ingest/document | jq
curl -s -F "file=@./shot.png" http://127.0.0.1:8000/api/ingest/document | jq
curl -s -F "file=@./main.py" http://127.0.0.1:8000/api/ingest/document | jq
```

**Auto (router)**

```bash
curl -s -X POST "http://127.0.0.1:8000/api/ingest/auto?url=https://example.com" | jq
curl -s -F "file=@./notes.pdf" http://127.0.0.1:8000/api/ingest/auto | jq
```

Ingest returns immediately with `status: queued`. Processing runs in background until `ready` or `error`.

### 9.4 Query

```bash
curl -s -X POST http://127.0.0.1:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "why we use langgraph and its benefits",
    "kind": "video",
    "source_id": null,
    "top_k": 6
  }' | jq
```

Response fields (important):

| Field | Meaning |
|-------|---------|
| `answer` | Markdown briefing |
| `evidence[]` | Passages with `location`, `location_type`, `score`, `channels` |
| `evidence_graph` | Nodes/edges for explainability |
| `retrieval` | hybrid mode, variants, corrected query |
| `cache` | `hit` / `miss` |

### 9.5 Delete

```bash
curl -s -X DELETE http://127.0.0.1:8000/api/sources/vid_xxxxxxxxxxxx | jq
# aliases:
# DELETE /api/videos/{id}
# DELETE /api/documents/{id}
```

### 9.6 Backup / restore

```bash
curl -s -X POST http://127.0.0.1:8000/api/admin/backup | jq
curl -s http://127.0.0.1:8000/api/admin/backups | jq
curl -s -X POST http://127.0.0.1:8000/api/admin/restore \
  -H 'Content-Type: application/json' \
  -d '{"backup_path":"/absolute/path/to/data/backups/store-YYYYMMDDThhmmssZ.zip"}' | jq
```

Restore only accepts zip files under `data/backups/` (path sandbox).

---

## 10. Evidence location types

| `location_type` | Typical sources |
|-----------------|-----------------|
| `page` | PDF |
| `cell` | Excel / CSV ranges |
| `slide` | PPTX |
| `timestamp` | Video / audio |
| `region` | Image OCR |
| `url` | Web page |
| `line` | Source code |
| `chapter` | EPUB |
| `passage` | Generic fallback |

---

## 11. System design checklist (desk-scale)

| Pillar | Status in v0.6 |
|--------|----------------|
| Maintainability / modularity / readability | Strong |
| Extensibility (adapters + capabilities) | Strong |
| Testability | Stronger (12+ unit tests) — expand e2e as needed |
| Scalability | Vertical single-process by design |
| Reliability | LLM fallbacks, delete cleanup, revisioned index |
| Availability | health + ready |
| Performance | Fast retrieve; LLM dominates latency; RAM cache; optional fast model |
| Security | Path sandbox, ID validation, rate limit, optional API key, security headers |
| Monitoring | `/api/metrics` + health cache stats |
| Logging | request id + latency |
| Deployment | Docker, compose, render.yaml, GitHub |
| Alerting | optional webhook on 5xx |
| Recovery | backup/restore zip |

**Not claimed:** multi-region HA, Neo4j knowledge graph DB, SQL warehouse RAG, OAuth connectors (Drive/Slack/Gmail). Those remain in capabilities as later / out_of_scope.

---

## 12. Latency expectations

| Step | Typical |
|------|---------|
| Hybrid retrieval (dozens–thousands of chunks) | milliseconds–low hundreds ms |
| Fireworks briefing (DeepSeek Pro) | often ~10–40s |
| Same question again (cache hit) | ~instant |
| With `PREFER_FAST_MODEL=true` | usually faster, quality may differ |

UI text “Retrieving… then writing briefing (LLM)” is honest: most wait time is LLM, not BM25.

---

## 13. Security notes

- Never commit `.env`
- Source IDs must match `vid_` / `doc_` + 12 hex chars
- Deletes cannot escape `data/`
- Upload size capped
- Rate limited per client path bucket
- If `API_KEY` set, lock mutating/query APIs
- Backup restore path-restricted to `data/backups`

Before public internet exposure: set `API_KEY`, reverse proxy (HTTPS), and consider auth beyond a shared key.

---

## 14. Troubleshooting

| Problem | Fix |
|---------|-----|
| 401 Unauthorized | Set `localStorage nexora_api_key` or send `X-API-Key`; or clear `API_KEY` in `.env` |
| Query empty / no evidence | Check source `ready`; fix typos; try Hinglish/English alternate; confirm scope |
| LLM slow | Enable `PREFER_FAST_MODEL=true`; rely on cache for repeats |
| OCR fails | Install tesseract (+ poppler for scanned PDF) |
| YouTube fails | Network/blocks; captions may be disabled → Whisper path needs ffmpeg |
| Docker disk full | `docker system prune` or run uvicorn locally |
| `.env` change ignored | Restart uvicorn/compose |
| Port 8000 busy | Kill old process or change port |

---

## 15. Project layout

```text
.
├── DOCUMENTATION.md          ← this file
├── README.md                 ← short overview
├── DEPLOY.md                 ← deploy notes
├── Dockerfile
├── docker-compose.yml
├── render.yaml
├── requirements.txt
├── .env.example
├── app/
│   ├── main.py
│   ├── static/index.html
│   └── … (modules listed above)
├── tests/
│   ├── test_security_memory.py
│   └── test_ops_quality.py
└── data/                     ← runtime (gitignored)
```

---

## 16. Quick command cheat-sheet

```bash
# dev
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# tests
PYTHONPATH=. pytest -q

# docker
docker compose up --build -d

# health
curl -s http://127.0.0.1:8000/api/health | jq .status
```

---

## 17. Version history (high level)

| Version | Highlights |
|---------|------------|
| 0.1–0.2 | Video RAG + document parsers + UI |
| 0.3 | Delete, RAM cache, services layer |
| 0.4 | Ops logging/rate limit; BM25 |
| 0.5 | OCR, web, EPUB, code, hybrid, multilingual |
| 0.6 | Auth, metrics, alerts, backup/restore, expanded tests |

---

*End of documentation. For interactive API exploration use `/docs`. For capability honesty use `/api/capabilities`.*
