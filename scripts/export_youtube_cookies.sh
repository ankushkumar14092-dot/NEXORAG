#!/usr/bin/env bash
# Export YouTube cookies from your local browser for Render.
# Usage: ./scripts/export_youtube_cookies.sh [chrome|safari|firefox|edge]
set -euo pipefail
BROWSER="${1:-chrome}"
OUT="${2:-youtube.cookies.txt}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
echo "Exporting YouTube cookies from browser: $BROWSER"
if ! command -v yt-dlp >/dev/null 2>&1; then
  echo "yt-dlp not found. Activate .venv or: pip install yt-dlp"
  exit 1
fi
set +e
perl -e 'alarm shift; exec @ARGV' 45 yt-dlp \
  --cookies-from-browser "$BROWSER" \
  --cookies "$OUT" \
  --skip-download \
  "https://www.youtube.com/watch?v=dQw4w9WgXcQ" 2>"${OUT}.log"
rc=$?
set -e
if [[ ! -s "$OUT" ]]; then
  echo "Auto-export failed (exit $rc)."
  echo "Try: ./scripts/export_youtube_cookies.sh safari"
  echo "Or Chrome extension 'Get cookies.txt LOCALLY' → export youtube.com → save as $OUT"
  [[ -f "${OUT}.log" ]] && tail -n 8 "${OUT}.log" || true
  exit 1
fi
rm -f "${OUT}.log"
echo "Wrote $OUT ($(wc -l < "$OUT" | tr -d ' ') lines)"
echo
echo "Next: Render → Environment → YOUTUBE_COOKIES = (paste full file contents)"
echo "Then Redeploy. Check https://nexorag.onrender.com/api/health → youtube.cloud_youtube_ready: true"
echo "Do NOT commit $OUT to git."
echo "--- preview (first 5 lines) ---"
head -n 5 "$OUT"
