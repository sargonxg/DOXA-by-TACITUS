#!/usr/bin/env python3
"""Inventory locked default Python and compiled-UI dependencies; no legal clearance claim."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def inventory(root=ROOT):
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    lock = (root / "infra/requirements-runtime.lock").read_text()
    pins = dict(re.findall(r"^([a-zA-Z0-9_.-]+)==([^\s;\\]+)", lock, re.MULTILINE))
    if "pymupdf" in {name.lower() for name in pins}:
        raise ValueError("The default runtime lock unexpectedly includes the legacy AGPL/commercial PDF dependency")
    packages = []
    for name, version in sorted(pins.items()):
        license_id, evidence = "NOASSERTION", "Installed matching metadata unavailable; review the pinned distribution license."
        try:
            distribution = importlib.metadata.distribution(name)
            if distribution.version == version:
                expression = distribution.metadata.get("License-Expression")
                if expression and re.fullmatch(r"[A-Za-z0-9.()+ -]{1,200}", expression):
                    license_id, evidence = expression, "License-Expression from matching installed distribution metadata; not a legal audit."
        except importlib.metadata.PackageNotFoundError:
            pass
        packages.append({"name": name, "versionInfo": version, "SPDXID": "SPDXRef-Python-" + re.sub(r"[^A-Za-z0-9.-]", "-", name),
                         "downloadLocation": "NOASSERTION", "filesAnalyzed": False,
                         "licenseConcluded": "NOASSERTION", "licenseDeclared": license_id,
                         "copyrightText": "NOASSERTION", "comment": evidence,
                         "externalRefs": [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": f"pkg:pypi/{name}@{version}"}]})
    frontend = json.loads((root / "frontend/package-lock.json").read_text())
    for key, entry in sorted(frontend.get("packages", {}).items()):
        if not key or entry.get("dev") or not entry.get("version"):
            continue
        name = key.rsplit("node_modules/", 1)[-1]
        identifier = hashlib.sha256(key.encode()).hexdigest()[:16]
        packages.append({"name": name, "versionInfo": entry["version"], "SPDXID": "SPDXRef-UI-" + identifier,
                         "downloadLocation": entry.get("resolved", "NOASSERTION"), "filesAnalyzed": False,
                         "licenseConcluded": "NOASSERTION", "licenseDeclared": entry.get("license", "NOASSERTION"),
                         "copyrightText": "NOASSERTION",
                         "comment": "Frontend runtime dependency in package-lock; bundling/tree-shaking may exclude some files."})
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unrecorded"
    fingerprint = hashlib.sha256((lock + json.dumps(frontend, sort_keys=True)).encode()).hexdigest()
    return {"spdxVersion": "SPDX-2.3", "dataLicense": "CC0-1.0", "SPDXID": "SPDXRef-DOCUMENT",
            "name": "DOXA default Python and browser runtime " + project["version"],
            "documentNamespace": f"https://github.com/sargonxg/DOXA-by-TACITUS/sbom/{project['version']}/{fingerprint}",
            "creationInfo": {"creators": ["Tool: DOXA-runtime-inventory-1"], "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
            "documentComment": f"Source revision {revision}; inventory fingerprint {fingerprint}. Python universal-lock union; optional model/OCR/publication/legacy extras, OS libraries and container layers require separate inventories. NOASSERTION is not license clearance. Working-tree changes may exist at generation time.",
            "packages": packages,
            "relationships": [{"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": p["SPDXID"]} for p in packages]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/runtime-sbom-v06.spdx.json")
    args = parser.parse_args()
    value = inventory()
    args.output.write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"packages": len(value["packages"]), "unresolved_declared_licenses": sum(p["licenseDeclared"] == "NOASSERTION" for p in value["packages"]), "output": str(args.output), "legal_clearance": False}))


if __name__ == "__main__":
    main()
