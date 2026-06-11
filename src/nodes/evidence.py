"""Node: Stage 7 — Attach evidence (papers, grants, programs) and resolve contact email.

Fans out GrantFetcher + ProgramFetcher + EmailResolver subagent calls in parallel per candidate.
Drops candidates with zero evidence (Stage 3 guarantees at least one paper in practice).

linked_programs is populated by ProgramFetcher via three strategies:
  1. Offline institution → doctoral program URL lookup table (~60 universities)
  2. FindAPhD.com live search by supervisor name + area
  3. PI homepage scrape for recruitment language + nearby links
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.graph.state import PipelineContext, PipelineState
from src.utils.email_resolver import resolve_email
from src.utils.grant_fetcher import fetch_grants_for_candidate
from src.utils.program_fetcher import fetch_programs_for_candidate
from src.utils.usecase_matcher import match_papers_to_usecases

logger = logging.getLogger(__name__)

_MAX_PAPERS = 3


def _select_top_papers(
    candidate: dict[str, Any], max_papers: int = _MAX_PAPERS
) -> list[dict[str, Any]]:
    papers = candidate.get("papers", [])
    return sorted(
        papers,
        key=lambda p: (p.get("year", 0), p.get("cited_by_count", 0)),
        reverse=True,
    )[:max_papers]


def _enrich_one(
    candidate: dict[str, Any], area: str, use_cases: list[str],
    citizenship: str | None = None, llm_client: Any = None,
) -> dict[str, Any] | None:
    papers = _select_top_papers(candidate)
    # Annotate each paper with relevance_note if it matches a student use case;
    # matched papers are sorted to the front so the blurb generator sees them first.
    papers = match_papers_to_usecases(papers, use_cases)
    grants = fetch_grants_for_candidate(candidate)

    if not papers and not grants:
        logger.debug("Drop %s — no evidence attached", candidate["name"])
        return None

    email = resolve_email(candidate)
    programs = fetch_programs_for_candidate(candidate, area, citizenship=citizenship, llm_client=llm_client)

    return {
        **candidate,
        "evidence_papers": papers,
        "evidence_grants": grants,
        "contact_email": email,
        "linked_programs": programs,
    }


class EvidenceNode:
    def __init__(self, context: PipelineContext) -> None:
        self._context = context

    def run(self, state: PipelineState) -> dict:
        """Graph node: attach evidence and resolve emails, parallel per candidate."""
        area_candidates = state["area_candidates"]
        parallelism = self._context.config["timeouts"]["parallelism_limit"]
        use_cases: list[str] = state["profile"].use_cases
        citizenship: str | None = state["profile"].citizenship

        result: dict[str, list[dict[str, Any]]] = {}

        for area, candidates in area_candidates.items():
            enriched: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=parallelism) as pool:
                futures = {
                    pool.submit(_enrich_one, cand, area, use_cases, citizenship, self._context.llm_client): cand
                    for cand in candidates
                }
                for future in as_completed(futures):
                    try:
                        enriched_cand = future.result()
                        if enriched_cand is not None:
                            enriched.append(enriched_cand)
                    except Exception as exc:
                        original = futures[future]
                        logger.warning(
                            "EvidenceAgent: error enriching %s: %s",
                            original.get("name"),
                            exc,
                        )

            logger.info(
                "EvidenceAgent: area %r — %d/%d candidates have evidence",
                area,
                len(enriched),
                len(candidates),
            )
            result[area] = enriched

        return {**state, "area_candidates": result}
