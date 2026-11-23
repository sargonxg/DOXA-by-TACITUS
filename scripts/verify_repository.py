#!/usr/bin/env python3
"""Verify documentation links, release versions and local artifact checksums offline."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(root=ROOT):
    failures, links, artifacts = [], 0, 0
    tracked = None
    try:
        listing = subprocess.run(["git", "ls-files", "--cached", "-z"], cwd=root,
                                 capture_output=True, check=True).stdout
        tracked = {name.decode() for name in listing.split(b"\0") if name}
    except (OSError, subprocess.CalledProcessError):
        pass  # Source ZIPs have no Git index; filesystem/hash checks still apply.

    def check_tracked(path):
        if tracked is None:
            return
        try:
            name = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            failures.append(f"Local publication points outside the repository: {path.name}")
            return
        present = any(item.startswith(name.rstrip("/") + "/") for item in tracked) if path.is_dir() else name in tracked
        if not present:
            failures.append(f"Linked artifact is not tracked in Git: {name}")
    documents = [root / name for name in ("README.md", "CONTRIBUTING.md", "CHANGELOG.md", "benchmarks/README.md", "frontend/README.md")]
    documents += sorted((root / "docs").glob("*.md"))
    for document in documents:
        markdown = document.read_text(encoding="utf-8")
        targets = re.findall(r'!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)', markdown)
        targets.extend(re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', markdown))
        for target in targets:
            target = target.strip("<>").split("#")[0]
            if not target or "://" in target or target.startswith(("mailto:", "/")):
                continue
            target = target.split("?")[0]
            links += 1
            if not (document.parent / target).exists():
                failures.append(f"Missing link in {document.relative_to(root)}: {target}")
            else:
                check_tracked(document.parent / target)
    for manifest in sorted((root / "examples").rglob("*.manifest.json")):
        value = json.loads(manifest.read_text())
        files = value.get("files", [])
        entries = files.values() if isinstance(files, dict) else files
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("sha256"):
                continue
            name = entry.get("file") or entry.get("name") or entry.get("path")
            if not isinstance(name, str) or "://" in name:
                continue
            candidate = (manifest.parent / name).resolve()
            if not candidate.exists():
                candidate = (root / name).resolve()
            if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
                failures.append(f"Invalid artifact path in {manifest.relative_to(root)}: {name}")
                continue
            artifacts += 1
            check_tracked(candidate)
            if file_hash(candidate) != entry["sha256"]:
                failures.append(f"Artifact hash mismatch: {candidate.relative_to(root)}")
            expected_bytes = entry.get("bytes")
            if expected_bytes is not None and expected_bytes != candidate.stat().st_size:
                failures.append(f"Artifact byte count mismatch: {candidate.relative_to(root)}")
    detached = root / "examples/v04-evidence-pack.zip.receipt.json"
    if detached.exists():
        value = json.loads(detached.read_text())
        candidate = (root / value["file"]).resolve()
        artifacts += 1
        if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
            failures.append("Missing or invalid detached evidence ZIP")
        elif file_hash(candidate) != value["sha256"] or candidate.stat().st_size != value["bytes"]:
            failures.append("Detached evidence ZIP hash/size mismatch")
        else:
            check_tracked(candidate)
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    python_version = re.search(r'__version__\s*=\s*"([^"]+)"', (root / "doxa/__init__.py").read_text()).group(1)
    if python_version != version:
        failures.append("Python module/project versions differ")
    for folder in ("frontend", "infra/cloudflare"):
        for name in ("package.json", "package-lock.json"):
            value = json.loads((root / folder / name).read_text())
            if value["version"] != version or ("packages" in value and value["packages"][""]["version"] != version):
                failures.append(f"Version differs: {folder}/{name}")
    return {"version": version, "local_links_checked": links, "artifact_hashes_checked": artifacts,
            "git_index_checked": tracked is not None,
            "failures": failures, "network_requests": 0, "model_requests": 0}


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, indent=2))
    sys.exit(bool(result["failures"]))
