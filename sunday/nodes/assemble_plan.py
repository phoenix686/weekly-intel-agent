import time
import logging
from datetime import datetime, timezone

from state import SundayGraphState, NodeCost
from sunday.memory_store_config import get_store
from sunday.plan_history import record_plan_history
from sunday.carry_forward import get_carry_forward_items

logger = logging.getLogger(__name__)

def assemble_plan(state: SundayGraphState) -> dict:
    t0 = time.perf_counter()

    # Carried items are injected here, at the very end of the graph --
    # never into state["classified_items"] itself, so plan_history/
    # prioritize_plan_items (both already run by this point anyway) never
    # see them. Built directly from last week's already-scored data, so
    # this can never trigger a re-score or get blocked by seen_items --
    # see sunday/carry_forward.py's module docstring for why.
    carried_items = get_carry_forward_items(state["run_id"])
    plan_items_with_carryover = state["classified_items"] + carried_items

    text, item_map = format_plan(
        plan_items_with_carryover,
        len(state["pending_approvals"]),
        state["run_id"],
        state["trello_cards"],
        state["prioritized_project_work"],
    )

    # plan_history must reflect what was ACTUALLY surfaced in Existing
    # Project Work (the bounded prioritize_plan_items selection), not the
    # full unbounded matched-item set -- cross-week movement detection
    # (sub-phase 4) depends on "surfaced last week" meaning "Pooja actually
    # saw it in the plan," not "it happened to match something that week."
    card_by_id_for_history = {c["card_id"]: c for c in state["trello_cards"]}
    surfaced_cards = [
        {
            "card_id": entry["matched_card_id"],
            "list_name": card_by_id_for_history.get(entry["matched_card_id"], {}).get("list_name", "Unknown"),
        }
        for entry in state["prioritized_project_work"]
    ]
    record_plan_history(state["run_id"], surfaced_cards)

    get_store().put(
        ("companion",),
        "current_weekly_plan",
        {
            "run_id": state["run_id"],
            "plan_text": text,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    cost = NodeCost(
        node_name="assemble_plan", input_tokens=0, output_tokens=0,
        cost_usd=0.0, latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )
    return {"plan_text": text, "plan_item_map": item_map, "costs": [cost]}
def _build_project_entries(prioritized_project_work: list[dict], trello_cards: list[dict], plan_items: list[dict]) -> list[dict]:
    """Existing Project Work is rendered entirely from prioritize_plan_items'
    bounded, priority-ordered selection (sub-phase 5/final sub-phase) --
    NOT re-derived from classified_items' matched_card_id, so a matched
    item the LLM didn't select for this week's ~3-5 simply doesn't render
    anywhere, by design (that's the bounding). A "stale_nudge" entry has
    no underlying classified_item at all -- it's rendered from the
    Trello card itself (its own name/url), not from any scored content.
    Order is preserved exactly as prioritize_plan_items returned it --
    that IS the priority order this renders in."""
    card_by_id = {c["card_id"]: c for c in trello_cards}
    item_by_url = {i["url"]: i for i in plan_items if i.get("url")}

    entries = []
    for entry in prioritized_project_work:
        card = card_by_id.get(entry["matched_card_id"], {})
        card_name = card.get("name", entry["matched_card_id"])
        src_item = item_by_url.get(entry.get("item_url")) if entry.get("source") == "new_item" else None

        if src_item is not None:
            title = (src_item.get("title") or src_item["text"])[:80]
            url = src_item["url"]
            tags = src_item.get("tags", [])
            text = src_item["text"]
        else:
            # stale_nudge, or a new_item whose url didn't resolve -- render
            # the Trello card itself rather than crashing or dropping it.
            title = card_name
            url = card.get("url", "")
            tags = []
            text = ""

        reasoning = entry["priority_reasoning"]
        if entry.get("movement_note"):
            reasoning = f"{reasoning} — {entry['movement_note']}"

        entries.append({
            "title": title, "url": url, "text": text, "tags": tags,
            "reasoning": reasoning, "card_name": card_name,
        })
    return entries


def format_plan(
    classified_items: list[dict],
    pending_approvals_count: int,
    run_id: str,
    trello_cards: list[dict],
    prioritized_project_work: list[dict] | None = None,
) -> tuple[str, dict[int, dict]]:
    prioritized_project_work = prioritized_project_work or []
    plan_items = [i for i in classified_items if i["classification"] == "plan_item"]
    project_entries = _build_project_entries(prioritized_project_work, trello_cards, plan_items)
    courses = [i for i in plan_items if "course" in i.get("tags", [])]
    reading = [i for i in plan_items if "course" not in i.get("tags", []) and i.get("matched_card_id") is None]

    if not reading and not courses and not project_entries:
        msg = "📋 *Weekly Plan*\n\n_Nothing on the plan this week."
        if pending_approvals_count > 0:
            msg += f" {pending_approvals_count} proposals pending approval — check Telegram."
        msg += "_"
        return msg, {}

    lines = ["📋 *Weekly Plan*", ""]
    counter = 1
    item_map: dict[int, dict] = {}

    if reading:
        lines.append("**Reading & Learning**")
        for item in reading:
            title = (item.get("title") or item["text"])[:80]
            reasoning = item["reasoning"].replace("_", r"\_")
            lines.append(f"{counter}. [{title}]({item['url']})")
            lines.append(f"   _{reasoning}_")
            lines.append("")
            item_map[counter] = {
                "url": item["url"], "title": title,
                "text": item["text"], "tags": item.get("tags", []),
                "reasoning": item["reasoning"], "section": "reading",
            }
            counter += 1

    if courses:
        lines.append("**Courses**")
        for item in courses:
            title = (item.get("title") or item["text"])[:80]
            reasoning = item["reasoning"].replace("_", r"\_")
            lines.append(f"{counter}. [{title}]({item['url']})")
            lines.append(f"   _{reasoning}_")
            lines.append("")
            item_map[counter] = {
                "url": item["url"], "title": title,
                "text": item["text"], "tags": item.get("tags", []),
                "reasoning": item["reasoning"], "section": "courses",
            }
            counter += 1

    if project_entries:
        lines.append("**Existing Project Work**")
        for entry in project_entries:
            reasoning = entry["reasoning"].replace("_", r"\_")
            lines.append(f"{counter}. [{entry['title']}]({entry['url']})")
            lines.append(f'   _{reasoning}_ — continues card: "{entry["card_name"]}"')
            lines.append("")
            item_map[counter] = {
                "url": entry["url"], "title": entry["title"],
                "text": entry["text"], "tags": entry["tags"],
                "reasoning": entry["reasoning"], "section": "existing_project_work",
            }
            counter += 1

    total_rendered = len(reading) + len(courses) + len(project_entries)
    if pending_approvals_count > 0:
        footer = f"_{total_rendered} plan items · {pending_approvals_count} proposals pending approval · run: {run_id[:8]}_"
    else:
        footer = f"_{total_rendered} plan items · run: {run_id[:8]}_"
    lines.append(footer)

    return "\n".join(lines), item_map


