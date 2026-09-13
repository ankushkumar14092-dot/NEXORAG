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
4. Use a plan with **disk** (Blueprint mounts `/app/data`).

Whisper + uploads need RAM/disk — free tiers often OOM. Prefer **Starter** or higher.

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
- Video URL ingest needs outbound network + yt-dlp; some hosts block YouTube.
- Do not bake API keys into the image.
