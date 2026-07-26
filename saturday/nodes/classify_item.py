# saturday/nodes/classify_item.py
import time
import json
import logging
import uuid
import anthropic
import groq

from core.state import SaturdayGraphState, NodeCost
from saturday.memory_store_config import get_store
from core.observability import record_node_summary
from core.groq_client import get_groq_client, GROQ_MODEL, groq_cost

logger = logging.getLogger(__name__)

# Kept intact but unused -- rollback safety net for the 2026-07-26 Groq
# swap (see core/groq_client.py's docstring). ANTHROPIC_API_KEY stays in
# .env.example for the same reason. Not called anywhere;
# _classify_item_anthropic_legacy below is the dead code path that used
# to call this.
client = anthropic.Anthropic()

_CLASSIFICATION_LOG_NAMESPACE = ("weekly_intel", "classification_log")


def _log_classifications(items: list[dict], run_id: str) -> None:
    """Log every classify_item decision (plan_item AND project_proposal
    alike, per closeout-spec.md Section 4 point 1) to the store, so
    plan_item decisions -- the majority of all items, since they bypass
    the approval gate by design -- stop leaving zero trace. A failed
    write here must never block the node's real return value (per
    closeout-spec.md Section 7's reliability requirement), so every
    failure is caught and logged, not raised."""
    store = get_store()
    for item in items:
        try:
            store.put(
                _CLASSIFICATION_LOG_NAMESPACE,
                str(uuid.uuid4()),
                {
                    "item_id": item["url"],
                    "decision": item["classification"],
                    "proposal_type": item["proposal_type"],
                    "run_id": run_id,
                },
            )
        except Exception as e:
            logger.warning(f"classify_item: classification_log write failed for {item['url']} (run={run_id}): {e}")

CLASSIFY_PROMPT = """You are classifying scored, Trello-correlated items into two categories for a weekly planning agent.

All items below have already been vetted by a prior scoring stage (keep=True) and judged worth including. Do not re-evaluate whether any item is noise, irrelevant, or lacks quality — that decision is already made and is not yours to revisit. Your only job is to route each item: is it a routine plan_item (reading material or continuation of existing tracked work), or a project_proposal (structurally new scope)?

For each item, decide:

1. classification: either "plan_item" or "project_proposal"
   - "plan_item" = routine content for the weekly reading/learning plan: articles, courses, tutorials, or work directly continuing an EXISTING tracked project (matched_card_id is not null)
   - "project_proposal" = something structurally new: a new project idea, or a genuine expansion of scope beyond what an existing card currently covers

2. proposal_type: only set if classification is "project_proposal"
   - "extend" = relates to an existing card (matched_card_id is not null) but represents new scope/direction for it, not just routine continuation
   - "new" = no existing card relates to this at all (matched_card_id is null)
   - null if classification is "plan_item"

Important: having a matched_card_id does NOT automatically make something a plan_item — judge whether the item represents routine work on that project (plan_item) or a structural expansion of it (project_proposal, extend). Likewise, having no matched_card_id does NOT automatically make something a project_proposal — a standalone article or course with no project relevance is still just a plan_item (reading material), not a proposal.

If multiple items relate to the same emerging idea, consider proposing that as ONE project_proposal rather than several redundant ones — flag which item_ids relate to the same proposal in your reasoning.

Existing Trello context (items already matched against these):
{cards_block}

Items to classify:
{items_block}

Return ONLY a JSON array, one object per item, in this exact shape:
[
  {{"item_id": "...", "classification": "plan_item" or "project_proposal", "proposal_type": "extend" or "new" or null, "classification_reasoning": "brief reason"}}
]"""

# Real bug hit running scripts/compare_groq_harness.py for real: gpt-oss-120b's
# first classify_item attempt returned a bare JSON array instead of the
# required {"results": [...]} object wrapper, failing schema validation
# even under strict:true -- root cause was the prompt's own trailing
# "Return ONLY a JSON array..." instruction with a bare-array example.
# Same substitution applied here, production-side.
_GROQ_TRAILING_INSTRUCTION_OLD = (
    'Return ONLY a JSON array, one object per item, in this exact shape:\n'
    '[\n'
    '  {"item_id": "...", "classification": "plan_item" or "project_proposal", "proposal_type": "extend" or "new" or null, "classification_reasoning": "brief reason"}\n'
    ']'
)
_GROQ_TRAILING_INSTRUCTION_NEW = (
    'Return one object per item, with: item_id, classification ("plan_item" or '
    '"project_proposal"), proposal_type ("extend" or "new" or null), '
    'classification_reasoning (brief reason).'
)

