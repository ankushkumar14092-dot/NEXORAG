#!/usr/bin/env python3
"""Push local YouTube cookies.txt to Render so URL ingest works in the cloud.

Usage:
  PYTHONPATH=. python scripts/push_youtube_cookies.py
  PYTHONPATH=. python scripts/push_youtube_cookies.py youtube.cookies.txt
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "cookies_file",
        nargs="?",
        default=str(ROOT / "youtube.cookies.txt"),
        help="Netscape cookies.txt path",
    )
    p.add_argument("--api", default="https://nexorag.onrender.com")
    args = p.parse_args()

    path = Path(args.cookies_file)
    if not path.exists():
        print(f"Missing {path}. Run: ./scripts/export_youtube_cookies.sh safari", file=sys.stderr)
        return 2

    key = (os.getenv("FIREWORKS_API_KEY") or "").strip()
    if not key:
        print("FIREWORKS_API_KEY missing in .env", file=sys.stderr)
        return 2

    cookies = path.read_text(encoding="utf-8", errors="ignore")
    api = args.api.rstrip("/")
    print("Pushing", path, "->", f"{api}/api/admin/youtube-cookies")
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        r = client.post(
            f"{api}/api/admin/youtube-cookies",
            json={"cookies": cookies, "setup_key": key},
        )
        print(r.status_code, r.text[:500])
        r.raise_for_status()
        data = r.json()
        print("youtube:", data.get("youtube"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
