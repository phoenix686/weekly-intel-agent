# sunday/nodes/correlate_trello.py
import time
import json
import logging
import anthropic

from state import SundayGraphState, NodeCost

logger = logging.getLogger(__name__)
client = anthropic.Anthropic()

CORRELATE_PROMPT = """You are matching newly scored items against existing Trello cards to decide if each item relates to work already tracked.

Existing Trello cards:
{cards_block}

Scored items to match:
{items_block}

For each item, decide whether it matches an existing card closely enough that it should be considered a continuation/extension of that card's work — not just topically similar, but genuinely the same underlying task or project.

Return ONLY a JSON array, one object per item, in this exact shape:
[
  {{"item_id": "...", "matched_card_id": "abc123" or null, "match_reasoning": "brief reason"}}
]

If nothing matches closely enough, use null for matched_card_id — don't force a weak match."""


def _format_cards(cards: list[dict]) -> str:
    return "\n".join(
        f"- id={c['card_id']} | list={c['list_name']} | {c['name']}"
        for c in cards
    )


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


def correlate_trello(state: SundayGraphState) -> dict:
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

    logger.info(f"correlate_trello matched {sum(1 for i in correlated_items if i['matched_card_id'])} / {len(correlated_items)} items (run_id={state['run_id']})")

    cost = NodeCost(
        node_name="correlate_trello",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round((input_tokens * 0.00025 + output_tokens * 0.00125) / 1000, 6),
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )

    return {"correlated_items": correlated_items, "costs": [cost]}