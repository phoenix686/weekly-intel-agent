import time
import logging
from datetime import datetime, timezone

from core.state import SundayGraphState, NodeCost
from core.observability import cost_breakdown_by_provider
from sunday.memory_store_config import get_store
from sunday.plan_history import record_plan_history
from sunday.carry_forward import get_carry_forward_items
from telegram.markdown import escape_html, format_cost_line

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

    # Real per-run $ cost so far, broken out by provider (2026-07-26).
    # Scoped to everything that ran BEFORE plan assembly (discovery,
    # read_trello, correlate_trello, classify_item, prioritize_plan_items)
    # -- NOT update_profile's weekly taste-profile rewrite, which runs
    # later in this same graph execution but is a separate weekly
    # maintenance cost, not something this specific plan message caused.
    cost_breakdown = cost_breakdown_by_provider(state["costs"])
    text, item_map = format_plan(
        plan_items_with_carryover,
        len(state["pending_approvals"]),
        state["run_id"],
        state["trello_cards"],
        state["prioritized_project_work"],
        state["uncategorized_items"],
        cost_breakdown=cost_breakdown,
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

    generated_at = datetime.now(timezone.utc).isoformat()

    get_store().put(
        ("companion",),
        "current_weekly_plan",
        {
            "run_id": state["run_id"],
            "plan_text": text,
            "generated_at": generated_at,
        },
    )

    village_summary = f"{len(item_map)} plan item(s)" if item_map else "no new content"
    get_store().put(
        ("village",),
        f"event:{generated_at}",
        {
            "agent": "weekly-intel",
            "event_type": "plan_ready",
            "summary": village_summary,
            "timestamp": generated_at,
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
            # [:80] matches every other title's truncation -- found missing
            # here 2026-07-19 while investigating a real near-4096-char
            # plan_text: a real card name can run far longer than a typical
            # article title (one real example was ~200 chars) and was
            # rendering in full, unlike every other title path.
            title = card_name[:80]
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


MAX_PLAN_TEXT_CHARS = 3900  # soft budget, headroom under Telegram's real 4096 hard limit
REASONING_CHAR_BUDGET = 150  # applied only when the full render exceeds MAX_PLAN_TEXT_CHARS


def _truncate(text: str, max_len: int) -> str:
    """Truncates the RAW text, before HTML-escaping -- truncating
    post-escape risks cutting mid-entity (e.g. splitting "&amp;" into
    "&am"). Appends an ellipsis so truncation is visible, not silently
    misleading."""
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def _render(
    reading: list[dict], courses: list[dict], project_entries: list[dict],
    pending_approvals_count: int, run_id: str, reasoning_budget: int | None = None,
    uncategorized_items: list[dict] | None = None, cost_breakdown: dict[str, float] | None = None,
) -> tuple[str, dict[int, dict]]:
    """One rendering pass. reasoning_budget, when set, caps each item's/
    entry's reasoning -- and, for Existing Project Work, the card_name
    shown in the "continues card" suffix -- to that many raw characters
    (see format_plan()'s length-safety docstring for why). item_map
    always stores the FULL, untruncated original text regardless of
    reasoning_budget: truncation is a rendering-time concern only, so a
    carried-forward item (sunday/carry_forward.py) that gets reused next
    week isn't permanently stuck with a truncated blurb just because
    this week's message happened to be near the length limit.

    uncategorized_items (2026-07-22, lightweight-uncategorized-flagging):
    rendered last, numbered CONTINUING the same item_map -- not a
    separate map -- so a reply naming a new tag for one of these resolves
    through the exact same telegram/feedback_router.py path as any other
    plan reply."""
    uncategorized_items = uncategorized_items or []
    lines = ["📋 <b>Weekly Plan</b>", ""]
    counter = 1
    item_map: dict[int, dict] = {}

    def _cap(raw: str) -> str:
        return _truncate(raw, reasoning_budget) if reasoning_budget else raw

    if reading:
        lines.append("<b>Reading & Learning</b>")
        for item in reading:
            title = (item.get("title") or item["text"])[:80]
            lines.append(f'{counter}. <a href="{escape_html(item["url"])}">{escape_html(title)}</a>')
            lines.append(f"   <i>{escape_html(_cap(item['reasoning']))}</i>")
            lines.append("")
            item_map[counter] = {
                "url": item["url"], "title": title,
                "text": item["text"], "tags": item.get("tags", []),
                "reasoning": item["reasoning"], "section": "reading",
            }
            counter += 1

    if courses:
        lines.append("<b>Courses</b>")
        for item in courses:
            title = (item.get("title") or item["text"])[:80]
            lines.append(f'{counter}. <a href="{escape_html(item["url"])}">{escape_html(title)}</a>')
            lines.append(f"   <i>{escape_html(_cap(item['reasoning']))}</i>")
            lines.append("")
            item_map[counter] = {
                "url": item["url"], "title": title,
                "text": item["text"], "tags": item.get("tags", []),
                "reasoning": item["reasoning"], "section": "courses",
            }
            counter += 1

    if project_entries:
        lines.append("<b>Existing Project Work</b>")
        for entry in project_entries:
            reasoning = _cap(entry["reasoning"])
            card_name = _cap(entry["card_name"])
            lines.append(f'{counter}. <a href="{escape_html(entry["url"])}">{escape_html(entry["title"])}</a>')
            lines.append(f'   <i>{escape_html(reasoning)}</i> — continues card: "{escape_html(card_name)}"')
            lines.append("")
            item_map[counter] = {
                "url": entry["url"], "title": entry["title"],
                "text": entry["text"], "tags": entry["tags"],
                "reasoning": entry["reasoning"], "section": "existing_project_work",
            }
            counter += 1

    if uncategorized_items:
        lines.append(f"<b>{len(uncategorized_items)} item(s) didn't match any existing topic</b>")
        for item in uncategorized_items:
            title = (item.get("title") or item["text"])[:80]
            url = item["url"]
            best_tag = item["best_tag"]
            score = item["similarity_score"]
            reasoning = _cap(f"closest existing tag: {best_tag} (cosine={score:.3f})")

            lines.append(f'{counter}. <a href="{escape_html(url)}">{escape_html(title)}</a>')
            lines.append(f"   <i>{escape_html(reasoning)}</i>")
            lines.append("")

            item_map[counter] = {
                "url": url, "title": title, "text": item["text"],
                "tags": ["uncategorized"],
                "reasoning": f"closest existing tag: {best_tag} (cosine={score:.3f})",
                "section": "uncategorized",
            }
            counter += 1

    total_rendered = len(reading) + len(courses) + len(project_entries)
    footer_parts = [f"{total_rendered} plan items"]
    if uncategorized_items:
        footer_parts.append(f"{len(uncategorized_items)} uncategorized")
    if pending_approvals_count > 0:
        footer_parts.append(f"{pending_approvals_count} proposals pending approval")
    footer_parts.append(f"run: {run_id[:8]}")
    lines.append(f"<i>{' · '.join(footer_parts)}</i>")

    cost_line = format_cost_line(cost_breakdown)
    if cost_line:
        lines.append(cost_line)

    return "\n".join(lines), item_map


def format_plan(
    classified_items: list[dict],
    pending_approvals_count: int,
    run_id: str,
    trello_cards: list[dict],
    prioritized_project_work: list[dict] | None = None,
    uncategorized_items: list[dict] | None = None,
    cost_breakdown: dict[str, float] | None = None,
) -> tuple[str, dict[int, dict]]:
    """Renders with Telegram HTML parse_mode (see telegram/bot_client.py) --
    NOT Markdown. item_map keeps RAW (unescaped) title/text/reasoning --
    only the rendered `lines` strings are HTML-escaped, at the point of
    interpolation. This matters for sunday/carry_forward.py, which reads
    item_map's stored fields back out next week and feeds them through
    format_plan() again as a fresh classified_item -- if item_map stored
    already-escaped text, a carried item would get double-escaped
    ("&amp;" -> "&amp;amp;") on its second render.

    Length safety (2026-07-19): Telegram hard-caps a single sendMessage
    at 4096 characters -- a real send_telegram_plan send already reached
    4032/4096 (98.4%) even before this safeguard existed, purely from the
    HTML tags added by the parse_mode fix (see docs/WORKFLOW.md). Item
    COUNT stays unbounded for Reading & Learning/Courses either way (that
    commitment is about selection, not per-item verbosity) -- if the full
    render exceeds MAX_PLAN_TEXT_CHARS, every item's reasoning (and, for
    Existing Project Work, the "continues card" name) is re-rendered
    capped to REASONING_CHAR_BUDGET characters instead, with a shrinking
    safety net for the rare case where even that fixed budget isn't
    enough. Chosen over splitting into multiple Telegram messages: fully
    contained here, zero changes needed to send_telegram_plan.py, the
    assemble_plan() node wrapper, or digest_item_map's one-entry-per-run
    shape that carry_forward.py's lookup already depends on.

    uncategorized_items (2026-07-22, lightweight-uncategorized-flagging):
    rendered in their own trailing section via _render() -- see that
    function's docstring. Present even when reading/courses/project_entries
    are all empty, so a week with genuinely nothing scored but some
    uncategorized items still surfaces them rather than showing the
    generic "nothing on the plan" message."""
    prioritized_project_work = prioritized_project_work or []
    uncategorized_items = uncategorized_items or []
    plan_items = [i for i in classified_items if i["classification"] == "plan_item"]
    project_entries = _build_project_entries(prioritized_project_work, trello_cards, plan_items)
    courses = [i for i in plan_items if "course" in i.get("tags", [])]
    reading = [i for i in plan_items if "course" not in i.get("tags", []) and i.get("matched_card_id") is None]

    if not reading and not courses and not project_entries and not uncategorized_items:
        msg = "📋 <b>Weekly Plan</b>\n\n<i>Nothing on the plan this week."
        if pending_approvals_count > 0:
            msg += f" {pending_approvals_count} proposals pending approval — check Telegram."
        msg += "</i>"
        cost_line = format_cost_line(cost_breakdown)
        if cost_line:
            msg += f"\n\n{cost_line}"
        return msg, {}

    text, item_map = _render(
        reading, courses, project_entries, pending_approvals_count, run_id,
        uncategorized_items=uncategorized_items, cost_breakdown=cost_breakdown,
    )

    if len(text) > MAX_PLAN_TEXT_CHARS:
        budget = REASONING_CHAR_BUDGET
        logger.warning(
            f"format_plan: rendered text {len(text)} chars exceeds the {MAX_PLAN_TEXT_CHARS}-char "
            f"budget -- re-rendering with reasoning capped to {budget} chars per item (run={run_id})"
        )
        text, item_map = _render(
            reading, courses, project_entries, pending_approvals_count, run_id,
            reasoning_budget=budget, uncategorized_items=uncategorized_items, cost_breakdown=cost_breakdown,
        )
        while len(text) > MAX_PLAN_TEXT_CHARS and budget > 20:
            budget //= 2
            logger.warning(f"format_plan: still over budget at {len(text)} chars -- shrinking reasoning cap to {budget} (run={run_id})")
            text, item_map = _render(
                reading, courses, project_entries, pending_approvals_count, run_id,
                reasoning_budget=budget, uncategorized_items=uncategorized_items, cost_breakdown=cost_breakdown,
            )

    return text, item_map
