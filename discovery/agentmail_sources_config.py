"""
Loads discovery/config/agentmail_sources.yaml -- the real sender-address-
to-source-name mapping for the shared AgentMail inbox. Gitignored (real
personal subscription data, same category as .env); see
discovery/config/agentmail_sources.yaml.example for the tracked
placeholder shape. Deliberately kept out of blog_sources.yaml entirely,
not just out of git -- discovery/nodes/scrape_blogs.py calls this
loader directly, alongside (not through) the blog_sources.yaml-driven
per-entry loop, since one shared inbox covering many senders doesn't fit
that file's one-entry-per-fetch model.

No langgraph imports, no I/O side effects beyond reading this one file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "config" / "agentmail_sources.yaml"


def load_agentmail_config() -> dict:
    """Returns {"inbox_id": str, "sources": [{name, sender, bucket}, ...]}.

    Raises FileNotFoundError with a clear message if the gitignored real
    config doesn't exist yet (e.g. a fresh clone, or before Pooja has
    copied agentmail_sources.yaml.example into place) -- callers should
    treat that as "AgentMail integration not configured on this machine
    yet", not crash the whole pipeline; see
    discovery/nodes/scrape_blogs.py's handling."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"{CONFIG_PATH} not found -- copy agentmail_sources.yaml.example to "
            "agentmail_sources.yaml and fill in the real inbox_id/sender addresses."
        )
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def sender_to_name_map() -> dict[str, str]:
    """{sender_address: source_name} -- used to attribute a fetched
    RawItem to the real publication it came from, since one shared inbox
    receives mail from all configured senders at once."""
    config = load_agentmail_config()
    return {source["sender"]: source["name"] for source in config.get("sources", [])}
