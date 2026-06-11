"""Node: Stage 8 — Score, tier, and balance candidates.

match_score = weighted combination of:
  topic_similarity  (keyword overlap, candidate concepts vs area + query hint)
  recency_score     (how recent the PI's best evidence paper is)
  evidence_strength (paper count + citation weight)
  seniority_score   (h_index, cited_by_count, works_count, i10_index composite)

Tiering and per-area balancing are config-driven.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from src.graph.state import PipelineContext, PipelineState

logger = logging.getLogger(__name__)


def _recency_score(papers: list[dict[str, Any]], current_year: int) -> float:
    if not papers:
        return 0.0
    best_year = max(p.get("year", 0) for p in papers)
    return max(0.0, 1.0 - (current_year - best_year) / 10.0)


def _evidence_strength(papers: list[dict[str, Any]]) -> float:
    if not papers:
        return 0.0
    total_citations = sum(p.get("cited_by_count", 0) for p in papers)
    return min(1.0, len(papers) * 0.2 + (total_citations / 50.0) * 0.8)


def _seniority_score(
    works_count: int, cited_by_count: int, h_index: int, i10_index: int
) -> float:
    return round(
        0.50 * min(1.0, h_index / 60.0)
        + 0.25 * min(1.0, cited_by_count / 10_000.0)
        + 0.15 * min(1.0, works_count / 200.0)
        + 0.10 * min(1.0, i10_index / 100.0),
        4,
    )


def _keyword_overlap_score(
    candidate_concepts: list[str], query_hint: str, area: str
) -> float:
    query_words = set(f"{area} {query_hint}".lower().split())
    concept_words = set(" ".join(candidate_concepts).lower().split())
    if not query_words:
        return 0.0
    return min(1.0, len(query_words & concept_words) / max(len(query_words), 1))


def compute_match_score(
    candidate: dict[str, Any],
    query_hint: str,
    area: str,
    current_year: int,
    weights: dict[str, float],
) -> float:
    evidence_papers = candidate.get("evidence_papers", [])
    topic_sim = _keyword_overlap_score(
        candidate.get("author_concepts", []), query_hint, area
    )
    recency = _recency_score(evidence_papers, current_year)
    evidence = _evidence_strength(evidence_papers)
    seniority = _seniority_score(
        works_count=candidate.get("works_count", 0),
        cited_by_count=candidate.get("cited_by_count", 0),
        h_index=candidate.get("h_index", 0),
        i10_index=candidate.get("i10_index", 0),
    )
    score = (
        weights.get("topic_similarity_weight", 0.5) * topic_sim
        + weights.get("recency_weight", 0.2) * recency
        + weights.get("evidence_strength_weight", 0.2) * evidence
        + weights.get("seniority_weight", 0.1) * seniority
    )
    return round(min(1.0, score), 4)


def assign_tier(
    score: float,
    reach_threshold: float = 0.75,
    target_threshold: float = 0.50,
) -> str:
    if score >= reach_threshold:
        return "reach"
    if score >= target_threshold:
        return "target"
    return "safety"


def score_and_tier(
    area_candidates: dict[str, list[dict[str, Any]]],
    query_plan: list[dict[str, Any]],
    current_year: int,
    weights: dict[str, float],
    reach_threshold: float,
    target_threshold: float,
) -> dict[str, list[dict[str, Any]]]:
    plan_by_area = {item["area"]: item for item in query_plan}
    result: dict[str, list[dict[str, Any]]] = {}
    for area, candidates in area_candidates.items():
        query_hint = plan_by_area.get(area, {}).get("query_hint", area)
        for cand in candidates:
            score = compute_match_score(cand, query_hint, area, current_year, weights)
            cand["match_score"] = score
            cand["tier"] = assign_tier(score, reach_threshold, target_threshold)
        result[area] = sorted(candidates, key=lambda c: c["match_score"], reverse=True)
    return result


def balance_and_select(
    area_candidates: dict[str, list[dict[str, Any]]],
    query_plan: list[dict[str, Any]],
    min_final: int = 50,
    max_per_area_fraction: float = 0.5,
) -> list[dict[str, Any]]:
    plan_by_area = {item["area"]: item for item in query_plan}
    total_quota = max(min_final, sum(item["target_count"] for item in query_plan))
    max_per_area = int(total_quota * max_per_area_fraction)

    selected: list[dict[str, Any]] = []
    for area, candidates in area_candidates.items():
        quota = min(
            plan_by_area.get(area, {}).get("target_count", total_quota), max_per_area
        )
        selected.extend(candidates[:quota])

    seen_ids: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for cand in selected:
        aid = cand["openalex_author_id"]
        if aid not in seen_ids:
            seen_ids.add(aid)
            deduped.append(cand)

    return sorted(deduped, key=lambda c: c["match_score"], reverse=True)


class ScoringNode:
    def __init__(self, context: PipelineContext) -> None:
        self._context = context

    def run(self, state: PipelineState) -> dict:
        """Graph node: score, tier, and balance all candidates."""
        import datetime

        area_candidates = state["area_candidates"]
        query_plan = state["query_plan"]
        cfg = self._context.config
        current_year = datetime.datetime.now().year

        logger.info("ScoringAgent: scoring and tiering candidates")
        area_candidates = score_and_tier(
            area_candidates,
            query_plan,
            current_year,
            cfg["scoring"],
            cfg["tiering"]["reach_threshold"],
            cfg["tiering"]["target_threshold"],
        )

        logger.info("ScoringAgent: balancing and selecting final candidates")
        final_candidates = balance_and_select(
            area_candidates,
            query_plan,
            min_final=cfg["quotas"]["min_final"],
            max_per_area_fraction=cfg["quotas"]["max_per_area_fraction"],
        )
        logger.info("ScoringAgent: %d final candidates selected", len(final_candidates))

        return {**state, "area_candidates": area_candidates, "final_candidates": final_candidates}
