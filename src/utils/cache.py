from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional


class QueryCache:
    def __init__(self, cache_dir: str = ".cache", ttl_seconds: int = 86400) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds

    def _key(self, endpoint: str, params: dict[str, Any]) -> str:
        raw = json.dumps({"endpoint": endpoint, "params": params}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def get(self, endpoint: str, params: dict[str, Any]) -> Optional[Any]:
        p = self._path(self._key(endpoint, params))
        if not p.exists():
            return None
        entry = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - entry["ts"] > self._ttl:
            p.unlink(missing_ok=True)
            return None
        return entry["data"]

    def set(self, endpoint: str, params: dict[str, Any], data: Any) -> None:
        p = self._path(self._key(endpoint, params))
        p.write_text(
            json.dumps({"ts": time.time(), "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )

    def clear_expired(self) -> int:
        removed = 0
        for p in self._dir.glob("*.json"):
            try:
                entry = json.loads(p.read_text(encoding="utf-8"))
                if time.time() - entry["ts"] > self._ttl:
                    p.unlink()
                    removed += 1
            except Exception:
                pass
        return removed
