#!/usr/bin/env python3
"""Synthetic persistence/scheduler load check; never an AI-quality benchmark."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import httpx  # noqa: E402

from benchmark import api_json, atomic_json, checksum, hardware, import_document  # noqa: E402
from doxa.api import create_app  # noqa: E402
from doxa.store import utcnow  # noqa: E402


async def run_load(args) -> int:
    if os.environ.get("DOXA_CLOUD_STATE_URL"):
        raise RuntimeError("Load checks require an isolated local data-dir; unset DOXA_CLOUD_STATE_URL.")
    started = time.perf_counter()
    app = create_app(args.data_dir, start_workers=False)
    headers = {"x-doxa-service-token": os.environ["DOXA_INTERNAL_TOKEN"]} if os.environ.get("DOXA_INTERNAL_TOKEN") else {}
    async with app.router.lifespan_context(app):
        store = app.state.store
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 0))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", headers=headers) as client:
            project = await api_json(client, "POST", "/api/projects", json={
                "name": "Synthetic scheduler load fixture",
                "description": "Generated infrastructure records. Not the real corpus, real PDFs, OCR throughput or semantic analysis.",
            })
            batch = "load-" + project["id"]
            store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)",
                          [batch, project["id"], "Synthetic infrastructure fixture", utcnow()])
            import_started = time.perf_counter()
            for i in range(args.documents):
                doc_id = f"synthetic-{i:05d}"
                blocks = [{"id": f"{doc_id}-page-{p + 1:03d}", "text":
                           f"Synthetic infrastructure fixture document {i + 1}, page record {p + 1}. Persistence and scheduler probe.",
                           "page": p + 1, "paragraph": 1}
                          for p in range(args.pages_per_document)]
                import_document(store, project["id"], batch, {
                    "id": doc_id, "title": f"Synthetic fixture {i + 1}", "author": "Synthetic infrastructure generator",
                    "institution": "DOXA test infrastructure", "document_type": "synthetic_load_fixture",
                    "synthetic": True, "page_count": args.pages_per_document, "blocks": blocks,
                    "source_url": f"urn:doxa:synthetic-load:{doc_id}", "source_sha256": checksum(blocks),
                    "license": "CC0-1.0",
                })
            import_seconds = time.perf_counter() - import_started
            counts = store.one("SELECT COUNT(*) AS documents,SUM(page_count) AS pages FROM documents WHERE project_id=?", [project["id"]])
            paginated = []
            offset = 0
            while True:
                page = await api_json(client, "GET", f"/api/projects/{project['id']}/documents", params={"offset": offset, "limit": 100})
                if not page:
                    break
                paginated.extend(d["id"] for d in page)
                offset += len(page)
            run = await api_json(client, "POST", f"/api/projects/{project['id']}/runs", json={
                "provider": "lexical", "model": "lexical-baseline", "lens_ids": ["discourse"],
                "budget_usd": 0, "question": "Infrastructure fixture only.",
            })
            run_id = run["id"]
            first = store.one("SELECT id FROM tasks WHERE run_id=? ORDER BY ordinal,id LIMIT 1", [run_id])
            # Simulate a process that died after obtaining a lease, before any result commit.
            store.execute("UPDATE tasks SET status='running',lease_token='simulated-dead-worker',lease_until=0,attempts=1 WHERE id=?", [first["id"]])
            scheduling_started = time.perf_counter()
            while True:
                result = await app.state.work_once(run_id)
                if not result["pending"]:
                    run = result["run"]
                    break
            scheduling_seconds = time.perf_counter() - scheduling_started
            recovered = store.one("SELECT status,attempts FROM tasks WHERE id=?", [first["id"]])
            before_replay = store.one("SELECT COUNT(*) AS n FROM findings WHERE run_id=?", [run_id])["n"]
            await app.state.work_once(run_id)
            after_replay = store.one("SELECT COUNT(*) AS n FROM findings WHERE run_id=?", [run_id])["n"]
            tasks = store.one("SELECT COUNT(*) AS total,SUM(status='completed') AS completed,SUM(status='failed') AS failed FROM tasks WHERE run_id=?", [run_id])
            checks = {
                "document_count": counts["documents"] == args.documents,
                "synthetic_page_record_count": counts["pages"] == args.documents * args.pages_per_document,
                "pagination_complete": len(paginated) == args.documents and len(set(paginated)) == args.documents,
                "scheduler_complete": run["status"] == "completed" and tasks["completed"] == tasks["total"] and not tasks["failed"],
                "expired_lease_recovered": recovered["status"] == "completed" and recovered["attempts"] == 2,
                "completed_run_replay_no_duplicate_findings": before_replay == after_replay,
                "no_model_requests": run.get("usage", {}).get("requests", 0) == 0,
            }
            report = {
                "schema_version": 1, "benchmark_kind": "synthetic_infrastructure_load", "synthetic": True,
                "generated_at": utcnow(), "documents": args.documents,
                "synthetic_page_records": args.documents * args.pages_per_document,
                "hardware": hardware(), "project_id": project["id"], "run_id": run_id,
                "checks": checks, "passed": all(checks.values()), "tasks": tasks,
                "timing_seconds": {"import": import_seconds, "scheduler": scheduling_seconds,
                                   "total": time.perf_counter() - started},
                "usage": run.get("usage", {}), "interpretation_accuracy": None,
                "limitations": [
                    "Generated page records test storage, pagination and scheduling, not PDF extraction, OCR, large binary upload or real document page throughput.",
                    "No LLM inference or semantic quality measurement is performed.",
                    "The real twenty-document corpus is benchmarked separately.",
                ],
            }
            atomic_json(args.output, report)
            print(json.dumps({"passed": report["passed"], "documents": args.documents,
                              "synthetic_page_records": report["synthetic_page_records"],
                              "seconds": report["timing_seconds"]}, indent=2), flush=True)
            return 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=1000)
    parser.add_argument("--pages-per-document", type=int, default=10)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "load-test")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks" / "load-test.json")
    args = parser.parse_args()
    if args.documents < 1 or args.pages_per_document < 1:
        parser.error("Document and page-record counts must be positive.")
    try:
        return asyncio.run(run_load(args))
    except Exception as error:
        print(f"Load check failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
