import time
from datetime import datetime, timezone

from core.state import ScoredItem, DailyGraphState, NodeCost
from sunday.memory_store_config import get_store
from telegram.markdown import escape_html

MAX_DIGEST_ITEMS = 15


def format_digest(
    scored_items: list[ScoredItem], run_id: str, uncategorized_items: list[dict] | None = None
) -> tuple[str, dict[int, dict]]:
    """Renders with Telegram HTML parse_mode (see telegram/bot_client.py) --
    NOT Markdown. This function used to escape underscores with MarkdownV2
    syntax (`\\_`) while bot_client.py sent with legacy v1 "Markdown",
    which has no escape mechanism at all -- real LLM-generated reasoning
    text containing a literal "_" could 400 the whole send (same root
    cause found and fixed in sunday/nodes/assemble_plan.py, 2026-07-19;
    see docs/WORKFLOW.md for the full investigation). item_map keeps RAW
    (unescaped) title/text/reasoning -- only the rendered `lines` strings
    are HTML-escaped, at the point of interpolation.

    uncategorized_items (2026-07-22, lightweight-uncategorized-flagging):
    items taste_prefilter couldn't match to any existing topic (below
    discovery/taste_vectors.py's 0.30 threshold) are no longer silently
    dropped -- they're rendered in a trailing section, numbered
    CONTINUING the same item_map (not a separate map), so a reply
    naming a new tag for one of these resolves through the exact same
    telegram/feedback_router.py path as any other digest reply -- no
    changes needed there."""
    uncategorized_items = uncategorized_items or []
    kept = [item for item in scored_items if item["keep"]]
    total_scored = len(scored_items)
    total_kept = len(kept)

    if not kept and not uncategorized_items:
        return "🤖 <b>Daily Digest</b>\n\n<i>Nothing new today.</i>", {}

    lines = ["🤖 <b>Daily Digest</b>", ""]
    item_map: dict[int, dict] = {}
    counter = 1

    if kept:
        for item in kept[:MAX_DIGEST_ITEMS]:
            title = (item.get("title") or item["text"])[:80]
            url = item["url"]
            tags = " ".join(f"<code>{escape_html(tag)}</code>" for tag in item["tags"])

            lines.append(f'{counter}. <a href="{escape_html(url)}">{escape_html(title)}</a>')
            lines.append(f"   Tags: {tags}")
            lines.append(f"   <i>{escape_html(item['reasoning'])}</i>")
            lines.append("")

            item_map[counter] = {
                "url": url,
                "title": title,
                "text": item["text"],
                "tags": item["tags"],
                "reasoning": item["reasoning"],
            }
            counter += 1
    else:
        lines.append("<i>Nothing new today.</i>")
        lines.append("")

    if uncategorized_items:
        lines.append(f"<b>{len(uncategorized_items)} item(s) didn't match any existing topic</b>")
        for item in uncategorized_items:
            title = (item.get("title") or item["text"])[:80]
            url = item["url"]
            best_tag = item["best_tag"]
            score = item["similarity_score"]
            reasoning = f"closest existing tag: {best_tag} (cosine={score:.3f})"

            lines.append(f'{counter}. <a href="{escape_html(url)}">{escape_html(title)}</a>')
            lines.append(f"   <i>{escape_html(reasoning)}</i>")
            lines.append("")

            item_map[counter] = {
                "url": url,
                "title": title,
                "text": item["text"],
                "tags": ["uncategorized"],
                "reasoning": reasoning,
            }
            counter += 1

    lines.append(
        f"<i>{total_scored} scored · {total_kept} kept · "
        f"{len(uncategorized_items)} uncategorized · run: {run_id[:8]}</i>"
    )

    return "\n".join(lines), item_map


def assemble_digest(state: DailyGraphState) -> dict:
    t0 = time.monotonic()
    text, item_map = format_digest(
        state["scored_items"], state["run_id"], uncategorized_items=state["uncategorized_items"]
    )

    generated_at = datetime.now(timezone.utc).isoformat()

    get_store().put(
        ("companion",),
        "current_daily_digest",
        {
            "run_id": state["run_id"],
            "digest_text": text,
            "generated_at": generated_at,
        },
    )

    total_kept = len([i for i in state["scored_items"] if i["keep"]])
    village_summary = f"{total_kept} item(s) kept" if total_kept else "no new content"
    get_store().put(
        ("village",),
        f"event:{generated_at}",
        {
            "agent": "weekly-intel",
            "event_type": "digest_ready",
            "summary": village_summary,
            "timestamp": generated_at,
        },
    )

    cost = NodeCost(
        node_name="assemble_digest",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=round((time.monotonic() - t0) * 1000, 2),
    )
    return {
        "digest_text": text,
        "digest_item_map": item_map,
        "costs": [cost],
    }

