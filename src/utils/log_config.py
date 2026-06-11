"""Centralised logging configuration for the PhD Shortlist pipeline.

Call `setup_logging()` once at startup (in run.py).  Every module that uses
`logging.getLogger(__name__)` will automatically inherit these handlers.

Log layout (date-based directories, one per calendar day):

    logs/
    └── 2026-06-10/
        ├── pipeline.log   ← INFO+  (full trace of every run that day)
        └── errors.log     ← ERROR+ (fast error triage, no noise)

Format:
    2026-06-10 14:30:22 [INFO    ] agents.profile_agent          : ProfileAgent: …
    2026-06-10 14:30:23 [ERROR   ] subagents.grant_fetcher       : CORDIS failed …
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

_LOG_FMT = (
    "%(asctime)s [%(levelname)-8s] %(name)-40s : %(message)s"
)
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# Noisy third-party loggers to quiet down
_SILENT_LOGGERS = [
    "httpx",
    "httpcore",
    "urllib3",
    "requests",
    "tenacity",
    "langchain",
    "langchain_core",
    "langchain_groq",
    "litellm",
    "openai",
]


def setup_logging(
    log_root: str = "logs",
    level: int = logging.INFO,
) -> Path:
    """Configure the root logger and return today's log directory.

    Safe to call multiple times — existing handlers are replaced, not stacked.

    Args:
        log_root: Root directory for all log files (default: ``logs/``).
        level:    Minimum level for console + pipeline.log (default: INFO).

    Returns:
        ``Path`` to today's log directory, e.g. ``logs/2026-06-10``.
    """
    today = date.today().isoformat()          # e.g. "2026-06-10"
    log_dir = Path(log_root) / today
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_LOG_FMT, datefmt=_DATE_FMT)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)             # let handlers decide their own level
    root.handlers.clear()

    # ── Console ───────────────────────────────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # ── pipeline.log  (INFO+, appended across runs in the same day) ───────────
    pipeline_fh = logging.FileHandler(
        log_dir / "pipeline.log", mode="a", encoding="utf-8"
    )
    pipeline_fh.setLevel(logging.INFO)
    pipeline_fh.setFormatter(formatter)
    root.addHandler(pipeline_fh)

    # ── errors.log  (ERROR+ only, for fast triage) ────────────────────────────
    error_fh = logging.FileHandler(
        log_dir / "errors.log", mode="a", encoding="utf-8"
    )
    error_fh.setLevel(logging.ERROR)
    error_fh.setFormatter(formatter)
    root.addHandler(error_fh)

    # ── Silence chatty third-party libraries ──────────────────────────────────
    for name in _SILENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialised — writing to %s", log_dir
    )
    return log_dir
