# saturday/nodes/correlate_trello.py
import time
import json
import logging
import anthropic
import groq

from core.state import SaturdayGraphState, NodeCost
from core.observability import record_node_summary
from core.groq_client import get_groq_client, GROQ_MODEL, groq_cost

logger = logging.getLogger(__name__)

# Kept intact but unused -- rollback safety net for the 2026-07-26 Groq
# swap (see core/groq_client.py's docstring). ANTHROPIC_API_KEY stays in
# .env.example for the same reason. Not called anywhere; _correlate_trello_anthropic_legacy
# below is the dead code path that used to call this.
client = anthropic.Anthropic()

CORRELATE_PROMPT = """You are matching newly scored items against existing Trello cards to decide if each item directly relates to work already tracked.

A match means: reading or acting on this item would directly help you make progress on the SPECIFIC task the card represents — not because they share vocabulary or are in the same broad domain, but because the item is genuinely about that card's particular project or task.

Reasons to return null rather than a match:
- The card name is vague, short, or stream-of-consciousness (e.g. "some thoughts on X thing"): if you cannot clearly identify the specific task the card represents, require much stronger and more explicit evidence before matching anything to it
- The item is a general explainer or tutorial on a topic the card happens to mention
- Multiple items all seem to match the same card — if one vague card is attracting several different items, that is a signal the card name is too broad to be a reliable match target; reconsider all of them
- The connection is "this technology area overlaps" rather than "this item is about this specific project"

Error asymmetry: a missed match (item appears in Reading & Learning instead of Existing Project Work) is a minor inconvenience. An incorrect match (item wrongly attributed to a card it doesn't actually relate to) corrupts the weekly plan. When in doubt, return null.

Existing Trello cards:
{cards_block}

Items to match:
{items_block}

Return ONLY a JSON array, one object per item:
[
  {{"item_id": "...", "matched_card_id": "abc123" or null, "match_reasoning": "brief reason"}}
]

If nothing matches closely enough, use null for matched_card_id."""

# Groq's structured-outputs contract requires the root schema to be a JSON
# object, not a bare array -- the production prompt above's own trailing
# "Return ONLY a JSON array..." instruction conflicts with that (confirmed
# for classify_item's identical phrasing in scripts/compare_groq_harness.py;
# correlate_trello's one real harness run happened not to trigger it, but
# it's the same conflict waiting to happen). Applied proactively here
# rather than waiting for it to fail in production.
_GROQ_TRAILING_INSTRUCTION_OLD = (
    'Return ONLY a JSON array, one object per item:\n'
    '[\n'
    '  {"item_id": "...", "matched_card_id": "abc123" or null, "match_reasoning": "brief reason"}\n'
    ']\n\n'
    'If nothing matches closely enough, use null for matched_card_id.'
)
_GROQ_TRAILING_INSTRUCTION_NEW = (
    'Return one object per item, with: item_id, matched_card_id (a card id '
    'string, or null if nothing matches closely enough), match_reasoning '
    '(brief reason).'
)

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


def _format_cards(cards: list[dict]) -> str:
    lines = []
    for c in cards:
        lines.append(f"- id={c['card_id']} | list={c['list_name']} | {c['name']}")
        for item in c.get("checklist_items", []):
            lines.append(f"    - checklist: {item}")
    return "\n".join(lines)


def _format_items(items: list[dict]) -> str:
    return "\n".join(
        f"- id={i['url']} | tags={i['tags']} | {i['reasoning'][:150]}"
        for i in items
    )


def _parse_json_response(raw: str) -> list:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw.strip())


def _correlate_trello_anthropic_legacy(state: SaturdayGraphState) -> dict:
    """Pre-2026-07-26 Haiku implementation. Not called anywhere -- kept
    verbatim for rollback safety, see core/groq_client.py's docstring."""
    t0 = time.perf_counter()

    kept_items = [i for i in state["scored_items"] if i["keep"]]
    logger.info(
        f"correlate_trello: {len(kept_items)} kept / {len(state['scored_items'])} scored items "
        f"(run_id={state['run_id']})"
    )

    prompt = CORRELATE_PROMPT.format(
        cards_block=_format_cards(state["trello_cards"]),
        items_block=_format_items(kept_items),
    )

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    try:
        matches = _parse_json_response(response.content[0].text)
    except json.JSONDecodeError:
        logger.warning(f"correlate_trello: first parse failed, retrying (run_id={state['run_id']})")
        retry_response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4096,
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response.content[0].text},
                {"role": "user", "content": "Return ONLY valid JSON. No markdown, no text before or after the array."},
            ],
        )
        input_tokens += retry_response.usage.input_tokens
        output_tokens += retry_response.usage.output_tokens
        try:
            matches = _parse_json_response(retry_response.content[0].text)
        except json.JSONDecodeError:
            logger.error(f"correlate_trello: retry parse also failed (run_id={state['run_id']})")
            cost = NodeCost(
                node_name="correlate_trello",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=round((input_tokens * 0.00025 + output_tokens * 0.00125) / 1000, 6),
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                provider="anthropic",
            )
            record_node_summary(
                run_id=state["run_id"], node_name="correlate_trello",
                items_in=len(kept_items), items_out=0, cost_usd=cost["cost_usd"],
                duration_seconds=round(time.perf_counter() - t0, 3),
                error_summary="JSON parse failed after retry",
            )
            return {
                "correlated_items": [{**item, "matched_card_id": None} for item in kept_items],
                "costs": [cost],
                "errors": state["errors"] + [f"correlate_trello JSON parse failed after retry (run_id={state['run_id']})"],
            }

    match_by_id = {m["item_id"]: m for m in matches}
    correlated_items = [
        {**item, "matched_card_id": match_by_id.get(item["url"], {}).get("matched_card_id")}
        for item in kept_items
    ]

    matched_count = sum(1 for i in correlated_items if i["matched_card_id"])
    logger.info(f"correlate_trello matched {matched_count} / {len(correlated_items)} items (run_id={state['run_id']})")

    cost = NodeCost(
        node_name="correlate_trello",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round((input_tokens * 0.00025 + output_tokens * 0.00125) / 1000, 6),
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        provider="anthropic",
    )

    record_node_summary(
        run_id=state["run_id"], node_name="correlate_trello",
        items_in=len(kept_items), items_out=matched_count, cost_usd=cost["cost_usd"],
        duration_seconds=round(time.perf_counter() - t0, 3),
    )

    return {"correlated_items": correlated_items, "costs": [cost]}