_CLASSIFY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "classification": {"type": "string", "enum": ["plan_item", "project_proposal"]},
                    "proposal_type": {"type": ["string", "null"], "enum": ["extend", "new", None]},
                    "classification_reasoning": {"type": "string"},
                },
                "required": ["item_id", "classification", "proposal_type", "classification_reasoning"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _format_cards(cards: list[dict]) -> str:
    return "\n".join(
        f"- id={c['card_id']} | list={c['list_name']} | {c['name']}"
        for c in cards
    )


def _format_items(items: list[dict]) -> str:
    return "\n".join(
        f"- id={i['url']} | matched_card_id={i.get('matched_card_id')} | "
        f"tags={i['tags']} | score_reasoning={i['reasoning'][:150]}"
        for i in items
    )


def _parse_json_response(raw: str) -> list:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw.strip())


VALID_CLASSIFICATIONS = {"plan_item", "project_proposal"}
VALID_PROPOSAL_TYPES = {"extend", "new", None}


def _validate_classification(entry: dict, item_id: str, run_id: str) -> dict:
    """Defensive validation — fall back to plan_item on any malformed entry
    rather than letting a bad classification silently reach await_approval
    or worse, get miscategorized as a proposal it shouldn't be."""
    classification = entry.get("classification")
    proposal_type = entry.get("proposal_type")

    if classification not in VALID_CLASSIFICATIONS:
        logger.warning(f"Invalid classification '{classification}' for item {item_id} (run={run_id}); defaulting to plan_item")
        return {"classification": "plan_item", "proposal_type": None,
                "classification_reasoning": entry.get("classification_reasoning", "fallback: invalid classification")}

    if classification == "plan_item" and proposal_type is not None:
        proposal_type = None  # enforce invariant regardless of what the model returned

    if classification == "project_proposal" and proposal_type not in {"extend", "new"}:
        logger.warning(f"Invalid proposal_type '{proposal_type}' for item {item_id} (run={run_id}); defaulting to 'new'")
        proposal_type = "new"

    return {
        "classification": classification,
        "proposal_type": proposal_type,
        "classification_reasoning": entry.get("classification_reasoning", ""),
    }


def _classify_item_anthropic_legacy(state: SaturdayGraphState) -> dict:
    """Pre-2026-07-26 Haiku implementation. Not called anywhere -- kept
    verbatim for rollback safety, see core/groq_client.py's docstring."""
    t0 = time.perf_counter()

    prompt = CLASSIFY_PROMPT.format(
        cards_block=_format_cards(state["trello_cards"]),
        items_block=_format_items(state["correlated_items"]),
    )

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    try:
        classifications = _parse_json_response(response.content[0].text)
    except json.JSONDecodeError:
        logger.warning(f"classify_item: first parse failed, retrying (run_id={state['run_id']})")
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
            classifications = _parse_json_response(retry_response.content[0].text)
        except json.JSONDecodeError:
            logger.error(f"classify_item: retry parse also failed (run_id={state['run_id']})")
            cost = NodeCost(
                node_name="classify_item",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=round((input_tokens * 0.00025 + output_tokens * 0.00125) / 1000, 6),
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                provider="anthropic",
            )
            fallback_items = [
                {**item, "classification": "plan_item", "proposal_type": None,
                 "classification_reasoning": "fallback: JSON parse failed"}
                for item in state["correlated_items"]
            ]
            _log_classifications(fallback_items, state["run_id"])
            record_node_summary(
                run_id=state["run_id"], node_name="classify_item",
                items_in=len(state["correlated_items"]), items_out=0, cost_usd=cost["cost_usd"],
                duration_seconds=round(time.perf_counter() - t0, 3),
                error_summary="JSON parse failed after retry",
            )
            return {
                "classified_items": fallback_items,
                "pending_approvals": [],
                "costs": [cost],
                "errors": state["errors"] + [f"classify_item JSON parse failed after retry (run_id={state['run_id']})"],
            }

    class_by_id = {c["item_id"]: c for c in classifications}

    classified_items = []
    pending_approvals = []
    for item in state["correlated_items"]:
        entry = class_by_id.get(item["url"], {})
        validated = _validate_classification(entry, item["url"], state["run_id"])
        classified_item = {**item, **validated}
        classified_items.append(classified_item)
        if validated["classification"] == "project_proposal":
            pending_approvals.append(classified_item)

    logger.info(
        f"classify_item: {len(classified_items) - len(pending_approvals)} plan_items, "
        f"{len(pending_approvals)} proposals (run_id={state['run_id']})"
    )

    _log_classifications(classified_items, state["run_id"])

    cost = NodeCost(
        node_name="classify_item",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round((input_tokens * 0.00025 + output_tokens * 0.00125) / 1000, 6),
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        provider="anthropic",
    )

    record_node_summary(
        run_id=state["run_id"], node_name="classify_item",
        items_in=len(state["correlated_items"]), items_out=len(pending_approvals), cost_usd=cost["cost_usd"],
        duration_seconds=round(time.perf_counter() - t0, 3),
    )

    return {
        "classified_items": classified_items,
        "pending_approvals": pending_approvals,
        "costs": [cost],
    }


