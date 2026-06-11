from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from langchain_core.messages import HumanMessage

from src.graph.state import PipelineContext, PipelineState
from src.utils.llm_schemas import CandidateReview
from src.utils.llm_utils import call_structured

logger = logging.getLogger(__name__)

_REVIEW_PROMPT = """\
You are a quality reviewer for a PhD supervisor shortlist. Your job is to catch two types of bad entries before they reach the student.

Student research area: {area}
Student specific topics: {student_interests}

Candidate:
  Name:             {name}
  Institution:      {institution}
  Research focus:   {research_focus}
  Evidence papers:
{paper_list}

---
CHECK 1 — INSTITUTION
Is "{institution}" a genuine academic institution where this person could formally supervise a PhD student?
Valid: universities, technical universities, research institutes (Max Planck, Fraunhofer, Helmholtz, CNRS, etc.), national labs, university-affiliated medical centres.
NOT valid: private pharmaceutical companies (Roche, Novartis, Sanofi, Pfizer, Merck, Bayer, AstraZeneca, Genentech, Lilly, Abbvie), private tech companies (Google, Microsoft, Amazon, Meta, DeepMind, Apple), consulting firms, banks, insurance companies, or purely industrial organisations.
If you are unsure, return keep=true.

CHECK 2 — DOMAIN RELEVANCE
Do the evidence papers SPECIFICALLY relate to "{area}"?
Generic "deep learning" or "machine learning" is NOT enough.
The work must be in the same domain as the student's area — not just use similar methods in an unrelated domain.
Examples of wrong-domain matches for a student in drug discovery / molecular property prediction:
  - laser welding defect detection
  - automated pain recognition in animals
  - gait freezing detection in Parkinson's patients
  - dental mesh segmentation
  - body composition from CT scans
  - autonomous driving with graph neural networks
If the papers are clearly from a different domain, return keep=false.
If even one paper genuinely relates to the student's area, return keep=true.

Return keep=true only if BOTH checks pass. Otherwise return keep=false and a short drop_reason.
Return JSON only.\
"""


def _review_one(
    cand: dict[str, Any],
    student_interests: list[str],
    llm_client: Any,
) -> dict[str, Any] | None:
    area = cand.get("area", "")
    paper_titles = [
        f"  - {p.get('title', '')} ({p.get('year', '')})"
        for p in cand.get("evidence_papers", [])[:3]
    ]
    paper_list = "\n".join(paper_titles) if paper_titles else "  - (no papers)"

    prompt = _REVIEW_PROMPT.format(
        area=area,
        student_interests=", ".join(student_interests) or area,
        name=cand.get("name", ""),
        institution=cand.get("institution", ""),
        research_focus=cand.get("area", ""),
        paper_list=paper_list,
    )

    try:
        result: CandidateReview = call_structured(
            llm_client, [HumanMessage(content=prompt)], CandidateReview
        )
        if not result.keep:
            logger.debug(
                "ReviewNode: drop %r at %r — %s",
                cand.get("name"), cand.get("institution"), result.drop_reason,
            )
            return None
        return cand
    except Exception as exc:
        logger.debug(
            "ReviewNode: LLM failed for %r — keeping (fail-open): %s",
            cand.get("name"), exc,
        )
        return cand  # on failure, keep the candidate


class ReviewNode:
    def __init__(self, context: PipelineContext) -> None:
        self._context = context

    def run(self, state: PipelineState) -> dict:
        """Graph node: LLM quality-gate — drop industry researchers and wrong-domain matches."""
        final_candidates: list[dict[str, Any]] = state["final_candidates"]
        student_interests: list[str] = state["profile"].research_interests
        parallelism = self._context.config["timeouts"].get("llm_parallelism_limit", 3)

        kept: list[dict[str, Any]] = []
        dropped = 0

        with ThreadPoolExecutor(max_workers=parallelism) as pool:
            futures = {
                pool.submit(_review_one, cand, student_interests, self._context.llm_client): cand
                for cand in final_candidates
            }
            for future in as_completed(futures):
                original = futures[future]
                try:
                    result = future.result()
                    if result is not None:
                        kept.append(result)
                    else:
                        dropped += 1
                except Exception as exc:
                    logger.warning(
                        "ReviewNode: error reviewing %s — keeping: %s",
                        original.get("name"), exc,
                    )
                    kept.append(original)

        logger.info(
            "ReviewNode: %d kept, %d dropped (of %d total)",
            len(kept), dropped, len(final_candidates),
        )
        return {**state, "final_candidates": kept}
