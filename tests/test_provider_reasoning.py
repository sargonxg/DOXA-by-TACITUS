import json

import httpx
import pytest

from doxa.providers import CLOUDFLARE_MODEL, CloudflareProvider

GPT = "@cf/openai/gpt-oss-120b"


def client_capture(monkeypatch):
    captured = []
    original = httpx.AsyncClient
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"model": GPT, "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": '{"decision":"uncertain"}'}}],
                                        "usage": {"prompt_tokens": 300, "completion_tokens": 600,
                                                  "completion_tokens_details": {"reasoning_tokens": 500}}})
    monkeypatch.setattr("doxa.providers.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    return captured


@pytest.mark.asyncio
async def test_explicit_gpt_reasoning_reserves_allowance_but_bills_actual_receipt_once(monkeypatch):
    captured = client_capture(monkeypatch)
    provider = CloudflareProvider({"model": GPT, "reasoning_effort": "low", "reasoning_token_allowance": 2048})
    assert provider.estimate_cost(300, 600) == pytest.approx((300 * .35 + (600 + 2048) * .75) / 1_000_000)
    result = await provider.generate([{"role": "user", "content": "A public test source."}], {"type": "object"}, max_tokens=600)
    assert captured[0]["reasoning_effort"] == "low" and captured[0]["max_tokens"] == 2648
    assert result["usage"]["cost_usd"] == pytest.approx((300 * .35 + 600 * .75) / 1_000_000)
    assert result["usage"]["reasoning_tokens_reported"] == 500
    profile = result["model_profile"]
    assert profile["reasoning_token_allowance"] == 2048
    assert profile["visible_output_tokens_requested"] == 600 and profile["total_output_tokens_requested"] == 2648
    assert profile["context_limit"] + 2048 == profile["context_window_including_reasoning"]
    assert "gptoss-reasoning-budget-1" in profile["batching_version"]


@pytest.mark.asyncio
async def test_default_gpt_and_gemma_profiles_do_not_gain_reasoning_allowances(monkeypatch):
    captured = client_capture(monkeypatch)
    default = CloudflareProvider({"model": GPT})
    assert default.profile["context_limit"] == 8192
    assert default.estimate_cost(300, 600) == pytest.approx((300 * .35 + 600 * .75) / 1_000_000)
    await default.generate([{"role": "user", "content": "Test."}], {}, max_tokens=400)
    assert captured[-1]["max_tokens"] == 400 and "reasoning_effort" not in captured[-1]
    gemma = CloudflareProvider({"model": CLOUDFLARE_MODEL, "reasoning_effort": None, "reasoning_token_allowance": 0})
    assert gemma.profile["context_limit"] == 32768
    await gemma.generate([{"role": "user", "content": "Test."}], {}, max_tokens=400)
    assert captured[-1]["max_tokens"] == 400 and "reasoning_effort" not in captured[-1]
    assert captured[-1]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize("allowance", [-1, 8193, 1.5, True, False, float("nan")])
def test_invalid_reasoning_allowance_is_rejected(allowance):
    with pytest.raises(ValueError, match="reasoning_token_allowance"):
        CloudflareProvider({"model": GPT, "reasoning_token_allowance": allowance})


def test_reasoning_cannot_be_disabled_or_silently_applied_to_another_model():
    with pytest.raises(ValueError, match="cannot be disabled"):
        CloudflareProvider({"model": GPT, "reasoning_effort": "none"})
    with pytest.raises(ValueError, match="GPT-OSS"):
        CloudflareProvider({"model": CLOUDFLARE_MODEL, "reasoning_effort": "low", "reasoning_token_allowance": 2048})
