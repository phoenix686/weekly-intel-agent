"""Token-safe, budget-aware access to structured model completions.

Callers describe the task, prompt, and JSON schema. This module owns the
provider request envelope, accounting, and the explicitly-authorized
Anthropic fallback. Batching decisions that change application semantics
remain with the caller, which can split on ``ModelRequestTooLarge``.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from typing import Any

import anthropic

from core.groq_client import GROQ_MODEL, get_groq_client, groq_cost

GROQ_TOTAL_TOKEN_LIMIT = 8_000
GROQ_SAFE_TOKEN_LIMIT = 7_200
DEFAULT_MAX_COMPLETION_TOKENS = 1_024

# Configurable rather than presented as timeless provider pricing. These
# defaults are conservative enough for enforcing the small per-run caps.
ANTHROPIC_INPUT_USD_PER_MILLION = float(
    os.getenv("ANTHROPIC_INPUT_USD_PER_MILLION", "1.0")
)
ANTHROPIC_OUTPUT_USD_PER_MILLION = float(
    os.getenv("ANTHROPIC_OUTPUT_USD_PER_MILLION", "5.0")
)
ANTHROPIC_FALLBACK_MODEL = os.getenv("ANTHROPIC_FALLBACK_MODEL", "claude-haiku-4-5")


class ModelGatewayError(RuntimeError):
    """Base error for provider-independent model failures."""


class ModelRequestTooLarge(ModelGatewayError):
    """The prompt cannot fit inside the configured provider envelope."""


class ModelBudgetExceeded(ModelGatewayError):
    """A requested fallback would exceed the run's Anthropic budget."""


@dataclass
class ModelBudget:
    anthropic_limit_usd: float
    anthropic_spend_usd: float = 0.0

    @property
    def anthropic_remaining_usd(self) -> float:
        return max(0.0, self.anthropic_limit_usd - self.anthropic_spend_usd)

    def reserve_anthropic(self, estimated_cost_usd: float) -> None:
        if estimated_cost_usd > self.anthropic_remaining_usd:
            raise ModelBudgetExceeded(
                "Anthropic fallback would exceed the configured run budget "
                f"(${estimated_cost_usd:.4f} requested, "
                f"${self.anthropic_remaining_usd:.4f} remaining)"
            )

    def record_anthropic(self, actual_cost_usd: float) -> None:
        self.anthropic_spend_usd += actual_cost_usd


@dataclass(frozen=True)
class ModelResult:
    data: dict[str, Any]
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    degraded: bool = False


def estimate_tokens(text: str) -> int:
    """Conservative provider-independent estimate for request gating."""
    return max(1, math.ceil(len(text) / 3.5))


def anthropic_cost(input_tokens: int, output_tokens: int) -> float:
    return round(
        input_tokens * ANTHROPIC_INPUT_USD_PER_MILLION / 1_000_000
        + output_tokens * ANTHROPIC_OUTPUT_USD_PER_MILLION / 1_000_000,
        6,
    )


def _parse_provider_json(raw: str, *, provider: str, task: str) -> dict[str, Any]:
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise ModelGatewayError(f"{task} {provider} returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ModelGatewayError(f"{task} {provider} returned JSON {type(data).__name__}, expected object")
    return data


class ModelGateway:
    def __init__(
        self,
        *,
        groq_client: Any,
        anthropic_client: Any | None,
        budget: ModelBudget | None = None,
        sleep: Any = time.sleep,
        max_retries: int = 3,
    ) -> None:
        self._groq = groq_client
        self._anthropic = anthropic_client
        self._budget = budget or ModelBudget(anthropic_limit_usd=0.0)
        self._sleep = sleep
        self._max_retries = max_retries

    @property
    def anthropic_spend_usd(self) -> float:
        """Serializable run-budget state for the next graph node/resume."""
        return self._budget.anthropic_spend_usd

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        value = getattr(exc, "status_code", None)
        if value is None:
            value = getattr(getattr(exc, "response", None), "status_code", None)
        return value

    def complete_json(
        self,
        *,
        task: str,
        prompt: str,
        json_schema: dict[str, Any],
        max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
        allow_anthropic_fallback: bool = False,
    ) -> ModelResult:
        prompt_tokens = estimate_tokens(prompt)
        if prompt_tokens + max_completion_tokens > GROQ_SAFE_TOKEN_LIMIT:
            raise ModelRequestTooLarge(
                f"{task} request estimated at {prompt_tokens + max_completion_tokens} "
                f"tokens, above the {GROQ_SAFE_TOKEN_LIMIT}-token safe envelope"
            )

        response = None
        last_error = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._groq.chat.completions.create(
                    model=GROQ_MODEL,
                    temperature=0,
                    max_completion_tokens=max_completion_tokens,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": task.replace("-", "_"),
                            "strict": True,
                            "schema": json_schema,
                        },
                    },
                )
                break
            except Exception as exc:
                status = self._status_code(exc)
                if status == 413:
                    raise ModelRequestTooLarge(f"{task} provider rejected request as too large") from exc
                last_error = exc
                if status not in {429, 500, 502, 503, 504} or attempt >= self._max_retries:
                    break
                self._sleep(0.5 * (2 ** attempt))
        if response is not None:
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens
            return ModelResult(
                data=_parse_provider_json(
                    response.choices[0].message.content,
                    provider="Groq",
                    task=task,
                ),
                provider="groq",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=groq_cost(input_tokens, output_tokens),
            )
        if not allow_anthropic_fallback or self._anthropic is None:
            raise ModelGatewayError(f"{task} Groq request failed: {last_error}") from last_error
        return self._complete_anthropic(
            task=task, prompt=prompt, json_schema=json_schema,
            max_completion_tokens=max_completion_tokens,
        )

    def _complete_anthropic(
        self,
        *,
        task: str,
        prompt: str,
        json_schema: dict[str, Any],
        max_completion_tokens: int,
    ) -> ModelResult:
        schema_text = json.dumps(json_schema, separators=(",", ":"))
        fallback_prompt = f"{prompt}\n\nReturn only JSON matching this schema:\n{schema_text}"
        estimated_input = math.ceil(estimate_tokens(fallback_prompt) * 1.15)
        estimated_cost = anthropic_cost(estimated_input, max_completion_tokens)
        self._budget.reserve_anthropic(estimated_cost)

        response = self._anthropic.messages.create(
            model=ANTHROPIC_FALLBACK_MODEL,
            max_tokens=max_completion_tokens,
            messages=[
                {
                    "role": "user",
                    "content": (
                        fallback_prompt
                    ),
                }
            ],
        )
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        actual_cost = anthropic_cost(input_tokens, output_tokens)
        self._budget.record_anthropic(actual_cost)
        return ModelResult(
            data=_parse_provider_json(
                response.content[0].text,
                provider="Anthropic fallback",
                task=task,
            ),
            provider="anthropic",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=actual_cost,
            degraded=True,
        )


def get_model_gateway(
    *,
    pipeline: str,
    anthropic_spend_usd: float = 0.0,
) -> ModelGateway:
    """Build a gateway while preserving the pipeline's persisted run budget."""
    if pipeline not in {"daily", "saturday"}:
        raise ValueError("pipeline must be daily or saturday")
    limit = 0.10 if pipeline == "daily" else 0.50
    return ModelGateway(
        groq_client=get_groq_client(),
        anthropic_client=anthropic.Anthropic(),
        budget=ModelBudget(
            anthropic_limit_usd=limit,
            anthropic_spend_usd=anthropic_spend_usd,
        ),
    )
