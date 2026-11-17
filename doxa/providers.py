"""Explicit inference adapters. Credentials are read only from server environment.

Adapters never download models or silently change the selected model/provider.
Token-rate costs are estimates, not provider invoices; local electricity is excluded.
"""
from __future__ import annotations

import json
import math
import os
import hashlib
from typing import Any
from urllib.parse import urlparse

import httpx

CLOUDFLARE_MODEL = "@cf/google/gemma-4-26b-a4b-it"
OLLAMA_MODEL = "qwen3.5:4b"


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, usage: dict | None = None, observation: dict | None = None):
        super().__init__(message)
        self.code = code
        self.usage = usage
        self.observation = observation


def _env(*names: str) -> str:
    return next((os.environ[name] for name in names if os.environ.get(name)), "")


def _rate(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        rate = float(value)
    except (ValueError, TypeError):
        raise ValueError("Token prices must be non-negative numbers") from None
    if rate < 0 or rate != rate or rate == float("inf"):
        raise ValueError("Token prices must be finite non-negative numbers")
    return rate


def _endpoint(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Provider base URL must be an HTTP(S) endpoint without embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Provider base URL must not contain a query string or fragment")
    return value.rstrip("/")


def _json_content(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ProviderError("invalid_response", "Provider returned no structured response content")
    value = value.strip()
    if value.startswith("```json") and value.endswith("```"):
        value = value[7:-3].strip()
    elif value.startswith("```") and value.endswith("```"):
        value = value[3:-3].strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        raise ProviderError("invalid_json", "Provider response was not valid JSON; no interpretation was committed") from None
    if not isinstance(parsed, dict):
        raise ProviderError("invalid_json", "Provider JSON response must be an object")
    return parsed


class BaseProvider:
    name = "base"
    mode = "remote"

    def __init__(self, config: dict):
        self.config = config
        self.model = str(config.get("model") or "")
        self.input_rate = _rate(config.get("input_price_per_million"))
        self.output_rate = _rate(config.get("output_price_per_million"))
        self.profile = {"provider": self.name, "model": self.model, "mode": self.mode,
                        "revision": config.get("model_revision") or "unresolved",
                        "quantization": config.get("quantization") or "provider_managed",
                        "context_limit": 8192, "temperature": 0, "structured_output": True,
                        "source_pack_bytes": 3600, "source_pack_windows": 20, "lens_batch_size": 2,
                        "batching_version": "local-bounded-2",
                        "citation_offset_unit": "unicode_code_points", "prompt_version": "doxa-evidence-1.2", "response_schema_version": "evidence-enums-1"}

    async def health(self) -> dict:
        raise NotImplementedError

    def _response_observation(self, visible) -> dict:
        """Observe visible message content only; never separate reasoning fields."""
        if visible is None:
            return {"visible_response_sha256": None, "visible_response_bytes": 0,
                    "response_origin": "visible_message_content_not_present"}
        raw = visible if isinstance(visible, str) else json.dumps(visible, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        result = {"visible_response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                  "visible_response_bytes": len(raw.encode()), "response_origin": "observed_visible_message_content"}
        if self.config.get("request_journal_mode") == "visible_io":
            result["_visible_response"] = visible
        return result

    def _request_observation(self, payload: dict) -> dict:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        result = {"provider_request_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                  "request_origin": "observed_adapter_outbound_json",
                  "request_parameters": {key: value for key, value in payload.items()
                                         if key not in {"messages", "format", "response_format"}}}
        if self.config.get("request_journal_mode") == "visible_io":
            result["_visible_request"] = payload
        return result

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        if self.mode == "local" or self.name == "lexical":
            return 0.0
        if self.input_rate is None or self.output_rate is None:
            return None
        return (input_tokens * self.input_rate + output_tokens * self.output_rate) / 1_000_000

    def _usage(self, input_tokens: int, output_tokens: int, *, tokens_estimated: bool = False) -> dict:
        return {"input_tokens": input_tokens, "output_tokens": output_tokens,
                # Reservation estimates may include an unconsumed reasoning
                # allowance. A provider receipt bills its actual token counts once.
                "cost_usd": BaseProvider.estimate_cost(self, input_tokens, output_tokens),
                "estimated": self.mode != "local" or tokens_estimated,
                "cost_basis": "local_compute_excluded" if self.mode == "local" else ("configured_token_rates" if self.input_rate is not None and self.output_rate is not None else "unknown"),
                "tokens_estimated": tokens_estimated, "requests": 1}


class LexicalProvider(BaseProvider):
    name = "lexical"
    mode = "local"

    def __init__(self, config: dict):
        super().__init__({**config, "model": "lexical-baseline"})
        self.profile.update({"model": "lexical-baseline", "revision": "1.0", "quantization": "not_applicable", "structured_output": False, "semantic_analysis": False})

    async def health(self) -> dict:
        return {"ok": True, **self.profile, "details": "Literal matching only. No model, semantic classification, or prejudice determination."}

    async def generate(self, *args, **kwargs) -> dict:
        raise ProviderError("unsupported", "Lexical baseline must use the literal matching pipeline")


class OllamaProvider(BaseProvider):
    name = "ollama"
    mode = "local"

    def __init__(self, config: dict):
        super().__init__({**config, "model": config.get("model") or OLLAMA_MODEL})
        self.base_url = _endpoint(str(config.get("base_url") or _env("DOXA_OLLAMA_BASE_URL", "OLLAMA_BASE_URL") or "http://127.0.0.1:11434"))
        self.profile["quantization"] = config.get("quantization") or "model_package_unresolved"
        try:
            self.num_threads = int(os.environ.get("DOXA_OLLAMA_NUM_THREADS", "4"))
        except ValueError:
            raise ValueError("DOXA_OLLAMA_NUM_THREADS must be an integer between 1 and 32") from None
        if not 1 <= self.num_threads <= 32:
            raise ValueError("DOXA_OLLAMA_NUM_THREADS must be between 1 and 32")
        self.profile["num_threads_requested"] = self.num_threads

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                models = response.json().get("models", [])
                requested = self.model if ":" in self.model else f"{self.model}:latest"
                match = next((m for m in models if m.get("name") in {self.model, requested} or m.get("model") in {self.model, requested}), None)
                if match is None:
                    return {"ok": False, **self.profile, "details": f"Model {self.model} is not installed. Install the selected model in Ollama; DOXA will not pull it automatically."}
                self.profile["revision"] = match.get("digest") or "unresolved"
                self.profile["quantization"] = match.get("details", {}).get("quantization_level", self.profile["quantization"])
                return {"ok": True, **self.profile, "details": "Ollama reachable; selected model installed. Structured-output capability is verified on the analysis request."}
        except (httpx.HTTPError, ValueError, TypeError):
            return {"ok": False, **self.profile, "details": "Ollama is unavailable at the configured endpoint. Start Ollama and verify the model installation."}

    async def generate(self, messages: list[dict], schema: dict, *, max_tokens: int = 3000, request_id: str | None = None) -> dict:
        payload = {"model": self.model, "messages": messages, "stream": False, "format": schema,
                   "think": False, "options": {"temperature": 0, "num_ctx": 8192, "num_predict": max_tokens, "num_thread": self.num_threads}}
        request_observation = self._request_observation(payload)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                if response.status_code >= 400:
                    raise ProviderError("provider_http_error", f"Ollama returned HTTP {response.status_code}; check selected model and runtime support", observation=request_observation)
                raw = response.json()
        except httpx.TimeoutException:
            raise ProviderError("timeout", "Ollama analysis timed out; the source range remains incomplete", observation=request_observation) from None
        except (httpx.HTTPError, ValueError):
            raise ProviderError("connection_error", "Could not read Ollama response", observation=request_observation) from None
        if raw.get("error"):
            raise ProviderError("provider_error", "Ollama reported an inference error; inspect local runtime logs", observation=request_observation)
        usage = self._usage(int(raw.get("prompt_eval_count", 0)), int(raw.get("eval_count", 0)))
        observation = {**request_observation, **self._response_observation(raw.get("message", {}).get("content"))}
        reason = raw.get("done_reason", "stop")
        if reason in {"length", "max_tokens"}:
            return {"content": None, "usage": usage, "finish_reason": "length", "model_profile": dict(self.profile), "provider_observation": observation}
        try:
            content = _json_content(raw.get("message", {}).get("content"))
        except ProviderError as error:
            error.usage = usage
            error.observation = observation
            raise
        return {"content": content, "usage": usage, "finish_reason": reason, "model_profile": dict(self.profile), "provider_observation": observation}


class OpenAICompatibleProvider(BaseProvider):
    name = "openai"

    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = _endpoint(str(config.get("base_url") or _env("DOXA_OPENAI_BASE_URL", "OPENAI_BASE_URL") or "https://api.openai.com/v1"))
        self.api_key = _env("DOXA_OPENAI_API_KEY", "OPENAI_API_KEY")
        # A self-hosted vLLM endpoint may deliberately have no key.
        host = urlparse(self.base_url).hostname or ""
        self.local_endpoint = host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
        # A localhost gateway can forward to a paid cloud model. Endpoint
        # location does not prove where inference executes.
        self.mode = str(config.get("inference_mode") or _env("DOXA_OPENAI_MODE") or ("local" if config.get("provider") == "vllm" else "remote"))
        if self.mode not in {"local", "remote"}:
            raise ValueError("Inference mode must be local or remote")
        self.profile["mode"] = self.mode
        self.profile["provider"] = self.name
        if self.mode == "remote" and self.model == CLOUDFLARE_MODEL and self.name in {"openai", "cloudflare"}:
            # Known managed model only. A generic compatible endpoint's model
            # name/window cannot be inferred from API compatibility alone.
            self.profile.update({"context_limit": 32768, "source_pack_bytes": 12000,
                                 "source_pack_windows": 64, "lens_batch_size": 12,
                                 "batching_version": "managed-gemma-2"})
        self.reasoning_effort = config.get("reasoning_effort")
        configured_allowance = config.get("reasoning_token_allowance")
        self.reasoning_token_allowance = 0 if configured_allowance is None else configured_allowance
        if (not isinstance(self.reasoning_token_allowance, int) or isinstance(self.reasoning_token_allowance, bool)
                or not 0 <= self.reasoning_token_allowance <= 8192):
            raise ValueError("reasoning_token_allowance must be an integer between 0 and 8192")
        self.explicit_reasoning = self.reasoning_effort is not None or self.reasoning_token_allowance > 0
        if self.explicit_reasoning:
            if "gpt-oss" not in self.model.lower():
                raise ValueError("Explicit reasoning allowance currently requires a GPT-OSS model")
            if self.reasoning_effort not in {None, "low", "medium", "high"}:
                raise ValueError("GPT-OSS reasoning_effort must be low, medium or high; reasoning cannot be disabled")
            total_context = 16384 if self.model in {"@cf/openai/gpt-oss-120b", "@cf/openai/gpt-oss-20b"} else int(self.profile["context_limit"])
            if self.reasoning_token_allowance >= total_context:
                raise ValueError("Reasoning allowance must leave room for visible output and source context")
            self.profile.update({"reasoning_effort_requested": self.reasoning_effort or "provider_default",
                                 "reasoning_effort_applied": "unverified_by_adapter",
                                 "reasoning_token_allowance": self.reasoning_token_allowance,
                                 "reasoning_profile_version": "gptoss-reasoning-budget-1",
                                 "context_window_including_reasoning": total_context,
                                 "context_limit": total_context - self.reasoning_token_allowance,
                                 "context_limit_basis": "source_plus_visible_output_budget_after_reasoning_reserve",
                                 "batching_version": f"gptoss-reasoning-budget-1:{self.reasoning_effort or 'default'}:{self.reasoning_token_allowance}"})

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        return super().estimate_cost(input_tokens, output_tokens + self.reasoning_token_allowance)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def health(self) -> dict:
        if not self.model:
            return {"ok": False, **self.profile, "details": "Choose an explicit model; DOXA never selects a remote model automatically."}
        if self.mode == "remote" and not self.api_key and not self.local_endpoint:
            return {"ok": False, **self.profile, "details": "The selected provider needs a server environment API key."}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(f"{self.base_url}/models", headers=self._headers())
                if response.status_code >= 400:
                    return {"ok": False, **self.profile, "details": f"Model availability preflight returned HTTP {response.status_code}; no inference was attempted."}
                raw = response.json()
                models = raw.get("data", [])
                if isinstance(models, list) and models and not any(m.get("id") == self.model for m in models if isinstance(m, dict)):
                    return {"ok": False, **self.profile, "details": "The configured model is not present in the provider's model listing."}
                return {"ok": True, **self.profile, "details": "Provider reachable; selected model availability checked. Exact structured-output support is checked during analysis."}
        except (httpx.HTTPError, ValueError, TypeError):
            return {"ok": False, **self.profile, "details": "Provider availability preflight failed; no inference was attempted."}

    async def generate(self, messages: list[dict], schema: dict, *, max_tokens: int = 3000, request_id: str | None = None) -> dict:
        if not self.model:
            raise ProviderError("model_required", "An explicit inference model is required")
        actual_max_tokens = max_tokens + self.reasoning_token_allowance
        payload = {"model": self.model, "messages": messages, "max_tokens": actual_max_tokens, "temperature": 0,
                   "response_format": {"type": "json_schema", "json_schema": {"name": "doxa_findings", "strict": True, "schema": schema}}}
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.explicit_reasoning:
            self.profile.update({"visible_output_tokens_requested": max_tokens,
                                 "total_output_tokens_requested": actual_max_tokens})
        if "qwen3.5" in self.model.lower() or self.model == CLOUDFLARE_MODEL:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        request_observation = self._request_observation(payload)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15)) as client:
                response = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=payload)
                if response.status_code == 429:
                    raise ProviderError("rate_limit", "Provider rate limit reached; retry the incomplete task later", observation=request_observation)
                if response.status_code >= 400:
                    raise ProviderError("provider_http_error", f"Inference returned HTTP {response.status_code}; check model, schema capabilities, permissions, and account balance", observation=request_observation)
                raw = response.json()
        except httpx.TimeoutException:
            raise ProviderError("timeout", "Remote inference timed out; usage may have been billed and the range remains incomplete", observation=request_observation) from None
        except (httpx.HTTPError, ValueError):
            raise ProviderError("connection_error", "Could not read remote inference response; check provider availability", observation=request_observation) from None
        choices = raw.get("choices") or []
        raw_usage = raw.get("usage") or {}
        if raw_usage:
            input_tokens = int(raw_usage.get("prompt_tokens", raw_usage.get("input_tokens", 0)))
            output_tokens = int(raw_usage.get("completion_tokens", raw_usage.get("output_tokens", 0)))
        else:
            input_tokens = math.ceil(sum(len(message.get("content", "").encode("utf-8")) for message in messages) / 3)
            returned_content = choices[0].get("message", {}).get("content", "") if choices else ""
            output_tokens = math.ceil(len(str(returned_content).encode("utf-8")) / 3)
        usage = self._usage(input_tokens, output_tokens, tokens_estimated=not bool(raw_usage))
        details = raw_usage.get("completion_tokens_details") or raw_usage.get("output_tokens_details") or {}
        if isinstance(details.get("reasoning_tokens"), int) and details["reasoning_tokens"] >= 0:
            usage["reasoning_tokens_reported"] = details["reasoning_tokens"]
        if not choices:
            raise ProviderError("invalid_response", "Provider returned no choices", usage=usage, observation=request_observation)
        choice = choices[0]
        observation = {**request_observation, **self._response_observation(choice.get("message", {}).get("content"))}
        reason = choice.get("finish_reason", "stop")
        self.profile["response_model"] = raw.get("model") or self.model
        if raw.get("system_fingerprint"):
            self.profile["system_fingerprint"] = raw["system_fingerprint"]
        if reason in {"length", "max_tokens"}:
            return {"content": None, "usage": usage, "finish_reason": "length", "model_profile": dict(self.profile), "provider_observation": observation}
        if reason in {"content_filter", "refusal"} or choice.get("message", {}).get("refusal"):
            raise ProviderError("refusal", "Provider declined this request; source coverage remains incomplete", usage=usage, observation=observation)
        try:
            content = _json_content(choice.get("message", {}).get("content"))
        except ProviderError as error:
            error.usage = usage
            error.observation = observation
            raise
        return {"content": content, "usage": usage, "finish_reason": reason, "model_profile": dict(self.profile), "provider_observation": observation}


class CloudflareProvider(OpenAICompatibleProvider):
    name = "cloudflare"

    def __init__(self, config: dict):
        model = str(config.get("model") or CLOUDFLARE_MODEL)
        # This catalogue entry retires on 2026-11-20. Do not start new runs on it.
        if "qwen3-30b-a3b" in model.lower():
            raise ValueError("The Workers AI Qwen3-30B-A3B model retires on 2026-11-20; select an available model explicitly")
        account = _env("DOXA_CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID")
        self.account_id = account
        self.proxy_base = _env("DOXA_CLOUDFLARE_BASE_URL")
        base = self.proxy_base or f"https://api.cloudflare.com/client/v4/accounts/{account or 'unconfigured'}/ai/v1"
        rates = {CLOUDFLARE_MODEL: (0.10, 0.30), "@cf/openai/gpt-oss-120b": (0.35, 0.75), "@cf/openai/gpt-oss-20b": (0.20, 0.30)}
        defaults = {"input_price_per_million": rates[model][0], "output_price_per_million": rates[model][1]} if model in rates else {}
        super().__init__({**defaults, **{key: value for key, value in config.items() if value is not None}, "model": model, "base_url": base, "inference_mode": "remote"})
        self.api_key = _env("DOXA_CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_TOKEN", "CF_API_TOKEN")
        self.profile.update({"provider": self.name, "pricing_checked_at": "2026-11-16"})
        self.profile["reasoning_requested"] = "disabled" if model == CLOUDFLARE_MODEL else (self.reasoning_effort or "provider_default")

    async def health(self) -> dict:
        if self.proxy_base:
            return await super().health()
        if not self.account_id or not self.api_key:
            return {"ok": False, **self.profile, "details": "Set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN on the server. No documents were sent."}
        # The compatibility listing can be large; query the first-party catalogue
        # by model name instead, without paying for an inference request.
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/models/search", params={"search": self.model}, headers=self._headers())
                if response.status_code >= 400:
                    return {"ok": False, **self.profile, "details": f"Cloudflare model catalogue preflight returned HTTP {response.status_code}."}
                raw = response.json()
                models = raw.get("result") or []
                if not raw.get("success", True) or not any(m.get("name") == self.model or m.get("id") == self.model for m in models if isinstance(m, dict)):
                    return {"ok": False, **self.profile, "details": "Selected Workers AI model was not found in the live catalogue; select a current model explicitly."}
                return {"ok": True, **self.profile, "details": "Workers AI credentials and selected model catalogue entry verified; no inference was requested."}
        except (httpx.HTTPError, ValueError, TypeError):
            return {"ok": False, **self.profile, "details": "Cloudflare model availability check failed; no inference was requested."}


class RunpodProvider(OpenAICompatibleProvider):
    name = "runpod"

    def __init__(self, config: dict):
        endpoint_id = _env("DOXA_RUNPOD_ENDPOINT_ID", "RUNPOD_ENDPOINT_ID")
        base = config.get("base_url") or _env("DOXA_RUNPOD_BASE_URL", "RUNPOD_BASE_URL") or (f"https://api.runpod.ai/v2/{endpoint_id}/openai/v1" if endpoint_id else "https://api.runpod.ai/unconfigured/openai/v1")
        super().__init__({**config, "base_url": base, "inference_mode": "remote"})
        self.api_key = _env("DOXA_RUNPOD_API_KEY", "RUNPOD_API_KEY")
        self.profile.update({"provider": self.name, "billing_note": "Token-rate estimates require configured rates. GPU initialization, execution, idle, and storage can be charged separately."})


async def get_provider(config: dict) -> BaseProvider:
    """Build an adapter; health is explicit and never starts model inference."""
    name = str(config.get("provider") or "lexical").lower()
    classes = {"lexical": LexicalProvider, "lexical-baseline": LexicalProvider, "ollama": OllamaProvider,
               "openai": OpenAICompatibleProvider, "openai-compatible": OpenAICompatibleProvider,
               "vllm": OpenAICompatibleProvider, "cloudflare": CloudflareProvider, "runpod": RunpodProvider}
    if name not in classes:
        raise ValueError(f"Unsupported provider: {name}")
    return classes[name](config)


build_provider = get_provider
