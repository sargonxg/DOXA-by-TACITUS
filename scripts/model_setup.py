#!/usr/bin/env python3
"""Explicit, bounded installation of reviewed public CPU model artifacts.

No inference startup calls this installer. Only pinned safetensors, tokenizer,
configuration and model-card files are accepted; no remote code or pickle.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from doxa.cpu_models import CPUModelError, CPUModels, verify_installation  # noqa: E402
from doxa.model_registry import (  # noqa: E402
    MANIFEST_VERSION, default_model_dir, get_model_profile, list_model_profiles,
)

MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_SMALL_ARTIFACT_BYTES = 32 * 1024 * 1024
DISK_RESERVE_BYTES = 512 * 1024 * 1024


def _open(url: str):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "DOXA-explicit-model-setup/3"}), timeout=90)


def download_model(model_key: str, model_dir: Path, *, max_download_bytes: int = 1200 * 1024 * 1024) -> dict:
    profile = get_model_profile(model_key)
    if profile["provider"] != "huggingface_cpu":
        raise ValueError("Only minilm/deberta are installed here; Ollama and cloud models use their explicit provider setup")
    if max_download_bytes <= 0:
        raise ValueError("Download byte limit must be positive")
    root = Path(model_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / model_key
    if destination.exists():
        return verify_installation(model_key, root)
    model_url = urllib.parse.quote(profile["model"], safe="/")
    revision = profile["revision"]
    with _open(f"https://huggingface.co/api/models/{model_url}/revision/{revision}?blobs=true") as response:
        metadata_bytes = response.read(MAX_METADATA_BYTES + 1)
    if len(metadata_bytes) > MAX_METADATA_BYTES:
        raise ValueError("Upstream model metadata exceeds the installer limit")
    metadata = json.loads(metadata_bytes)
    if metadata.get("sha") != revision:
        raise ValueError("Upstream metadata does not match the pinned model revision")
    declared_license = str(metadata.get("cardData", {}).get("license", "")).lower()
    if declared_license != profile["license"].lower():
        raise ValueError("Upstream model license differs from the reviewed registry")
    siblings = {item["rfilename"]: item for item in metadata.get("siblings", [])}
    selected = []
    for filename in profile["files"]:
        item = siblings.get(filename)
        if not item or not isinstance(item.get("size"), int) or item["size"] < 1:
            raise ValueError(f"Missing artifact size for pinned file: {filename}")
        if filename != "model.safetensors" and item["size"] > MAX_SMALL_ARTIFACT_BYTES:
            raise ValueError(f"Configuration/tokenizer artifact exceeds its limit: {filename}")
        selected.append((filename, item))
    expected_bytes = sum(item["size"] for _, item in selected)
    byte_limit = min(max_download_bytes, profile["max_download_bytes"])
    if expected_bytes > byte_limit:
        raise ValueError(f"Pinned bundle needs {expected_bytes} bytes, above the {byte_limit}-byte download limit")
    if shutil.disk_usage(root).free < expected_bytes + DISK_RESERVE_BYTES:
        raise ValueError("Insufficient disk space for the bundle and a 512MiB safety reserve")
    stage = Path(tempfile.mkdtemp(prefix=f".{model_key}-", dir=root))
    artifacts = []
    actual_total = 0
    try:
        for filename, item in selected:
            path = stage / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            checksum = hashlib.sha256()
            actual = 0
            url = f"https://huggingface.co/{model_url}/resolve/{revision}/{urllib.parse.quote(filename, safe='/')}"
            with _open(url) as response, path.open("xb") as handle:
                while chunk := response.read(1024 * 1024):
                    actual += len(chunk)
                    actual_total += len(chunk)
                    if actual > item["size"] or actual_total > byte_limit:
                        raise ValueError(f"Download exceeded the declared artifact/bundle size: {filename}")
                    handle.write(chunk)
                    checksum.update(chunk)
            if actual != item["size"]:
                raise ValueError(f"Incomplete download: {filename}")
            digest = checksum.hexdigest()
            upstream_digest = item.get("lfs", {}).get("sha256")
            if upstream_digest and upstream_digest != digest:
                raise ValueError(f"Upstream SHA256 mismatch: {filename}")
            artifacts.append({"path": filename, "bytes": actual, "sha256": digest,
                              "upstream_sha256": upstream_digest})
        manifest = {"manifest_version": MANIFEST_VERSION, "model_key": model_key,
                    "model": profile["model"], "revision": revision, "license": profile["license"],
                    "license_evidence": "README.md", "model_card": profile["model_card"],
                    "installed_at": datetime.now(timezone.utc).isoformat(), "artifacts": artifacts,
                    "download_bytes": actual_total, "registry_version": profile["registry_version"],
                    "runtime_policy": "cpu; safetensors only; local_files_only; trust_remote_code=False"}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        # Rename only a complete bundle. Concurrent installers do not replace an installation.
        try:
            stage.rename(destination)
        except OSError:
            if not destination.exists():
                raise
        return verify_installation(model_key, root)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["list", "download", "verify", "health", "smoke"])
    parser.add_argument("models", nargs="*", help="Explicit registry IDs: minilm and/or deberta")
    parser.add_argument("--model-dir", type=Path, default=default_model_dir())
    parser.add_argument("--max-download-mib", type=int, default=1200, help="Hard byte cap per explicitly requested model")
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        if args.action == "list":
            result = list_model_profiles()
        elif args.action in {"download", "verify"}:
            if not args.models:
                parser.error("Name the model(s) explicitly; no default download is performed")
            result = []
            for key in args.models:
                installation = (download_model(key, args.model_dir, max_download_bytes=args.max_download_mib * 1024 * 1024)
                                if args.action == "download" else verify_installation(key, args.model_dir))
                result.append({"model_profile": installation["model_profile"],
                               "manifest": installation["manifest"], "hashes_verified": True})
        else:
            runtime = CPUModels(args.model_dir, threads=args.threads)
            if args.action == "health":
                result = runtime.health(args.models[0] if args.models else None, verify_artifacts=True)
            else:
                texts = ["The newspaper condemns threats against Jewish people.",
                         "The report describes support for equal rights for Muslim residents."]
                result = {"health": runtime.health(verify_artifacts=True),
                          "embedding_dimensions": [len(vector) for vector in runtime.embed(texts)],
                          "nli": runtime.score_rubrics(texts[0], [
                              {"id": "condemnation", "text": "The newspaper condemns threats against Jewish people."},
                              {"id": "endorsement", "text": "The newspaper endorses threats against Jewish people."}]),
                          "note": "Runtime smoke only; these two inputs are not an accuracy evaluation."}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (CPUModelError, ValueError, OSError, urllib.error.URLError) as error:
        print(json.dumps({"ok": False, "code": getattr(error, "code", "setup_failed"), "details": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
