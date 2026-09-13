from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from docx import Document
from openpyxl import load_workbook


@dataclass
class DocUnit:
    """One extractable unit with a stable location label."""

    text: str
    location: str  # e.g. Page 3, Sheet1!A1:C4, Slide 2
    page: int | None = None
    sheet: str | None = None
    cell_range: str | None = None


DOC_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".xlsx",
    ".xls",
    ".json",
    ".xml",
    ".pptx",
    ".html",
    ".htm",
}


def parse_document(path: Path) -> list[DocUnit]:
    suffix = path.suffix.lower()
    if suffix not in DOC_EXTENSIONS:
        raise ValueError(f"Unsupported document type: {suffix}")

    if suffix == ".pdf":
        return _parse_pdf(path)
    if suffix == ".docx":
        return _parse_docx(path)
    if suffix in {".txt", ".md", ".markdown"}:
        return _parse_text(path)
    if suffix == ".csv":
        return _parse_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return _parse_xlsx(path)
    if suffix == ".json":
        return _parse_json(path)
    if suffix == ".xml":
        return _parse_text(path, label_prefix="XML")
    if suffix == ".pptx":
        return _parse_pptx(path)
    if suffix in {".html", ".htm"}:
        return _parse_html(path)
    raise ValueError(f"Unsupported document type: {suffix}")


def _parse_pdf(path: Path) -> list[DocUnit]:
    reader = PdfReader(str(path))
    units: list[DocUnit] = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        # Split long pages into paragraph-ish blocks while keeping page location.
        blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
        if not blocks:
            blocks = [text]
        for bi, block in enumerate(blocks, start=1):
            loc = f"Page {i}" if len(blocks) == 1 else f"Page {i}, block {bi}"
            units.append(DocUnit(text=block, location=loc, page=i))
    if not units:
        raise RuntimeError("PDF produced no extractable text (maybe scanned/image-only)")
    return units


def _parse_docx(path: Path) -> list[DocUnit]:
    doc = Document(str(path))
    units: list[DocUnit] = []
    buf: list[str] = []
    para_idx = 0

    def flush() -> None:
        nonlocal para_idx
        text = "\n".join(buf).strip()
        buf.clear()
        if not text:
            return
        para_idx += 1
        units.append(DocUnit(text=text, location=f"Paragraph group {para_idx}"))

    for para in doc.paragraphs:
        t = para.text.strip()
        if not t:
            flush()
            continue
        buf.append(t)
        if len("\n".join(buf)) > 1200:
            flush()
    flush()

    for ti, table in enumerate(doc.tables, start=1):
        rows = []
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            units.append(
                DocUnit(text="\n".join(rows), location=f"Table {ti}")
            )

    if not units:
        raise RuntimeError("DOCX produced no text")
    return units


def _parse_text(path: Path, label_prefix: str = "Section") -> list[DocUnit]:
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        raise RuntimeError("Empty text file")
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not parts:
        parts = [text]
    units: list[DocUnit] = []
    for i, part in enumerate(parts, start=1):
        # Further split huge sections
        if len(part) > 2000:
            for j in range(0, len(part), 1500):
                chunk = part[j : j + 1500].strip()
                if chunk:
                    units.append(
                        DocUnit(text=chunk, location=f"{label_prefix} {i}.{j // 1500 + 1}")
                    )
        else:
            units.append(DocUnit(text=part, location=f"{label_prefix} {i}"))
    return units


def _parse_csv(path: Path) -> list[DocUnit]:
    units: list[DocUnit] = []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        raise RuntimeError("Empty CSV")
    header = rows[0]
    batch: list[str] = []
    start_row = 2
    for i, row in enumerate(rows[1:], start=2):
        line = " | ".join(f"{header[j] if j < len(header) else f'col{j}'}: {row[j] if j < len(row) else ''}" for j in range(max(len(header), len(row))))
        batch.append(line)
        if len(batch) >= 25:
            units.append(
                DocUnit(
                    text="Header: " + " | ".join(header) + "\n" + "\n".join(batch),
                    location=f"Rows {start_row}-{i}",
                )
            )
            batch = []
            start_row = i + 1
    if batch:
        end = start_row + len(batch) - 1
        units.append(
            DocUnit(
                text="Header: " + " | ".join(header) + "\n" + "\n".join(batch),
                location=f"Rows {start_row}-{end}",
            )
        )
    return units


