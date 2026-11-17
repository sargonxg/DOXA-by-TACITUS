#!/usr/bin/env python3
"""Explicitly build/search an installed CPU passage index; never download weights."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doxa.cpu_models import CPUModels  # noqa: E402
from doxa.semantic import SemanticIndex  # noqa: E402
from doxa.store import Store  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "search"])
    parser.add_argument("project_id")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--text", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    store = Store(args.data_dir)
    try:
        store.init()
        index = SemanticIndex(store, CPUModels(args.model_dir, threads=args.threads))
        result = index.build(args.project_id) if args.action == "build" else index.search(args.project_id, args.text, args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()
