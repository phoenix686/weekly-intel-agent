"""
Groq (openai/gpt-oss-120b) vs. Anthropic (claude-haiku-4-5) comparison
harness -- COMPARISON ONLY, does not swap any production node's model.

Pulls the exact real historical inputs AND real Haiku outputs for the
three highest-stakes nodes (score_node, correlate_trello, classify_item)
directly from the real LangGraph Postgres checkpoint of the most recent
real Saturday run (thread_id == run_id, per scripts/run_saturday.py) --
not reconstructed from scattered log namespaces, the actual final graph
state as it was persisted. Reuses each node's REAL prompt-building code
(imported directly from discovery/nodes/score.py, saturday/nodes/
correlate_trello.py, saturday/nodes/classify_item.py) so the only real
variable being tested is the model + parsing mechanism -- everything
else (persona, rules, formatting) is byte-identical to production.

Groq side uses native structured outputs (response_format={"type":
"json_schema", "json_schema": {"strict": True, "schema": {...}}}) --
deliberately NOT the markdown-fence-stripping _parse_json_response()
workaround built for Claude's free-form JSON responses. Groq's own
structured-outputs contract requires the root schema to be a JSON
object, not a bare array (confirmed via Groq's docs) -- every schema
here wraps its real array of results in a top-level {"results": [...]}
object accordingly.

Real Groq pricing for openai/gpt-oss-120b (https://groq.com/pricing,
checked 2026-07-26): $0.15 / 1M input tokens, $0.60 / 1M output tokens.

Requires GROQ_API_KEY in .env (real key needed -- there is no way to
produce real comparison numbers without one; this script does not
guess/estimate costs or fabricate results).

Run: uv run --env-file .env python scripts/compare_groq_harness.py [run_id]
(run_id defaults to the most recent real Saturday run known at the time
this was written: 08b5d13b-8d7b-4b7b-94ab-4576c6cd0906)
"""
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import os
from groq import Groq

from core.checkpointer_config import get_checkpointer
from discovery.nodes.score import TASTE_PROFILE, ALLOWED_TAGS
from saturday.nodes.correlate_trello import (
    CORRELATE_PROMPT, _format_cards as _correlate_format_cards, _format_items as _correlate_format_items,
)
from saturday.nodes.classify_item import (
    CLASSIFY_PROMPT, _format_cards as _classify_format_cards, _format_items as _classify_format_items,
)

DEFAULT_RUN_ID = "08b5d13b-8d7b-4b7b-94ab-4576c6cd0906"
GROQ_MODEL = "openai/gpt-oss-120b"

# https://groq.com/pricing, checked 2026-07-26 -- real published rate,
# not estimated.
GROQ_COST_PER_INPUT_TOKEN = 0.15 / 1_000_000
GROQ_COST_PER_OUTPUT_TOKEN = 0.60 / 1_000_000

groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])


def _fetch_real_run_state(run_id: str) -> dict:
    """Pulls the real, historical final graph state for a completed
    Saturday run directly from the Postgres checkpointer -- thread_id ==
    run_id (see scripts/run_saturday.py). This is the actual persisted
    state, not a reconstruction from scattered observability namespaces."""
    checkpointer = get_checkpointer()
    config = {"configurable": {"thread_id": run_id}}
    tup = checkpointer.get_tuple(config)
    if tup is None:
        raise RuntimeError(f"No checkpoint found for run_id={run_id!r} -- was a real Saturday run ever completed with this run_id?")
    return tup.checkpoint["channel_values"]


def _real_haiku_cost(state: dict, node_name: str) -> dict:
    for c in state.get("costs", []):
        if c["node_name"] == node_name:
            return c
    raise RuntimeError(f"No real NodeCost record found for {node_name!r} in this run's checkpoint")


def _call_groq_structured(prompt: str, schema_name: str, json_schema: dict) -> tuple[dict, int, int, float]:
    t0 = time.perf_counter()
    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,  # pinned to isolate determinism from genuine model disagreement
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
        },
    )
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    parsed = json.loads(response.choices[0].message.content)
    return parsed, response.usage.prompt_tokens, response.usage.completion_tokens, latency_ms


def _groq_cost(input_tokens: int, output_tokens: int) -> float:
    return round(input_tokens * GROQ_COST_PER_INPUT_TOKEN + output_tokens * GROQ_COST_PER_OUTPUT_TOKEN, 6)


