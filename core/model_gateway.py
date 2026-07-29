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
from dataclasses import dataclass
from typing import Any

from core.groq_client import GROQ_MODEL, groq_cost

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


class ModelGateway:
    def __init__(
        self,
        *,
        groq_client: Any,
        anthropic_client: Any | None,
        budget: ModelBudget | None = None,
    ) -> None:
        self._groq = groq_client
        self._anthropic = anthropic_client
        self._budget = budget or ModelBudget(anthropic_limit_usd=0.0)

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
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens
            return ModelResult(
                data=json.loads(response.choices[0].message.content),
                provider="groq",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=groq_cost(input_tokens, output_tokens),
            )
        except ModelRequestTooLarge:
            raise
        except Exception as exc:
            if not allow_anthropic_fallback or self._anthropic is None:
                raise ModelGatewayError(f"{task} Groq request failed: {exc}") from exc
            return self._complete_anthropic(
                task=task,
                prompt=prompt,
                json_schema=json_schema,
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
        estimated_input = estimate_tokens(prompt)
        estimated_cost = anthropic_cost(estimated_input, max_completion_tokens)
        self._budget.reserve_anthropic(estimated_cost)

        response = self._anthropic.messages.create(
            model=ANTHROPIC_FALLBACK_MODEL,
            max_tokens=max_completion_tokens,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"{prompt}\n\nReturn only JSON matching this schema:\n"
                        f"{json.dumps(json_schema, separators=(',', ':'))}"
                    ),
                }
            ],
        )
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        actual_cost = anthropic_cost(input_tokens, output_tokens)
        self._budget.record_anthropic(actual_cost)
        return ModelResult(
            data=json.loads(response.content[0].text.strip()),
            provider="anthropic",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=actual_cost,
            degraded=True,
        )

