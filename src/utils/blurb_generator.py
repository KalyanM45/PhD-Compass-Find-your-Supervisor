"""Subagent: generate a single grounded why_match blurb via LLM."""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq

from src.utils.clients import invoke_with_retry
from src.utils.prompts import load

logger = logging.getLogger(__name__)

_PROMPT = load("why_match_blurb")
_UC_SECTION = load("why_match_uc_section")


def generate_blurb(
    candidate: dict[str, Any],
    capability_profile: list[str],
    llm_client: ChatGroq,
    use_cases: list[str] | None = None,
) -> str:
    """Return a grounded why_match sentence for this candidate.

    When ``use_cases`` are provided and any evidence paper carries a
    ``relevance_note`` (set by ``usecase_matcher``), those papers are surfaced
    prominently in the prompt so the LLM references the applied-domain overlap.
    """
    evidence_papers: list[dict[str, Any]] = candidate.get("evidence_papers", [])

    # Separate use-case-matched papers from the rest
    uc_papers = [p for p in evidence_papers if p.get("relevance_note")]

    papers_str = "; ".join(
        f"\"{p['title']}\" ({p.get('year', 'n/a')})"
        for p in evidence_papers
    ) or "none"
    grants_str = "; ".join(
        f"\"{g['title']}\" ({g.get('funder', '')})"
        for g in candidate.get("evidence_grants", [])
    ) or "none"

    # Build the use-case section only when there are matches
    if uc_papers and use_cases:
        uc_papers_str = "; ".join(
            f"\"{p['title']}\" ({p.get('year', 'n/a')}) — {p['relevance_note']}"
            for p in uc_papers
        )
        uc_papers_section = _UC_SECTION.format(
            use_cases=", ".join(use_cases),
            uc_papers=uc_papers_str,
        )
    else:
        uc_papers_section = ""

    prompt = _PROMPT.format(
        capability_profile=", ".join(capability_profile),
        uc_papers_section=uc_papers_section,
        papers=papers_str,
        grants=grants_str,
    )

    try:
        response = invoke_with_retry(llm_client, [HumanMessage(content=prompt)])
        return response.content.strip()
    except Exception as exc:
        logger.warning(
            "Blurb generation failed for %s: %s", candidate["name"], exc
        )
        # Prefer a use-case-matched paper for the fallback sentence
        top_paper = (uc_papers or evidence_papers or [{}])[0]
        paper_title = top_paper.get("title", "recent work")
        skill = capability_profile[0] if capability_profile else "your research"
        return (
            f"Your background in {skill} directly relates to "
            f"Prof. {candidate['name']}'s paper \"{paper_title}\"."
        )
