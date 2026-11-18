#!/usr/bin/env python3
"""Publish frozen method contracts and a truthful offline study preflight."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from doxa.functional_evaluation import prepare_execution  # noqa: E402
from doxa.method_cards import freeze_method_cards, study_protocol  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'benchmarks')
    args = parser.parse_args(argv)
    book = json.loads((ROOT / 'benchmarks/evaluation-v04/functional-casebook.json').read_text())
    values = {'v06-method-cards.json': freeze_method_cards(), 'v06-study-protocol.json': study_protocol(),
              'v06-functional-preflight.json': prepare_execution(book, limit=5)}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in values.items():
        (args.output_dir / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'files': sorted(values), 'network_requests': 0, 'model_requests': 0, 'human_reference_labels_created': 0}))


if __name__ == '__main__':
    main()
