from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from src.utils.clients import invoke_with_retry
from src.utils.llm_utils import call_structured
from src.utils.prompts import load

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "openalex_data"

_DOMAINS: list[dict] = []
_FIELDS: list[dict] = []
_SUBFIELDS: list[dict] = []
_TOPICS: list[dict] = []


def _load() -> None:
    global _DOMAINS, _FIELDS, _SUBFIELDS, _TOPICS
    if _DOMAINS:
        return
    _DOMAINS = json.loads((_DATA_DIR / "domains.json").read_text(encoding="utf-8"))
    _FIELDS = json.loads((_DATA_DIR / "fields.json").read_text(encoding="utf-8"))
    _SUBFIELDS = json.loads((_DATA_DIR / "subfields.json").read_text(encoding="utf-8"))
    _TOPICS = json.loads((_DATA_DIR / "topics.json").read_text(encoding="utf-8"))
    logger.info(
        "topic_selector: loaded %d domains / %d fields / %d subfields / %d topics",
        len(_DOMAINS), len(_FIELDS), len(_SUBFIELDS), len(_TOPICS),
    )


class _SelectedIds(BaseModel):
    selected_ids: list[str] = Field(default_factory=list)


def _llm_pick(
    llm_client: Any,
    interests: list[str],
    candidates: list[dict],
    id_key: str,
    name_key: str,
    level: str,
) -> list[str]:
    """Show the candidate list to the LLM and return the selected IDs."""
    lines = []
    for c in candidates:
        line = f"  {c[id_key]}: {c[name_key]}"
        if "description" in c and c["description"]:
            line += f" — {c['description'][:150]}"
        lines.append(line)
    candidates_text = "\n".join(lines)

    prompt = load("topic_selector").format(
        level=level,
        interests_json=json.dumps(interests, indent=2),
        candidates_text=candidates_text,
    )

    result = call_structured(llm_client, [HumanMessage(content=prompt)], _SelectedIds)
    return result.selected_ids


def select_topics(interests: list[str], llm_client: Any) -> list[str]:
    """Return OpenAlex topic IDs relevant to the student's research interests.

    Args:
        interests: Combined list of stated + revealed interests from profile enrichment.
        llm_client: GroqClientPool instance (must support .with_structured_output()).

    Returns:
        List of topic IDs like ["T11273", "T10211", ...] ready for use as
        OpenAlex filter: topics.id:T11273|T10211|...
    """
    _load()

    # Step 1 — pick domains (list is tiny ~4 items, always show all)
    domain_ids = _llm_pick(llm_client, interests, _DOMAINS, "domain_id", "domain_name", "domain")
    logger.info("topic_selector step 1 — domains: %s", domain_ids)
    if not domain_ids:
        logger.warning("topic_selector: no domains selected — skipping topic filter")
        return []

    # Step 2 — pick fields within selected domains
    fields_in_scope = [f for f in _FIELDS if f["domain_id"] in domain_ids]
    field_ids = _llm_pick(llm_client, interests, fields_in_scope, "field_id", "field_name", "field")
    logger.info("topic_selector step 2 — fields: %s", field_ids)
    if not field_ids:
        return []

    # Step 3 — pick subfields within selected fields
    subfields_in_scope = [s for s in _SUBFIELDS if s["field_id"] in field_ids]
    subfield_ids = _llm_pick(llm_client, interests, subfields_in_scope, "subfield_id", "subfield_name", "subfield")
    logger.info("topic_selector step 3 — subfields: %s", subfield_ids)
    if not subfield_ids:
        return []

    # Step 4 — pick specific topics within selected subfields
    topics_in_scope = [t for t in _TOPICS if t["subfield_id"] in subfield_ids]
    topic_ids = _llm_pick(llm_client, interests, topics_in_scope, "topic_id", "topic_name", "topic")
    logger.info("topic_selector step 4 — %d topics selected", len(topic_ids))

    return topic_ids
