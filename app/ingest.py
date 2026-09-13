from __future__ import annotations

import subprocess
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from app.config import settings
from app.models import Segment, extract_youtube_id


_whisper_model = None


def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        _whisper_model = WhisperModel(
            settings.whisper_model,
            device="cpu",
            compute_type="int8",
        )
    return _whisper_model


def fetch_youtube_captions(url: str) -> tuple[str, list[Segment], float | None]:
    video_id = extract_youtube_id(url)
    if not video_id:
        raise ValueError("Not a valid YouTube URL")

    api = YouTubeTranscriptApi()
    fetched = None
    last_err: Exception | None = None

    # Prefer explicit languages, then any listed track (incl. auto-generated).
    try:
        fetched = api.fetch(video_id, languages=["en", "hi", "en-US", "en-GB", "en-IN"])
    except Exception as exc:
        last_err = exc
        try:
            listing = api.list(video_id)
            # Manual tracks first, then generated
            ordered = sorted(
                list(listing),
                key=lambda t: (getattr(t, "is_generated", True), getattr(t, "language_code", "")),
            )
            for track in ordered:
                try:
                    fetched = track.fetch()
                    break
                except Exception as track_err:
                    last_err = track_err
        except Exception as list_err:
            last_err = list_err

    if fetched is None:
        raise RuntimeError(
            f"YouTube captions unavailable: {last_err or 'no transcript tracks'}"
        ) from last_err

    segments: list[Segment] = []
    for item in fetched:
        text = str(item.text).replace("\n", " ").strip()
        if not text:
            continue
        start = float(item.start)
        end = start + float(item.duration)
        segments.append(Segment(start=start, end=end, text=text))

    if not segments:
        raise RuntimeError("YouTube captions returned empty transcript")

    duration_val = segments[-1].end if segments else None
    return video_id, segments, duration_val


def download_audio_from_url(url: str, out_dir: Path) -> tuple[Path, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(out_dir / "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f",
        "bestaudio/best",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "5",
        "-o",
        out_tmpl,
        "--no-playlist",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "yt-dlp failed")

    mp3s = sorted(out_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mp3s:
        raise RuntimeError("Audio download produced no mp3 file")
    audio_path = mp3s[0]
    return audio_path, audio_path.stem


def extract_audio_from_video(video_path: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / f"{video_path.stem}.mp3"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "5",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg failed")
    return audio_path


def transcribe_audio(audio_path: Path) -> tuple[list[Segment], float | None]:
    model = get_whisper()
    segments_iter, info = model.transcribe(
        str(audio_path),
        beam_size=1,
        vad_filter=True,
    )
    segments: list[Segment] = []
    for seg in segments_iter:
        text = seg.text.strip()
        if not text:
            continue
        segments.append(Segment(start=float(seg.start), end=float(seg.end), text=text))
    duration = float(info.duration) if info and info.duration else (
        segments[-1].end if segments else None
    )
    return segments, duration


def resolve_title_from_url(url: str) -> str:
    """Best-effort title without requiring yt-dlp (cloud YouTube often blocks it)."""
    vid = extract_youtube_id(url)
    if vid:
        try:
            import httpx

            r = httpx.get(
                "https://www.youtube.com/oembed",
                params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
                timeout=10.0,
                follow_redirects=True,
            )
            if r.status_code == 200:
                title = (r.json() or {}).get("title")
                if title:
                    return str(title).strip()
        except Exception:
            pass
        return vid

    cmd = ["yt-dlp", "--get-title", "--no-playlist", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().splitlines()[0]
    return url
