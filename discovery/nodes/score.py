import json
import logging
import time
import anthropic
from state import DiscoverySubgraphState, ScoredItem, NodeCost
from discovery.seen_items import mark_seen

logger = logging.getLogger(__name__)

TASTE_PROFILE = """
You are scoring bookmarks for an AI/ML engineer focused on agentic AI engineering.

Keep content that is technical, specific, and actionable about any of these topics:
- Agentic frameworks and patterns (LangGraph, LangChain, agent loops, harness engineering)
- Memory systems for AI agents (LangMem, Mem0, vector stores, knowledge graphs)
- LLM tooling, APIs, SDKs, prompt engineering, context engineering
- Evals, observability, tracing, LangSmith
- Distributed systems and infrastructure applicable to AI agents
- AI engineering as a role — skills needed, how teams are structured,
  what the job looks like day-to-day, technical interview prep

Content format doesn't matter — tutorials, opinion pieces, critiques, essays,
walkthroughs, courses, papers, and threads all qualify if the topic is in scope.

Drop content that is:
- Pure job hunting, salary negotiation, or hiring posts
- Generic AI hype with no actionable technical content
- Tool announcements with no explanation of what the tool does
- Community events, meetups, conference announcements
- Design tools, lifestyle, or anything unrelated to AI engineering
"""

ALLOWED_TAGS = {
    "agentic-engineering", "memory-systems", "llm-tooling",
    "evals", "learning-resource", "distributed-systems", "noise",
}

DROPPED_TAG_LOG = "data/dropped_tags.log"

client = anthropic.Anthropic()

BATCH_SIZE = 50


def _log_dropped_tag(tag: str, item_id: str, run_id: str) -> None:
    with open(DROPPED_TAG_LOG, "a") as f:
        f.write(f"{run_id},{item_id},{tag}\n")
    logger.warning(f"Dropped invalid tag '{tag}' for item {item_id} (run={run_id})")


def _validate_tags(item_tags: list[str], item_id: str, run_id: str) -> list[str]:
    invalid = set(item_tags) - ALLOWED_TAGS
    for tag in invalid:
        _log_dropped_tag(tag, item_id, run_id)
    return [t for t in item_tags if t in ALLOWED_TAGS]


def _score_batch(batch: list, offset: int, run_id: str = "unknown") -> tuple[list[ScoredItem], int, int]:
    items_text = "\n\n".join(
        f"[{i}] URL: {item['url']}\nTitle: {item['title']}\nText: {item['text'][:500]}"
        for i, item in enumerate(batch)
    )

    prompt = f"""{TASTE_PROFILE}

Assign 1-3 tags from EXACTLY this list — no other tags are permitted:
agentic-engineering, memory-systems, llm-tooling, evals, learning-resource,
distributed-systems, noise

Score each bookmark below. Return a JSON array with one object per item,
in the same order. Each object must have:
- "index": the item's index number
- "keep": true or false
- "reasoning": one sentence explaining why this should rank higher or lower
  than other kept items today — reference urgency, depth, or how directly it
  applies to active work; do not just describe what the item covers
- "tags": list of 1-3 tags from the permitted list above

Bookmarks to score:
{items_text}

Return only valid JSON. No markdown, no explanation outside the array."""

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    results = json.loads(raw.strip())

    _skip = {"keep", "reasoning", "tags"}
    scored = []
    for r in results:
        item = batch[r["index"]]
        validated_tags = _validate_tags(r["tags"], item["url"], run_id)
        scored.append(ScoredItem(
            **{k: item[k] for k in item if k not in _skip},
            keep=r["keep"],
            reasoning=r["reasoning"],
            tags=validated_tags,
        ))

    return scored, response.usage.input_tokens, response.usage.output_tokens


def score_node(state: DiscoverySubgraphState) -> dict:
    t0 = time.perf_counter()
    items = state["clustered_items"]
    run_id = state.get("run_id", "unknown")

    all_scored: list[ScoredItem] = []
    total_input = 0
    total_output = 0

    for offset in range(0, len(items), BATCH_SIZE):
        batch = items[offset:offset + BATCH_SIZE]
        scored, inp, out = _score_batch(batch, offset, run_id)
        all_scored.extend(scored)
        total_input += inp
        total_output += out
        logger.info(f"scored {offset + len(batch)}/{len(items)}")

    cost_usd = (total_input * 0.00025 + total_output * 0.00125) / 1000

    cost = NodeCost(
        node_name="score_node",
        input_tokens=total_input,
        output_tokens=total_output,
        latency_ms=round((time.perf_counter() - t0) * 1000, 4),
        cost_usd=round(cost_usd, 6),
    )

    mark_seen([item["url"] for item in all_scored])

    return {
        "scored_items": all_scored,
        "costs": [cost],
        "stage": "scored",
    }

if __name__ == "__main__":
    from logging_config import setup_logging
    setup_logging()

    with open("data/clustered_items.json", encoding="utf-8") as f:
        clustered_items = json.load(f)

    state = {"clustered_items": clustered_items}
    result = score_node(state)

    with open("data/scored_items.json", "w", encoding="utf-8") as f:
        json.dump(result["scored_items"], f, ensure_ascii=False, indent=2)

    kept = [x for x in result["scored_items"] if x["keep"]]
    dropped = [x for x in result["scored_items"] if not x["keep"]]
    logger.info(f"Kept: {len(kept)}, Dropped: {len(dropped)}")
    for item in result["scored_items"]:
        logger.info(f"{'KEEP' if item['keep'] else 'DROP'} {item['tags']} — {item['title'][:60]}")
        logger.info(f"  {item['reasoning']}")
    logger.info(f"Cost: {result['costs'][0]}")
    logger.info(f"Saved {len(result['scored_items'])} scored items to data/scored_items.json")