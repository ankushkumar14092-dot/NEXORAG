#!/usr/bin/env python3
"""Fetch YouTube captions on a local/residential IP and push them to Render.

Cloud hosts (Render/Vercel) get HTTP 403 / LOGIN_REQUIRED from YouTube.
This script runs on your laptop, then POSTs segments to production.

Usage:
  python scripts/ingest_youtube_remote.py 'https://www.youtube.com/watch?v=VIDEO_ID'
  python scripts/ingest_youtube_remote.py VIDEO_ID --api https://nexorag.onrender.com
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ingest import fetch_youtube_captions, resolve_title_from_url  # noqa: E402
from app.models import extract_youtube_id  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("url_or_id", help="YouTube URL or 11-char video id")
    p.add_argument(
        "--api",
        default="https://nexorag.onrender.com",
        help="Evidence Desk API base (Render)",
    )
    p.add_argument("--api-key", default="", help="Optional X-API-Key")
    args = p.parse_args()

    raw = args.url_or_id.strip()
    if extract_youtube_id(raw):
        url = raw if raw.startswith("http") else f"https://www.youtube.com/watch?v={raw}"
    elif len(raw) == 11:
        url = f"https://www.youtube.com/watch?v={raw}"
    else:
        print("Not a YouTube URL/id", file=sys.stderr)
        return 2

    print("Fetching captions locally…", url)
    video_id, segments, duration = fetch_youtube_captions(url)
    title = resolve_title_from_url(url)
    print(f"OK {video_id}: {len(segments)} segments, duration≈{duration}, title={title!r}")

    payload = {
        "url": url,
        "title": title,
        "segments": [{"start": s.start, "end": s.end, "text": s.text} for s in segments],
        "method": "youtube_captions_local_push",
    }
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["X-API-Key"] = args.api_key

    api = args.api.rstrip("/")
    print("Posting to", f"{api}/api/ingest/captions")
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        r = client.post(f"{api}/api/ingest/captions", json=payload, headers=headers)
        print("status", r.status_code, r.text[:400])
        r.raise_for_status()
        data = r.json()
        sid = (data.get("source") or {}).get("id")
        print("queued", sid, "— poll /api/sources until ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
