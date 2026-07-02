import time
from state import ScoredItem, DailyGraphState, NodeCost

MAX_DIGEST_ITEMS = 15


def format_digest(scored_items: list[ScoredItem], run_id: str) -> str:
    kept = [item for item in scored_items if item["keep"]]
    total_scored = len(scored_items)
    total_kept = len(kept)

    if not kept:
        return "🤖 *Daily Digest*\n\n_Nothing new today._"

    lines = ["🤖 *Daily Digest*", ""]

    for i, item in enumerate(kept[:MAX_DIGEST_ITEMS], 1):
        title = item["title"][:80]
        url = item["url"]
        tags = " ".join(f"`{tag}`" for tag in item["tags"])
        reasoning = item["reasoning"].replace("_", r"\_")

        lines.append(f"{i}. [{title}]({url})")
        lines.append(f"   Tags: {tags}")
        lines.append(f"   _{reasoning}_")
        lines.append("")

    lines.append(f"_{total_scored} scored · {total_kept} kept · run: {run_id[:8]}_")

    return "\n".join(lines)


def assemble_digest(state: DailyGraphState) -> dict:
    t0 = time.monotonic()
    text = format_digest(state["scored_items"], state["run_id"])
    cost = NodeCost(
        node_name="assemble_digest",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=round((time.monotonic() - t0) * 1000, 2),
    )
    return {
        "digest_text": text,
        "costs": [cost],
    }

