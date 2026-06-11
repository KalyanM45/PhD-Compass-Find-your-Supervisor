from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

# Logging must be configured before any application imports so all loggers
# inherit the handlers from the start.
from src.utils.log_config import setup_logging

from src.orchestrator import Orchestrator
from src.graph.state import PipelineContext
from src.utils.cache import QueryCache
from src.utils.clients import LLMClientPool, OpenAlexClient
from src.utils.schema import StudentProfile

logger = logging.getLogger(__name__)


def load_config(path: str = "config/config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_profile(path: str) -> StudentProfile:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return StudentProfile(**data)


def main() -> None:
    parser = argparse.ArgumentParser(description="PhD Shortlist Builder")
    parser.add_argument("--profile", required=True, help="Path to student profile JSON")
    parser.add_argument("--out", required=True, help="Path for output shortlist JSON")
    parser.add_argument("--config", default="config/config.yaml", help="Config file")
    parser.add_argument(
        "--log-dir", default="logs", help="Root directory for log files (default: logs/)"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Set console log level to DEBUG"
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_dir = setup_logging(log_root=args.log_dir, level=log_level)
    logger.info("Log directory: %s", log_dir)

    start_time = time.time()
    cfg = load_config(args.config)
    profile = load_profile(args.profile)

    logger.info("Processing student: %s", profile.student_id)

    cache = QueryCache(
        cache_dir=cfg["cache"]["dir"],
        ttl_seconds=cfg["cache"]["ttl_seconds"],
    )
    openalex = OpenAlexClient(
        mailto=cfg["openalex"]["mailto"],
        cache=cache,
        timeout=cfg["timeouts"]["api_request_seconds"],
    )
    llm_client = LLMClientPool.from_env(
        model=cfg["llm"]["model"],
        max_tokens=cfg["llm"]["max_tokens"],
        api_base=cfg["llm"].get("api_base"),
    )

    context = PipelineContext(
        config=cfg,
        llm_client=llm_client,
        openalex=openalex,
        llm_model=cfg["llm"]["model"],
    )

    orchestrator = Orchestrator(context)
    shortlist = orchestrator.run(profile, start_time)

    out_path = Path(args.out)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = out_path.with_stem(f"{out_path.stem}_{timestamp}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(shortlist.model_dump_json(indent=2), encoding="utf-8")

    logger.info(
        "Done — %d recommendations written to %s in %.1fs",
        shortlist.run_metadata.total_recommendations,
        out_path,
        shortlist.run_metadata.wall_clock_seconds,
    )

    # Flush Langfuse async trace queue before process exits
    try:
        from langfuse import Langfuse
        Langfuse().flush()
    except Exception:
        pass


if __name__ == "__main__":
    main()
