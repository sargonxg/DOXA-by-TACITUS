"""Optional, offline-only CPU retrieval and NLI rubric scoring.

Only scripts/model_setup.py downloads artifacts. Runtime loading is restricted to
verified safetensors and ordinary tokenizer/config files; no pickle or remote code.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

from .model_registry import MANIFEST_VERSION, default_model_dir, get_model_profile


class CPUModelError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def verify_installation(model_key: str, model_dir: Path | None = None, *, hashes: bool = True) -> dict:
    """Validate pinned identity and every required artifact. Never use the network."""
    profile = get_model_profile(model_key)
    if profile["provider"] != "huggingface_cpu":
        raise CPUModelError("unsupported_model", "This profile is not a local CPU artifact bundle")
    root = (Path(model_dir) if model_dir is not None else default_model_dir()).resolve()
    directory = root / model_key
    if directory.is_symlink():
        raise CPUModelError("invalid_installation", "Model bundle directory must not be a symbolic link")
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise CPUModelError("model_not_installed", f"Install {model_key} explicitly with scripts/model_setup.py download {model_key}")
    try:
        if manifest_path.stat().st_size > 1024 * 1024:
            raise ValueError("Oversized manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("manifest_version") != MANIFEST_VERSION or manifest.get("model") != profile["model"]
                or manifest.get("revision") != profile["revision"] or manifest.get("license") != profile["license"]):
            raise ValueError("Manifest identity does not match the pinned registry")
        artifacts = manifest["artifacts"]
        if not isinstance(artifacts, list) or len(artifacts) != len(profile["files"]):
            raise ValueError("Manifest artifact list is incomplete")
        if {item["path"] for item in artifacts} != set(profile["files"]):
            raise ValueError("Unexpected or missing artifact")
        for item in artifacts:
            relative = Path(item["path"])
            path = directory / relative
            if relative.is_absolute() or ".." in relative.parts or path.is_symlink():
                raise ValueError("Unsafe artifact path")
            if not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Artifact escapes its installation directory")
            if not path.is_file() or path.stat().st_size != item["bytes"]:
                raise ValueError(f"Missing or changed artifact: {relative}")
            if hashes and _digest(path) != item["sha256"]:
                raise ValueError(f"Artifact checksum mismatch: {relative}")
        if sum(item["bytes"] for item in artifacts) > profile["max_download_bytes"]:
            raise ValueError("Artifact bundle exceeds its registry disk limit")
    except (KeyError, ValueError, TypeError, OSError) as error:
        raise CPUModelError("invalid_installation", str(error)) from error
    return {"directory": str(directory), "manifest": manifest, "model_profile": profile,
            "hashes_verified": hashes}


def token_windows(text: str, tokenizer: Any, budget: int, overlap: int = 32) -> list[dict]:
    """Cover all source tokens with exact Unicode offsets; never truncate silently."""
    if not isinstance(text, str) or not text.strip():
        raise CPUModelError("invalid_input", "A nonempty source text is required")
    if len(text) > 1_000_000:
        raise CPUModelError("input_limit", "Pass bounded source units of at most 1,000,000 characters")
    if budget < 1 or overlap < 0 or overlap >= budget:
        raise CPUModelError("invalid_window", "Overlap must be nonnegative and smaller than the token budget")
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True, truncation=False, verbose=False)
    offsets = [(int(start), int(end)) for start, end in encoded["offset_mapping"] if end > start]
    if not offsets:
        raise CPUModelError("no_source_tokens", "Tokenizer produced no source tokens")
    if math.ceil(len(offsets) / (budget - overlap)) > 4096:
        raise CPUModelError("window_limit", "Split the source into smaller units before CPU inference")
    windows = []
    position = 0
    while position < len(offsets):
        end_position = min(position + budget, len(offsets))
        start, end = offsets[position][0], offsets[end_position - 1][1]
        # Boundary whitespace belongs to the first/last window; citations remain exact.
        if position == 0:
            start = 0
        if end_position == len(offsets):
            end = len(text)
        # A window can start/end inside a wordpiece. Re-tokenizing that exact
        # substring can add tokens, so check it rather than assume the count.
        while True:
            actual_count = len(tokenizer(text[start:end], add_special_tokens=False, truncation=False, verbose=False)["input_ids"])
            if actual_count <= budget:
                break
            end_position -= max(1, actual_count - budget)
            if end_position <= position + overlap:
                raise CPUModelError("tokenizer_window", "Tokenizer cannot form a bounded overlapping source window")
            end = offsets[end_position - 1][1]
        windows.append({"start": start, "end": end, "text": text[start:end],
                        "token_count": actual_count})
        if end_position == len(offsets):
            break
        position = end_position - overlap
    return windows


class CPUModels:
    def __init__(self, model_dir: Path | None = None, *, threads: int = 4, batch_size: int = 8,
                 embedding_batching: str = "length_ascending"):
        if not 1 <= threads <= 32 or not 1 <= batch_size <= 64:
            raise ValueError("CPU threads must be 1–32 and batch size 1–64")
        if embedding_batching not in {"source_order", "length_ascending"}:
            raise ValueError("Embedding batching must be source_order or length_ascending")
        self.model_dir = Path(model_dir) if model_dir is not None else default_model_dir()
        self.threads, self.batch_size = threads, batch_size
        self.embedding_batching = embedding_batching
        self._loaded: dict[str, tuple[Any, Any]] = {}

    def model_profile(self, key: str) -> dict:
        profile = get_model_profile(key)
        profile["runtime_settings"] = {"device": "cpu", "threads": self.threads, "batch_size": self.batch_size,
                                       "batching_policy": self.embedding_batching if key == "minilm" else "source_order"}
        if key == "minilm":
            profile["runtime_settings"]["pooling"] = "attention_mask_mean_then_l2"
        return profile

    def health(self, model_key: str | None = None, *, verify_artifacts: bool = False) -> dict:
        keys = [model_key] if model_key else ["minilm", "deberta"]
        missing = [name for name in ("torch", "transformers", "tokenizers", "safetensors", "numpy")
                   if importlib.util.find_spec(name) is None]
        entries = []
        for key in keys:
            profile = self.model_profile(key)
            try:
                installation = verify_installation(key, self.model_dir, hashes=verify_artifacts)
                entries.append({"ok": not missing, "model_profile": profile, "installed": True,
                                "hashes_verified": installation["hashes_verified"], "missing_dependencies": missing})
            except CPUModelError as error:
                entries.append({"ok": False, "model_profile": profile, "installed": False,
                                "code": error.code, "details": str(error), "missing_dependencies": missing})
        return {"ok": all(entry["ok"] for entry in entries), "device": "cpu", "models": entries,
                "automatic_downloads": False}

    def _load(self, key: str) -> tuple[Any, Any]:
        if key in self._loaded:
            return self._loaded[key]
        installation = verify_installation(key, self.model_dir)
        try:
            import torch
            from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as error:
            raise CPUModelError("dependencies_missing", "Install CPU PyTorch, then the DOXA cpu extra; see docs/models-v3.md") from error
        torch.set_num_threads(self.threads)
        directory = installation["directory"]
        try:
            tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, trust_remote_code=False, use_fast=True)
            if not tokenizer.is_fast:
                raise CPUModelError("tokenizer_incompatible", "Exact source-window offsets require the pinned fast tokenizer")
            loader = AutoModel if key == "minilm" else AutoModelForSequenceClassification
            model = loader.from_pretrained(directory, local_files_only=True, trust_remote_code=False,
                                           use_safetensors=True).to("cpu").eval()
            if key == "deberta" and {str(k): v for k, v in model.config.id2label.items()} != {"0": "entailment", "1": "not_entailment"}:
                raise CPUModelError("labels_changed", "The classifier label mapping differs from the pinned profile")
        except CPUModelError:
            raise
        except Exception as error:
            raise CPUModelError("load_failed", f"The verified local model could not be loaded: {type(error).__name__}: {error}") from error
        self._loaded[key] = tokenizer, model
        return tokenizer, model

    def _get_tokenizer(self, key: str):
        return self._load(key)[0]

    def _encode_embeddings(self, texts: list[str]) -> list[list[float]]:
        import torch
        tokenizer, model = self._load("minilm")
        order = list(range(len(texts)))
        if self.embedding_batching == "length_ascending":
            lengths = [len(ids) for ids in tokenizer(texts, padding=False, truncation=False, verbose=False)["input_ids"]]
            order.sort(key=lambda index: (lengths[index], index))
        vectors = [None] * len(texts)
        with torch.inference_mode():
            for position in range(0, len(texts), self.batch_size):
                indices = order[position:position + self.batch_size]
                batch = tokenizer([texts[index] for index in indices], padding=True, truncation=False,
                                  return_tensors="pt").to("cpu")
                hidden = model(**batch).last_hidden_state
                mask = batch["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                normalized = torch.nn.functional.normalize(pooled, p=2, dim=1).tolist()
                for index, vector in zip(indices, normalized):
                    vectors[index] = vector
        if any(vector is None for vector in vectors):
            raise CPUModelError("invalid_output", "Embedding batch did not produce one vector per original source window")
        return vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Normalized 384D vectors. Oversize inputs must use embed_windows()."""
        if not isinstance(texts, list) or len(texts) > 4096:
            raise CPUModelError("input_limit", "Embed at most 4096 bounded text inputs at once")
        if not texts:
            return []
        if any(not isinstance(text, str) for text in texts) or sum(len(text) for text in texts) > 2_000_000:
            raise CPUModelError("input_limit", "Embedding text batches must be strings totalling at most 2000000 characters")
        tokenizer = self._get_tokenizer("minilm")
        for text in texts:
            if not isinstance(text, str) or not text.strip() or len(text) > 100_000:
                raise CPUModelError("invalid_input", "Embedding inputs must be nonempty bounded strings")
            count = len(tokenizer(text, add_special_tokens=True, truncation=False, verbose=False)["input_ids"])
            if count <= tokenizer.num_special_tokens_to_add(pair=False):
                raise CPUModelError("no_source_tokens", "Tokenizer produced no source tokens for an embedding input")
            if count > 256:
                raise CPUModelError("input_too_long", "MiniLM accepts 256 tokens; use embed_windows() to preserve full coverage")
        return self._encode_embeddings(texts)

    def embed_windows(self, texts: list[str], *, overlap: int = 32) -> dict:
        if not isinstance(texts, list) or len(texts) > 4096:
            raise CPUModelError("input_limit", "Provide at most 4096 source inputs")
        if any(not isinstance(text, str) for text in texts) or sum(len(text) for text in texts) > 2_000_000:
            raise CPUModelError("input_limit", "Embedding source batches must total at most 2000000 characters")
        tokenizer = self._get_tokenizer("minilm")
        budget = 256 - tokenizer.num_special_tokens_to_add(pair=False)
        windows = []
        nonindexable = []
        for index, text in enumerate(texts):
            try:
                windows.extend({**window, "input_index": index} for window in token_windows(text, tokenizer, budget, overlap))
            except CPUModelError as error:
                if error.code != "no_source_tokens":
                    raise
                nonindexable.append({"input_index": index, "reason": "tokenizer_produced_no_source_tokens",
                                     "characters": len(text)})
        if len(windows) > 4096:
            raise CPUModelError("window_limit", "Embed at most 4096 windows at once")
        vectors = self.embed([window["text"] for window in windows])
        if len(vectors) != len(windows):
            raise CPUModelError("invalid_output", "Embedding window/vector counts differ; no partial result is returned")
        return {"model_profile": self.model_profile("minilm"), "offset_unit": "unicode_code_points",
                "windows": [{**window, "embedding": vector} for window, vector in zip(windows, vectors)],
                "nonindexable_inputs": nonindexable}

    def _predict_pairs(self, pairs: list[tuple[str, str]]) -> list[list[float]]:
        import torch
        tokenizer, model = self._load("deberta")
        logits = []
        with torch.inference_mode():
            for position in range(0, len(pairs), self.batch_size):
                batch = pairs[position:position + self.batch_size]
                encoded = tokenizer([pair[0] for pair in batch], [pair[1] for pair in batch],
                                    padding=True, truncation=False, return_tensors="pt").to("cpu")
                if encoded["input_ids"].shape[1] > 512:
                    raise CPUModelError("input_too_long", "Combined source/hypothesis exceeds the classifier's 512-token limit")
                logits.extend(model(**encoded).logits.tolist())
        return logits

    def score_rubrics(self, text: str, hypotheses: list[dict], *, window_overlap: int = 32) -> dict:
        """Independent hypothesis scores per exact window; no prejudice decision/threshold."""
        if not isinstance(hypotheses, list) or not 1 <= len(hypotheses) <= 64:
            raise CPUModelError("invalid_hypotheses", "Provide 1–64 explicit rubric hypotheses")
        ids = set()
        for item in hypotheses:
            if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]
                    or item["id"] in ids or not isinstance(item.get("text"), str) or not item["text"].strip()
                    or len(item["text"]) > 4000):
                raise CPUModelError("invalid_hypotheses", "Hypotheses need unique string IDs and nonempty bounded text")
            ids.add(item["id"])
        tokenizer = self._get_tokenizer("deberta")
        hypothesis_lengths = [len(tokenizer(item["text"], add_special_tokens=False, truncation=False, verbose=False)["input_ids"])
                              for item in hypotheses]
        if max(hypothesis_lengths) > 256:
            raise CPUModelError("hypothesis_too_long", "A hypothesis may use at most 256 tokens")
        budget = 512 - max(hypothesis_lengths) - tokenizer.num_special_tokens_to_add(pair=True)
        windows = token_windows(text, tokenizer, budget, window_overlap)
        if len(windows) * len(hypotheses) > 16384:
            raise CPUModelError("pair_limit", "Split the rubric/source batch into at most 16384 window/hypothesis pairs")
        pairs = [(window["text"], item["text"]) for window in windows for item in hypotheses]
        logits = self._predict_pairs(pairs)
        if len(logits) != len(pairs):
            raise CPUModelError("invalid_output", "Classifier returned an incomplete pair batch")
        result = []
        offset = 0
        for window in windows:
            scores = []
            for item in hypotheses:
                values = logits[offset]
                offset += 1
                if len(values) != 2 or not all(math.isfinite(value) for value in values):
                    raise CPUModelError("invalid_output", "Classifier logits must be two finite numbers")
                maximum = max(values)
                weights = [math.exp(value - maximum) for value in values]
                scores.append({"id": item["id"], "hypothesis": item["text"],
                               "entailment_score": weights[0] / sum(weights),
                               "logits": {"entailment": values[0], "not_entailment": values[1]}})
            result.append({**window, "scores": scores})
        return {"model_profile": self.model_profile("deberta"), "windows": result,
                "offset_unit": "unicode_code_points", "aggregation": "none",
                "score_meaning": "Uncalibrated source/hypothesis entailment; not-entailment is not contradiction or probability of prejudice.",
                "full_source_token_coverage": True, "pairs_evaluated": len(pairs)}
