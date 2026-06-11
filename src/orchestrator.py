"""Orchestrator: wire all agents into a PipelineGraph and run it.

Graph topology (linear DAG, parallelism inside each node):

  parse_profile
       │
  enrich_profile          ← ProfileNode (LLM call #1)
       │
  retrieve_papers         ← RetrievalNode (query expand + parallel OpenAlex fetch)
       │
  build_candidates        ← CandidateNode (extract PIs + disambiguate + country filter)
       │
  attach_evidence         ← EvidenceNode (papers + grants + email + linked_programs, parallel)
       │
  score_and_balance       ← ScoringNode (score, tier, balance)
       │
  generate_why_match      ← WhyMatchNode (LLM calls #2…N, parallel)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.graph.pipeline import build_pipeline
from src.graph.state import PipelineContext, PipelineState
from src.utils.schema import (
    Evidence,
    Grant,
    LinkedProgram,
    OpenPosition,
    Paper,
    Recommendation,
    RunMetadata,
    Shortlist,
    StudentProfile,
    Supervisor,
)

logger = logging.getLogger(__name__)


def _build_recommendation(cand: dict[str, Any], area: str) -> Recommendation:
    papers = [
        Paper(
            title=p.get("title", ""),
            year=p.get("year"),
            doi=p.get("doi"),
            url=p.get("url") or p.get("openalex_id", ""),
            openalex_id=p.get("openalex_id"),
            relevance_note=p.get("relevance_note"),  # set by usecase_matcher
        )
        for p in cand.get("evidence_papers", [])
    ]
    grants = [
        Grant(
            title=g.get("title", ""),
            funder=g.get("funder"),
            id=g.get("id"),
            url=g.get("url", ""),
            years=g.get("years"),
        )
        for g in cand.get("evidence_grants", [])
    ]
    supervisor = Supervisor(
        name=cand["name"],
        openalex_author_id=cand.get("openalex_author_id"),
        orcid=cand.get("orcid"),
        institution=cand.get("institution", ""),
        country=cand["country"],
        contact_email=cand.get("contact_email"),
        research_focus=cand.get("area"),
    )
    # Build LinkedProgram objects from program_fetcher output
    linked_programs = []
    for prog in cand.get("linked_programs", []):
        open_positions = [
            OpenPosition(
                title=pos.get("title", ""),
                url=pos.get("url", ""),
                deadline=pos.get("deadline"),
            )
            for pos in prog.get("open_positions", [])
            if pos.get("url")
        ]
        if prog.get("url"):
            linked_programs.append(
                LinkedProgram(
                    name=prog.get("name", ""),
                    url=prog["url"],
                    open_positions=open_positions,
                )
            )

    return Recommendation(
        supervisor=supervisor,
        research_area=area,
        evidence=Evidence(papers=papers, grants=grants),
        why_match=cand.get("why_match", ""),
        match_score=cand.get("match_score", 0.0),
        tier=cand.get("tier"),
        linked_programs=linked_programs,
    )


class Orchestrator:
    """Builds and drives the pipeline graph for one student profile."""

    def __init__(self, context: PipelineContext) -> None:
        self._context = context
        # Compile the LangGraph pipeline once; reuse across .run() calls
        self._graph = build_pipeline(context)

    def run(self, profile: StudentProfile, start_time: float) -> Shortlist:
        import time

        # Seed state with the student profile — all other keys are populated
        # by the graph nodes as execution proceeds
        initial_state: PipelineState = {"profile": profile}

        logger.info(
            "Orchestrator: invoking LangGraph pipeline for student %s",
            profile.student_id,
        )

        # .invoke() runs all nodes in edge order (START → … → END) and returns
        # the final merged state dict
        final_state = self._graph.invoke(initial_state)

        # Assemble Recommendation objects
        recommendations: list[Recommendation] = []
        final_candidates: list[dict[str, Any]] = final_state.get("final_candidates", [])
        for cand in final_candidates:
            try:
                rec = _build_recommendation(cand, cand.get("area", ""))
                recommendations.append(rec)
            except Exception as exc:
                logger.warning(
                    "Orchestrator: skipping %s — schema error: %s",
                    cand.get("name"),
                    exc,
                )

        # Coverage summary
        coverage: dict[str, int] = {}
        for rec in recommendations:
            coverage[rec.research_area] = coverage.get(rec.research_area, 0) + 1

        # Email hit rate
        emails_found = sum(
            1 for rec in recommendations if rec.supervisor.contact_email is not None
        )
        email_hit_rate = emails_found / max(len(recommendations), 1)
        wall_clock = round(time.time() - start_time, 1)

        min_final = self._context.config.get("quotas", {}).get("min_final", 50)
        if len(recommendations) < min_final:
            logger.warning(
                "Orchestrator: only %d recommendations produced (min_final=%d in config)",
                len(recommendations),
                min_final,
            )

        shortlist = Shortlist(
            student_id=profile.student_id,
            generated_at=datetime.now(timezone.utc),
            target_countries=final_state["countries"],
            target_intake=profile.target_intake,
            recommendations=recommendations,
            coverage_summary=coverage,
            run_metadata=RunMetadata(
                total_recommendations=len(recommendations),
                wall_clock_seconds=wall_clock,
                email_hit_rate=round(email_hit_rate, 3),
                deferred_limitations=[
                    "alphabetical-authorship fields (math/econ) break last-author PI heuristic",
                    "linked_programs not yet live-scraped (department URL acceptable for v1)",
                    "grant coverage limited to CORDIS (EU); DFG/UKRI not yet integrated",
                ],
            ),
        )

        logger.info(
            "Orchestrator: done — %d recommendations in %.1fs",
            len(recommendations),
            wall_clock,
        )
        return shortlist
