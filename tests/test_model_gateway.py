import json
from unittest.mock import MagicMock

import pytest

from core.model_gateway import (
    ModelBudget,
    ModelGateway,
    ModelRequestTooLarge,
)


def _groq_response(payload: dict, prompt_tokens: int = 100, completion_tokens: int = 40):
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    response.usage.prompt_tokens = prompt_tokens
    response.usage.completion_tokens = completion_tokens
    return response


def test_structured_request_never_exceeds_the_groq_envelope():
    groq = MagicMock()
    gateway = ModelGateway(groq_client=groq, anthropic_client=None)

    with pytest.raises(ModelRequestTooLarge):
        gateway.complete_json(
            task="score",
            prompt="x" * 30_000,
            json_schema={"type": "object"},
        )

    groq.chat.completions.create.assert_not_called()


def test_structured_request_uses_bounded_completion_tokens():
    groq = MagicMock()
    groq.chat.completions.create.return_value = _groq_response({"results": []})
    gateway = ModelGateway(groq_client=groq, anthropic_client=None)

    result = gateway.complete_json(
        task="score",
        prompt="short prompt",
        json_schema={"type": "object"},
    )

    assert result.data == {"results": []}
    assert result.provider == "groq"
    kwargs = groq.chat.completions.create.call_args.kwargs
    assert kwargs["max_completion_tokens"] == 1024


def test_anthropic_fallback_is_used_only_within_the_budget():
    groq = MagicMock()
    groq.chat.completions.create.side_effect = RuntimeError("provider unavailable")
    anthropic = MagicMock()
    anthropic.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"results": []}')],
        usage=MagicMock(input_tokens=100, output_tokens=30),
    )
    budget = ModelBudget(anthropic_limit_usd=0.10)
    gateway = ModelGateway(
        groq_client=groq,
        anthropic_client=anthropic,
        budget=budget,
    )

    result = gateway.complete_json(
        task="score",
        prompt="short prompt",
        json_schema={"type": "object"},
        allow_anthropic_fallback=True,
    )

    assert result.provider == "anthropic"
    assert result.degraded is True
    assert budget.anthropic_spend_usd > 0

