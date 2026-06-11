"""Scraper: FindAPhD.com — largest UK/EU PhD vacancy aggregator.

Search URL: https://www.findaphd.com/phds/search/?Keywords=term&CountryCode=GB
Returns structured listings with title, supervisor, funding, eligibility, deadline.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_TIMEOUT = 15
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; phd-shortlist-bot/1.0; research use)"}
_BASE = "https://www.findaphd.com"


def _extract_eligibility(text: str) -> list[str]:
    """Parse raw text to determine who is eligible. Returns list like ['UK', 'EU', 'International']."""
    t = text.lower()
    if any(p in t for p in ["uk students only", "home students only", "uk nationals only", "restricted to uk"]):
        return ["UK_only"]
    if any(p in t for p in ["uk and eu", "uk/eu", "home and eu", "eu and uk"]):
        return ["UK", "EU"]
    if any(p in t for p in ["open to all", "international students welcome", "all nationalities", "worldwide"]):
        return ["UK", "EU", "International"]
    if any(p in t for p in ["eu students", "eu citizens", "european"]):
        return ["EU"]
    return ["Unknown"]


def _extract_funding(text: str) -> str:
    t = text.lower()
    if any(p in t for p in ["fully funded", "full funding", "stipend", "scholarship"]):
        return "fully_funded"
    if any(p in t for p in ["self-funded", "self funded", "unfunded", "no funding"]):
        return "self_funded"
    if any(p in t for p in ["partial", "fees only", "fee waiver"]):
        return "partial"
    return "unknown"


def search(supervisor_name: str, area: str, country_code: str) -> list[dict[str, Any]]:
    """Search FindAPhD for open positions matching supervisor + area."""
    last_name = supervisor_name.split()[-1] if supervisor_name else ""
    params = {
        "Keywords": f"{last_name} {area}",
        "CountryCode": country_code.upper(),
        "PhdSearchFilterOptions": "3",  # show funded only if possible
    }
    try:
        resp = requests.get(f"{_BASE}/phds/search/", params=params, headers=_HEADERS, timeout=_TIMEOUT)
        if not resp.ok:
            logger.debug("FindAPhD: HTTP %d for %r", resp.status_code, supervisor_name)
            return []
        return _parse(resp.text, supervisor_name)
    except Exception as exc:
        logger.debug("FindAPhD: error for %r: %s", supervisor_name, exc)
        return []


def _parse(html: str, supervisor_name: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    last_name = supervisor_name.split()[-1].lower() if supervisor_name else ""
    results = []

    # FindAPhD uses div.phd-result__item or similar cards
    cards = (
        soup.find_all("div", class_=re.compile(r"phd-result", re.I))
        or soup.find_all("div", class_=re.compile(r"result-item|listing-item", re.I))
        or soup.find_all("article")
    )

    for card in cards[:15]:
        card_text = card.get_text(" ", strip=True)
        # Only include if supervisor last name appears in the listing
        if last_name and last_name not in card_text.lower():
            continue

        title_tag = card.find(["h3", "h4", "h2"])
        link_tag = card.find("a", href=re.compile(r"/phds/", re.I))
        if not link_tag:
            link_tag = card.find("a", href=True)
        if not title_tag or not link_tag:
            continue

        title = title_tag.get_text(strip=True)
        href = link_tag["href"]
        url = href if href.startswith("http") else urljoin(_BASE, href)

        # Deadline
        deadline = None
        for cls in [r"deadline", r"date", r"closing"]:
            tag = card.find(class_=re.compile(cls, re.I))
            if tag:
                m = re.search(r"\d{1,2}[\s\-/]\w+[\s\-/]\d{4}|\d{4}-\d{2}-\d{2}", tag.get_text())
                if m:
                    deadline = m.group()
                    break

        results.append({
            "title": title,
            "url": url,
            "deadline": deadline,
            "funding_status": _extract_funding(card_text),
            "eligible_citizenships": _extract_eligibility(card_text),
            "description": card_text[:1000],  # raw text for LLM eligibility check
            "source": "findaphd",
        })

    logger.debug("FindAPhD: %d positions found for %r", len(results), supervisor_name)
    return results
