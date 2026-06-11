from __future__ import annotations

import logging
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_EU_ADJACENT = {"DE", "NL", "CH", "FR", "BE", "AT", "SE", "FI", "DK", "NO"}


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
def _cordis_fetch(pi_name: str, timeout: int = 15) -> list[dict[str, Any]]:
    url = "https://cordis.europa.eu/api/search/api/search"
    params = {
        "q": f'"{pi_name}"',
        "p": 1,
        "num": 5,
        "fl": "id,title,startDate,endDate,fundingScheme,relations",
        "format": "json",
    }
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        hits = (
            data.get("payload", {})
            .get("results", {})
            .get("project", {})
            .get("hits", [])
        )
        grants = []
        for proj in hits[:3]:
            pid = proj.get("id", "")
            start, end = proj.get("startDate", ""), proj.get("endDate", "")
            years = f"{start[:4]}–{end[:4]}" if start and end else ""
            grants.append(
                {
                    "title": proj.get("title", ""),
                    "funder": "European Commission (Horizon)",
                    "id": pid,
                    "url": f"https://cordis.europa.eu/project/id/{pid}",
                    "years": years,
                }
            )
        return grants
    except Exception as exc:
        logger.debug("CORDIS fetch failed for %r: %s", pi_name, exc)
        return []


def fetch_grants_for_candidate(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Return grant list for candidate; empty list if country not covered."""
    if candidate.get("country") not in _EU_ADJACENT:
        return []
    return _cordis_fetch(candidate["name"])
