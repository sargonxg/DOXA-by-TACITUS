"""Fetch the small, release-pinned baseline for browser QA. No hosted inference."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    release = json.loads((Path(__file__).resolve().parents[1] / "browser-workspace/release.json").read_text())
    base = "https://huggingface.co/spaces/Giuliocc/doxa-lab/resolve/" + release["source_revision"] + "/dist/"
    receipts = {}
    for name in ["model/model_manifest.json", "model/feature_spec.json", "model/vocabulary.json", "model/idf.f32", "model/coefficients.f32", "model/intercepts.f32"]:
        with urlopen(base + name, timeout=45) as response:
            data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("Unexpected fixture size: " + name)
        target = args.destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        receipts[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (args.destination / "qa-assets.json").write_text(json.dumps(receipts, indent=2) + "\n")
    print("Prepared six pinned baseline files. Zero hosted model calls.")


if __name__ == "__main__":
    main()