def _col_letter(idx: int) -> str:
    # 1-based excel column index to letter
    letters = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters or "A"


def _parse_xlsx(path: Path) -> list[DocUnit]:
    wb = load_workbook(str(path), data_only=True, read_only=True)
    units: list[DocUnit] = []
    for sheet in wb.worksheets:
        rows_buf: list[str] = []
        start_r: int | None = None
        end_r: int | None = None
        max_c = 1
        for r_idx, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            values = list(row)
            if all(v is None or str(v).strip() == "" for v in values):
                continue
            if start_r is None:
                start_r = r_idx
            end_r = r_idx
            max_c = max(max_c, len(values))
            cells = []
            for ci, v in enumerate(values, start=1):
                if v is None or str(v).strip() == "":
                    continue
                cells.append(f"{_col_letter(ci)}{r_idx}={v}")
            if cells:
                rows_buf.append("; ".join(cells))
            if len(rows_buf) >= 30 and start_r is not None and end_r is not None:
                cell_range = f"{_col_letter(1)}{start_r}:{_col_letter(max_c)}{end_r}"
                units.append(
                    DocUnit(
                        text="\n".join(rows_buf),
                        location=f"{sheet.title}!{cell_range}",
                        sheet=sheet.title,
                        cell_range=cell_range,
                    )
                )
                rows_buf = []
                start_r = None
                end_r = None
        if rows_buf and start_r is not None and end_r is not None:
            cell_range = f"{_col_letter(1)}{start_r}:{_col_letter(max_c)}{end_r}"
            units.append(
                DocUnit(
                    text="\n".join(rows_buf),
                    location=f"{sheet.title}!{cell_range}",
                    sheet=sheet.title,
                    cell_range=cell_range,
                )
            )
    wb.close()
    if not units:
        raise RuntimeError("Excel workbook produced no cell values")
    return units


def _parse_json(path: Path) -> list[DocUnit]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _parse_text(path, label_prefix="JSON text")

    units: list[DocUnit] = []

    def walk(obj, path_label: str) -> None:
        if isinstance(obj, dict):
            # Keep small objects together
            text = json.dumps(obj, ensure_ascii=False, indent=2)
            if len(text) <= 1500:
                units.append(DocUnit(text=text, location=path_label or "$"))
                return
            for k, v in obj.items():
                walk(v, f"{path_label}.{k}" if path_label else k)
        elif isinstance(obj, list):
            if len(json.dumps(obj, ensure_ascii=False)) <= 1500:
                units.append(
                    DocUnit(
                        text=json.dumps(obj, ensure_ascii=False, indent=2),
                        location=path_label or "$",
                    )
                )
                return
            for i, item in enumerate(obj):
                walk(item, f"{path_label}[{i}]")
        else:
            units.append(DocUnit(text=str(obj), location=path_label or "$"))

    walk(data, "$")
    if not units:
        raise RuntimeError("JSON produced no units")
    return units


def _parse_pptx(path: Path) -> list[DocUnit]:
    from pptx import Presentation

    prs = Presentation(str(path))
    units: list[DocUnit] = []
    for i, slide in enumerate(prs.slides, start=1):
        texts: list[str] = []
        for shape in slide.shapes:
            if hasattr(shape, "text"):
                t = (shape.text or "").strip()
                if t:
                    texts.append(t)
        if texts:
            units.append(DocUnit(text="\n".join(texts), location=f"Slide {i}"))
    if not units:
        raise RuntimeError("PPTX produced no text")
    return units


def _parse_html(path: Path) -> list[DocUnit]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    # lightweight tag strip
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    tmp = path.with_suffix(".txt")
    # reuse text chunking without writing file
    if not text:
        raise RuntimeError("HTML produced no text")
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    units: list[DocUnit] = []
    buf: list[str] = []
    idx = 1
    for part in parts:
        buf.append(part)
        if sum(len(x) for x in buf) > 1200:
            units.append(DocUnit(text=" ".join(buf), location=f"HTML section {idx}"))
            idx += 1
            buf = []
    if buf:
        units.append(DocUnit(text=" ".join(buf), location=f"HTML section {idx}"))
    return units
