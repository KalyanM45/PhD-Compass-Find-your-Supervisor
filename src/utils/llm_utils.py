"""Reliable structured output for small/local LLMs.

with_structured_output() breaks on models like qwen3.5 that:
  - emit <think>...</think> reasoning blocks before the JSON
  - wrap JSON in markdown fences
  - include prose before/after the object

call_structured() handles all of those cases by calling the LLM directly
and extracting the JSON from the raw text response.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Type, TypeVar

from src.utils.clients import invoke_with_retry

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks emitted by Qwen3 and similar reasoning models.

    Also handles unclosed blocks (truncated responses where </think> never arrives).
    """
    # Complete blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Unclosed block — strip from <think> to end of string
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL)
    return text.strip()


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from an LLM response."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    obj = re.search(r"\{.*\}", text, re.DOTALL)
    if obj:
        return json.loads(obj.group(0))
    raise ValueError(f"no JSON object found: {text[:300]!r}")


def call_structured(llm_client: Any, messages: list, schema: Type[T]) -> T:
    """Invoke the LLM and parse the response as *schema* (a Pydantic BaseModel).

    Works with small local models (Ollama, qwen3.5, etc.) that don't reliably
    support JSON-schema mode.  Falls back to a default instance if parsing fails
    so the pipeline can continue rather than crash.
    """
    raw = invoke_with_retry(llm_client, messages)
    text = raw.content if hasattr(raw, "content") else str(raw)
    text = _strip_thinking(text)
    try:
        data = _extract_json(text)
        return schema.model_validate(data)
    except Exception as exc:
        logger.warning(
            "call_structured: JSON parse failed (%s) — returning defaults. output: %r",
            exc,
            text[:300],
        )
        try:
            return schema()
        except Exception:
            # Schema has required fields with no defaults — build with model_construct
            # so Pydantic skips validation (caller must handle missing fields gracefully)
            return schema.model_construct()
