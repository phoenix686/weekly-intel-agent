import time
from datetime import datetime, timezone

from state import ScoredItem, DailyGraphState, NodeCost
from sunday.memory_store_config import get_store
from telegram.markdown import escape_html

MAX_DIGEST_ITEMS = 15


def format_digest(scored_items: list[ScoredItem], run_id: str) -> tuple[str, dict[int, dict]]:
    """Renders with Telegram HTML parse_mode (see telegram/bot_client.py) --
    NOT Markdown. This function used to escape underscores with MarkdownV2
    syntax (`\\_`) while bot_client.py sent with legacy v1 "Markdown",
    which has no escape mechanism at all -- real LLM-generated reasoning
    text containing a literal "_" could 400 the whole send (same root
    cause found and fixed in sunday/nodes/assemble_plan.py, 2026-07-19;
    see docs/WORKFLOW.md for the full investigation). item_map keeps RAW
    (unescaped) title/text/reasoning -- only the rendered `lines` strings
    are HTML-escaped, at the point of interpolation."""
    kept = [item for item in scored_items if item["keep"]]
    total_scored = len(scored_items)
    total_kept = len(kept)

    if not kept:
        return "🤖 <b>Daily Digest</b>\n\n<i>Nothing new today.</i>", {}

    lines = ["🤖 <b>Daily Digest</b>", ""]
    item_map: dict[int, dict] = {}

    for i, item in enumerate(kept[:MAX_DIGEST_ITEMS], 1):
        title = (item.get("title") or item["text"])[:80]
        url = item["url"]
        tags = " ".join(f"<code>{escape_html(tag)}</code>" for tag in item["tags"])

        lines.append(f'{i}. <a href="{escape_html(url)}">{escape_html(title)}</a>')
        lines.append(f"   Tags: {tags}")
        lines.append(f"   <i>{escape_html(item['reasoning'])}</i>")
        lines.append("")

        item_map[i] = {
            "url": url,
            "title": title,
            "text": item["text"],
            "tags": item["tags"],
            "reasoning": item["reasoning"],
        }

    lines.append(f"<i>{total_scored} scored · {total_kept} kept · run: {run_id[:8]}</i>")

    return "\n".join(lines), item_map


def assemble_digest(state: DailyGraphState) -> dict:
    t0 = time.monotonic()
    text, item_map = format_digest(state["scored_items"], state["run_id"])

    get_store().put(
        ("companion",),
        "current_daily_digest",
        {
            "run_id": state["run_id"],
            "digest_text": text,
            "generated_at": datetime.now(timezone.utc).isoformat(),
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

