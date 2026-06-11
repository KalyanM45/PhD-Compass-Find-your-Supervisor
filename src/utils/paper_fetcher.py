from __future__ import annotations

from typing import Any

from src.utils.clients import OpenAlexClient


def fetch_papers_for_area(
    area_plan: dict[str, Any],
    client: OpenAlexClient,
    max_papers: int = 300,
    min_citations: int = 0,
) -> list[dict[str, Any]]:
    """Return up to max_papers works for one plan area."""
    papers = client.search_works(
        query=area_plan["query_hint"],
        countries=area_plan["countries"],
        year_floor=area_plan["recency_floor"],
        min_citations=min_citations,
        per_page=min(200, max_papers),
        max_pages=max(1, max_papers // 200),
        topic_ids=area_plan.get("topic_ids") or None,
    )
    return papers[:max_papers]
