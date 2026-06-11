"""Subagent: resolve contact email for a single PI candidate.

Tries ORCID public API first, then scrapes the PI's homepage.
Returns None rather than fabricating an address.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_SCRAPE_TIMEOUT = 10


def _from_orcid(orcid: str) -> Optional[str]:
    clean = orcid.replace("https://orcid.org/", "").strip("/")
    try:
        resp = requests.get(
            f"https://pub.orcid.org/v3.0/{clean}/emails",
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if resp.ok:
            for entry in resp.json().get("email", []):
                if addr := entry.get("email"):
                    return addr
    except Exception:
        pass
    return None


def _from_homepage(url: str) -> Optional[str]:
    try:
        resp = requests.get(
            url,
            timeout=_SCRAPE_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; phd-shortlist-bot/1.0)"},
        )
        if resp.ok:
            valid = [
                m
                for m in _EMAIL_RE.findall(resp.text)
                if not m.endswith((".png", ".jpg", ".gif"))
                and "example" not in m
                and "noreply" not in m
            ]
            if valid:
                return valid[0]
    except Exception:
        pass
    return None


def resolve_email(candidate: dict[str, Any]) -> Optional[str]:
    """Return email address or None — never fabricated."""
    if orcid := candidate.get("orcid"):
        if email := _from_orcid(orcid):
            return email

    homepage = candidate.get("_author_record", {}).get("homepage_url")
    if homepage:
        if email := _from_homepage(homepage):
            return email

    return None
