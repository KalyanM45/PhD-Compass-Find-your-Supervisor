from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING
from typing_extensions import TypedDict

from src.utils.schema import StudentProfile  # must be a real import; LangGraph resolves type hints at runtime

if TYPE_CHECKING:
    from src.utils.clients import LLMClientPool, OpenAlexClient


# ---------------------------------------------------------------------------
# Graph state — the single dict that flows through every LangGraph node
# ---------------------------------------------------------------------------

class PipelineState(TypedDict, total=False):
    """Typed state shared across all pipeline nodes.

    ``total=False`` makes every key optional so nodes can return partial
    updates without needing to carry keys they did not touch.
    """

    # Input — loaded once before the graph runs
    profile: "StudentProfile"

    # Set by enrich_profile
    enrichment: dict[str, Any]        # LLM-extracted capability profile + concepts
    countries: list[str]              # ISO-normalised target countries
    query_plan: list[dict[str, Any]]  # per-area search plan with quotas

    # Set by retrieve_papers
    papers_by_area: dict[str, list[dict[str, Any]]]

    # Set/updated by build_candidates → attach_evidence → score_and_balance
    area_candidates: dict[str, list[dict[str, Any]]]

    # Set by score_and_balance; why_match blurbs added by generate_why_match
    final_candidates: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Pipeline context — shared resources injected into every node via closure
# ---------------------------------------------------------------------------

@dataclass
class PipelineContext:
    """Immutable resources passed to every node at graph-build time.

    Rather than threading these through state (which would make them
    serialisable/visible to LangGraph), we capture them in closures when
    adding nodes to the StateGraph via ``_wrap(agent_fn, context)``.
    """

    config: dict[str, Any]
    llm_client: "LLMClientPool"
    openalex: "OpenAlexClient"
    llm_model: str
