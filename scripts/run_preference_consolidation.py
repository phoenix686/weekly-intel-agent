from dotenv import load_dotenv

import anthropic

from core.preferences import consolidate_weekly_locked, render_preference_context
from core.tracing import validate_tracing
from discovery.taste_vectors import recompute_topic_vectors
from saturday.memory_store_config import get_store

load_dotenv()
validate_tracing()
snapshot, usage = consolidate_weekly_locked(get_store(), anthropic.Anthropic())
if usage:
    recompute_topic_vectors(render_preference_context(snapshot))
    print(f"Preference snapshot v{snapshot['version']} consolidated")
else:
    print("Fewer than five new confirmed feedback events; snapshot unchanged")
