from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


def load(name: str) -> str:
    """Return the raw template string for the given prompt name (without .txt extension)."""
    return (_PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
