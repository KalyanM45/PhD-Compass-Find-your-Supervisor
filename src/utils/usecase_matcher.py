"""Subagent: cross-match PI paper titles/concepts against student use-case keywords.

Keyword-based (no LLM) — fast, deterministic, cache-free.

A paper is considered a match if at least one keyword from any use-case phrase
appears in the paper title or its OpenAlex x_concepts display names.
Matched papers receive a `relevance_note` field and are sorted to the front so
the blurb generator and orchestrator can prioritise them.
"""
from __future__ import annotations

import re
from typing import Any

_STOP: frozenset[str] = frozenset(
    {"for", "and", "the", "via", "with", "of", "in", "on", "to", "a", "an", "is",
     "are", "at", "by", "from", "its", "that", "this", "as", "be"}
)


def _keyword_set(phrase: str) -> frozenset[str]:
    """Tokenise a use-case phrase into meaningful lowercase keywords."""
    tokens = re.split(r"\W+", phrase.lower())
    return frozenset(t for t in tokens if len(t) >= 3 and t not in _STOP)


def match_papers_to_usecases(
    papers: list[dict[str, Any]],
    use_cases: list[str],
) -> list[dict[str, Any]]:
    """Annotate papers that match student use cases; return matched-first order.

    Args:
        papers: Paper dicts from OpenAlex (may have 'title', 'concepts' list).
        use_cases: Student's applied-domain strings, e.g. ["drug discovery"].

    Returns:
        Same paper dicts, sorted matched→unmatched.  Matched papers have
        ``relevance_note`` set; original relative order is preserved within
        each group.
    """
    if not use_cases or not papers:
        return papers

    uc_kw: dict[str, frozenset[str]] = {
        uc: kws for uc in use_cases if (kws := _keyword_set(uc))
    }
    if not uc_kw:
        return papers

    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for paper in papers:
        title = (paper.get("title") or "").lower()
        concepts_text = " ".join(
            (c.get("display_name") or "").lower()
            for c in paper.get("concepts", [])
        )
        search_text = title + " " + concepts_text

        hit_cases = [
            uc for uc, kws in uc_kw.items()
            if any(kw in search_text for kw in kws)
        ]

        if hit_cases:
            p = dict(paper)
            p["relevance_note"] = "Matches your use case: " + "; ".join(hit_cases)
            matched.append(p)
        else:
            unmatched.append(paper)

    return matched + unmatched
