import time
from datetime import datetime, timezone

from state import ScoredItem, DailyGraphState, NodeCost
from sunday.memory_store_config import get_store

MAX_DIGEST_ITEMS = 15


def format_digest(scored_items: list[ScoredItem], run_id: str) -> tuple[str, dict[int, dict]]:
    kept = [item for item in scored_items if item["keep"]]
    total_scored = len(scored_items)
    total_kept = len(kept)

    if not kept:
        return "🤖 *Daily Digest*\n\n_Nothing new today._", {}

    lines = ["🤖 *Daily Digest*", ""]
    item_map: dict[int, dict] = {}

    for i, item in enumerate(kept[:MAX_DIGEST_ITEMS], 1):
        title = (item.get("title") or item["text"])[:80]
        url = item["url"]
        tags = " ".join(f"`{tag}`" for tag in item["tags"])
        reasoning = item["reasoning"].replace("_", r"\_")

        lines.append(f"{i}. [{title}]({url})")
        lines.append(f"   Tags: {tags}")
        lines.append(f"   _{reasoning}_")
        lines.append("")

        item_map[i] = {
            "url": url,
            "title": title,
            "text": item["text"],
            "tags": item["tags"],
            "reasoning": item["reasoning"],
        }

    lines.append(f"_{total_scored} scored · {total_kept} kept · run: {run_id[:8]}_")

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

