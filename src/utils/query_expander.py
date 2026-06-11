"""Subagent: expand a single query hint with synonym variants."""
from __future__ import annotations

_SYNONYMS: dict[str, list[str]] = {
    "graph neural network": ["GNN", "message passing neural network", "MPNN"],
    "geometric deep learning": ["equivariant neural network", "geometric ML"],
    "drug discovery": ["molecular property prediction", "computational drug design"],
    "natural language processing": ["NLP", "text mining", "language model"],
    "computer vision": ["image recognition", "visual representation learning"],
    "reinforcement learning": ["RL", "policy gradient", "reward maximisation"],
}


def expand_query(query_hint: str) -> list[str]:
    """Return the original query plus any known synonym expansions (deduped)."""
    variants = [query_hint]
    lower = query_hint.lower()
    for canonical, syns in _SYNONYMS.items():
        if canonical in lower:
            variants.extend(syns)
            break
    return list(dict.fromkeys(variants))
