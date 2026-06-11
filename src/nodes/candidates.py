from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from src.graph.state import PipelineContext, PipelineState
from src.utils.author_disambiguator import disambiguate_candidate
from src.utils.country_verifier import verify_candidate_country

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage 4 helpers (local, no API)
# ---------------------------------------------------------------------------


def _author_id(authorship: dict[str, Any]) -> Optional[str]:
    return authorship.get("author", {}).get("id")


def _affiliation_country(authorship: dict[str, Any]) -> Optional[str]:
    insts = authorship.get("institutions", [])
    return insts[0].get("country_code") if insts else None


def _affiliation_institution(authorship: dict[str, Any]) -> Optional[str]:
    insts = authorship.get("institutions", [])
    return insts[0].get("display_name") if insts else None


def _is_likely_pi(position: int, total: int) -> bool:
    return total <= 2 or position == total - 1


def extract_pi_candidates(
    papers_by_area: dict[str, list[dict[str, Any]]],
    target_countries: list[str],
) -> dict[str, list[dict[str, Any]]]:
    area_candidates: dict[str, list[dict[str, Any]]] = {}

    for area, papers in papers_by_area.items():
        seen: dict[str, dict[str, Any]] = {}

        for paper in papers:
            authorships = paper.get("authorships", [])
            total = len(authorships)
            paper_ref = {
                "openalex_id": paper.get("id"),
                "title": paper.get("title", ""),
                "year": paper.get("publication_year"),
                "doi": paper.get("doi"),
                "url": (
                    f"https://doi.org/{paper['doi']}"
                    if paper.get("doi")
                    else paper.get("id", "")
                ),
                "cited_by_count": paper.get("cited_by_count", 0),
            }

            for pos, authorship in enumerate(authorships):
                aid = _author_id(authorship)
                if not aid:
                    continue
                country = _affiliation_country(authorship)
                if country not in target_countries:
                    continue

                if aid in seen:
                    seen[aid]["papers"].append(paper_ref)
                    if _is_likely_pi(pos, total):
                        seen[aid]["likely_pi"] = True
                else:
                    seen[aid] = {
                        "openalex_author_id": aid,
                        "name": authorship.get("author", {}).get("display_name", "Unknown"),
                        "institution": _affiliation_institution(authorship) or "",
                        "country": country,
                        "papers": [paper_ref],
                        "area": area,
                        "likely_pi": _is_likely_pi(pos, total),
                    }

        area_candidates[area] = list(seen.values())
        logger.info(
            "Stage 4 — area %r: %d unique candidates from %d papers",
            area,
            len(area_candidates[area]),
            len(papers),
        )

    return area_candidates


def deduplicate_across_areas(
    area_candidates: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Merge paper lists for authors appearing in multiple areas."""
    global_seen: dict[str, dict[str, Any]] = {}
    for area, candidates in area_candidates.items():
        for cand in candidates:
            aid = cand["openalex_author_id"]
            if aid not in global_seen:
                global_seen[aid] = cand
            else:
                existing_ids = {p["openalex_id"] for p in global_seen[aid]["papers"]}
                for p in cand["papers"]:
                    if p["openalex_id"] not in existing_ids:
                        global_seen[aid]["papers"].append(p)
    return area_candidates


# ---------------------------------------------------------------------------
# Stage 5 — parallel disambiguation
# ---------------------------------------------------------------------------


def _disambiguate_area(
    area: str,
    candidates: list[dict[str, Any]],
    query_hint: str,
    context: PipelineContext,
    parallelism: int,
) -> list[dict[str, Any]]:
    survivors: list[dict[str, Any]] = []
    sup_cfg = context.config.get("supervision", {})
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = {
            pool.submit(
                disambiguate_candidate,
                cand,
                area,
                query_hint,
                context.openalex,
                llm_client=context.llm_client,
                min_works=sup_cfg.get("min_works_count", 10),
                min_h_index=sup_cfg.get("min_h_index", 5),
                orcid_faculty_check=sup_cfg.get("orcid_faculty_check", True),
            ): cand
            for cand in candidates
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                survivors.append(result)
    logger.info(
        "Stage 5 — area %r: %d/%d survived disambiguation",
        area,
        len(survivors),
        len(candidates),
    )
    return survivors


# ---------------------------------------------------------------------------
# Stage 6 — parallel country verification
# ---------------------------------------------------------------------------


def _filter_country_area(
    area: str,
    candidates: list[dict[str, Any]],
    target_countries: list[str],
    recency_floor: int,
    context: PipelineContext,
    parallelism: int,
) -> list[dict[str, Any]]:
    survivors: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = {
            pool.submit(
                verify_candidate_country,
                cand,
                target_countries,
                context.openalex,
                recency_floor,
            ): cand
            for cand in candidates
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                survivors.append(result)
    logger.info(
        "Stage 6 — area %r: %d/%d survived country filter",
        area,
        len(survivors),
        len(candidates),
    )
    return survivors


class CandidateNode:
    def __init__(self, context: PipelineContext) -> None:
        self._context = context

    def run(self, state: PipelineState) -> dict:
        """Graph node: extract candidates, then disambiguate and country-filter."""
        papers_by_area = state["papers_by_area"]
        query_plan = state["query_plan"]
        countries = state["countries"]
        cfg = self._context.config
        parallelism = cfg["timeouts"]["parallelism_limit"]

        # Stage 4
        logger.info("CandidateAgent: extracting PI candidates")
        area_candidates = extract_pi_candidates(papers_by_area, countries)
        area_candidates = deduplicate_across_areas(area_candidates)

        # Stage 5
        logger.info("CandidateAgent: disambiguating candidates")
        plan_by_area = {item["area"]: item for item in query_plan}
        area_candidates = {
            area: _disambiguate_area(
                area,
                candidates,
                plan_by_area.get(area, {}).get("query_hint", area),
                self._context,
                parallelism,
            )
            for area, candidates in area_candidates.items()
        }

        # Stage 6
        recency_floor = min(item["recency_floor"] for item in query_plan)
        logger.info("CandidateAgent: applying country hard filter")
        area_candidates = {
            area: _filter_country_area(
                area, candidates, countries, recency_floor, self._context, parallelism
            )
            for area, candidates in area_candidates.items()
        }

        return {**state, "area_candidates": area_candidates}
