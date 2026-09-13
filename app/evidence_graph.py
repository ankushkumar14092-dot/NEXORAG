"""
In-process evidence graph (lite knowledge model).

Not Neo4j — a query-time graph of sources, passages, and locations
used for explainability + conflict neighborhood hints.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.hybrid import RankedHit


def build_evidence_graph(question: str, hits: list[RankedHit]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {"id": "q:user", "type": "question", "label": question[:120]}
    ]
    edges: list[dict[str, str]] = []
    by_source: dict[str, list[str]] = defaultdict(list)

    for i, hit in enumerate(hits, start=1):
        cid = f"c:{i}"
        sid = hit.chunk.source_id or hit.chunk.video_id or "unknown"
        source_node = f"s:{sid}"
        loc = hit.chunk.location_label or f"{hit.chunk.start_label}-{hit.chunk.end_label}"
        loc_node = f"l:{sid}:{loc}"

        if not any(n["id"] == source_node for n in nodes):
            nodes.append(
                {
                    "id": source_node,
                    "type": "source",
                    "label": hit.source_title,
                    "kind": hit.chunk.kind,
                }
            )
        nodes.append(
            {
                "id": cid,
                "type": "passage",
                "label": (hit.chunk.text or "")[:100],
                "location": loc,
                "location_type": hit.location_type,
                "score": round(hit.score, 4),
            }
        )
        if not any(n["id"] == loc_node for n in nodes):
            nodes.append(
                {
                    "id": loc_node,
                    "type": "location",
                    "label": loc,
                    "location_type": hit.location_type,
                }
            )

        edges.append({"from": "q:user", "to": cid, "rel": "retrieved"})
        edges.append({"from": cid, "to": source_node, "rel": "from_source"})
        edges.append({"from": cid, "to": loc_node, "rel": "at_location"})
        by_source[sid].append(cid)

    # Co-source edges (same document/video neighborhood)
    for sid, cids in by_source.items():
        for a, b in zip(cids, cids[1:]):
            edges.append({"from": a, "to": b, "rel": "same_source"})

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "passages": len(hits),
            "sources": len(by_source),
            "edges": len(edges),
        },
    }
