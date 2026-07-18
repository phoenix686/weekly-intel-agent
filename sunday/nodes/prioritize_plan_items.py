# sunday/nodes/prioritize_plan_items.py
import time
import json
import logging
import anthropic

from state import SundayGraphState, NodeCost
from observability import record_node_summary

logger = logging.getLogger(__name__)
client = anthropic.Anthropic()

MAX_PROJECT_WORK_ITEMS = 5

PRIORITIZE_PROMPT = """You are helping Pooja, an AI/ML engineer, decide what existing Trello project work is genuinely worth her limited weekly hours. She does this project as a side effort alongside a full-time day job, specifically to reclaim time her job doesn't otherwise give her. Your job is NOT to list everything relevant -- it's to identify what's actually worth prioritizing this week.

You are choosing, honestly, between two kinds of candidates:
1. New content this week that directly continues an existing tracked Trello card ("New items matched to existing cards" below).
2. Existing Trello cards that have gone stale or idle and deserve a nudge, even with no new content this week ("Full Trello board state" below). A card that's been sitting untouched for weeks can be MORE worth surfacing than a shiny new article, if the underlying work still matters -- weigh these honestly against each other, not by recency of the trigger.

Cross-week movement since the last plan (ground truth from Trello's actual state, not a self-reported flag):
{movement_block}

Rules:
- Do NOT surface a card whose movement status above is "completed" or "archived" -- that work is done or shelved, leave it out entirely.
- If you include a card whose movement status is "unchanged", you MUST explicitly acknowledge in movement_note that it hasn't moved since last week -- never silently repeat it as if it were new, and never silently drop it either without deciding it's not worth including.
- A card with no listed movement status (was not in last week's plan, or this is the first-ever run) has no prior-week context -- judge it purely on its own merits (staleness per last_activity, or being matched to strong new content).

New items matched to existing cards this week:
{items_block}

Full Trello board state (every Dump + In Progress card, including cards with no new content this week -- last_activity is Trello's own timestamp, ISO 8601):
{cards_block}

Select AT MOST {max_items} entries for this week's Existing Project Work section, ordered from HIGHEST to LOWEST priority. Target 3-{max_items} entries; fewer (including zero) is correct if there genuinely isn't enough worth surfacing this week -- never pad the list just to hit the target. Return ONLY a JSON array:
[
  {{
    "matched_card_id": "...",
    "source": "new_item" or "stale_nudge",
    "item_url": "..." or null,
    "priority_reasoning": "one sentence: why this rank, referencing urgency, staleness, or depth -- not just a description of the content",
    "movement_note": "..." or null
  }}
]

"item_url" must be one of the URLs from "New items matched to existing cards" above when source is "new_item", and null when source is "stale_nudge" (a card with no new content this week). "matched_card_id" must be a real card id from the Trello board state above."""


def _format_movements(movements: list[dict]) -> str:
    if not movements:
        return "(no prior plan to compare against -- this is the first run, or no cards were surfaced last week)"
    return "\n".join(
        f"- card_id={m['card_id']} | status={m['status']} | "
        f"previous_list={m['previous_list_name']} | current_list={m['current_list_name']}"
        for m in movements
    )


def _format_items(items: list[dict]) -> str:
    if not items:
        return "(none this week)"
    return "\n".join(
        f"- url={i['url']} | matched_card_id={i['matched_card_id']} | "
        f"tags={i['tags']} | reasoning={i['reasoning'][:150]}"
        for i in items
    )


def _format_cards(cards: list[dict]) -> str:
    if not cards:
        return "(no open Dump/In Progress cards)"
    return "\n".join(
        f"- id={c['card_id']} | list={c['list_name']} | last_activity={c.get('last_activity')} | {c['name']}"
        for c in cards
    )


def _parse_json_response(raw: str) -> list:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw.strip())


