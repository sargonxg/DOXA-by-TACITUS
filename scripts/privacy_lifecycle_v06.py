#!/usr/bin/env python3
"""Explicit private operator actions for retention and deletion recovery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init-ledger", "restore-check", "retention-tick", "status", "record-expiry"))
    parser.add_argument("--data-dir", required=True, help="Native customer workspace; never the ledger directory")
    parser.add_argument("--env-file", help="Private0600 operator environment file")
    parser.add_argument("--project-id")
    parser.add_argument("--target", choices=("backup", "provider"))
    parser.add_argument("--reference", help="External reference; only its SHA-256 enters the ledger")
    parser.add_argument("--expires-at", help="Timezone-aware ISO8601 expiry")
    parser.add_argument("--attest-expired", action="store_true", help="Record an operator attestation; not independent verification")
    args = parser.parse_args(argv)
    from doxa.cli import load_private_environment
    from doxa import retention
    from doxa.store import Store
    if args.env_file:
        load_private_environment(args.env_file)
    store = Store(args.data_dir)
    try:
        store.init()
        if args.action == "init-ledger":
            result = retention.initialize_deletion_ledger(store)
        elif args.action == "restore-check":
            result = retention.restore_guard(store)
        elif args.action == "retention-tick":
            result = retention.retention_tick(store)
        elif args.action == "record-expiry":
            if not all((args.project_id, args.target, args.reference, args.expires_at)):
                parser.error("record-expiry requires --project-id, --target, --reference and --expires-at")
            result = retention.record_external_retention(store, args.project_id, target=args.target,
                                                        reference=args.reference, expires_at=args.expires_at,
                                                        status="expiry_attested" if args.attest_expired else "expected")
        else:
            ledger = retention.DeletionLedger(store).read()
            events = ledger["events"]
            result = {"schema_version": ledger["schema_version"], "sequence": ledger["sequence"],
                      "deletion_obligations": sum(event["kind"] == "requested" for event in events),
                      "completion_records": sum(event["kind"] == "completed" for event in events),
                      "external_retention_records": sum(event["kind"] == "external_retention" for event in events),
                      "backup_expiry_independently_verified": False}
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, retention.RestoreBlockedError):
        # Underlying command errors can contain private paths or cloud details.
        print("Privacy lifecycle action could not complete. Verify private configuration, current ledger, active writers and storage; do not serve a restored workspace until restore-check succeeds.", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
