"""OpenAlex HTTP client, shared LLM retry helper, and LiteLLM key-rotation pool."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils.cache import QueryCache

logger = logging.getLogger(__name__)


def _get_langfuse_callback() -> Any:
    """Return a Langfuse LangChain callback handler if credentials are available, else None.

    Requires LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY in the environment.
    If the langchain package is not installed, logs a warning and returns None.
    Run `uv add langchain` to enable tracing.
    """
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return None
    for import_path in ("langfuse.callback", "langfuse.langchain"):
        try:
            import importlib
            mod = importlib.import_module(import_path)
            handler = mod.CallbackHandler()
            logger.info("Langfuse tracing enabled via %s", import_path)
            return handler
        except ImportError:
            continue
        except Exception as exc:
            logger.warning("Langfuse init failed (%s): %s", import_path, exc)
            return None
    logger.warning(
        "Langfuse langchain integration unavailable — run `uv add langchain` to enable tracing"
    )
    return None


def _is_rate_limit(exc: Exception) -> bool:
    """Detect 429 / rate-limit errors from any LiteLLM-supported provider."""
    msg = str(exc).lower()
    return "rate_limit" in msg or "429" in msg or "ratelimiterror" in type(exc).__name__.lower()


class LLMClientPool:
    """Key pool that sticks with one key until it is rate-limited, then switches.

    On a 429 the pool rotates to the next key and immediately raises so the
    outer ``invoke_with_retry`` backoff fires.  This means only one key is
    consumed per invoke() call — the next retry will use the rotated-to key.

    Thread-safe: the rotation index is protected by a lock, safe for the
    ThreadPoolExecutor in why_match_agent.
    """

    def __init__(self, clients: list[Any]) -> None:
        if not clients:
            raise ValueError("LLMClientPool requires at least one client")
        self._clients = clients
        self._idx = 0
        self._lock = threading.Lock()
        logger.info("LLMClientPool: %d key(s) loaded", len(clients))

    @classmethod
    def from_env(cls, model: str, max_tokens: int, api_base: str | None = None) -> "LLMClientPool":
        """Build pool from LLM_API_KEYS / LLM_API_KEY (comma-separated).

        Falls back to GROQ_API_KEYS / GROQ_API_KEY for backward compatibility.
        The model string must use LiteLLM provider-prefix format, e.g.
        ``ollama/qwen3:0.5b``, ``groq/llama-3.3-70b-versatile``, ``openai/gpt-4o``.

        Local providers (e.g. Ollama) require no API key — a single keyless
        client is created when no key env var is set.
        """
        from langchain_groq import ChatGroq

        langfuse_cb = _get_langfuse_callback()
        callbacks = [langfuse_cb] if langfuse_cb else None

        raw = (
            os.environ.get("LLM_API_KEYS")
            or os.environ.get("LLM_API_KEY")
            or os.environ.get("GROQ_API_KEYS")
            or os.environ.get("GROQ_API_KEY", "")
        )
        keys = [k.strip() for k in raw.split(",") if k.strip()]

        base_kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens}
        if callbacks:
            base_kwargs["callbacks"] = callbacks

        if keys:
            clients = [ChatGroq(**base_kwargs, groq_api_key=k) for k in keys]
        else:
            logger.warning("LLMClientPool: no Groq API key found — set GROQ_API_KEY in .env")
            clients = [ChatGroq(**base_kwargs)]

        return cls(clients)

    def with_structured_output(self, schema: Any) -> "LLMClientPool":
        """Return a new pool where every client is bound to the given Pydantic schema."""
        structured_clients = [c.with_structured_output(schema) for c in self._clients]
        pool = LLMClientPool.__new__(LLMClientPool)
        pool._clients = structured_clients
        pool._idx = self._idx
        pool._lock = self._lock   # share the same lock so rotation stays in sync
        return pool

    def _rotate(self) -> None:
        with self._lock:
            self._idx += 1

    def invoke(self, messages: list) -> Any:
        """Invoke using the current key. On rate-limit, rotate and raise immediately.

        The outer invoke_with_retry loop handles the sleep + retry.  The next
        retry will use whichever key the pool rotated to, so keys are consumed
        one at a time rather than all at once in a burst.
        """
        with self._lock:
            idx = self._idx % len(self._clients)
        client = self._clients[idx]
        try:
            return client.invoke(messages)
        except Exception as exc:
            if _is_rate_limit(exc):
                self._rotate()
                logger.warning(
                    "LLMClientPool: key[%d] rate-limited — rotated to key[%d]",
                    idx,
                    self._idx % len(self._clients),
                )
            raise


# Backward-compatible alias
GroqClientPool = LLMClientPool


def _parse_retry_after(exc: Exception) -> float | None:
    """Extract the retry-after wait time from a Groq 429 response.

    Checks (in order):
      1. Retry-After HTTP header on the response object
      2. 'try again in Xs / Xms' in the exception body dict
      3. Same pattern in str(exc) as a last resort
    """
    # 1. HTTP Retry-After header (most reliable)
    response = getattr(exc, "response", None)
    if response is not None:
        header = getattr(response, "headers", {}).get("retry-after") or \
                 getattr(response, "headers", {}).get("x-ratelimit-reset-requests")
        if header:
            try:
                return float(header)
            except (ValueError, TypeError):
                pass

    # 2. Error body dict (Groq wraps the message here)
    body = getattr(exc, "body", None)
    text = str(body) if body else str(exc)

    match = re.search(r"try again in (\d+(?:\.\d+)?)(ms|s)", text, re.IGNORECASE)
    if not match:
        return None
    value, unit = float(match.group(1)), match.group(2).lower()
    return value / 1000.0 if unit == "ms" else value


def invoke_with_retry(
    llm_client: Any,
    messages: list,
    *,
    max_attempts: int = 5,
) -> Any:
    """Call llm_client.invoke() with precise rate-limit-aware retry.

    When the error message contains 'try again in Xs / Xms' (Groq / LiteLLM
    providers that embed retry-after hints) we parse and sleep that exact
    duration + 0.15 s buffer.  Otherwise falls back to 2 s exponential backoff.
    Raises the last exception if all attempts are exhausted.
    """
    fallback_wait = 2.0
    last_exc: Exception | None = None

    for attempt in range(max_attempts):
        try:
            return llm_client.invoke(messages)
        except Exception as exc:
            last_exc = exc
            if _is_rate_limit(exc):
                retry_after = _parse_retry_after(exc)
                if retry_after is not None:
                    sleep_for = retry_after + 0.15
                    logger.warning(
                        "invoke_with_retry: rate-limited (attempt %d/%d) — "
                        "sleeping %.2fs (retry-after + buffer)",
                        attempt + 1, max_attempts, sleep_for,
                    )
                else:
                    sleep_for = min(fallback_wait, 30.0)
                    fallback_wait *= 2
                    logger.warning(
                        "invoke_with_retry: rate-limited (attempt %d/%d) — "
                        "sleeping %.2fs (fallback backoff)",
                        attempt + 1, max_attempts, sleep_for,
                    )
                time.sleep(sleep_for)
            else:
                # Non-rate-limit error: short fixed backoff then propagate on last attempt
                if attempt < max_attempts - 1:
                    time.sleep(2.0)
                else:
                    raise

    raise last_exc  # type: ignore[misc]


class OpenAlexClient:
    BASE = "https://api.openalex.org"

    def __init__(self, mailto: str, cache: QueryCache, timeout: int = 20) -> None:
        self._mailto = mailto
        self._cache = cache
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": f"phd-shortlist/1.0 (mailto:{mailto})"}
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        cached = self._cache.get(path, params)
        if cached is not None:
            return cached

        url = f"{self.BASE}{path}"
        params = {**params, "mailto": self._mailto}
        resp = self._session.get(url, params=params, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        self._cache.set(path, params, data)
        return data

    def search_works(
        self,
        query: str,
        countries: list[str],
        year_floor: int,
        min_citations: int = 0,
        per_page: int = 200,
        max_pages: int = 2,
        topic_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not countries:
            logger.warning("search_works: empty countries list — returning no results")
            return []
        country_codes = "|".join(countries)
        filters = (
            f"institutions.country_code:{country_codes},"
            f"publication_year:>{year_floor - 1},"
            f"cited_by_count:>{min_citations - 1},"
            f"type:article|preprint"
        )
        if topic_ids:
            filters += ",topics.id:" + "|".join(topic_ids)

        all_works: list[dict[str, Any]] = []
        cursor = "*"
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "search": query,
                "filter": filters,
                "sort": "cited_by_count:desc",
                "per-page": per_page,
                "cursor": cursor,
                "select": (
                    "id,doi,title,publication_year,cited_by_count,"
                    "authorships,primary_location,concepts,topics"
                ),
            }
            data = self._get("/works", params)
            results = data.get("results", [])
            all_works.extend(results)

            meta = data.get("meta", {})
            cursor = meta.get("next_cursor")
            if not cursor or len(results) < per_page:
                break
            time.sleep(0.1)

        logger.info(
            "OpenAlex returned %d works for query=%r countries=%s",
            len(all_works),
            query,
            countries,
        )
        return all_works

    def get_author(self, openalex_author_id: str) -> Optional[dict[str, Any]]:
        path = f"/authors/{openalex_author_id.split('/')[-1]}"
        try:
            return self._get(path, {})
        except Exception as exc:
            logger.warning("Author fetch failed %s: %s", openalex_author_id, exc)
            return None

    def search_authors(self, query: str) -> list[dict[str, Any]]:
        try:
            data = self._get("/authors", {"search": query, "per-page": 10})
            return data.get("results", [])
        except Exception as exc:
            logger.warning("Author search failed for %r: %s", query, exc)
            return []

    def get_author_recent_works(
        self, openalex_author_id: str, recency_floor: int
    ) -> list[dict[str, Any]]:
        raw_id = openalex_author_id.split("/")[-1]
        try:
            data = self._get(
                "/works",
                {
                    "filter": (
                        f"authorships.author.id:{raw_id},"
                        f"publication_year:>{recency_floor - 1}"
                    ),
                    "per-page": 20,
                    "select": "authorships",
                },
            )
            return data.get("results", [])
        except Exception as exc:
            logger.warning(
                "Recent works fetch failed for %s: %s", openalex_author_id, exc
            )
            return []
