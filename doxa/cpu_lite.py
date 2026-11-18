"""Optional offline ONNX embedding candidate; no download or model promotion.

A local operator must export and inspect the bundle explicitly. Checksums and
declared base identity establish bundle identity, not semantic parity with its
base model. Benchmark/evaluate candidates before choosing them for a project.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .cpu_models import CPUModelError
from .model_registry import get_model_profile

MAX_BUNDLE_BYTES = 160 * 1024 * 1024


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_bundle(directory):
    root = Path(directory)
    if root.is_symlink() or any(parent.is_symlink() for parent in root.absolute().parents):
        raise CPUModelError("invalid_installation", "ONNX bundle must not use symbolic-link directories")
    root = root.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise CPUModelError("model_not_installed", "Explicitly prepare a local ONNX bundle; no automatic export or download occurs")
    base = get_model_profile("minilm")
    try:
        if manifest_path.stat().st_size > 65536:
            raise ValueError("ONNX manifest is oversized")
        manifest = json.loads(manifest_path.read_text())
        if (manifest.get("schema_version") != "doxa-onnx-embedding-1"
                or manifest.get("base_model") != base["model"]
                or manifest.get("base_revision") != base["revision"]
                or manifest.get("license") != base["license"]
                or manifest.get("precision") not in {"int8", "fp32"}):
            raise ValueError("ONNX manifest identity does not match the declared MiniLM candidate contract")
        artifacts = manifest["artifacts"]
        if not isinstance(artifacts, list) or len(artifacts) != 2 or {item["path"] for item in artifacts} != {"model.onnx", "tokenizer.json"}:
            raise ValueError("The ONNX bundle needs exactly model.onnx and tokenizer.json")
        total = 0
        for item in artifacts:
            path = root / item["path"]
            if path.is_symlink() or not path.is_file() or path.stat().st_size != item["bytes"]:
                raise ValueError("ONNX artifact is missing, linked or changed")
            total += item["bytes"]
            if total > MAX_BUNDLE_BYTES:
                raise ValueError("ONNX bundle exceeds its 160 MiB bound")
            if _sha256(path) != item["sha256"]:
                raise ValueError("ONNX artifact checksum differs from its manifest")
    except (ValueError, TypeError, KeyError, OSError) as error:
        raise CPUModelError("invalid_installation", str(error)) from error
    return {"directory": root, "manifest": manifest, "manifest_sha256": _sha256(manifest_path),
            "hashes_verified": True, "quality_status": "unqualified_candidate",
            "automatic_downloads": False}


class ONNXEmbeddings:
    """Bounded feature extraction candidate, intentionally separate from CPUModels."""
    def __init__(self, directory, *, threads=2, batch_size=2):
        if not isinstance(threads, int) or not 1 <= threads <= 8 or not isinstance(batch_size, int) or not 1 <= batch_size <= 16:
            raise ValueError("ONNX CPU threads must be 1–8 and batch size 1–16")
        self.bundle = inspect_bundle(directory)
        self.threads, self.batch_size = threads, batch_size
        self._session = self._tokenizer = None

    def model_profile(self):
        manifest = self.bundle["manifest"]
        return {"id": "minilm-onnx-candidate", "role": "retrieval", "model": manifest["base_model"],
                "base_revision": manifest["base_revision"], "derived_manifest_sha256": self.bundle["manifest_sha256"],
                "runtime": "onnxruntime_cpu", "precision": manifest["precision"], "max_input_tokens": 256,
                "precision_verification": "operator_declared_not_graph_verified",
                "dimensions": 384, "pooling": "attention_mask_mean_then_l2", "device": "cpu",
                "threads": self.threads, "batch_size": self.batch_size,
                "quality_status": "unqualified_candidate", "semantic_parity_measured": False,
                "notice": "Retrieval similarity is not a prejudice or stance verdict. Quantization can change scores."}

    def _load(self):
        if self._session is not None:
            return
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as error:
            raise CPUModelError("dependencies_missing", "Install the optional cpu-lite dependencies; the default application needs no ONNX runtime") from error
        # Recheck files immediately before loading; never trust a cached health result.
        inspect_bundle(self.bundle["directory"])
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_mem_pattern = False
        directory = self.bundle["directory"]
        try:
            tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
            tokenizer.no_truncation()
            tokenizer.no_padding()
            session = ort.InferenceSession(str(directory / "model.onnx"), sess_options=options,
                                           providers=["CPUExecutionProvider"])
            inputs = {item.name: item for item in session.get_inputs()}
            if not {"input_ids", "attention_mask"}.issubset(inputs) or set(inputs) - {"input_ids", "attention_mask", "token_type_ids"}:
                raise ValueError("ONNX model input contract must be MiniLM token IDs, mask and optional type IDs")
            if any(item.type != "tensor(int64)" for item in inputs.values()):
                raise ValueError("ONNX model inputs must be int64 tensors")
            if "last_hidden_state" not in {item.name for item in session.get_outputs()}:
                raise ValueError("ONNX model must expose last_hidden_state")
        except Exception as error:
            raise CPUModelError("load_failed", f"The local ONNX candidate failed its runtime contract: {type(error).__name__}") from error
        self._session, self._tokenizer = session, tokenizer

    def embed(self, texts):
        if not isinstance(texts, list) or len(texts) > 4096 or any(not isinstance(text, str) or not text.strip() or len(text) > 100000 for text in texts):
            raise CPUModelError("input_limit", "Provide at most 4096 nonempty strings of at most 100000 characters")
        if sum(map(len, texts)) > 2_000_000:
            raise CPUModelError("input_limit", "An ONNX embedding request must total at most 2000000 characters")
        if not texts:
            return []
        self._load()
        import numpy as np
        vectors = []
        for position in range(0, len(texts), self.batch_size):
            batch = self._tokenizer.encode_batch(texts[position:position + self.batch_size], add_special_tokens=True)
            if any(len(item.ids) > 256 for item in batch):
                raise CPUModelError("input_too_long", "MiniLM supports 256 tokens; split source windows explicitly, without truncating text")
            if any(len(item.ids) <= 2 for item in batch):
                raise CPUModelError("no_source_tokens", "An input produced no source tokens")
            maximum = max(len(item.ids) for item in batch)
            ids = np.zeros((len(batch), maximum), dtype=np.int64)
            mask, types = np.zeros_like(ids), np.zeros_like(ids)
            for index, item in enumerate(batch):
                ids[index, :len(item.ids)] = item.ids
                mask[index, :len(item.ids)] = 1
                types[index, :len(item.ids)] = item.type_ids
            available = {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}
            inputs = {item.name: available[item.name] for item in self._session.get_inputs()}
            hidden = np.asarray(self._session.run(["last_hidden_state"], inputs)[0])
            if hidden.shape != (len(batch), maximum, 384) or not np.isfinite(hidden).all():
                raise CPUModelError("invalid_output", "ONNX output is not a finite MiniLM hidden-state tensor")
            pooled = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1)[:, None]
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            if not np.isfinite(norms).all() or (norms <= 0).any():
                raise CPUModelError("invalid_output", "ONNX output contains an empty or invalid vector")
            vectors.extend((pooled / norms).tolist())
        return vectors
