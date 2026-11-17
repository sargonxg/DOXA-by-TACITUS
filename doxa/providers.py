"""Explicit inference adapters. Credentials are read only from server environment.

Adapters never download models or silently change the selected model/provider.
Token-rate costs are estimates, not provider invoices; local electricity is excluded.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any
from urllib.parse import urlparse

import httpx

CLOUDFLARE_MODEL = "@cf/google/gemma-4-26b-a4b-it"
OLLAMA_MODEL = "qwen3.5:4b"


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, usage: dict | None = None):
        super().__init__(message)
        self.code = code
        self.usage = usage


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

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        if self.mode == "local" or self.name == "lexical":
            return 0.0
        if self.input_rate is None or self.output_rate is None:
            return None
        return (input_tokens * self.input_rate + output_tokens * self.output_rate) / 1_000_000

    def _usage(self, input_tokens: int, output_tokens: int, *, tokens_estimated: bool = False) -> dict:
        return {"input_tokens": input_tokens, "output_tokens": output_tokens,
                "cost_usd": self.estimate_cost(input_tokens, output_tokens),
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
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                if response.status_code >= 400:
                    raise ProviderError("provider_http_error", f"Ollama returned HTTP {response.status_code}; check selected model and runtime support")
                raw = response.json()
        except httpx.TimeoutException:
            raise ProviderError("timeout", "Ollama analysis timed out; the source range remains incomplete") from None
        except (httpx.HTTPError, ValueError):
            raise ProviderError("connection_error", "Could not read Ollama response") from None
        if raw.get("error"):
            raise ProviderError("provider_error", "Ollama reported an inference error; inspect local runtime logs")
        usage = self._usage(int(raw.get("prompt_eval_count", 0)), int(raw.get("eval_count", 0)))
        reason = raw.get("done_reason", "stop")
        if reason in {"length", "max_tokens"}:
            return {"content": None, "usage": usage, "finish_reason": "length", "model_profile": dict(self.profile)}
        try:
            content = _json_content(raw.get("message", {}).get("content"))
        except ProviderError as error:
            error.usage = usage
            raise
        return {"content": content, "usage": usage, "finish_reason": reason, "model_profile": dict(self.profile)}


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
        payload = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": 0,
                   "response_format": {"type": "json_schema", "json_schema": {"name": "doxa_findings", "strict": True, "schema": schema}}}
        if "qwen3.5" in self.model.lower() or self.model == CLOUDFLARE_MODEL:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15)) as client:
                response = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=payload)
                if response.status_code == 429:
                    raise ProviderError("rate_limit", "Provider rate limit reached; retry the incomplete task later")
                if response.status_code >= 400:
                    raise ProviderError("provider_http_error", f"Inference returned HTTP {response.status_code}; check model, schema capabilities, permissions, and account balance")
                raw = response.json()
        except httpx.TimeoutException:
            raise ProviderError("timeout", "Remote inference timed out; usage may have been billed and the range remains incomplete") from None
        except (httpx.HTTPError, ValueError):
            raise ProviderError("connection_error", "Could not read remote inference response; check provider availability") from None
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
        if not choices:
            raise ProviderError("invalid_response", "Provider returned no choices", usage=usage)
        choice = choices[0]
        reason = choice.get("finish_reason", "stop")
        self.profile["response_model"] = raw.get("model") or self.model
        if raw.get("system_fingerprint"):
            self.profile["system_fingerprint"] = raw["system_fingerprint"]
        if reason in {"length", "max_tokens"}:
            return {"content": None, "usage": usage, "finish_reason": "length", "model_profile": dict(self.profile)}
        if reason in {"content_filter", "refusal"} or choice.get("message", {}).get("refusal"):
            raise ProviderError("refusal", "Provider declined this request; source coverage remains incomplete", usage=usage)
        try:
            content = _json_content(choice.get("message", {}).get("content"))
        except ProviderError as error:
            error.usage = usage
            raise
        return {"content": content, "usage": usage, "finish_reason": reason, "model_profile": dict(self.profile)}


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
        self.profile["reasoning_requested"] = "disabled" if model == CLOUDFLARE_MODEL else "provider_default"

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
