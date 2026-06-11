from __future__ import annotations

import logging
from typing import Any

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage

from src.graph.state import PipelineContext, PipelineState
from src.utils.clients import invoke_with_retry
from src.utils.llm_schemas import ProfileEnrichment
from src.utils.llm_utils import call_structured
from src.utils.prompts import load
from src.utils.schema import StudentProfile
from src.utils.topic_selector import select_topics

logger = logging.getLogger(__name__)


def enrich_profile(
    profile: StudentProfile,
    llm_client: ChatGroq,
) -> dict[str, Any]:
    """
    Call LLM to extract structured enrichment from the student profile.

    Args:
        profile: The student's profile data.
        llm_client: An instance of ChatLiteLLM to call the LLM.
        max_attempts: Number of attempts for LLM calls before falling back.

    Returns:
        A dictionary containing the enriched profile data, including:
        - openalex_concepts: List of research areas with optional query hints.
        - normalised_countries: List of target countries normalised by the LLM.
    """

    profile_json = profile.model_dump_json(indent=2)
    prompt = load("profile_enrichment").format(profile_json=profile_json)

    result = call_structured(llm_client, [HumanMessage(content=prompt)], ProfileEnrichment)
    return result.model_dump()


def build_query_plan(
    profile: StudentProfile,
    enrichment: dict[str, Any],
    topic_ids: list[str],
    total_target: int,
    recency_years: int,
) -> list[dict[str, Any]]:
    """Build a per-area query plan using LLM-normalised countries and per-area quota."""
    areas = enrichment.get("openalex_concepts", [])
    if not areas:
        areas = [{"name": i, "query_hint": i} for i in profile.research_interests]

    n = len(areas)
    per_area = max(1, total_target // n) if n else total_target
    floor_year = profile.target_intake.year - recency_years
    countries = enrichment.get("normalised_countries") or profile.target_countries

    return [
        {
            "area": area["name"],
            "query_hint": area.get("query_hint", area["name"]),
            "countries": countries,
            "recency_floor": floor_year,
            "target_count": per_area,
            "topic_ids": topic_ids,
        }
        for area in areas
    ]


class ProfileNode:
    def __init__(self, context: PipelineContext) -> None:
        self._context = context

    def run(self, state: PipelineState) -> dict:
        """Graph node: enrich profile via LLM and build the per-area query plan."""
        profile: StudentProfile = state["profile"]
        cfg = self._context.config

        logger.info("ProfileAgent: enriching profile via LLM")
        enrichment = enrich_profile(profile, self._context.llm_client)

        # Use LLM-normalised countries as the authoritative list for all downstream nodes
        countries = enrichment.get("normalised_countries") or profile.target_countries

        # Derive OpenAlex topic IDs via 4-step hierarchical LLM selection
        logger.info("ProfileAgent: selecting OpenAlex topics via hierarchy")
        interests = list(dict.fromkeys(
            enrichment.get("stated_interests", []) +
            enrichment.get("revealed_interests", []) +
            profile.research_interests
        ))
        topic_ids = select_topics(interests, self._context.llm_client)
        logger.info("ProfileAgent: %d topic IDs selected", len(topic_ids))

        query_plan = build_query_plan(
            profile,
            enrichment,
            topic_ids=topic_ids,
            total_target=cfg["quotas"]["total_target"],
            recency_years=cfg["openalex"]["recency_years"],
        )

        return {
            **state,
            "enrichment": enrichment,
            "countries": countries,
            "query_plan": query_plan,
        }
