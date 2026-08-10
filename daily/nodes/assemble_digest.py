import time
from datetime import datetime, timezone

from core.state import ScoredItem, DailyGraphState, NodeCost
from core.observability import cost_breakdown_by_provider
from telegram.markdown import escape_html, format_cost_line
from discovery.story_clusterer import diversify

MAX_DIGEST_ITEMS = 10


def _is_non_actionable_source_error(error: str) -> bool:
    return "MarkTechPost:" in error and "bot-challenge" in error


def _actionable_errors(errors: list[str]) -> list[str]:
    return [error for error in errors if not _is_non_actionable_source_error(error)]


def format_digest(
    scored_items: list[ScoredItem], run_id: str, uncategorized_items: list[dict] | None = None,
    cost_breakdown: dict[str, float] | None = None,
    outcome_status: str = "no_new_stories",
) -> tuple[str, dict[int, dict]]:
    """Renders with Telegram HTML parse_mode (see telegram/bot_client.py) --
    NOT Markdown. This function used to escape underscores with MarkdownV2
    syntax (`\\_`) while bot_client.py sent with legacy v1 "Markdown",
    which has no escape mechanism at all -- real LLM-generated reasoning
    text containing a literal "_" could 400 the whole send (same root
    cause found and fixed in saturday/nodes/assemble_plan.py, 2026-07-19;
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
    all_kept = [item for item in scored_items if item["keep"]]
    kept = diversify(all_kept, limit=MAX_DIGEST_ITEMS)
    total_scored = len(scored_items)
    total_kept = len(all_kept)
    cost_line = format_cost_line(cost_breakdown)

    if not kept and not uncategorized_items:
        messages = {
            "sources_degraded": "Source outage: the run could not establish that there were no new stories.",
            "provider_degraded": "Model provider degraded: no digest could be selected reliably.",
            "all_filtered": "New candidates were collected, but all were filtered from today’s digest.",
            "pipeline_failed": "The intelligence pipeline failed before a reliable digest was produced.",
            "no_new_stories": "Nothing new today.",
        }
        text = f"🤖 <b>Daily Digest</b>\n\n<i>{messages[outcome_status]}</i>"
        if cost_line:
            text += f"\n\n{cost_line}"
        return text, {}

    lines = ["🤖 <b>Daily Digest</b>", ""]
    if outcome_status == "provider_degraded":
        lines.extend(["⚠️ <i>Model provider fallback changed this run’s behavior.</i>", ""])
    elif outcome_status == "sources_degraded":
        lines.extend(["⚠️ <i>One or more sources were degraded; coverage may be incomplete.</i>", ""])
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
            supporting = item.get("supporting_sources", [])
            if supporting:
                lines.append("   Also covered by: " + ", ".join(
                    f'<a href="{escape_html(source["url"])}">{escape_html(source["source"])}</a>'
                    for source in supporting
                ))
            lines.append("")

            item_map[counter] = {
                "url": url,
                "title": title,
                "text": item["text"],
                "tags": item["tags"],
                "reasoning": item["reasoning"],
                "supporting_sources": supporting,
            }
            counter += 1
    else:
        lines.append("<i>Nothing new today.</i>")
        lines.append("")

    uncategorized_slots = max(0, MAX_DIGEST_ITEMS - min(total_kept, MAX_DIGEST_ITEMS))
    shown_uncategorized = uncategorized_items[:uncategorized_slots]
    if shown_uncategorized:
        lines.append(
            f"<b>{len(uncategorized_items)} item(s) didn't match any existing topic</b>"
        )
        for item in shown_uncategorized:
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

    # shown must be the REAL rendered count, not total_kept -- total_kept
    # alone silently lied here for months (confirmed real, 2026-07-23: a
    # real run with 22 kept items rendered a footer claiming "22 kept"
    # while only the first MAX_DIGEST_ITEMS=15 were actually visible
    # above it). "shown/kept" makes the truncation itself visible in the
    # message text instead of only discoverable by cross-referencing
    # node_summary.
    shown = min(total_kept, MAX_DIGEST_ITEMS)
    lines.append(
        f"<i>{total_scored} scored · {shown}/{total_kept} shown · "
        f"{len(uncategorized_items)} uncategorized · run: {run_id[:8]}</i>"
    )
    if cost_line:
        lines.append(cost_line)

    return "\n".join(lines), item_map


def assemble_digest(state: DailyGraphState) -> dict:
    t0 = time.monotonic()
    # Real per-run $ cost, broken out by provider (2026-07-26) -- state["costs"]
    # is already complete by this point in the daily graph (discovery_subgraph,
    # the only cost-incurring stage, has already run; send_telegram_digest,
    # the only node left, is free), so this is the true run total, not a
    # partial figure.
    cost_breakdown = cost_breakdown_by_provider(state["costs"])
    errors = _actionable_errors(state.get("errors", []))
    if any("provider_degraded" in error for error in errors):
        status = "provider_degraded"
    elif errors:
        status = "sources_degraded"
    elif state["scored_items"] and not any(item["keep"] for item in state["scored_items"]):
        status = "all_filtered"
    else:
        status = "no_new_stories"
    text, item_map = format_digest(
        state["scored_items"], state["run_id"], uncategorized_items=state["uncategorized_items"],
        cost_breakdown=cost_breakdown,
        outcome_status=status,
    )

    generated_at = datetime.now(timezone.utc).isoformat()

    cost = NodeCost(
        node_name="assemble_digest",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=round((time.monotonic() - t0) * 1000, 2),
    )
    return {
        "digest_text": text,
        "digest_generated_at": generated_at,
        "digest_item_map": item_map,
        "digest_status": status,
        "costs": [cost],
    }

