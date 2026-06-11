from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.graph.state import PipelineContext, PipelineState
from src.utils.paper_fetcher import fetch_papers_for_area
from src.utils.query_expander import expand_query

logger = logging.getLogger(__name__)


def expand_query_plan(query_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach keyword_variants to every plan item."""
    return [
        {**item, "keyword_variants": expand_query(item["query_hint"])}
        for item in query_plan
    ]


def retrieve_all_areas(
    query_plan: list[dict[str, Any]],
    context: PipelineContext,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch papers for all areas in parallel (one thread per area)."""
    cfg = context.config
    max_per_area = cfg["openalex"]["max_works_per_area"]
    min_citations = cfg["openalex"]["min_citation_count"]

    papers_by_area: dict[str, list[dict[str, Any]]] = {}

    with ThreadPoolExecutor(max_workers=len(query_plan) or 1) as pool:
        futures = {
            pool.submit(
                fetch_papers_for_area,
                item,
                context.openalex,
                max_per_area,
                min_citations,
            ): item["area"]
            for item in query_plan
        }
        for future in as_completed(futures):
            area = futures[future]
            try:
                papers_by_area[area] = future.result()
                logger.info(
                    "RetrievalAgent: area %r — %d papers fetched",
                    area,
                    len(papers_by_area[area]),
                )
            except Exception as exc:
                logger.warning("RetrievalAgent: area %r failed: %s", area, exc)
                papers_by_area[area] = []

    return papers_by_area


class RetrievalNode:
    def __init__(self, context: PipelineContext) -> None:
        self._context = context

    def run(self, state: PipelineState) -> dict:
        """Graph node: expand queries then retrieve papers for all areas."""
        query_plan = state["query_plan"]

        logger.info("RetrievalAgent: expanding %d-area query plan", len(query_plan))
        expanded_plan = expand_query_plan(query_plan)

        logger.info("RetrievalAgent: fetching papers from OpenAlex")
        papers_by_area = retrieve_all_areas(expanded_plan, self._context)

        return {
            **state,
            "query_plan": expanded_plan,
            "papers_by_area": papers_by_area,
        }
