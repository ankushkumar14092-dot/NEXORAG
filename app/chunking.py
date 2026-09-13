from __future__ import annotations

from app.config import settings
from app.models import Chunk, Segment, format_ts, new_id


def segments_to_chunks(
    video_id: str,
    segments: list[Segment],
    window: float | None = None,
    overlap: float | None = None,
) -> list[Chunk]:
    if not segments:
        return []

    window = window if window is not None else settings.chunk_seconds
    overlap = overlap if overlap is not None else settings.chunk_overlap_seconds

    chunks: list[Chunk] = []
    i = 0
    while i < len(segments):
        start = segments[i].start
        end = start
        texts: list[str] = []
        j = i
        while j < len(segments):
            seg = segments[j]
            if texts and (seg.end - start) > window:
                break
            texts.append(seg.text.strip())
            end = seg.end
            j += 1

        text = " ".join(t for t in texts if t).strip()
        if text:
            start_label = format_ts(start)
            end_label = format_ts(end)
            chunks.append(
                Chunk(
                    id=new_id("chk"),
                    text=text,
                    source_id=video_id,
                    video_id=video_id,
                    kind="video",
                    start=start,
                    end=end,
                    start_label=start_label,
                    end_label=end_label,
                    location_label=f"{start_label}-{end_label}",
                )
            )

        if j >= len(segments):
            break

        next_i = j
        cutoff = max(start, end - overlap)
        for k in range(i + 1, j):
            if segments[k].start >= cutoff:
                next_i = k
                break
        if next_i <= i:
            next_i = i + 1
        i = next_i

    return chunks


def doc_units_to_chunks(source_id: str, units: list) -> list[Chunk]:
    chunks: list[Chunk] = []
    for unit in units:
        text = (unit.text or "").strip()
        if not text:
            continue
        # Split oversized units
        if len(text) <= 1800:
            pieces = [text]
        else:
            pieces = []
            step = 1500
            for i in range(0, len(text), step):
                part = text[i : i + step].strip()
                if part:
                    pieces.append(part)
        for pi, part in enumerate(pieces, start=1):
            loc = unit.location if len(pieces) == 1 else f"{unit.location} (part {pi})"
            chunks.append(
                Chunk(
                    id=new_id("chk"),
                    text=part,
                    source_id=source_id,
                    video_id=source_id,
                    kind="document",
                    location_label=loc,
                    start_label=loc,
                    end_label=loc,
                )
            )
    return chunks
