#!/usr/bin/env python3
"""Follow a case or look up a criterion in the published study graph."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.bias_study.graph import case_trace, graph_index, graph_counts  # noqa: E402

DEFAULT_GRAPH = ROOT / "frontend/public/studies/muslim-coverage/evidence.jsonld"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--case", help="Editorial case slug, such as school-hijab")
    choice.add_argument("--criterion", help="Frozen criterion ID, such as discrimination_claim")
    choice.add_argument("--summary", action="store_true", help="Count records by type")
    args = parser.parse_args()

    try:
        graph = json.loads(args.graph.read_text())
        index = graph_index(graph)
        if args.case:
            note = next((node for node in index.values() if "doxa:EditorialNote" in node["@type"]
                         and node["recordId"] == args.case), None)
            if note is None:
                parser.error(f"unknown editorial case {args.case!r}")
            result = case_trace(note, index)
        elif args.criterion:
            criteria = {node["@id"] for node in index.values() if "doxa:Criterion" in node["@type"]
                        and node["recordId"] == args.criterion}
            if not criteria:
                parser.error(f"unknown criterion {args.criterion!r}")
            result = [
                {
                    "decision": node["recordId"],
                    "outcome": node["outcome"],
                    "article": index[index[node["scoredUnit"]]["fromSource"]]["recordId"],
                }
                for node in index.values() if node.get("evaluates") in criteria
            ]
        else:
            result = graph_counts(graph)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(f"cannot read the graph: {error}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
