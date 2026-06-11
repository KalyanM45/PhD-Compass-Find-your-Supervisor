from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.graph.state import PipelineContext, PipelineState
from src.utils.blurb_generator import generate_blurb

logger = logging.getLogger(__name__)


class WhyMatchNode:
    def __init__(self, context: PipelineContext) -> None:
        self._context = context

    def run(self, state: PipelineState) -> dict:
        """Graph node: generate why_match blurbs in parallel for all final candidates."""
        final_candidates: list[dict[str, Any]] = state["final_candidates"]
        enrichment = state["enrichment"]
        cfg = self._context.config

        capability_profile: list[str] = enrichment.get(
            "capability_profile", state["profile"].skills
        )
        use_cases: list[str] = state["profile"].use_cases
        parallelism = cfg["timeouts"].get("llm_parallelism_limit", 3)
        timeout_per_item = cfg["timeouts"]["why_match_seconds"]

        updated: list[dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=parallelism) as pool:
            futures = {
                pool.submit(
                    generate_blurb, cand, capability_profile, self._context.llm_client, use_cases
                ): cand
                for cand in final_candidates
            }
            for future in as_completed(
                futures, timeout=timeout_per_item * max(len(final_candidates), 1)
            ):
                original = futures[future]
                try:
                    blurb = future.result(timeout=timeout_per_item)
                    original["why_match"] = blurb
                    updated.append(original)
                except Exception as exc:
                    logger.warning(
                        "WhyMatchAgent: timeout/error for %s: %s",
                        original.get("name"),
                        exc,
                    )
                    evidence_papers = original.get("evidence_papers", [{}])
                    uc_papers = [p for p in evidence_papers if p.get("relevance_note")]
                    top_paper = (uc_papers or evidence_papers or [{}])[0]
                    original["why_match"] = (
                        f"Prof. {original['name']}'s recent work on "
                        f"\"{top_paper.get('title', 'this topic')}\" "
                        f"aligns with the student's background."
                    )
                    updated.append(original)

        logger.info("WhyMatchAgent: generated %d blurbs", len(updated))
        return {**state, "final_candidates": updated}
