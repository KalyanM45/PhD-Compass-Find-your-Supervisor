"""Subagent: verify a single candidate's current country from recent works.

Returns the confirmed ISO country code, or None if unverifiable.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Optional

from src.utils.clients import OpenAlexClient

logger = logging.getLogger(__name__)

_MIN_RECENT_PAPERS = 1
_MAJORITY_THRESHOLD = 0.5


def verify_candidate_country(
    candidate: dict[str, Any],
    target_countries: list[str],
    client: OpenAlexClient,
    recency_floor: int,
) -> Optional[dict[str, Any]]:
    """Return the updated candidate if country confirmed, or None to drop.

    None is also returned when the country is unverifiable (sparse works) — the
    caller decides to keep or drop in that case (currently: keep).
    """
    author_id = candidate["openalex_author_id"]
    recent_works = client.get_author_recent_works(author_id, recency_floor)

    if not recent_works:
        logger.debug("%s — no recent works found, keeping (unverifiable)", candidate["name"])
        return candidate  # keep; Stage 3 already enforced country at retrieval

    country_counts: Counter[str] = Counter()
    for work in recent_works:
        for authorship in work.get("authorships", []):
            if authorship.get("author", {}).get("id") == author_id:
                for inst in authorship.get("institutions", []):
                    if cc := inst.get("country_code"):
                        country_counts[cc] += 1

    if not country_counts:
        logger.debug("%s — no country info in recent works, keeping", candidate["name"])
        return candidate

    top_country, top_count = country_counts.most_common(1)[0]
    total = sum(country_counts.values())

    if total < _MIN_RECENT_PAPERS or top_count / total < _MAJORITY_THRESHOLD:
        logger.debug("%s — country ambiguous, keeping", candidate["name"])
        return candidate

    if top_country not in target_countries:
        logger.debug(
            "Drop %s — confirmed country %r not in target_countries",
            candidate["name"],
            top_country,
        )
        return None

    cand = dict(candidate)
    cand["country"] = top_country
    return cand
