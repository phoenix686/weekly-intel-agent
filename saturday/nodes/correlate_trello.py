# saturday/nodes/correlate_trello.py
import time
import json
import logging
import anthropic

from core.state import SaturdayGraphState, NodeCost
from core.observability import record_node_summary

logger = logging.getLogger(__name__)
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