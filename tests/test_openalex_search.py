"""Quick diagnostic for OpenAlex search queries.

Run from the project root:
    python tests/test_openalex_search.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MAILTO = "mhemakalyan@gmail.com"
BASE = "https://api.openalex.org"
COUNTRIES = ["IN", "US", "GB"]
YEAR_FLOOR = 2020

QUERIES = [
    "SE3 equivariant message passing neural networks",
    "equivariant GNN SE3 molecular geometry",
    "geometric deep learning molecular property prediction",
    "graph neural network drug discovery",
    "message passing neural network protein",
]

TOPIC_IDS = []  # leave empty to test without topic filter first


def _get(path: str, params: dict) -> dict:
    url = BASE + path
    params = {**params, "mailto": MAILTO}
    resp = requests.get(url, params=params, timeout=20)
    print(f"  URL: {resp.url}")
    resp.raise_for_status()
    return resp.json()


def test_query(query: str, *, use_search_param: bool, use_topics: bool) -> int:
    country_codes = "|".join(COUNTRIES)
    filters = (
        f"institutions.country_code:{country_codes},"
        f"publication_year:>{YEAR_FLOOR - 1},"
        f"type:article|preprint"
    )
    if use_topics and TOPIC_IDS:
        filters += ",topics.id:" + "|".join(TOPIC_IDS)

    params: dict = {
        "filter": filters,
        "per-page": 5,
        "select": "id,title,publication_year,cited_by_count",
    }

    if use_search_param:
        # Current approach — may be an invalid parameter
        params["search.title_and_abstract"] = query
    else:
        # Correct OpenAlex approach
        params["search"] = query

    data = _get("/works", params)
    count = data.get("meta", {}).get("count", 0)
    results = data.get("results", [])
    for r in results[:3]:
        print(f"    • [{r.get('publication_year')}] {r.get('title', '')[:80]}")
    return count


def main() -> None:
    for query in QUERIES:
        print(f"\n{'='*70}")
        print(f"Query: {query!r}")

        print("\n  [A] search.title_and_abstract (current code), no topics:")
        n = test_query(query, use_search_param=True, use_topics=False)
        print(f"  -> {n} results")

        print("\n  [B] search (correct param), no topics:")
        n = test_query(query, use_search_param=False, use_topics=False)
        print(f"  -> {n} results")

        if TOPIC_IDS:
            print("\n  [C] search (correct param) + topic filter:")
            n = test_query(query, use_search_param=False, use_topics=True)
            print(f"  -> {n} results")


if __name__ == "__main__":
    main()