def classify_item(state: SaturdayGraphState) -> dict:
    t0 = time.perf_counter()

    prompt = CLASSIFY_PROMPT.format(
        cards_block=_format_cards(state["trello_cards"]),
        items_block=_format_items(state["correlated_items"]),
    ).replace(_GROQ_TRAILING_INSTRUCTION_OLD, _GROQ_TRAILING_INSTRUCTION_NEW)

    groq_client = get_groq_client()

    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0,
            max_completion_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "classify_item", "strict": True, "schema": _CLASSIFY_JSON_SCHEMA},
            },
        )
    except groq.APIError as e:
        logger.error(f"classify_item: Groq call failed after retries (run_id={state['run_id']}): {e}")
        cost = NodeCost(
            node_name="classify_item",
            input_tokens=0, output_tokens=0,
            cost_usd=0.0,
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            provider="groq",
            error=str(e),
        )
        fallback_items = [
            {**item, "classification": "plan_item", "proposal_type": None,
             "classification_reasoning": "fallback: Groq call failed after retries"}
            for item in state["correlated_items"]
        ]
        _log_classifications(fallback_items, state["run_id"])
        record_node_summary(
            run_id=state["run_id"], node_name="classify_item",
            items_in=len(state["correlated_items"]), items_out=0, cost_usd=0.0,
            duration_seconds=round(time.perf_counter() - t0, 3),
            error_summary="Groq API call failed after retries",
        )
        return {
            "classified_items": fallback_items,
            "pending_approvals": [],
            "costs": [cost],
            "errors": state["errors"] + [f"classify_item Groq call failed after retries (run_id={state['run_id']}): {e}"],
        }

    input_tokens = response.usage.prompt_tokens
    output_tokens = response.usage.completion_tokens

    try:
        classifications = json.loads(response.choices[0].message.content)["results"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"classify_item: malformed structured-output response (run_id={state['run_id']}): {e}")
        cost = NodeCost(
            node_name="classify_item",
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=groq_cost(input_tokens, output_tokens),
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            provider="groq",
        )
        fallback_items = [
            {**item, "classification": "plan_item", "proposal_type": None,
             "classification_reasoning": "fallback: malformed structured-output response"}
            for item in state["correlated_items"]
        ]
        _log_classifications(fallback_items, state["run_id"])
        record_node_summary(
            run_id=state["run_id"], node_name="classify_item",
            items_in=len(state["correlated_items"]), items_out=0, cost_usd=cost["cost_usd"],
            duration_seconds=round(time.perf_counter() - t0, 3),
            error_summary="malformed structured-output response",
        )
        return {
            "classified_items": fallback_items,
            "pending_approvals": [],
            "costs": [cost],
            "errors": state["errors"] + [f"classify_item malformed structured-output response (run_id={state['run_id']}): {e}"],
        }

    class_by_id = {c["item_id"]: c for c in classifications}

    classified_items = []
    pending_approvals = []
    for item in state["correlated_items"]:
        entry = class_by_id.get(item["url"], {})
        validated = _validate_classification(entry, item["url"], state["run_id"])
        classified_item = {**item, **validated}
        classified_items.append(classified_item)
        if validated["classification"] == "project_proposal":
            pending_approvals.append(classified_item)

    logger.info(
        f"classify_item: {len(classified_items) - len(pending_approvals)} plan_items, "
        f"{len(pending_approvals)} proposals (run_id={state['run_id']})"
    )

    _log_classifications(classified_items, state["run_id"])

    cost = NodeCost(
        node_name="classify_item",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=groq_cost(input_tokens, output_tokens),
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        provider="groq",
    )

    # items_out = proposal count, not total classified_items -- nothing is
    # dropped here either; the real judgment call is plan_item-vs-proposal,
    # so that's what "dropped" (= plan_items) should reflect.
    record_node_summary(
        run_id=state["run_id"], node_name="classify_item",
        items_in=len(state["correlated_items"]), items_out=len(pending_approvals), cost_usd=cost["cost_usd"],
        duration_seconds=round(time.perf_counter() - t0, 3),
    )

    return {
        "classified_items": classified_items,
        "pending_approvals": pending_approvals,
        "costs": [cost],
    }
