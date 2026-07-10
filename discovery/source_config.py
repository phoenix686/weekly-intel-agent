"""
Persisted, editable source-list config for discovered sources (Part C
step 5) -- a plain JSON file, not hardcoded Python, so new sources can be
added without a code change. Read by discovery/nodes/discovered_sources.py
at runtime.

No langgraph imports, no LLM calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

SOURCES_PATH = Path("data/sources.json")

_EMPTY: dict = {"daily": [], "sunday": []}


def load_sources() -> dict:
    if not SOURCES_PATH.exists():
        return {"daily": [], "sunday": []}
    return json.loads(SOURCES_PATH.read_text(encoding="utf-8"))


def add_source(bucket: str, name: str, feed_url: str) -> None:
    if bucket not in ("daily", "sunday"):
        raise ValueError(f"bucket must be 'daily' or 'sunday', got {bucket!r}")

    sources = load_sources()
    if any(s["feed_url"] == feed_url for s in sources[bucket]):
        return  # already present, no-op

    sources[bucket].append({
        "name": name,
        "feed_url": feed_url,
        "added_at": datetime.now(timezone.utc).isoformat(),
    })
    SOURCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOURCES_PATH.write_text(json.dumps(sources, indent=2), encoding="utf-8")
