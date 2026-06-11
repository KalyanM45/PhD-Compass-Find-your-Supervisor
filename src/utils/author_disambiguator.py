"""Subagent: disambiguate a single PI candidate via OpenAlex author record.

Five-layer validation:
  1. x_concepts keyword overlap — fast pre-filter; if clear mismatch, drop immediately
  2. LLM semantic relevance     — called only when keyword result is ambiguous (0-1 hits);
                                   decides "does this researcher's career overlap with the student's area?"
  3. Institution type check     — drop if primary affiliation is company / funder / archive
  4. Bibliometric gate          — drop if works_count + h_index clearly indicate PhD student / postdoc
  5. ORCID employment           — confirm or override based on stated employment role;
                                   handles non-English titles (Maître de conférences, W2 Professor, etc.)

Returns the enriched candidate dict, or None if it should be dropped.
Precision over recall — drop on clear evidence, keep on ambiguity.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from src.utils.clients import OpenAlexClient, invoke_with_retry
from src.utils.llm_utils import call_structured
from src.utils.prompts import load
from config.constants import (
    CONCEPT_SCORE_THRESHOLD,
    KEYWORD_CLEAR_MATCH,
    NON_SUPERVISING_TYPES,
    FACULTY_KEYWORDS,
    JUNIOR_KEYWORDS,
)

logger = logging.getLogger(__name__)

_STOPWORDS = {"for", "and", "the", "of", "in", "a", "an", "with", "on", "to"}

_CONCEPT_SCORE_THRESHOLD = CONCEPT_SCORE_THRESHOLD
_KEYWORD_CLEAR_MATCH = KEYWORD_CLEAR_MATCH
_NON_SUPERVISING_TYPES = NON_SUPERVISING_TYPES
_FACULTY_KEYWORDS = FACULTY_KEYWORDS
_JUNIOR_KEYWORDS = JUNIOR_KEYWORDS


# ---------------------------------------------------------------------------
# Layer 1 — keyword overlap (fast, no API)
# ---------------------------------------------------------------------------

def _author_concept_names(author_record: dict[str, Any]) -> set[str]:
    concepts = author_record.get("x_concepts", []) or []
    return {
        c["display_name"].lower()
        for c in concepts
        if c.get("score", 0) >= _CONCEPT_SCORE_THRESHOLD
    }


def _query_keywords(area: str, query_hint: str) -> set[str]:
    text = f"{area} {query_hint}".lower()
    return {w for w in text.split() if w not in _STOPWORDS and len(w) > 3}


def _keyword_overlap_score(author_concepts: set[str], query_keywords: set[str]) -> int:
    return sum(
        1 for kw in query_keywords if any(kw in concept for concept in author_concepts)
    )


# ---------------------------------------------------------------------------
# Layer 2 — LLM semantic relevance (called only on ambiguous cases)
# ---------------------------------------------------------------------------

class _RelevanceDecision(BaseModel):
    is_relevant: bool = False
    reason: str = ""


def _llm_is_relevant(
    candidate_name: str,
    author_concepts: set[str],
    area: str,
    query_hint: str,
    llm_client: Any,
) -> bool:
    """Ask LLM whether this researcher's career topics overlap with the student's area."""
    concepts_text = ", ".join(sorted(author_concepts)[:30]) or "not available"
    prompt = load("author_relevance").format(
        area=area,
        query_hint=query_hint,
        candidate_name=candidate_name,
        concepts_text=concepts_text,
    )

    result = call_structured(llm_client, [HumanMessage(content=prompt)], _RelevanceDecision)
    logger.debug(
        "LLM relevance for %s: %s — %s",
        candidate_name, result.is_relevant, result.reason,
    )
    return result.is_relevant


# ---------------------------------------------------------------------------
# Layer 3 — institution type check
# ---------------------------------------------------------------------------

def _institution_can_supervise(author_record: dict[str, Any]) -> Optional[bool]:
    """Return False if the author's current institution cannot offer PhD supervision.

    Checks last_known_institutions first (current affiliation), then falls back to
    affiliations sorted by most recent year. Returns None if no data available.

    Returns:
        False — current institution type is company / funder / archive
        True  — current institution type confirms supervision is possible
        None  — no affiliation data (caller keeps the candidate)
    """
    # Prefer last_known_institutions — directly the author's current institution(s)
    current = author_record.get("last_known_institutions") or []
    if not current:
        # Fall back to affiliations sorted by most recent year
        affiliations = author_record.get("affiliations") or []
        if not affiliations:
            return None
        affiliations_sorted = sorted(
            affiliations,
            key=lambda a: max(a.get("years") or [0]),
            reverse=True,
        )
        current = [a.get("institution") or {} for a in affiliations_sorted]

    for inst in current:
        inst_type = inst.get("type")
        if inst_type in _NON_SUPERVISING_TYPES:
            return False
        if inst_type:
            return True  # first institution with a known type determines the result

    return None


# ---------------------------------------------------------------------------
# Layer 4 — bibliometric gate
# ---------------------------------------------------------------------------

def _passes_metrics_gate(works_count: int, h_index: int, min_works: int, min_h: int) -> bool:
    return works_count >= min_works or h_index >= min_h


# ---------------------------------------------------------------------------
# Layer 4 — ORCID employment check
# ---------------------------------------------------------------------------

def _check_orcid_employment(orcid: str, timeout: int = 8) -> Optional[bool]:
    """Check ORCID public API employment history.

    Returns:
        True  — at least one role matches faculty/PI keywords
        False — all roles are clearly junior (no faculty role found)
        None  — no data or API unavailable
    """
    raw_orcid = orcid.split("/")[-1]
    url = f"https://pub.orcid.org/v3.0/{raw_orcid}/employments"
    try:
        resp = requests.get(url, headers={"Accept": "application/json"}, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception as exc:
        logger.debug("ORCID fetch failed for %s: %s", orcid, exc)
        return None

    roles: list[str] = []
    for group in data.get("affiliation-group", []):
        for summary in group.get("summaries", []):
            role = (
                summary.get("employment-summary", {}).get("role-title") or ""
            ).lower().strip()
            if role:
                roles.append(role)

    if not roles:
        return None

    has_faculty = any(any(kw in role for kw in _FACULTY_KEYWORDS) for role in roles)
    has_only_junior = all(any(kw in role for kw in _JUNIOR_KEYWORDS) for role in roles)

    if has_faculty:
        return True
    if has_only_junior:
        return False
    return None  # roles exist but don't clearly classify (e.g. "Wissenschaftlicher Mitarbeiter")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def disambiguate_candidate(
    candidate: dict[str, Any],
    area: str,
    query_hint: str,
    client: OpenAlexClient,
    llm_client: Any = None,
    min_works: int = 10,
    min_h_index: int = 5,
    orcid_faculty_check: bool = True,
) -> Optional[dict[str, Any]]:
    """Return enriched candidate or None if it should be dropped."""
    author_record = client.get_author(candidate["openalex_author_id"])
    if author_record is None:
        logger.debug("Drop %s — author record not found", candidate["name"])
        return None

    author_concepts = _author_concept_names(author_record)
    query_kw = _query_keywords(area, query_hint)

    # --- Layer 1: keyword overlap ---
    overlap = _keyword_overlap_score(author_concepts, query_kw)

    if author_concepts:
        if overlap >= _KEYWORD_CLEAR_MATCH:
            # Clear match — no LLM needed
            pass
        elif llm_client is not None:
            # Ambiguous (0 or 1 keyword hit) — ask LLM
            relevant = _llm_is_relevant(
                candidate["name"], author_concepts, area, query_hint, llm_client
            )
            if not relevant:
                logger.debug(
                    "Drop %s — LLM says career topics not relevant to area %r",
                    candidate["name"], area,
                )
                return None
        else:
            # No LLM available — fall back to strict keyword rule
            if overlap == 0:
                logger.debug(
                    "Drop %s — zero keyword overlap and no LLM available",
                    candidate["name"], area,
                )
                return None

    # --- Layer 3: institution type ---
    inst_ok = _institution_can_supervise(author_record)
    if inst_ok is False:
        logger.debug(
            "Drop %s — primary institution is company/funder/archive (cannot supervise)",
            candidate["name"],
        )
        return None

    # --- Extract metrics ---
    summary = author_record.get("summary_stats", {})
    works_count = author_record.get("works_count", 0)
    h_index = summary.get("h_index", 0)
    i10_index = summary.get("i10_index", 0)
    cited_by_count = author_record.get("cited_by_count", 0)
    orcid = author_record.get("orcid")

    # --- Layer 5: ORCID employment (before metric gate — confirmed faculty overrides) ---
    orcid_status: Optional[bool] = None
    if orcid_faculty_check and orcid:
        orcid_status = _check_orcid_employment(orcid)
        if orcid_status is True:
            logger.debug("%s — ORCID confirms faculty role", candidate["name"])
        elif orcid_status is False:
            logger.debug(
                "Drop %s — ORCID shows only junior roles, no faculty role found",
                candidate["name"],
            )
            return None

    # --- Layer 4: bibliometric gate (skipped if ORCID confirmed faculty) ---
    if orcid_status is not True:
        likely_pi = candidate.get("likely_pi", False)
        if not _passes_metrics_gate(works_count, h_index, min_works, min_h_index) and not likely_pi:
            logger.debug(
                "Drop %s — below metric thresholds (works=%d h=%d) and not last-author",
                candidate["name"], works_count, h_index,
            )
            return None

    # --- All layers passed: enrich and return ---
    cand = dict(candidate)
    if orcid:
        cand["orcid"] = orcid
    cand["works_count"] = works_count
    cand["cited_by_count"] = cited_by_count
    cand["h_index"] = h_index
    cand["i10_index"] = i10_index
    cand["author_concepts"] = list(author_concepts)
    if orcid_status is True:
        cand["orcid_faculty_confirmed"] = True

    return cand