def _print_cost_table(node_name: str, haiku_cost: dict, groq_input: int, groq_output: int, groq_latency_ms: float) -> None:
    groq_cost = _groq_cost(groq_input, groq_output)
    print(f"\n--- {node_name}: cost/latency (real numbers) ---")
    print(f"  Haiku : in={haiku_cost['input_tokens']:>5} out={haiku_cost['output_tokens']:>4}  "
          f"cost=${haiku_cost['cost_usd']:.6f}  latency={haiku_cost['latency_ms']:.1f}ms")
    print(f"  Groq  : in={groq_input:>5} out={groq_output:>4}  "
          f"cost=${groq_cost:.6f}  latency={groq_latency_ms:.1f}ms")
    cost_ratio = haiku_cost["cost_usd"] / groq_cost if groq_cost else float("inf")
    latency_delta = groq_latency_ms - haiku_cost["latency_ms"]
    print(f"  Haiku is {cost_ratio:.2f}x the cost of Groq" if cost_ratio >= 1
          else f"  Groq is {1/cost_ratio:.2f}x the cost of Haiku")
    print(f"  Groq latency is {'+' if latency_delta >= 0 else ''}{latency_delta:.1f}ms vs. Haiku")


# ── score_node comparison ───────────────────────────────────────────────────

