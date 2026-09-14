# Deploy Nexora Evidence Desk

Production shape: **one Docker container** (FastAPI + ffmpeg + Whisper). Persist `/app/data` on a volume.

## 1. Local production run (Docker)

```bash
# from project root, with .env present
docker compose up --build -d
open http://127.0.0.1:8000
```

Required env (in `.env` or host secrets):

- `FIREWORKS_API_KEY`
- `FIREWORKS_MODEL` (default: `accounts/fireworks/models/deepseek-v4-pro-0813`)

## 2. Render (easiest cloud)

1. Push this repo to GitHub (do **not** commit `.env`).
2. [Render](https://dashboard.render.com) → New → Blueprint → select repo (`render.yaml`).
3. Set secret `FIREWORKS_API_KEY` in the dashboard.
4. Use a plan with **disk** (Blueprint mounts `/app/data`). **Without disk, uploads vanish on every deploy.**

If your live service was created outside Blueprint (e.g. named `nexorag`), open Render → Disks → add disk mounted at `/app/data`, then set env:

```text
DATA_DIR=/app/data
NEXORA_PERSISTENT_STORAGE=true
PREFER_FAST_MODEL=true
```

The UI also mirrors the desk snapshot into browser `localStorage` and auto-restores after an empty redeploy.

Whisper + uploads need RAM/disk — free tiers often OOM. Prefer **Starter** or higher.

### YouTube on Render (required for URL ingest)

YouTube blocks Render/Vercel datacenter IPs (`403` / “Sign in to confirm you’re not a bot”).
Localhost works because your home IP is not blocked.

**Pick one:**

#### A) Browser cookies (recommended for URL ingest)

Fast path (no Render dashboard paste):

```bash
./scripts/export_youtube_cookies.sh safari   # or chrome
PYTHONPATH=. python scripts/push_youtube_cookies.py
```

This POSTs cookies to `/api/admin/youtube-cookies` (auth = your `FIREWORKS_API_KEY`) and stores them on the Render disk.

Manual path:

1. Chrome extension “Get cookies.txt LOCALLY” → export youtube.com while logged in.
2. Render → Environment → `YOUTUBE_COOKIES` = paste file contents → redeploy.

Check `GET /api/health` → `youtube.cloud_youtube_ready: true`.

Cookies expire — re-export + push when YouTube ingest breaks again.

Cloud UI tip: with local `uvicorn` on `:8000`, Process can fetch captions via localhost bridge even before cookies are installed.

#### B) Residential HTTP proxy

```text
YOUTUBE_HTTP_PROXY=http://user:pass@proxy-host:port
```

#### C) No cookies / proxy

- Upload the **video/audio file**, or
- From your laptop: `PYTHONPATH=. python scripts/ingest_youtube_remote.py 'YOUTUBE_URL'`

## 3. Fly.io

```bash
fly launch --name nexora-evidence --region sin --no-deploy
fly secrets set FIREWORKS_API_KEY=fw_...
fly volumes create nexora_data --size 10 --region sin
# attach volume to /app/data in fly.toml [[mounts]]
fly deploy
```

## 4. Frontend on Vercel (UI only)

Backend stays on Render (`https://nexorag.onrender.com`). Static UI:

```bash
cd frontend
vercel --prod
```

`frontend/config.js` sets `window.NEXORA_API_BASE`. On Render, set `CORS_ORIGINS=*` (default) or your Vercel URL.

## 5. Notes

- Single worker (`--workers 1`) — in-memory RAM cache + BM25 index are process-local.
- Do not bake API keys or YouTube cookies into the image / git.
- Faster answers: set `PREFER_FAST_MODEL=true` on Render.
