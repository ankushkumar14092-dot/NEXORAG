from __future__ import annotations

from pathlib import Path

from PIL import Image

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


def ocr_image(path: Path, *, max_side: int = 2400) -> str:
    """OCR a single image with Tesseract. Returns extracted text."""
    try:
        import pytesseract
    except ImportError as exc:
        raise RuntimeError("pytesseract not installed") from exc

    img = Image.open(path)
    if img.mode not in {"RGB", "L"}:
        img = img.convert("RGB")

    w, h = img.size
    scale = min(1.0, max_side / max(w, h, 1))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))

    text = pytesseract.image_to_string(img) or ""
    return text.strip()


def ocr_pil_image(img: Image.Image) -> str:
    import pytesseract

    if img.mode not in {"RGB", "L"}:
        img = img.convert("RGB")
    return (pytesseract.image_to_string(img) or "").strip()


def ocr_pdf_pages(path: Path, *, max_pages: int = 30, dpi: int = 150) -> list[tuple[int, str]]:
    """
    Rasterize PDF pages and OCR them (scanned / image-only PDFs).
    Requires poppler (pdftoppm) for pdf2image.
    """
    try:
        from pdf2image import convert_from_path
    except ImportError as exc:
        raise RuntimeError("pdf2image not installed") from exc

    try:
        images = convert_from_path(
            str(path),
            dpi=dpi,
            first_page=1,
            last_page=max_pages,
        )
    except Exception as exc:
        raise RuntimeError(
            f"PDF rasterize failed (is poppler installed?): {exc}"
        ) from exc

    out: list[tuple[int, str]] = []
    for i, img in enumerate(images, start=1):
        text = ocr_pil_image(img)
        if text:
            out.append((i, text))
    return out
