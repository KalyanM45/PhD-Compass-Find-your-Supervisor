"""Pydantic schemas for LLM-generated outputs.

Each class represents the expected JSON structure returned by one LLM call.
Using Pydantic here gives us:
  - Field-level validation (types, required vs optional)
  - Automatic coercion (e.g. a string where a list is expected)
  - Clear contract between the prompt and the code that consumes the output
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# profile_enrichment prompt output  (src/nodes/profile.py)
# ---------------------------------------------------------------------------

class OpenAlexConcept(BaseModel):
    """A single research area grounded to an OpenAlex-searchable query term."""
    name: str                  # human-readable area label, e.g. "geometric deep learning"
    query_hint: str            # search keyword sent to OpenAlex, e.g. "equivariant GNN SE3"


class ProfileEnrichment(BaseModel):
    """Structured output returned by the profile_enrichment LLM call.

    Fields map 1-to-1 to the numbered items in prompts/profile_enrichment.txt.
    All fields are optional so partial LLM responses still parse; downstream
    code falls back to profile data when a field is missing.
    """

    # Concrete hands-on skills extracted from projects/thesis/publications.
    # Used by why_match_agent to write personalised blurbs.
    capability_profile: list[str] = Field(default_factory=list)

    # Research areas the student explicitly states they want to pursue.
    # Not used downstream today — kept for future gap-analysis features.
    stated_interests: list[str] = Field(default_factory=list)

    # Research areas implied by the student's actual work (may differ from stated).
    # Not used downstream today — reserved for scoring enhancements.
    revealed_interests: list[str] = Field(default_factory=list)

    # Mismatches between stated and revealed interests flagged by the LLM.
    # Not used downstream today — surfaced to the user in future UI.
    gap_flags: list[str] = Field(default_factory=list)

    # Per-area query plan fed directly into OpenAlex search.
    # Critical: drives the entire retrieval stage.
    openalex_concepts: list[OpenAlexConcept] = Field(default_factory=list)

    # Dense paragraph combining thesis + projects for future embedding similarity.
    # Not used downstream today — reserved for semantic search stage.
    embedding_text: str = ""

    # ISO 3166-1 alpha-2 country codes resolved from the student's target_countries.
    # Critical: used as the OpenAlex country filter in every search query.
    normalised_countries: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# eligibility_check prompt output  (src/utils/program_fetcher.py)
# ---------------------------------------------------------------------------

class EligibilityCheck(BaseModel):
    """Structured output for LLM-based PhD position eligibility check."""
    eligible: bool = True          # True if the student's citizenship is allowed
    restriction: str = "Unknown"   # plain-English summary e.g. "UK/EU only" or "open to all"