def _validate_selection(raw_selection: list, valid_card_ids: set[str], valid_item_urls: set[str]) -> list[dict]:
    """Defensive validation against a real (possibly non-compliant)
    model response -- drop any entry pointing at a card_id/item_url the
    model invented, and hard-cap at MAX_PROJECT_WORK_ITEMS regardless of
    what the model returned, since the bound is a real requirement, not
    a suggestion."""
    validated = []
    for entry in raw_selection:
        card_id = entry.get("matched_card_id")
        if card_id not in valid_card_ids:
            logger.warning(f"prioritize_plan_items: dropping entry with unknown matched_card_id {card_id!r}")
            continue
        source = entry.get("source") if entry.get("source") in {"new_item", "stale_nudge"} else "stale_nudge"
        item_url = entry.get("item_url")
        if source == "new_item" and item_url not in valid_item_urls:
            logger.warning(f"prioritize_plan_items: dropping new_item entry with unknown item_url {item_url!r}")
            continue
        if source == "stale_nudge":
            item_url = None
        validated.append({
            "matched_card_id": card_id,
            "source": source,
            "item_url": item_url,
            "priority_reasoning": entry.get("priority_reasoning", ""),
            "movement_note": entry.get("movement_note"),
        })
        if len(validated) == MAX_PROJECT_WORK_ITEMS:
            break
    return validated


def prioritize_plan_items(state: SundayGraphState) -> dict:
    t0 = time.perf_counter()

    matched_items = [
        i for i in state["classified_items"]
        if i["classification"] == "plan_item"
        and "course" not in i.get("tags", [])
        and i.get("matched_card_id") is not None
    ]
    trello_cards = state["trello_cards"]
    movements = state["card_movements"]

    valid_card_ids = {c["card_id"] for c in trello_cards}
    valid_item_urls = {i["url"] for i in matched_items}

    prompt = PRIORITIZE_PROMPT.format(
        movement_block=_format_movements(movements),
        items_block=_format_items(matched_items),
        cards_block=_format_cards(trello_cards),
        max_items=MAX_PROJECT_WORK_ITEMS,
    )

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    try:
        raw_selection = _parse_json_response(response.content[0].text)
    except json.JSONDecodeError:
        logger.warning(f"prioritize_plan_items: first parse failed, retrying (run_id={state['run_id']})")
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
            raw_selection = _parse_json_response(retry_response.content[0].text)
        except json.JSONDecodeError:
            logger.error(f"prioritize_plan_items: retry parse also failed (run_id={state['run_id']})")
            cost = NodeCost(
                node_name="prioritize_plan_items",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=round((input_tokens * 0.00025 + output_tokens * 0.00125) / 1000, 6),
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
            # Graceful degradation: fall back to this week's matched items,
            # unprioritized (original order), capped at the same bound --
            # preserves at least the "new content" candidates rather than
            # surfacing nothing, same fallback philosophy as
            # correlate_trello/classify_item's own JSON-failure paths.
            fallback = [
                {
                    "matched_card_id": i["matched_card_id"], "source": "new_item",
                    "item_url": i["url"], "priority_reasoning": "fallback: JSON parse failed, unprioritized",
                    "movement_note": None,
                }
                for i in matched_items[:MAX_PROJECT_WORK_ITEMS]
            ]
            record_node_summary(
                run_id=state["run_id"], node_name="prioritize_plan_items",
                items_in=len(matched_items), items_out=len(fallback), cost_usd=cost["cost_usd"],
                duration_seconds=round(time.perf_counter() - t0, 3),
                error_summary="JSON parse failed after retry",
            )
            return {
                "prioritized_project_work": fallback,
                "costs": [cost],
                "errors": state["errors"] + [f"prioritize_plan_items JSON parse failed after retry (run_id={state['run_id']})"],
            }

    selection = _validate_selection(raw_selection, valid_card_ids, valid_item_urls)

    logger.info(
        f"prioritize_plan_items: selected {len(selection)} of {len(matched_items)} matched item(s) "
        f"+ {len(trello_cards)} board card(s) considered (run_id={state['run_id']})"
    )

    cost = NodeCost(
        node_name="prioritize_plan_items",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round((input_tokens * 0.00025 + output_tokens * 0.00125) / 1000, 6),
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )

    record_node_summary(
        run_id=state["run_id"], node_name="prioritize_plan_items",
        items_in=len(matched_items), items_out=len(selection), cost_usd=cost["cost_usd"],
        duration_seconds=round(time.perf_counter() - t0, 3),
    )

    return {"prioritized_project_work": selection, "costs": [cost]}
