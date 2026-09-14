from __future__ import annotations

import re
import subprocess
from pathlib import Path

import httpx
from youtube_transcript_api import YouTubeTranscriptApi

from app.config import settings
from app.models import Segment, extract_youtube_id


_whisper_model = None
_YT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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


def _segments_from_json3(payload: dict) -> list[Segment]:
    segments: list[Segment] = []
    for event in payload.get("events") or []:
        segs = event.get("segs") or []
        if not segs:
            continue
        text = "".join(str(s.get("utf8") or "") for s in segs).replace("\n", " ").strip()
        if not text:
            continue
        start_ms = float(event.get("tStartMs") or 0)
        dur_ms = float(event.get("dDurationMs") or 0)
        start = start_ms / 1000.0
        end = (start_ms + dur_ms) / 1000.0 if dur_ms else start + 2.0
        segments.append(Segment(start=start, end=end, text=text))
    return segments



def _fetch_captions_innertube(video_id: str) -> list[Segment]:
    """InnerTube + timedtext. Uses cookies/proxy when configured (needed on Render)."""
    from app.youtube_auth import httpx_client_kwargs

    clients = [
        {"clientName": "ANDROID", "clientVersion": "20.10.38"},
        {
            "clientName": "IOS",
            "clientVersion": "20.10.4",
            "deviceModel": "iPhone16,2",
            "osName": "iOS",
            "osVersion": "17.5",
        },
    ]
    preferred = ("en", "hi", "en-US", "en-GB", "en-IN")
    errors: list[str] = []

    with httpx.Client(**httpx_client_kwargs()) as client:
        for cinfo in clients:
            body = {
                "context": {"client": {**cinfo, "hl": "en", "gl": "US"}},
                "videoId": video_id,
            }
            resp = client.post(
                "https://www.youtube.com/youtubei/v1/player?prettyPrint=false",
                json=body,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                errors.append(f"{cinfo['clientName']}: HTTP {resp.status_code}")
                continue
            data = resp.json()
            play = (data.get("playabilityStatus") or {}).get("status")
            if play and play != "OK":
                reason = (data.get("playabilityStatus") or {}).get("reason") or play
                errors.append(f"{cinfo['clientName']}: {reason}")
                continue
            tracks = (
                ((data.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {})
                .get("captionTracks")
                or []
            )
            if not tracks:
                errors.append(f"{cinfo['clientName']}: no captionTracks")
                continue

            def rank(track: dict) -> tuple:
                code = (track.get("languageCode") or "").lower()
                pref = preferred.index(code) if code in preferred else 99
                generated = 1 if (track.get("kind") or "") == "asr" else 0
                return (pref, generated)

            for track in sorted(tracks, key=rank):
                base = track.get("baseUrl") or ""
                if not base:
                    continue
                if "fmt=" in base:
                    base = re.sub(r"fmt=[^&]+", "fmt=json3", base)
                else:
                    base = base + ("&" if "?" in base else "?") + "fmt=json3"
                tt = client.get(base)
                if tt.status_code != 200:
                    continue
                try:
                    payload = tt.json()
                except Exception:
                    continue
                segments = _segments_from_json3(payload)
                if segments:
                    return segments
            errors.append(f"{cinfo['clientName']}: empty timedtext")
    raise RuntimeError(" | ".join(errors) or "InnerTube caption tracks empty")



def _fetch_captions_library(video_id: str) -> list[Segment]:
    from app.youtube_auth import youtube_proxy_url

    proxy = youtube_proxy_url()
    kwargs = {}
    if proxy:
        try:
            from youtube_transcript_api.proxies import GenericProxyConfig

            kwargs["proxy_config"] = GenericProxyConfig(http_url=proxy, https_url=proxy)
        except Exception:
            pass
    api = YouTubeTranscriptApi(**kwargs)
    fetched = None
    last_err: Exception | None = None
    try:
        fetched = api.fetch(video_id, languages=["en", "hi", "en-US", "en-GB", "en-IN"])
    except Exception as exc:
        last_err = exc
        try:
            listing = api.list(video_id)
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
        raise RuntimeError(str(last_err or "youtube-transcript-api failed"))

    segments: list[Segment] = []
    for item in fetched:
        text = str(item.text).replace("\n", " ").strip()
        if not text:
            continue
        start = float(item.start)
        end = start + float(item.duration)
        segments.append(Segment(start=start, end=end, text=text))
    if not segments:
        raise RuntimeError("youtube-transcript-api returned empty transcript")
    return segments


def _fetch_captions_proxy(video_id: str) -> tuple[list[Segment], str | None]:
    proxy = (settings.youtube_caption_proxy or "").strip().rstrip("/")
    if not proxy:
        raise RuntimeError("youtube_caption_proxy not configured")
    url = f"{proxy}?v={video_id}"
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        resp = client.get(url)
        if resp.status_code != 200:
            detail = resp.text[:300]
            raise RuntimeError(f"proxy HTTP {resp.status_code}: {detail}")
        data = resp.json()
    raw = data.get("segments") or []
    title = data.get("title")
    segments = [
        Segment(
            start=float(item.get("start") or 0),
            end=float(item.get("end") or float(item.get("start") or 0) + 2),
            text=str(item.get("text") or "").replace("\n", " ").strip(),
        )
        for item in raw
        if str(item.get("text") or "").strip()
    ]
    if not segments:
        raise RuntimeError("caption proxy returned empty segments")
    return segments, title if isinstance(title, str) else None


def fetch_youtube_captions(url: str) -> tuple[str, list[Segment], float | None]:
    video_id = extract_youtube_id(url)
    if not video_id:
        raise ValueError("Not a valid YouTube URL")

    errors: list[str] = []
    segments: list[Segment] = []
    proxy_title: str | None = None

    # InnerTube first — works on residential IPs; often 403 on Render.
    try:
        segments = _fetch_captions_innertube(video_id)
    except Exception as exc:
        errors.append(f"innertube: {exc}")

    if not segments:
        try:
            segments = _fetch_captions_library(video_id)
        except Exception as exc:
            errors.append(f"library: {exc}")

    # Vercel (or other) proxy — different IP from Render datacenter.
    if not segments:
        try:
            segments, proxy_title = _fetch_captions_proxy(video_id)
        except Exception as exc:
            errors.append(f"proxy: {exc}")

    if not segments:
        raise RuntimeError(
            "YouTube captions unavailable (" + " | ".join(errors) + ")"
        )

    duration_val = segments[-1].end if segments else None
    # proxy_title unused here; caller may refresh title via oEmbed
    _ = proxy_title
    return video_id, segments, duration_val


def download_audio_from_url(url: str, out_dir: Path) -> tuple[Path, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(out_dir / "%(id)s.%(ext)s")
    from app.youtube_auth import ensure_youtube_cookie_file, youtube_proxy_url

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
    ]
    cookies = ensure_youtube_cookie_file()
    if cookies:
        cmd.extend(["--cookies", str(cookies)])
    proxy = youtube_proxy_url()
    if proxy:
        cmd.extend(["--proxy", proxy])
    cmd.append(url)
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