_SCORE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "keep": {"type": "boolean"},
                    "reasoning": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(ALLOWED_TAGS)},
                    },
                },
                "required": ["index", "keep", "reasoning", "tags"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


def compare_score_node(state: dict) -> None:
    items = state["scored_items"]  # real ClusteredItem+ScoredItem fields both present
    items_text = "\n\n".join(
        f"[{i}] URL: {item['url']}\nTitle: {item['title']}\nText: {item['text'][:500]}"
        for i, item in enumerate(items)
    )
    prompt = f"""{TASTE_PROFILE}

Assign 1-3 tags from EXACTLY this list — no other tags are permitted:
agentic-engineering, memory-systems, llm-tooling, evals, learning-resource,
distributed-systems, new-tool-launch, noise, course

Score each bookmark below. Return one object per item, in the same order,
with: index (the item's index number), keep (true/false), reasoning (one
sentence explaining why this should rank higher or lower than other kept
items today — reference urgency, depth, or how directly it applies to
active work; do not just describe what the item covers), tags (1-3 tags
from the permitted list above).

Bookmarks to score:
{items_text}"""

    parsed, in_tok, out_tok, latency_ms = _call_groq_structured(prompt, "score_batch", _SCORE_JSON_SCHEMA)
    groq_by_index = {r["index"]: r for r in parsed["results"]}

    print("\n" + "=" * 70)
    print(f"SCORE_NODE comparison -- {len(items)} real item(s) from this run")
    print("=" * 70)
    agree = 0
    for i, item in enumerate(items):
        haiku_keep, haiku_tags = item["keep"], sorted(item["tags"])
        g = groq_by_index.get(i, {})
        groq_keep, groq_tags = g.get("keep"), sorted(g.get("tags", []))
        match = (haiku_keep == groq_keep) and (haiku_tags == groq_tags)
        agree += match
        marker = "MATCH" if match else "DISAGREE"
        print(f"\n[{marker}] {item['title'][:70]}")
        print(f"  URL: {item['url']}")
        print(f"  Haiku: keep={haiku_keep} tags={haiku_tags}")
        print(f"         reasoning: {item['reasoning']}")
        print(f"  Groq : keep={groq_keep} tags={groq_tags}")
        print(f"         reasoning: {g.get('reasoning')}")
    print(f"\nAgreement (keep + tags both match): {agree}/{len(items)} ({100*agree/len(items):.0f}%)")

    haiku_cost = _real_haiku_cost(state, "score_node")
    _print_cost_table("score_node", haiku_cost, in_tok, out_tok, latency_ms)


# ── correlate_trello comparison ──────────────────────────────────────────────

_CORRELATE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "matched_card_id": {"type": ["string", "null"]},
                    "match_reasoning": {"type": "string"},
                },
                "required": ["item_id", "matched_card_id", "match_reasoning"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


def compare_correlate_trello(state: dict) -> None:
    # Real input: the kept scored_items (what correlate_trello actually
    # receives), not correlated_items (that's its own output).
    kept_items = [i for i in state["scored_items"] if i["keep"]]
    cards = state["trello_cards"]

    prompt = CORRELATE_PROMPT.format(
        cards_block=_correlate_format_cards(cards),
        items_block=_correlate_format_items(kept_items),
    )

    parsed, in_tok, out_tok, latency_ms = _call_groq_structured(prompt, "correlate_trello", _CORRELATE_JSON_SCHEMA)
    groq_by_id = {r["item_id"]: r for r in parsed["results"]}

    print("\n" + "=" * 70)
    print(f"CORRELATE_TRELLO comparison -- {len(kept_items)} real item(s), {len(cards)} real card(s)")
    print("=" * 70)
    agree = 0
    for item in state["correlated_items"]:  # real Haiku output for this run
        haiku_match = item["matched_card_id"]
        g = groq_by_id.get(item["url"], {})
        groq_match = g.get("matched_card_id")
        match = haiku_match == groq_match
        agree += match
        marker = "MATCH" if match else "DISAGREE"
        print(f"\n[{marker}] {item['title'][:70]}")
        print(f"  URL: {item['url']}")
        print(f"  Haiku: matched_card_id={haiku_match}")
        print(f"  Groq : matched_card_id={groq_match}  reasoning: {g.get('match_reasoning')}")
    print(f"\nAgreement (matched_card_id): {agree}/{len(state['correlated_items'])} ({100*agree/len(state['correlated_items']):.0f}%)")

    haiku_cost = _real_haiku_cost(state, "correlate_trello")
    _print_cost_table("correlate_trello", haiku_cost, in_tok, out_tok, latency_ms)


# ── classify_item comparison ─────────────────────────────────────────────────

_CLASSIFY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "classification": {"type": "string", "enum": ["plan_item", "project_proposal"]},
                    "proposal_type": {"type": ["string", "null"], "enum": ["extend", "new", None]},
                    "classification_reasoning": {"type": "string"},
                },
                "required": ["item_id", "classification", "proposal_type", "classification_reasoning"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


def compare_classify_item(state: dict) -> None:
    # Real input: correlate_trello's real output for this run (already
    # carries matched_card_id).
    correlated_items = state["correlated_items"]
    cards = state["trello_cards"]

    prompt = CLASSIFY_PROMPT.format(
        cards_block=_classify_format_cards(cards),
        items_block=_classify_format_items(correlated_items),
    )
    # Real finding (first attempt, unmodified prompt): gpt-oss-120b
    # returned a bare JSON array here -- matching the production prompt's
    # own literal "Return ONLY a JSON array..." instruction -- which
    # failed schema validation against the required {"results": [...]}
    # object wrapper even under strict:true. Same substitution already
    # applied to score_node's prompt in this harness; correlate_trello's
    # identical phrasing happened not to trigger this on its one real
    # run, so left as-is there (already-captured real result, not
    # retried). Swapping only the trailing instruction block -- the
    # substantive task/persona/rules text above is untouched.
    prompt = prompt.replace(
        'Return ONLY a JSON array, one object per item, in this exact shape:\n'
        '[\n'
        '  {"item_id": "...", "classification": "plan_item" or "project_proposal", "proposal_type": "extend" or "new" or null, "classification_reasoning": "brief reason"}\n'
        ']',
        'Return one object per item, with: item_id, classification ("plan_item" or '
        '"project_proposal"), proposal_type ("extend" or "new" or null), '
        'classification_reasoning (brief reason).'
    )

    parsed, in_tok, out_tok, latency_ms = _call_groq_structured(prompt, "classify_item", _CLASSIFY_JSON_SCHEMA)
    groq_by_id = {r["item_id"]: r for r in parsed["results"]}

    print("\n" + "=" * 70)
    print(f"CLASSIFY_ITEM comparison -- {len(correlated_items)} real item(s)")
    print("=" * 70)
    agree = 0
    for item in state["classified_items"]:  # real Haiku output for this run
        haiku_class = (item["classification"], item["proposal_type"])
        g = groq_by_id.get(item["url"], {})
        groq_class = (g.get("classification"), g.get("proposal_type"))
        match = haiku_class == groq_class
        agree += match
        marker = "MATCH" if match else "DISAGREE"
        print(f"\n[{marker}] {item['title'][:70]}")
        print(f"  URL: {item['url']}")
        print(f"  Haiku: classification={item['classification']} proposal_type={item['proposal_type']}")
        print(f"         reasoning: {item['classification_reasoning']}")
        print(f"  Groq : classification={g.get('classification')} proposal_type={g.get('proposal_type')}")
        print(f"         reasoning: {g.get('classification_reasoning')}")
    print(f"\nAgreement (classification + proposal_type): {agree}/{len(state['classified_items'])} ({100*agree/len(state['classified_items']):.0f}%)")

    haiku_cost = _real_haiku_cost(state, "classify_item")
    _print_cost_table("classify_item", haiku_cost, in_tok, out_tok, latency_ms)


def main() -> None:
    run_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN_ID
    print(f"Pulling real historical state for run_id={run_id} from the Postgres checkpointer...")
    state = _fetch_real_run_state(run_id)
    print(f"Real data loaded: {len(state['scored_items'])} scored_items, "
          f"{len(state['trello_cards'])} trello_cards, "
          f"{len(state['correlated_items'])} correlated_items, "
          f"{len(state['classified_items'])} classified_items")

    compare_score_node(state)
    compare_correlate_trello(state)
    compare_classify_item(state)


if __name__ == "__main__":
    main()
