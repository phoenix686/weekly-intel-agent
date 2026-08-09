# saturday/nodes/prioritize_plan_items.py
import time
import json
import logging

from core.state import SaturdayGraphState, NodeCost
from core.model_gateway import ModelGatewayError, get_model_gateway
from core.observability import record_node_summary

logger = logging.getLogger(__name__)

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
- CRITICAL: if a card has NO entry in the cross-week movement list above, you have ZERO real data on whether it moved, and movement_note MUST reflect that -- it must be null, or if non-null must say only that movement status is unavailable/unknown. Do NOT write movement_note text that states or implies a cross-week change status (e.g. "unchanged since last week", "no movement in N days", "still stuck") for such a card -- that would be inventing a claim with no real data behind it, even if it sounds plausible from last_activity alone. Staleness reasoning from last_activity belongs in priority_reasoning (e.g. "idle 43 days per last_activity"), never phrased in movement_note as if it were a real cross-week comparison.

New items matched to existing cards this week:
{items_block}

Full Trello board state (every Dump + In Progress card, including cards with no new content this week -- last_activity is Trello's own timestamp, ISO 8601):
{cards_block}

Select AT MOST {max_items} entries for this week's Existing Project Work section, ordered from HIGHEST to LOWEST priority. Target 3-{max_items} entries; fewer (including zero) is correct if there genuinely isn't enough worth surfacing this week -- never pad the list just to hit the target. Return a JSON object with a "results" array:
{{"results": [
  {{
    "matched_card_id": "...",
    "source": "new_item" or "stale_nudge",
    "item_url": "..." or null,
    "priority_reasoning": "one sentence: why this rank, referencing urgency, staleness, or depth -- not just a description of the content",
    "movement_note": "..." or null
  }}
]}}

"item_url" must be one of the URLs from "New items matched to existing cards" above when source is "new_item", and null when source is "stale_nudge" (a card with no new content this week). "matched_card_id" must be a real card id from the Trello board state above."""

_PRIORITIZE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "matched_card_id": {"type": "string"},
                    "source": {
                        "type": "string",
                        "enum": ["new_item", "stale_nudge"],
                    },
                    "item_url": {"type": ["string", "null"]},
                    "priority_reasoning": {"type": "string"},
                    "movement_note": {"type": ["string", "null"]},
                },
                "required": [
                    "matched_card_id",
                    "source",
                    "item_url",
                    "priority_reasoning",
                    "movement_note",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


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


def prioritize_plan_items(state: SaturdayGraphState) -> dict:
    t0 = time.perf_counter()

    matched_items = [
        i for i in state["classified_items"]
        if i["classification"] == "plan_item"
        and "course" not in i.get("tags", [])
        and i.get("matched_card_id") is not None
    ]

    if not matched_items:
        # No new content matched to an existing Trello card this week --
        # removed (2026-07-22, Step 6) the prior fallback that still sent
        # the FULL Trello board state to Haiku and let it backfill Existing
        # Project Work with pure stale_nudge picks even with zero new
        # items. format_plan() already renders "Nothing on the plan this
        # week" (or simply omits the Existing Project Work section, if
        # Reading/Courses/uncategorized have real content) when
        # prioritized_project_work is empty -- no board cards enter into
        # consideration at all here, so none can leak into the plan as a
        # substitute for real new content. Zero-cost: no LLM call is made.
        logger.info(
            f"prioritize_plan_items: 0 new plan item(s) matched to a Trello card this week -- "
            f"skipping board prioritization, no fallback (run_id={state['run_id']})"
        )
        cost = NodeCost(
            node_name="prioritize_plan_items",
            input_tokens=0, output_tokens=0, cost_usd=0.0,
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        )
        record_node_summary(
            run_id=state["run_id"], node_name="prioritize_plan_items",
            items_in=0, items_out=0, cost_usd=0.0,
            duration_seconds=round(time.perf_counter() - t0, 3),
        )
        return {"prioritized_project_work": [], "costs": [cost]}

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

    gateway = get_model_gateway(
        pipeline="saturday",
        anthropic_spend_usd=state.get("anthropic_spend_usd", 0.0),
    )
    try:
        model_result = gateway.complete_json(
            task="prioritize_plan_items",
            prompt=prompt,
            json_schema=_PRIORITIZE_JSON_SCHEMA,
            max_completion_tokens=1024,
            allow_anthropic_fallback=True,
        )
        raw_selection = model_result.data["results"]
    except (ModelGatewayError, KeyError, TypeError) as exc:
        logger.error(
            "prioritize_plan_items: model gateway failed (run_id=%s): %s",
            state["run_id"],
            exc,
        )
        fallback = [
            {
                "matched_card_id": item["matched_card_id"],
                "source": "new_item",
                "item_url": item["url"],
                "priority_reasoning": "fallback: provider unavailable, unprioritized",
                "movement_note": None,
            }
            for item in matched_items[:MAX_PROJECT_WORK_ITEMS]
        ]
        cost = NodeCost(
            node_name="prioritize_plan_items",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            provider="groq",
            error=str(exc),
        )
        record_node_summary(
            run_id=state["run_id"],
            node_name="prioritize_plan_items",
            items_in=len(matched_items),
            items_out=len(fallback),
            cost_usd=0.0,
            duration_seconds=round(time.perf_counter() - t0, 3),
            error_summary="model gateway failed",
        )
        return {
            "prioritized_project_work": fallback,
            "costs": [cost],
            "errors": [f"prioritize_plan_items provider failure: {exc}"],
            "anthropic_spend_usd": gateway.anthropic_spend_usd,
        }

    selection = _validate_selection(raw_selection, valid_card_ids, valid_item_urls)

    logger.info(
        f"prioritize_plan_items: selected {len(selection)} of {len(matched_items)} matched item(s) "
        f"+ {len(trello_cards)} board card(s) considered (run_id={state['run_id']})"
    )

    cost = NodeCost(
        node_name="prioritize_plan_items",
        input_tokens=model_result.input_tokens,
        output_tokens=model_result.output_tokens,
        cost_usd=model_result.cost_usd,
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        provider=model_result.provider,
    )

    record_node_summary(
        run_id=state["run_id"], node_name="prioritize_plan_items",
        items_in=len(matched_items), items_out=len(selection), cost_usd=cost["cost_usd"],
        duration_seconds=round(time.perf_counter() - t0, 3),
    )

    return {
        "prioritized_project_work": selection,
        "costs": [cost],
        "errors": (
            ["provider_degraded: Anthropic fallback used during project prioritization"]
            if model_result.degraded
            else []
        ),
        "anthropic_spend_usd": gateway.anthropic_spend_usd,
    }
