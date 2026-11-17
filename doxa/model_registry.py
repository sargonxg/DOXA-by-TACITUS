"""Reviewed model profiles, not claims of domain accuracy.

CPU artifacts are pinned to immutable upstream commits. Managed providers may
not expose immutable weights; their revision limitation is stated explicitly.
Importing this module never imports an ML runtime or accesses the network.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path

REGISTRY_VERSION = "doxa-models-3.0"
MANIFEST_VERSION = 1

_PROFILES = {
    "minilm": {
        "id": "minilm", "role": "retrieval", "provider": "huggingface_cpu",
        "model": "sentence-transformers/all-MiniLM-L6-v2",
        "revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        "license": "Apache-2.0", "languages": ["en"], "parameters": 22713728,
        "max_input_tokens": 256, "dimensions": 384, "runtime": "transformers_cpu",
        "experimental": False, "calibration": "not_applicable",
        "intended_use": "Semantic retrieval and clustering of short source windows; not prejudice classification.",
        "limitations": ["The sentence-transformer limit is 256 wordpieces, despite the encoder's 512 positions.",
                         "Similarity is not evidence of shared stance, endorsement or prejudice."],
        "files": ["README.md", "config.json", "model.safetensors", "tokenizer.json",
                  "tokenizer_config.json", "special_tokens_map.json", "vocab.txt",
                  "sentence_bert_config.json", "1_Pooling/config.json", "modules.json"],
        "max_download_bytes": 160 * 1024 * 1024,
    },
    "deberta": {
        "id": "deberta", "role": "rubric_scoring", "provider": "huggingface_cpu",
        "model": "MoritzLaurer/deberta-v3-base-zeroshot-v2.0-c",
        "revision": "bddf8c5411c34ac3565e16e04384fd68b2618dda",
        "license": "MIT", "languages": ["en"], "parameters": 184423682,
        "max_input_tokens": 512, "runtime": "transformers_cpu", "experimental": True,
        "calibration": "uncalibrated", "id2label": {"0": "entailment", "1": "not_entailment"},
        "intended_use": "Experimental independent source/hypothesis entailment scoring for explicit rubric propositions.",
        "limitations": ["512 tokens includes premise, hypothesis and special tokens.",
                         "Not-entailment is not contradiction; scores are not probabilities of prejudice.",
                         "General NLI, not an antisemitism or anti-Muslim specialist; no DOXA fine-tuning.",
                         "The -c variant uses its author's commercially friendly training-data mix."],
        "files": ["README.md", "config.json", "model.safetensors", "tokenizer.json",
                  "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json", "spm.model"],
        "max_download_bytes": 900 * 1024 * 1024,
    },
    "cloud-gemma": {
        "id": "cloud-gemma", "role": "primary", "provider": "cloudflare",
        "model": "@cf/google/gemma-4-26b-a4b-it", "license": "Apache-2.0",
        "revision": "provider_managed_unpinned", "runtime": "managed_api",
        "context_limit": 32768, "max_input_tokens": 12000, "max_output_tokens": 2048,
        "temperature": 0, "reasoning": False, "input_price_per_million": 0.10,
        "output_price_per_million": 0.30, "experimental": True,
        "calibration": "uncalibrated", "languages": ["en"],
        "intended_use": "Cloud primary for complete bounded-unit source analysis; requires evaluation and review.",
        "limitations": ["Managed API does not guarantee an immutable weight revision.",
                         "Prices are estimates, not invoices; no accuracy claim follows from model availability."],
        "documentation": "https://developers.cloudflare.com/workers-ai/models/gemma-4-26b-a4b-it/",
    },
    "cloud-verifier": {
        "id": "cloud-verifier", "role": "verifier", "provider": "cloudflare",
        "model": "@cf/openai/gpt-oss-120b", "license": "Apache-2.0",
        "revision": "provider_managed_unpinned", "runtime": "managed_api",
        "context_limit": 16384, "max_input_tokens": 12000, "max_output_tokens": 1024,
        "temperature": 0, "reasoning": "provider_default", "reasoning_requested": None,
        "reasoning_actual": "unresolved", "input_price_per_million": 0.35,
        "output_price_per_million": 0.75, "experimental": True,
        "calibration": "uncalibrated", "languages": ["en"],
        "intended_use": "Independent-family source/criterion/attribution review; disagreements need adjudication.",
        "limitations": ["Model agreement is not independent human validation.",
                         "Managed API does not guarantee an immutable weight revision."],
        "documentation": "https://developers.cloudflare.com/workers-ai/models/gpt-oss-120b/",
    },
    "local-qwen": {
        "id": "local-qwen", "role": "primary", "provider": "ollama",
        "model": "qwen3.5:4b", "license": "Apache-2.0", "revision": "resolve_installed_ollama_digest",
        "runtime": "ollama", "context_limit": 8192, "max_input_tokens": 6144,
        "max_output_tokens": 1024, "temperature": 0, "reasoning": False,
        "quantization": "Q4", "concurrency": 1, "experimental": True,
        "calibration": "uncalibrated", "languages": ["en"],
        "intended_use": "Fully local fallback; 16GB physical RAM is a test target, not a throughput guarantee.",
        "limitations": ["Resolve and freeze the installed Ollama package digest before a run.",
                         "CPU processing of large corpora may take substantial time; no automatic model pull."],
        "documentation": "https://huggingface.co/Qwen/Qwen3.5-4B",
    },
}


def get_model_profile(model_key: str) -> dict:
    """Return an isolated JSON-safe profile; unknown IDs never fall back."""
    if model_key not in _PROFILES:
        raise ValueError(f"Unknown model profile: {model_key}")
    profile = deepcopy(_PROFILES[model_key])
    profile["registry_version"] = REGISTRY_VERSION
    profile["reviewed_at"] = "2026-11-16"
    if profile["provider"] == "cloudflare":
        profile["availability_policy"] = "Check the selected provider model before each run; never silently substitute."
    if profile["provider"] == "huggingface_cpu":
        profile["model_card"] = f"https://huggingface.co/{profile['model']}/blob/{profile['revision']}/README.md"
    return profile


def list_model_profiles() -> list[dict]:
    return [get_model_profile(key) for key in _PROFILES]


def default_model_dir() -> Path:
    """Resolve a directory without creating it or downloading anything."""
    configured = os.environ.get("DOXA_CPU_MODEL_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".cache" / "doxa" / "cpu-models"
