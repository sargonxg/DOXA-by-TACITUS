#!/usr/bin/env python3
"""Run a separately budgeted second-model comparison of a recorded v3 run.

The selected text leaves the machine only with --allow-remote-text. Existing
primary results and human reviews are never changed. Agreement is not accuracy.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True, help="Existing primary run in this local workspace")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package-dir", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--allow-remote-text", action="store_true", required=True)
    parser.add_argument("--budget", type=float, default=1.5, help="Total verifier run cap, not an incremental allowance; maximum $2")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if not math.isfinite(args.budget) or not 0 < args.budget <= 2:
        parser.error("Budget must be positive and at most $2")
    if not 1 <= args.concurrency <= 8:
        parser.error("Concurrency must be between 1 and 8")
    if not (args.data_dir / "doxa.sqlite3").is_file():
        parser.error("Use an existing recorded workspace; no new corpus is imported")
    return args


async def verify(args) -> int:
    import httpx
    from doxa.api import create_app
    from doxa.store import utcnow
    from doxa.verification import compare_runs
    from scripts.benchmark import api_json, run_worker_pool
    from scripts.benchmark_v3 import atomic_json
    from scripts.package_v3 import package_run

    if os.environ.get("DOXA_CLOUD_STATE_URL"):
        raise ValueError("Use an isolated local benchmark workspace")
    checkpoint = args.output.with_suffix(args.output.suffix + ".checkpoint.json")
    if checkpoint.exists() and not args.resume:
        raise ValueError("Existing verifier checkpoint: use --resume rather than creating duplicate paid work")
    if args.resume and not checkpoint.exists():
        raise ValueError("Resume requires the original checkpoint")
    state = json.loads(checkpoint.read_text()) if args.resume else {
        "primary_run_id": args.run_id, "created_at": utcnow(), "sessions": [], "elapsed_seconds": 0,
    }
    if state["primary_run_id"] != args.run_id:
        raise ValueError("Resume cannot change the primary run")
    started, elapsed_before = time.perf_counter(), state.get("elapsed_seconds", 0)
    state["sessions"].append({"started_at": utcnow(), "budget_usd": args.budget, "concurrency": args.concurrency})
    app = create_app(args.data_dir, start_workers=False, use_saved_showcase=False)
    async with app.router.lifespan_context(app):
        store = app.state.store
        primary = store.one("SELECT id,project_id,status FROM runs WHERE id=?", [args.run_id])
        if not primary or primary["status"] not in {"completed", "partial"}:
            raise ValueError("Choose a settled completed or partial primary run")
        project_id = primary["project_id"]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            if not state.get("verifier_run_id"):
                run = await api_json(client, "POST", f"/api/v3/projects/{project_id}/verification",
                                     json={"run_id": args.run_id, "allow_remote_text": True, "budget_usd": args.budget})
                state["verifier_run_id"] = run["id"]
            else:
                run = await api_json(client, "GET", f"/api/runs/{state['verifier_run_id']}")
                if run["project_id"] != project_id or run["config"].get("verification_of") != args.run_id:
                    raise ValueError("Checkpoint is not this primary run's verifier")
                if run["status"] in {"partial", "failed", "cancelled", "paused_budget"}:
                    run = await api_json(client, "POST", f"/api/runs/{run['id']}/resume", json={"budget_usd": args.budget})
                store.execute("UPDATE tasks SET lease_until=0 WHERE run_id=? AND status='running'", [run["id"]])
            atomic_json(checkpoint, state)
            lock, last_log = asyncio.Lock(), 0.0

            async def save():
                state["elapsed_seconds"] = elapsed_before + time.perf_counter() - started
                state["status"] = run["status"]
                atomic_json(checkpoint, state)
                result = compare_runs(store, project_id, args.run_id, run["id"])
                result.update({"generated_at": utcnow(), "sessions": state["sessions"],
                               "elapsed_seconds": state["elapsed_seconds"], "usage": run.get("usage", {}),
                               "budget_accounted_usd": run.get("spent_usd", 0)})
                atomic_json(args.output, result)
                return result

            async def settled(result, error):
                nonlocal run, last_log
                async with lock:
                    run = await api_json(client, "GET", f"/api/runs/{state['verifier_run_id']}")
                    comparison = await save()
                    if time.perf_counter() - last_log > 5 or run["status"] not in {"queued", "running"}:
                        print(f"Verifier {run['status']}; {comparison['verifier_units_accepted']} accepted selected units", flush=True)
                        last_log = time.perf_counter()
                    return error is None and run["status"] in {"queued", "running"}

            try:
                problems = await run_worker_pool(app.state.work_once, run["id"], args.concurrency, settled)
            finally:
                run = await api_json(client, "GET", f"/api/runs/{state['verifier_run_id']}")
                await save()
            if args.package_dir and run["status"] in {"completed", "partial"}:
                package_run(store, run["id"], args.package_dir,
                            benchmark={"kind": "independent_model_subset", "sessions": state["sessions"],
                                       "elapsed_seconds": state["elapsed_seconds"]})
            return 0 if run["status"] == "completed" and not problems else 1


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.env_file:
        from dotenv import load_dotenv
        if not args.env_file.is_file():
            print("Environment file unavailable", file=sys.stderr)
            return 2
        load_dotenv(args.env_file, override=True)
    try:
        return asyncio.run(verify(args))
    except KeyboardInterrupt:
        print("Interrupted; use the original arguments and --resume.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Verification stopped ({type(error).__name__}); inspect the local checkpoint.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
