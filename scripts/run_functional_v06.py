#!/usr/bin/env python3
"""Prepare offline; execute only explicit, reviewed, budgeted model trials."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from doxa.functional_evaluation import execute_casebook, prepare_execution  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--casebook', type=Path, default=ROOT / 'benchmarks/evaluation-v04/functional-casebook.json')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--review', type=Path, help='Explicit human fixture-rubric review bound to this casebook')
    parser.add_argument('--provider-config', type=Path, help='Private operator JSON; never echoed')
    parser.add_argument('--budget-usd', type=float, default=0)
    parser.add_argument('--allow-remote-text', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.casebook.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Functional casebook exceeds16MiB')
        casebook = json.loads(args.casebook.read_text())
        if not args.execute:
            result = prepare_execution(casebook, limit=args.limit)
            print(json.dumps(result, indent=2))
            return 0
        if not args.review or not args.provider_config or not args.output:
            raise ValueError('Execution requires --review, --provider-config and --output')
        info = args.provider_config.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError('Provider configuration must be a private regular file(mode0600)')
        if args.provider_config.stat().st_size > 64 * 1024 or args.review.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('Configuration or review exceeds its size bound')
        result = asyncio.run(execute_casebook(casebook, json.loads(args.review.read_text()), json.loads(args.provider_config.read_text()),
                            execute=True, limit=args.limit, budget_usd=args.budget_usd, allow_remote_text=args.allow_remote_text))
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        print(json.dumps({'status': result['status'], 'executed_fixtures': result['executed_fixtures'], 'output': str(args.output),
                          'interpretation_accuracy': None, 'notice': 'Model outputs require separate human behavioral adjudication.'}))
        return 0 if result['status'] == 'completed' else 2
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
