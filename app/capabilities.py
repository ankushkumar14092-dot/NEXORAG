"""
Nexora capability catalog — full OmniRAG map with honest status.

Nothing is "forgotten" at the product/architecture layer.
Only `live` / `partial` capabilities are executable today.
Everything else is explicitly deferred so we do not ship fake connectors.
"""

from __future__ import annotations

from typing import Any, Literal

Status = Literal["live", "partial", "next", "later", "out_of_scope"]

# Evidence Desk wedge: ship depth, not 47 shallow connectors.
CATALOG: list[dict[str, Any]] = [
    {
        "id": "documents",
        "name": "Documents",
        "status": "live",
        "formats": [".pdf", ".docx", ".txt", ".md", ".html", ".htm", ".xml", ".json"],
        "evidence": ["page", "section", "paragraph"],
        "notes": "Text extract only; scanned/OCR not live",
    },
    {
        "id": "spreadsheets",
        "name": "Spreadsheets / tabular",
        "status": "live",
        "formats": [".xlsx", ".xls", ".csv"],
        "evidence": ["sheet", "cell_range"],
        "notes": "Values only; formulas/charts not preserved as objects",
    },
    {
        "id": "presentations",
        "name": "Presentations",
        "status": "live",
        "formats": [".pptx"],
        "evidence": ["slide"],
        "notes": "Slide text; speaker notes/images not full multimodal",
    },
    {
        "id": "video",
        "name": "Video",
        "status": "partial",
        "formats": [".mp4", ".mov", ".mkv", ".webm", ".avi"],
        "evidence": ["start", "end", "timestamp"],
        "notes": "Transcript + timestamps. No scene/frame/OCR/speaker diarization yet",
    },
    {
        "id": "audio",
        "name": "Audio",
        "status": "partial",
        "formats": [".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"],
        "evidence": ["start", "end", "timestamp"],
        "notes": "Via Whisper path; no speaker labels yet",
    },
    {
        "id": "youtube",
        "name": "YouTube URL",
        "status": "partial",
        "formats": ["http(s) URL"],
        "evidence": ["start", "end", "timestamp"],
        "notes": "Captions first, else download+Whisper. No channel/playlist crawl",
    },
    {
        "id": "images",
        "name": "Images / OCR",
        "status": "live",
        "formats": [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"],
        "evidence": ["region", "Image (OCR)"],
        "notes": "Tesseract OCR → BM25 chunks",
    },
    {
        "id": "web_url",
        "name": "Single web page",
        "status": "live",
        "formats": ["http(s) URL"],
        "evidence": ["url", "heading", "paragraph"],
        "notes": "POST /api/ingest/web — single page, not full-site crawl",
    },
    {
        "id": "epub_books",
        "name": "EPUB / books",
        "status": "live",
        "formats": [".epub"],
        "evidence": ["chapter", "section"],
        "notes": "Chapter-labeled chunks",
    },
    {
        "id": "code_files",
        "name": "Source code files",
        "status": "live",
        "formats": [".py", ".js", ".ts", ".java", ".go", ".rs", ".sql", ".sh"],
        "evidence": ["path", "line_start", "line_end"],
        "notes": "Upload code → line-range locations",
    },
    {
        "id": "github",
        "name": "GitHub / Git platforms",
        "status": "later",
        "formats": ["repo URL", "API"],
        "evidence": ["repo", "path", "commit", "line"],
        "notes": "Connector + auth; not upload desk",
    },
    {
        "id": "research",
        "name": "Research papers / arXiv / Scholar",
        "status": "later",
        "formats": ["PDF", "DOI", "API"],
        "evidence": ["section", "page", "doi"],
        "notes": "Mostly PDF desk today; APIs are connectors",
    },
    {
        "id": "enterprise_drive",
        "name": "Drive / SharePoint / Dropbox / S3",
        "status": "later",
        "formats": ["OAuth", "API"],
        "evidence": ["path", "file_id", "version"],
        "notes": "Enterprise connector product — separate from Evidence Desk MVP",
    },
    {
        "id": "team_chat",
        "name": "Slack / Teams / Discord",
        "status": "later",
        "formats": ["API"],
        "evidence": ["channel", "message_id", "thread", "ts"],
        "notes": "Permissions + retention nightmare; not MVP",
    },
    {
        "id": "email",
        "name": "Gmail / Outlook / IMAP",
        "status": "later",
        "formats": ["OAuth", "IMAP"],
        "evidence": ["message_id", "thread", "attachment"],
        "notes": "High sensitivity; auth-first",
    },
    {
        "id": "notion_confluence",
        "name": "Notion / Confluence / wikis",
        "status": "later",
        "formats": ["API"],
        "evidence": ["page_id", "block", "url"],
        "notes": "Connector family",
    },
    {
        "id": "project_mgmt",
        "name": "Jira / Linear / Asana",
        "status": "later",
        "formats": ["API"],
        "evidence": ["issue_key", "comment"],
        "notes": "Connector family",
    },
    {
        "id": "databases",
        "name": "SQL / NoSQL / warehouses",
        "status": "out_of_scope",
        "formats": ["connection string"],
        "evidence": ["db", "table", "row"],
        "notes": "Different product (analytics/BI). Do not pretend RAG covers this",
    },
    {
        "id": "knowledge_graph",
        "name": "Knowledge graphs / RDF / SPARQL",
        "status": "out_of_scope",
        "formats": ["graph DB"],
        "evidence": ["entity", "edge"],
        "notes": "After evidence desk has users — not a day-1 store",
    },
    {
        "id": "realtime",
        "name": "Realtime streams / IoT / queues",
        "status": "out_of_scope",
        "formats": ["stream"],
        "evidence": ["event_id", "ts"],
        "notes": "Streaming platform, not evidence desk",
    },
    {
        "id": "medical_phi",
        "name": "Medical records / PHI",
        "status": "out_of_scope",
        "formats": ["regulated"],
        "evidence": [],
        "notes": "Compliance product; dangerous to fake",
    },
    {
        "id": "social",
        "name": "Social media firehose",
        "status": "out_of_scope",
        "formats": ["API"],
        "evidence": ["post_id"],
        "notes": "ToS + spam; not evidence-grade MVP",
    },
    {
        "id": "scanned_ocr",
        "name": "Scanned / handwritten docs",
        "status": "partial",
        "formats": ["image PDF", "scan"],
        "evidence": ["page", "OCR"],
        "notes": "Scanned PDF OCR via pdf2image+Tesseract; handwriting quality varies",
    },
    {
        "id": "web_crawl",
        "name": "Full-site crawl / domain",
        "status": "later",
        "formats": ["sitemap", "domain"],
        "evidence": ["url"],
        "notes": "After single-URL ingest works well",
    },
    {
        "id": "custom_connector",
        "name": "Custom API / plugin",
        "status": "later",
        "formats": ["plugin"],
        "evidence": ["adapter-defined"],
        "notes": "SourceAdapter interface — add without rewriting core RAG",
    },
]


def capabilities_payload() -> dict[str, Any]:
    by_status: dict[str, list[str]] = {
        "live": [],
        "partial": [],
        "next": [],
        "later": [],
        "out_of_scope": [],
    }
    for item in CATALOG:
        by_status[item["status"]].append(item["id"])

    return {
        "product": "Nexora Evidence Desk",
        "stance": (
            "Universal catalog is mapped. Only live/partial are executable. "
            "Building all OmniRAG categories now is rejected — depth over breadth."
        ),
        "retrieval": {
            "live": [
                "source_detection",
                "modality_router",
                "bm25_keyword",
                "tfidf_sparse_vector",
                "graph_structure_boost",
                "rrf_rerank",
                "evidence_graph",
                "claim_conflict_briefing",
            ],
            "next": ["dense_neural_embeddings", "cross_encoder_rerank"],
            "later": ["neo4j_kg", "metadata_filters", "visual_search"],
            "out_of_scope_for_now": ["sql_retrieval", "cross_tenant_enterprise_search"],
        },
        "counts": {k: len(v) for k, v in by_status.items()},
        "by_status": by_status,
        "catalog": CATALOG,
    }
