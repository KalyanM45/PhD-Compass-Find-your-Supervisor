from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin, urlencode

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_TIMEOUT = 15
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; phd-shortlist-bot/1.0; research use)"}
_BASE = "https://phdscanner.com"


def _extract_funding(text: str) -> str:
    t = text.lower()
    if any(p in t for p in ["fully funded", "full stipend", "epsrc", "bbsrc", "ukri funded"]):
        return "fully_funded"
    if any(p in t for p in ["self-funded", "self funded", "no funding"]):
        return "self_funded"
    if any(p in t for p in ["fees paid", "fee waiver", "partial"]):
        return "partial"
    return "unknown"


def _extract_eligibility(text: str) -> list[str]:
    t = text.lower()
    if any(p in t for p in ["uk students only", "home students", "uk nationals"]):
        return ["UK_only"]
    if any(p in t for p in ["uk and eu", "uk/eu", "home/eu"]):
        return ["UK", "EU"]
    if any(p in t for p in ["open to all", "international", "all nationalities"]):
        return ["UK", "EU", "International"]
    return ["Unknown"]


def search(supervisor_name: str, area: str) -> list[dict[str, Any]]:
    """Search PhD Scanner for positions matching supervisor + area."""
    last_name = supervisor_name.split()[-1] if supervisor_name else ""
    query = f"{last_name} {area}".strip()

    try:
        url = f"{_BASE}/phd-opportunities/?search={urlencode({'q': query})}"
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if not resp.ok:
            # Try alternative URL pattern
            url = f"{_BASE}/search/?q={urlencode({'': query})[1:]}"
            resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if not resp.ok:
            logger.debug("PhDScanner: HTTP %d for %r", resp.status_code, supervisor_name)
            return []
        return _parse(resp.text, supervisor_name)
    except Exception as exc:
        logger.debug("PhDScanner: error for %r: %s", supervisor_name, exc)
        return []


def _parse(html: str, supervisor_name: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    last_name = supervisor_name.split()[-1].lower() if supervisor_name else ""
    results = []

    # PhD Scanner listing cards — try multiple selectors as page structure may vary
    cards = (
        soup.find_all("div", class_=re.compile(r"opportunity|vacancy|listing|result|project", re.I))
        or soup.find_all("article")
        or soup.find_all("li", class_=re.compile(r"phd|result", re.I))
    )

    for card in cards[:15]:
        card_text = card.get_text(" ", strip=True)
        if last_name and last_name not in card_text.lower():
            continue

        title_tag = card.find(["h2", "h3", "h4"])
        link_tag = card.find("a", href=True)
        if not title_tag or not link_tag:
            continue

        title = title_tag.get_text(strip=True)
        href = link_tag["href"]
        url = href if href.startswith("http") else urljoin(_BASE, href)

        # Deadline
        deadline = None
        date_tag = card.find(string=re.compile(r"\d{1,2}[\s/\-]\w+[\s/\-]\d{4}", re.I))
        if date_tag:
            m = re.search(r"\d{1,2}[\s\-/]\w+[\s\-/]\d{4}", str(date_tag))
            if m:
                deadline = m.group()

        results.append({
            "title": title,
            "url": url,
            "deadline": deadline,
            "funding_status": _extract_funding(card_text),
            "eligible_citizenships": _extract_eligibility(card_text),
            "description": card_text[:1000],  # raw text for LLM eligibility check
            "source": "phdscanner",
        })

    logger.debug("PhDScanner: %d positions found for %r", len(results), supervisor_name)
    return results
