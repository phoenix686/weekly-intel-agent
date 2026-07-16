"""
One-time manual bootstrap: computes the initial taste_topic_vectors from
the current data/taste_profile.yaml, so discovery/taste_vectors.py's
pre-filter has something to compare against from day one, rather than
being permanently permissive until the first real Sunday consolidated
rewrite recomputes them via sunday/nodes/update_profile.py.

Run: uv run --env-file .env python scripts/bootstrap_topic_vectors.py
"""
from dotenv import load_dotenv
load_dotenv()

from logging_config import setup_logging
setup_logging()

from pathlib import Path

from discovery.taste_vectors import recompute_topic_vectors

TASTE_PROFILE_PATH = Path("data/taste_profile.yaml")

profile_text = TASTE_PROFILE_PATH.read_text(encoding="utf-8") if TASTE_PROFILE_PATH.exists() else ""
costs = recompute_topic_vectors(profile_text)

ok = sum(1 for c in costs if not c.get("error"))
print(f"Bootstrapped {ok}/{len(costs)} topic vectors from {TASTE_PROFILE_PATH}")
for c in costs:
    if c.get("error"):
        print(f"  FAILED: {c['error']}")
