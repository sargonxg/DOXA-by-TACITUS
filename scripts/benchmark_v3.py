#!/usr/bin/env python3
"""Benchmark whole real documents through the v3 API and shared durable scheduler.

Inference runs only when this command is invoked with a configured provider.
Environment values are never printed or saved. Source sampling selects complete
articles, never short passages. Interpretation accuracy remains unclaimed.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def checksum(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def source_selection(collection: str, limit: int | None = None, root: Path = ROOT) -> dict:
    """Freeze identities/hashes without loading corpus bodies into memory."""
    if collection in {"institutions", "political"}:
        paths = sorted((root / "corpus" / "documents").glob("*.json"))
        if len(paths) != 20:
            raise ValueError("Expected the twenty frozen institutional sources")
        expected_count = 20
        if collection == "political":
            paths = [path for path in paths if path.stem in {"us01", "us03", "us04", "uk07"}]
            expected_count = 4
        paths = paths[:limit] if limit else paths
        selected = []
        for path in paths:
            source = json.loads(path.read_text(encoding="utf-8"))
            selected.append({"source_id": source["id"], "content_sha256": source["content_sha256"], "path": str(path.relative_to(root))})
        return {"collection": collection, "documents": len(selected), "sampled_documents": len(selected) != expected_count,
                "passage_sampling": False, "selection_policy": "Four purposive political-framing teaching sources; different genres and dates, no matched fairness claim. Full source text." if collection == "political" else "Frozen source order; all blocks of each selected source.",
                "sources": selected, "manifest_sha256": checksum(selected)}
    base = root / "corpus" / "news"
    manifest_path, collection_path = base / "manifest.jsonl", base / "collection.json"
    if not manifest_path.exists() or not collection_path.exists():
        raise ValueError("The frozen licensed news manifest is unavailable; finish the collector before benchmarking")
    metadata = json.loads(collection_path.read_text())
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if metadata.get("status") != "complete" or metadata.get("article_count") != len(rows) or len(rows) != 5000:
        raise ValueError("The news benchmark requires the exact frozen 5,000-article collection")
    if collection == "news-sample":
        count = limit or 20
        positions = [min(len(rows) - 1, int((index + 0.5) * len(rows) / count)) for index in range(count)]
        rows = [rows[index] for index in positions]
    elif limit is not None:
        raise ValueError("Use news-sample for a subset; the news collection never narrows silently")
    selected = [{"source_id": row["id"], "content_sha256": row["content_sha256"],
                 "path": str((base / row["metadata_path"]).relative_to(root))} for row in rows]
    return {"collection": collection, "documents": len(selected), "sampled_documents": collection == "news-sample",
            "passage_sampling": False, "selection_policy": "Deterministic equal-position source strata; complete articles." if collection == "news-sample" else "All 5,000 frozen articles.",
            "sources": selected, "manifest_sha256": file_hash(manifest_path), "collection_sha256": file_hash(collection_path),
            "publisher": metadata.get("publisher"), "frame_start": metadata.get("frame_start"), "frame_end": metadata.get("frame_end"),
            "selection_notice": metadata.get("selection_notice")}


def import_selection(store, project_id: str, selection: dict) -> list[dict]:
    from scripts.benchmark import import_document
    from doxa.store import utcnow
    if selection["collection"] == "news":
        from doxa.news import import_news
        result = import_news(store, project_id, ROOT)
        if result["documents"] != 5000:
            raise ValueError("News import did not retain the full frozen selection")
    else:
        batch_id = "v3-benchmark-" + project_id
        store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)", [batch_id, project_id, selection["collection"], utcnow()])
        for member in selection["sources"]:
            source = json.loads((ROOT / member["path"]).read_text(encoding="utf-8"))
            import_document(store, project_id, batch_id, source)
    by_source = {}
    for row in store.query("SELECT id,sha256,metadata_json FROM documents WHERE project_id=?", [project_id]):
        metadata = json.loads(row["metadata_json"])
        by_source[metadata.get("source_id")] = row
    snapshots = []
    for member in selection["sources"]:
        row = by_source.get(member["source_id"])
        if row is None:
            raise ValueError("A frozen source is missing after import")
        snapshots.append({"id": row["id"], "sha256": row["sha256"], "source_id": member["source_id"]})
    return snapshots


def progress_summary(store, state: dict, run: dict) -> dict:
    states = store.query("SELECT status,COUNT(*) count FROM v3_unit_statuses WHERE run_id=? GROUP BY status", [run["id"]])
    decisions = store.query("SELECT result,criterion_kind,COUNT(*) count FROM v3_assessments WHERE run_id=? GROUP BY result,criterion_kind", [run["id"]])
    return {"schema_version": "3.0", "benchmark_kind": "real_complete_document_contextual_analysis",
            "generated_at": __import__("doxa.store", fromlist=["utcnow"]).utcnow(),
            "collection": {key: value for key, value in state["selection"].items() if key != "sources"},
            "run_id": run["id"], "project_id": run["project_id"], "status": run["status"],
            "config": run["config"], "model_profile": run.get("model_profile", {}), "progress": run.get("progress", {}),
            "usage": run.get("usage", {}), "budget_accounted_usd": run.get("spent_usd", 0),
            "unit_states": states, "decision_counts": decisions, "elapsed_seconds": state.get("elapsed_seconds", 0),
            "sessions": state.get("sessions", []), "error_tasks": store.one("SELECT COUNT(*) count FROM tasks WHERE run_id=? AND error IS NOT NULL", [run["id"]])["count"],
            "interpretation_accuracy": None, "independent_human_labels": False,
            "notice": "All text blocks of each selected article/document are selected. Coverage and failures remain explicit. Exact anchors do not establish interpretation accuracy."}


async def run_benchmark(args) -> int:
    # Import after --env-file is loaded; dotenv and provider initialization must
    # never capture an unintended account before explicit server configuration.
    import httpx
    from doxa.api import create_app
    from doxa.providers import get_provider
    from doxa.store import utcnow
    from scripts.benchmark import api_json, hardware, run_worker_pool
    from scripts.package_v3 import package_run

    if os.environ.get("DOXA_CLOUD_STATE_URL"):
        raise ValueError("Use a local isolated data-dir, not a remote application database, for this benchmark")
    selection = source_selection(args.collection, args.limit)
    config = {"provider": args.provider, "model": args.model or "", "methodology_version": "3.0",
              "allow_remote_text": args.provider == "cloudflare", "lens_ids": args.lens_ids,
              "question": args.question, "persona": args.persona, "budget_usd": args.budget_usd}
    engine_files = ["doxa/contextual.py", "doxa/models.py", "doxa/lenses.py"]
    engine_fingerprint = checksum({name: file_hash(ROOT / name) for name in engine_files})
    from doxa.lenses import get_lens
    engine_components = {"contextual": file_hash(ROOT / "doxa/contextual.py"),
                         "models": file_hash(ROOT / "doxa/models.py"),
                         "selected_lenses": checksum([get_lens(identity) for identity in args.lens_ids])}
    analytical = {key: value for key, value in config.items() if key != "budget_usd"}
    signature = checksum({"config": analytical, "selection": selection, "engine": engine_fingerprint})
    checkpoint = args.output.with_suffix(args.output.suffix + ".checkpoint.json")
    started = time.perf_counter()
    if args.resume:
        if not checkpoint.exists():
            raise ValueError("Resume requires the original checkpoint and data-dir")
        state = json.loads(checkpoint.read_text())
        compatible_extension = False
        frozen_path = args.data_dir / "frozen-engine-compatibility.json"
        if state["signature"] != signature and frozen_path.exists():
            frozen = json.loads(frozen_path.read_text())
            prior_signature = checksum({"config": analytical, "selection": selection, "engine": state["engine_fingerprint"]})
            compatible_extension = (state["signature"] == prior_signature and frozen.get("engine_fingerprint") == state["engine_fingerprint"]
                                    and frozen.get("components") == engine_components)
            if compatible_extension:
                state.setdefault("compatible_extensions", []).append({"previous_file_fingerprint": state["engine_fingerprint"],
                    "current_file_fingerprint": engine_fingerprint, "selected_components": engine_components,
                    "reason": "Unselected lens extension; selected rubric packs, core engine and response contracts are byte-equivalent."})
        if (state["signature"] != signature and not compatible_extension) or state["data_dir"] != str(args.data_dir.resolve()):
            raise ValueError("Analytical configuration, source manifest, engine version or data-dir changed; start a new run")
        if compatible_extension:
            # Keep the original signature: the frozen selected method remains authoritative.
            engine_fingerprint = state["engine_fingerprint"]
    else:
        if args.output.exists() or checkpoint.exists():
            raise ValueError("Output exists; use --resume or choose an isolated output and data-dir")
        state = {"schema_version": "3.0", "signature": signature, "selection": selection,
                 "data_dir": str(args.data_dir.resolve()), "engine_fingerprint": engine_fingerprint,
                 "sessions": [], "elapsed_seconds": 0, "run_id": None, "project_id": None}
        args.data_dir.mkdir(parents=True, exist_ok=True)
        (args.data_dir / "frozen-engine-compatibility.json").write_text(json.dumps({"engine_fingerprint": engine_fingerprint,
            "components": engine_components, "lens_ids": args.lens_ids}, indent=2) + "\n")
    elapsed_before = state.get("elapsed_seconds", 0)
    provider = await get_provider(config)
    readiness = await provider.health()
    previous = state.get("provider_preflight") or {}
    if previous.get("revision") not in {None, "unresolved"} and readiness.get("revision") != previous["revision"]:
        raise ValueError("Installed model revision changed; do not mix it into a resumed run")
    state["provider_preflight"] = readiness
    state["sessions"].append({"started_at": utcnow(), "hardware": hardware(), "provider_preflight": readiness,
                              "concurrency": args.concurrency, "requested_budget_usd": args.budget_usd})
    if not readiness.get("ok"):
        state["status"] = "provider_unavailable"
        atomic_json(checkpoint, state)
        atomic_json(args.output, {"status": "provider_unavailable", "provider_preflight": readiness,
                                  "collection": {key: value for key, value in selection.items() if key != "sources"},
                                  "interpretation_accuracy": None, "independent_human_labels": False})
        print("Provider unavailable; no inference started. Inspect the saved readiness result.", flush=True)
        return 2
    headers = {"x-doxa-service-token": os.environ["DOXA_INTERNAL_TOKEN"]} if os.environ.get("DOXA_INTERNAL_TOKEN") else {}
    app = create_app(args.data_dir, start_workers=False, use_saved_showcase=False)
    async with app.router.lifespan_context(app):
        store = app.state.store
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 0))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", headers=headers) as client:
            if not state.get("project_id"):
                project = await api_json(client, "POST", "/api/projects", json={"name": f"V3 {args.collection} · {args.provider}", "description": "Whole real sources; no independent accuracy claim."})
                state["project_id"] = project["id"]
                atomic_json(checkpoint, state)
                state["source_snapshots"] = import_selection(store, project["id"], selection)
                atomic_json(checkpoint, state)
            elif not state.get("source_snapshots"):
                # Import failure happened before run creation. Reuse the full
                # frozen selection only when the importer is idempotent.
                if args.collection != "news":
                    raise ValueError("Interrupted source import: use a fresh isolated workspace for this non-news cohort")
                state["source_snapshots"] = import_selection(store, state["project_id"], selection)
            for source in state["source_snapshots"]:
                current = store.one("SELECT sha256 FROM documents WHERE id=? AND project_id=?", [source["id"], state["project_id"]])
                if current is None or current["sha256"] != source["sha256"]:
                    raise ValueError("A persisted source differs from the benchmark's frozen selection")
            if not state.get("run_id"):
                run = await api_json(client, "POST", f"/api/projects/{state['project_id']}/runs", json={**config, "document_ids": [item["id"] for item in state["source_snapshots"]]})
                if str(run.get("config", {}).get("methodology_version")) != "3.0":
                    raise ValueError("The API did not create a v3 contextual run; no worker has been started")
                state["run_id"] = run["id"]
            else:
                run = await api_json(client, "GET", f"/api/runs/{state['run_id']}")
                if run["status"] in {"partial", "failed", "cancelled", "paused_budget"}:
                    run = await api_json(client, "POST", f"/api/runs/{state['run_id']}/resume", json={"budget_usd": args.budget_usd})
                # This CLI exclusively owns its isolated worker workspace.
                store.execute("UPDATE tasks SET lease_until=0 WHERE run_id=? AND status='running'", [state["run_id"]])
            atomic_json(checkpoint, state)
            lock = asyncio.Lock()
            last_log = 0.0
            worker_failed = False

            async def settled(result, error):
                nonlocal last_log, worker_failed, run
                async with lock:
                    if error:
                        worker_failed = True
                        state.setdefault("worker_errors", []).append({"type": type(error).__name__, "at": utcnow()})
                    run = await api_json(client, "GET", f"/api/runs/{state['run_id']}")
                    state["status"] = run["status"]
                    state["elapsed_seconds"] = elapsed_before + time.perf_counter() - started
                    atomic_json(checkpoint, state)
                    atomic_json(args.output, progress_summary(store, state, run))
                    now = time.perf_counter()
                    if now - last_log >= 5 or run["status"] not in {"queued", "running"}:
                        progress = run.get("progress", {})
                        print(f"{progress.get('documents_complete', 0)}/{selection['documents']} complete sources; {progress.get('documents_settled', progress.get('documents_done', 0))} settled; {run['status']}", flush=True)
                        last_log = now
                    return not worker_failed and run["status"] in {"queued", "running"}

            try:
                problems = await run_worker_pool(app.state.work_once, state["run_id"], args.concurrency, settled)
                if problems:
                    worker_failed = True
                run = await api_json(client, "GET", f"/api/runs/{state['run_id']}")
            finally:
                state["elapsed_seconds"] = elapsed_before + time.perf_counter() - started
                state["status"] = run["status"]
                atomic_json(checkpoint, state)
                summary = progress_summary(store, state, run)
                atomic_json(args.output, summary)
            if run["status"] not in {"queued", "running", "importing"}:
                archive = package_run(store, run["id"], args.package_dir or args.output.parent,
                                      benchmark={"collection": {key: value for key, value in selection.items() if key != "sources"},
                                                 "sessions": state["sessions"], "elapsed_seconds": state["elapsed_seconds"],
                                                 "engine_fingerprint": engine_fingerprint})
                summary["archive"] = {"manifest": str((args.package_dir or args.output.parent) / "showcase.json"), "parts": len(archive["parts"]), "counts": archive["counts"]}
                atomic_json(args.output, summary)
            return 0 if run["status"] == "completed" and not worker_failed else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", choices=["news", "institutions", "news-sample", "political"], default="news")
    parser.add_argument("--limit", type=int, help="Whole-source pilot size; news-sample defaults to 20. Full news always selects 5,000.")
    parser.add_argument("--provider", choices=["cloudflare", "ollama"], default="cloudflare")
    parser.add_argument("--model", default="")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--budget", "--budget-usd", dest="budget_usd", type=float, default=10)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package-dir", type=Path)
    parser.add_argument("--env-file", "--envfile", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--lens-ids", nargs="+", default=["discourse", "ihra", "jda", "nexus", "anti-muslim-hostility"])
    parser.add_argument("--question", default="Apply the selected criteria in context, distinguishing attribution, counter-speech, uncertainty and competing frameworks.")
    parser.add_argument("--persona", default="research")
    args = parser.parse_args(argv)
    if not 1 <= args.concurrency <= 32:
        parser.error("Concurrency must be between 1 and 8")
    if args.provider == "ollama" and args.concurrency != 1:
        parser.error("CPU-only Ollama requires --concurrency 1")
    if not math.isfinite(args.budget_usd) or not 0 <= args.budget_usd <= 10000:
        parser.error("Budget must be a finite non-negative amount up to 10000")
    if args.limit is not None and (args.limit < 1 or args.limit > (20 if args.collection == "institutions" else 5000)):
        parser.error("Invalid whole-document limit")
    if args.collection == "news" and args.limit is not None:
        parser.error("Full news cannot be silently narrowed; use --collection news-sample")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.env_file:
        if not args.env_file.is_file():
            print("Environment configuration file was not found.", file=sys.stderr)
            return 2
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=True)
    try:
        return asyncio.run(run_benchmark(args))
    except KeyboardInterrupt:
        print("Interrupted. Durable work remains in the data-dir; resume with the same analytical arguments.", file=sys.stderr)
        return 130
    except Exception as error:
        # Exception type is safe telemetry. Configuration/HTTP exceptions can
        # contain endpoint details, so never echo arbitrary exception strings.
        print(f"Benchmark stopped ({type(error).__name__}). Inspect the local checkpoint and application diagnostics.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
