#!/usr/bin/env python3
"""Build and inspect a self-contained application wheel without fetching models."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if not (ROOT / "frontend/dist/index.html").is_file():
        parser.error("Run npm --prefix frontend run build first")
    args.output.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                    "--wheel-dir", str(args.output.resolve()), str(ROOT)],
                   env={**os.environ, "DOXA_REQUIRE_FRONTEND": "1"}, check=True)
    candidates = sorted(args.output.glob("doxa_by_tacitus-*.whl"), key=lambda path: path.stat().st_mtime)
    if not candidates:
        raise RuntimeError("No DOXA wheel was produced")
    with zipfile.ZipFile(candidates[-1]) as archive:
        names = archive.namelist()
        if "doxa/static/index.html" not in names or not any(name.startswith("doxa/static/assets/") for name in names):
            raise RuntimeError("Release wheel lacks its compiled frontend")
        if any(name.startswith(("data/", "corpus/", "benchmarks/")) for name in names):
            raise RuntimeError("Unexpected workspace or full evidence dataset in wheel")
    print(f"Verified application wheel: {candidates[-1].name}")


if __name__ == "__main__":
    main()
