from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.docs_parse import DocUnit


def fetch_web_page(url: str, *, timeout: float = 30.0) -> tuple[str, list[DocUnit]]:
    """
    Fetch a single web page and extract readable text units with locations.
    Returns (title, units).
    """
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")

    headers = {
        "User-Agent": (
            "NexoraEvidenceDesk/0.4 (+https://github.com/ankushkumar14092-dot/NEXORAG; "
            "research ingest)"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "").lower()
        if "html" not in content_type and "xml" not in content_type and "text" not in content_type:
            raise RuntimeError(f"Unsupported content-type for web ingest: {content_type or 'unknown'}")
        html = resp.text

    title = _title_from_html(html) or urlparse(url).netloc or url
    units = _units_from_html(html, url)
    if not units:
        raise RuntimeError("No readable text extracted from page")
    return title, units


def _title_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(" ", strip=True)
    return ""


def _units_from_html(html: str, url: str) -> list[DocUnit]:
    # Prefer trafilatura when available for cleaner article text
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if extracted and extracted.strip():
            parts = [p.strip() for p in re.split(r"\n\s*\n", extracted) if p.strip()]
            units: list[DocUnit] = []
            for i, part in enumerate(parts, start=1):
                units.append(
                    DocUnit(text=part, location=f"URL · section {i}")
                )
            if units:
                units[0].text = f"Source: {url}\n\n{units[0].text}"
                return units
    except Exception:
        pass

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
        tag.decompose()

    units: list[DocUnit] = []
    for el in soup.find_all(["h1", "h2", "h3", "p", "li", "pre", "blockquote"]):
        text = el.get_text(" ", strip=True)
        if len(text) < 40 and el.name not in {"h1", "h2", "h3"}:
            continue
        if not text:
            continue
        tag = el.name.upper()
        units.append(DocUnit(text=text, location=f"URL · {tag}"))

    if not units:
        body = soup.get_text("\n", strip=True)
        parts = [p.strip() for p in re.split(r"\n\s*\n", body) if len(p.strip()) > 40]
        for i, part in enumerate(parts[:80], start=1):
            units.append(DocUnit(text=part, location=f"URL · block {i}"))

    if units:
        units[0].text = f"Source: {url}\n\n{units[0].text}"
    return units
