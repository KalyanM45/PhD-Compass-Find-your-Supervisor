"""LangGraph pipeline for the PhD Shortlist Builder.

Graph topology (linear DAG — each node depends on the previous one):

    START
      │
  enrich_profile       ← ProfileNode   : LLM call #1 — extract capability
      │                                   profile, openalex concepts, countries
  retrieve_papers      ← RetrievalNode : expand queries + parallel OpenAlex fetch
      │
  build_candidates     ← CandidateNode : extract PIs, disambiguate, country-filter
      │
  attach_evidence      ← EvidenceNode  : papers + grants + email + programs (parallel)
      │
  score_and_balance    ← ScoringNode   : score, tier, balance across areas
      │
  generate_why_match   ← WhyMatchNode  : LLM calls #2…N (parallel, rate-limited)
      │
     END

Why LangGraph?
--------------
LangGraph manages the state dict automatically — each node returns only the
keys it updates and LangGraph merges them.  This gives us:
  • Built-in streaming / checkpointing hooks (not used yet, easy to add).
  • Clear graph topology visible in LangGraph Studio / tracing tools.
  • Standard edge API instead of a hand-rolled topological sort.

Node contract
-------------
Every node class exposes a ``run(state) -> dict`` method that accepts the full
PipelineState and returns a *partial* dict of updated keys.
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from src.graph.state import PipelineContext, PipelineState
from src.nodes.profile import ProfileNode
from src.nodes.retrieval import RetrievalNode
from src.nodes.candidates import CandidateNode
from src.nodes.evidence import EvidenceNode
from src.nodes.scoring import ScoringNode
from src.nodes.why_match import WhyMatchNode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------

def build_pipeline(context: PipelineContext):
    """Build and compile the LangGraph StateGraph for one pipeline run.

    Returns a compiled ``CompiledGraph`` whose ``.invoke(state)`` method
    executes all nodes in order and returns the final state.

    Args:
        context: Shared resources (LLM pool, OpenAlex client, config) captured
                 by each node instance at build time.
    """
    # ------------------------------------------------------------------
    # 1. Declare the graph with our TypedDict state schema
    # ------------------------------------------------------------------
    graph = StateGraph(PipelineState)

    # ------------------------------------------------------------------
    # 2. Instantiate node objects (context is captured in __init__)
    # ------------------------------------------------------------------
    profile_node = ProfileNode(context)
    retrieval_node = RetrievalNode(context)
    candidate_node = CandidateNode(context)
    evidence_node = EvidenceNode(context)
    scoring_node = ScoringNode(context)
    why_match_node = WhyMatchNode(context)

    # ------------------------------------------------------------------
    # 3. Register nodes
    #    Use default-argument capture (n=node) so each lambda closes over
    #    the correct instance rather than the last value in the loop.
    # ------------------------------------------------------------------

    # Stage 1: enrich the student profile via LLM, build the per-area query plan
    graph.add_node("enrich_profile", lambda s, n=profile_node: n.run(s))

    # Stage 2–3: expand keywords + fetch papers from OpenAlex (parallel per area)
    graph.add_node("retrieve_papers", lambda s, n=retrieval_node: n.run(s))

    # Stage 4–6: extract PI candidates from paper authorships, disambiguate,
    #            verify country via recent works
    graph.add_node("build_candidates", lambda s, n=candidate_node: n.run(s))

    # Stage 7: attach evidence (papers, grants, email, PhD programs) per candidate
    #          and annotate papers that match student use-cases (relevance_note)
    graph.add_node("attach_evidence", lambda s, n=evidence_node: n.run(s))

    # Stage 8: compute match_score, assign tier (reach/target/safety),
    #          balance across research areas, produce flat final_candidates list
    graph.add_node("score_and_balance", lambda s, n=scoring_node: n.run(s))

    # Stage 9: generate personalised why_match blurbs via LLM (parallel,
    #          rate-limited to llm_parallelism_limit concurrent calls)
    graph.add_node("generate_why_match", lambda s, n=why_match_node: n.run(s))

    # ------------------------------------------------------------------
    # 4. Wire edges — linear pipeline, each node feeds the next
    # ------------------------------------------------------------------

    graph.add_edge(START,                "enrich_profile")
    graph.add_edge("enrich_profile",     "retrieve_papers")
    graph.add_edge("retrieve_papers",    "build_candidates")
    graph.add_edge("build_candidates",   "attach_evidence")
    graph.add_edge("attach_evidence",    "score_and_balance")
    graph.add_edge("score_and_balance",  "generate_why_match")
    graph.add_edge("generate_why_match", END)

    # ------------------------------------------------------------------
    # 5. Compile — validates the graph and returns a Runnable
    # ------------------------------------------------------------------
    compiled = graph.compile()

    logger.info(
        "LangGraph pipeline compiled — %d nodes, linear DAG",
        6,  # enrich → retrieve → candidates → evidence → score → why_match
    )
    return compiled