def correlate_trello(state: SaturdayGraphState) -> dict:
    t0 = time.perf_counter()

    kept_items = [i for i in state["scored_items"] if i["keep"]]
    logger.info(
        f"correlate_trello: {len(kept_items)} kept / {len(state['scored_items'])} scored items "
        f"(run_id={state['run_id']})"
    )

    prompt = CORRELATE_PROMPT.format(
        cards_block=_format_cards(state["trello_cards"]),
        items_block=_format_items(kept_items),
    ).replace(_GROQ_TRAILING_INSTRUCTION_OLD, _GROQ_TRAILING_INSTRUCTION_NEW)

    groq_client = get_groq_client()

    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0,
            max_completion_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "correlate_trello", "strict": True, "schema": _CORRELATE_JSON_SCHEMA},
            },
        )
    except groq.APIError as e:
        logger.error(f"correlate_trello: Groq call failed after retries (run_id={state['run_id']}): {e}")
        cost = NodeCost(
            node_name="correlate_trello",
            input_tokens=0, output_tokens=0,
            cost_usd=0.0,
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            provider="groq",
            error=str(e),
        )
        record_node_summary(
            run_id=state["run_id"], node_name="correlate_trello",
            items_in=len(kept_items), items_out=0, cost_usd=0.0,
            duration_seconds=round(time.perf_counter() - t0, 3),
            error_summary="Groq API call failed after retries",
        )
        return {
            "correlated_items": [{**item, "matched_card_id": None} for item in kept_items],
            "costs": [cost],
            "errors": state["errors"] + [f"correlate_trello Groq call failed after retries (run_id={state['run_id']}): {e}"],
        }

    input_tokens = response.usage.prompt_tokens
    output_tokens = response.usage.completion_tokens

    try:
        matches = json.loads(response.choices[0].message.content)["results"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"correlate_trello: malformed structured-output response (run_id={state['run_id']}): {e}")
        cost = NodeCost(
            node_name="correlate_trello",
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=groq_cost(input_tokens, output_tokens),
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            provider="groq",
        )
        record_node_summary(
            run_id=state["run_id"], node_name="correlate_trello",
            items_in=len(kept_items), items_out=0, cost_usd=cost["cost_usd"],
            duration_seconds=round(time.perf_counter() - t0, 3),
            error_summary="malformed structured-output response",
        )
        return {
            "correlated_items": [{**item, "matched_card_id": None} for item in kept_items],
            "costs": [cost],
            "errors": state["errors"] + [f"correlate_trello malformed structured-output response (run_id={state['run_id']}): {e}"],
        }

    match_by_id = {m["item_id"]: m for m in matches}
    correlated_items = [
        {**item, "matched_card_id": match_by_id.get(item["url"], {}).get("matched_card_id")}
        for item in kept_items
    ]

    matched_count = sum(1 for i in correlated_items if i["matched_card_id"])
    logger.info(f"correlate_trello matched {matched_count} / {len(correlated_items)} items (run_id={state['run_id']})")

    cost = NodeCost(
        node_name="correlate_trello",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=groq_cost(input_tokens, output_tokens),
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        provider="groq",
    )

    # items_out = matched count (not total correlated_items -- nothing is
    # actually dropped here, every kept item survives; the real judgment
    # call this node makes is matched-vs-not, so that's what "dropped"
    # should reflect).
    record_node_summary(
        run_id=state["run_id"], node_name="correlate_trello",
        items_in=len(kept_items), items_out=matched_count, cost_usd=cost["cost_usd"],
        duration_seconds=round(time.perf_counter() - t0, 3),
    )

    return {"correlated_items": correlated_items, "costs": [cost]}
