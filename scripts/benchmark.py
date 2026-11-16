#!/usr/bin/env python3
"""Run a reproducible benchmark through DOXA's actual API scheduler and providers.

A full lexical scan is infrastructure evidence, never semantic validation.
Model providers may incur real charges. See docs/benchmarking.md.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import subprocess
import sys
import time

try:
    import resource
except ImportError:  # Windows does not expose POSIX process resource counters.
    resource = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from doxa.api import create_app, unpack  # noqa: E402
from doxa.providers import get_provider  # noqa: E402
from doxa.store import utcnow  # noqa: E402

DEFAULT_LENSES = ["discourse", "ihra", "jda", "nexus", "anti-muslim-hostility"]
AUDIT_SEED = 20261116


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def checksum(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def hardware() -> dict:
    memory = None
    try:
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        pass
    gpu = None
    try:
        probe = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if probe.returncode == 0:
            gpu = probe.stdout.strip().splitlines()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {
        "platform": platform.platform(), "machine": platform.machine(),
        "python": platform.python_version(), "cpu": platform.processor() or platform.machine(),
        "logical_cpus": os.cpu_count(), "host_ram_bytes": memory,
        "visible_nvidia_gpus": gpu,
        "note": "Host/container observations, not a promise of model acceleration. Provider runtime hardware may be managed remotely.",
    }


def select_blocks(blocks: list[dict], limit: int | None = None, policy: str = "spread") -> list[dict]:
    """Deterministic source-position sampling; never rank by anticipated labels."""
    if not limit or limit >= len(blocks):
        return list(blocks)
    if policy == "prefix":
        return blocks[:limit]
    eligible = [block for block in blocks if len(block["text"]) >= 120]
    candidates = eligible if len(eligible) >= limit else blocks
    # Midpoints of equal source-position strata avoid sampling only cover text.
    indices = [min(len(candidates) - 1, int((i + 0.5) * len(candidates) / limit)) for i in range(limit)]
    return [candidates[index] for index in indices]


def import_document(store, project_id: str, batch_id: str, source: dict, *, block_limit: int | None = None, sample_policy: str = "spread") -> dict:
    """Persist authentic extracted blocks with provenance using the shared Store schema."""
    source_id = source["id"]
    selected = select_blocks(source["blocks"], block_limit, sample_policy)
    doc_id = hashlib.sha256(f"{project_id}:{source_id}".encode()).hexdigest()[:32]
    key = f"documents/{doc_id}/blocks.json"
    store.put_json(key, selected)
    metadata = {k: v for k, v in source.items() if k != "blocks"}
    metadata["source_id"] = source_id
    metadata["benchmark_selection"] = {
        "full_document_blocks": len(source["blocks"]), "selected_blocks": len(selected),
        "full_document_characters": sum(len(b["text"]) for b in source["blocks"]),
        "selected_characters": sum(len(b["text"]) for b in selected),
        "sampled": len(selected) != len(source["blocks"]),
        "selection": sample_policy if block_limit else "all",
        "selected_block_ids": [block["id"] for block in selected],
        "eligibility": "Prefer blocks with at least 120 characters, then deterministic source-position strata; no label-based ranking." if block_limit and sample_policy == "spread" else "Unfiltered source order.",
    }
    values = {
        "id": doc_id, "project_id": project_id, "batch_id": batch_id,
        "title": source["title"], "author": source.get("author") or "Unknown",
        "institution": source.get("institution", ""), "document_type": source.get("document_type", "document"),
        "published_at": source.get("published_at"), "filename": source_id + ".json",
        "mime_type": "application/json", "sha256": checksum(selected),
        "status": "ready", "word_count": sum(len(b["text"].split()) for b in selected),
        "page_count": source.get("page_count") or 0, "source_url": source.get("source_url", ""),
        "license": source.get("license", ""), "blocks_key": key,
        "created_at": utcnow(), "metadata_json": json.dumps(metadata, ensure_ascii=False),
    }
    store.execute(
        "INSERT INTO documents(" + ",".join(values) + ") VALUES(" + ",".join("?" for _ in values) + ")",
        list(values.values()),
    )
    store.execute("INSERT INTO batch_members(batch_id,document_id) VALUES(?,?) ON CONFLICT(batch_id,document_id) DO NOTHING", [batch_id, doc_id])
    if store.fts:
        store.index_document(doc_id, project_id, values["title"], selected)
    return {"id": doc_id, "source_id": source_id, **metadata["benchmark_selection"]}


async def api_json(client, method: str, path: str, **kwargs):
    response = await client.request(method, path, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path}: HTTP {response.status_code}: {response.text[:1000]}")
    return response.json()


async def run_worker_pool(work_once, run_id: str, concurrency: int, on_settled) -> list[Exception]:
    """Refill independent workers immediately; one slow task cannot block peers."""
    stop = asyncio.Event()

    async def worker():
        while not stop.is_set():
            result, error = None, None
            try:
                result = await work_once(run_id)
                if result.get("idle"):
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = exc
                stop.set()
            try:
                keep_running = await on_settled(result, error)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                stop.set()
                return exc
            if not keep_running:
                stop.set()
            if error:
                return error
            # No claimable task can remain while this worker waits. Other
            # workers may still own a lease and will settle/checkpoint it.
            if not result.get("pending", False):
                return None
        return None

    settled = await asyncio.gather(*(worker() for _ in range(concurrency)))
    return [error for error in settled if isinstance(error, Exception)]


def snapshot(store, state: dict, selections: list[dict], started: float, elapsed_before: float) -> dict:
    project_id, run_id = state["project_id"], state.get("run_id")
    documents = []
    for row in store.query("SELECT * FROM documents WHERE project_id=? ORDER BY created_at,id", [project_id]):
        doc = unpack(row)
        doc["blocks"] = store.get_json(doc["blocks_key"])
        documents.append(doc)
    runs = [unpack(store.one("SELECT * FROM runs WHERE id=?", [run_id]))] if run_id else []
    findings = []
    if run_id:
        for row in store.query("SELECT payload_json FROM findings WHERE run_id=?", [run_id]):
            findings.append(json.loads(row["payload_json"]))
    source_map = {doc["id"]: {block["id"]: block for block in doc["blocks"]} for doc in documents}
    source_order = {doc["id"]: {b["id"]: i for i, b in enumerate(doc["blocks"])} for doc in documents}
    findings.sort(key=lambda f: (f["document_id"], source_order.get(f["document_id"], {}).get(f["block_id"], -1), f.get("start", 0), f["id"]))
    invalid = []
    for finding in findings:
        block = source_map.get(finding["document_id"], {}).get(finding["block_id"])
        start, end = finding.get("start"), finding.get("end")
        valid = bool(block and isinstance(start, int) and isinstance(end, int) and
                     0 <= start < end <= len(block["text"]) and block["text"][start:end] == finding["quote"])
        if not valid:
            invalid.append(finding["id"])
    first = []
    for doc in documents:
        first.extend([f for f in findings if f["document_id"] == doc["id"]][:5])
    chosen = {f["id"] for f in first}
    remaining = sorted([f for f in findings if f["id"] not in chosen], key=lambda f: f["id"])
    extra = random.Random(AUDIT_SEED).sample(remaining, min(20, len(remaining)))
    tasks = store.query("SELECT * FROM tasks WHERE run_id=? ORDER BY ordinal,id", [run_id]) if run_id else []
    task_results = []
    errors = []
    for task in tasks:
        if task.get("result_key"):
            result = store.get_json(task["result_key"])
            task_results.append({
                "task_id": task["id"], "document_id": task["document_id"], "status": task["status"],
                "coverage": result.get("coverage", {}), "usage": result.get("usage", {}),
                "model_profile": result.get("model_profile", {}), "errors": result.get("errors", []),
            })
            errors.extend({"task_id": task["id"], "document_id": task["document_id"], **error}
                          for error in result.get("errors", []))
        elif task.get("error"):
            errors.append({"task_id": task["id"], "document_id": task["document_id"], "message": task["error"]})
    full_characters = sum(s["full_document_characters"] for s in selections)
    selected_characters = sum(s["selected_characters"] for s in selections)
    processed = sum(t["coverage"].get("processed_characters", 0) for t in task_results)
    complete = bool(runs and runs[0]["status"] == "completed" and processed == selected_characters
                    and not invalid and not state.get("session_failed"))
    sampled = len(selections) != 20 or any(s["sampled"] for s in selections)
    counts = Counter(f.get("lens_id", "unknown") for f in findings)
    elapsed = elapsed_before + time.perf_counter() - started
    usage = (runs[0] if runs else {}).get("usage", {})
    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None
    return {
        "schema_version": "1.0", "benchmark_kind": "real_public_corpus", "generated_at": utcnow(),
        "project": unpack(store.one("SELECT * FROM projects WHERE id=?", [project_id])),
        "profile": "research", "filters": {"run_id": run_id} if run_id else {},
        "documents": documents, "findings": findings, "runs": runs,
        "metrics": {
            "documents": len(documents), "total_words": sum(d["word_count"] for d in documents),
            "total_pages": sum(d.get("page_count") or 0 for d in documents), "findings": len(findings),
            "by_lens": [{"name": name, "count": count} for name, count in counts.most_common()],
            "coverage": 100 * processed / selected_characters if selected_characters else 0,
            "coverage_detail": {
                "basis": "extracted text characters; excludes images and unextracted content",
                "selected_characters": selected_characters, "processed_characters": processed,
                "full_selected_document_characters": full_characters,
                "selected_documents": len(selections), "bundled_documents": 20,
                "sampled": sampled, "selected_input_complete": complete,
                "full_corpus_scan_complete": complete and not sampled,
                "semantic_analysis_performed": state["config"]["provider"] != "lexical",
            },
            "usage": usage, "cost_usd": usage.get("cost_usd"),
            "wall_seconds": elapsed,
            "peak_process_rss_bytes": (max_rss if sys.platform == "darwin" else max_rss * 1024) if max_rss is not None else None,
            "interpretation_accuracy": None, "independent_human_labels": False,
            "methodology_notice": "Research preview. Citation integrity is not interpretation accuracy. Lexical scans perform no semantic classification.",
        },
        "benchmark": {
            "config": state["config"], "signature": state["signature"], "selection": selections,
            "source_snapshots": state["source_snapshots"], "hardware": state["hardware"],
            "provider_preflight": state.get("provider_preflight"), "sessions": state.get("sessions", []),
            "worker_errors": state.get("worker_errors", []),
            "task_results": task_results, "errors": errors,
            "citation_audit": {"checked": len(findings), "valid": len(findings) - len(invalid),
                               "invalid_finding_ids": invalid, "offset_unit": "unicode_code_points"},
            "qualitative_error_audit": {
                "status": "sample_prepared_not_independently_reviewed", "seed": AUDIT_SEED,
                "protocol": "First five source-ordered findings per document plus twenty seeded additional findings. Inspect all invalid citations and unresolved attribution separately.",
                "reviewer": None, "findings": first + extra,
                "notice": "A deterministic sample is not a completed qualitative review or human ground truth.",
            },
        },
    }


async def run_benchmark(args) -> int:
    if os.environ.get("DOXA_CLOUD_STATE_URL"):
        raise RuntimeError("Benchmark data-dir must be local; unset DOXA_CLOUD_STATE_URL for this isolated benchmark.")
    sources = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "corpus" / "documents").glob("*.json"))]
    if len(sources) != 20:
        raise RuntimeError("Expected the twenty bundled real documents.")
    selected = sources[:args.max_documents] if args.max_documents else sources
    source_snapshots = [{"id": d["id"], "content_sha256": d["content_sha256"], "source_sha256": d["source_sha256"]} for d in selected]
    config = {"provider": args.provider, "model": args.model or "", "lens_ids": args.lens_ids,
              "persona": args.persona, "question": args.question, "budget_usd": args.budget_usd}
    if args.input_price_per_million is not None:
        config["input_price_per_million"] = args.input_price_per_million
    if args.output_price_per_million is not None:
        config["output_price_per_million"] = args.output_price_per_million
    signature_input = {"config": config, "sources": source_snapshots, "max_blocks": args.max_blocks_per_document}
    if args.max_blocks_per_document:
        signature_input["sample_policy"] = args.sample_policy
    signature = checksum(signature_input)
    checkpoint = args.output.with_suffix(args.output.suffix + ".checkpoint.json")
    started = time.perf_counter()
    if args.resume:
        if not checkpoint.exists():
            raise RuntimeError("--resume requires the output checkpoint and its original data-dir.")
        state = json.loads(checkpoint.read_text())
        if state["signature"] != signature or state["data_dir"] != str(args.data_dir.resolve()):
            raise RuntimeError("Resume configuration, source snapshots or data-dir changed. Use a new output/data-dir.")
        elapsed_before = state.get("elapsed_seconds", 0.0)
    else:
        if args.output.exists() or checkpoint.exists():
            raise RuntimeError("Output already exists; choose --resume or a new output file.")
        state = {"schema_version": 1, "signature": signature, "config": config,
                 "data_dir": str(args.data_dir.resolve()), "source_snapshots": source_snapshots,
                 "hardware": hardware(), "sessions": [], "run_id": None}
        elapsed_before = 0.0
    provider = await get_provider(config)
    readiness = await provider.health()
    previous = state.get("provider_preflight") or {}
    if args.resume and previous.get("revision") not in {None, "unresolved"} and readiness.get("revision") != previous["revision"]:
        raise RuntimeError("Installed model revision changed. Do not mix it into this run; create a new benchmark.")
    state["provider_preflight"] = readiness
    state["session_failed"] = False
    state["sessions"].append({"started_at": utcnow(), "hardware": hardware(), "provider_preflight": readiness,
                              "concurrency": args.concurrency})
    if not readiness.get("ok"):
        state["status"] = "provider_unavailable"
        state["elapsed_seconds"] = elapsed_before + time.perf_counter() - started
        atomic_json(checkpoint, state)
        atomic_json(args.output, {"benchmark_kind": "real_public_corpus", "status": "provider_unavailable",
                                 "config": config, "provider_preflight": readiness, "source_snapshots": source_snapshots,
                                 "metrics": {"interpretation_accuracy": None, "independent_human_labels": False}})
        print("Provider unavailable; no inference started. See benchmark output.", flush=True)
        return 2
    app = create_app(args.data_dir, start_workers=False)
    headers = {}
    if os.environ.get("DOXA_INTERNAL_TOKEN"):
        headers["x-doxa-service-token"] = os.environ["DOXA_INTERNAL_TOKEN"]
    async with app.router.lifespan_context(app):
        store = app.state.store
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 0))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", headers=headers) as client:
            if not args.resume or not state.get("project_id"):
                project = await api_json(client, "POST", "/api/projects", json={
                    "name": f"20-document benchmark · {args.provider}",
                    "description": "Real public corpus; research preview; independent interpretation labels unavailable.",
                })
                state["project_id"] = project["id"]
                batch_id = "benchmark-" + project["id"]
                store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?)",
                              [batch_id, project["id"], "Real public twenty-document corpus", utcnow()])
                state["selections"] = [import_document(store, project["id"], batch_id, d, block_limit=args.max_blocks_per_document, sample_policy=args.sample_policy) for d in selected]
            selections = state["selections"]
            if not state.get("run_id"):
                run = await api_json(client, "POST", f"/api/projects/{state['project_id']}/runs", json={
                    **config, "document_ids": [selection["id"] for selection in selections],
                })
                state["run_id"] = run["id"]
            else:
                run = await api_json(client, "GET", f"/api/runs/{state['run_id']}")
                if run["status"] in {"partial", "failed", "cancelled", "paused_budget"}:
                    run = await api_json(client, "POST", f"/api/runs/{state['run_id']}/resume")
                # This isolated CLI owns its worker; recover a task interrupted by the previous invocation.
                store.execute("UPDATE tasks SET lease_until=0 WHERE run_id=? AND status='running'", [state["run_id"]])
            atomic_json(checkpoint, state)
            last_done = -1
            checkpoint_lock = asyncio.Lock()

            def remember_worker_error(error):
                state["session_failed"] = True
                details = {"type": type(error).__name__, "message": str(error)[:500]}
                previous = state.setdefault("worker_errors", [])
                if not any(item.get("type") == details["type"] and item.get("message") == details["message"] for item in previous):
                    previous.append({"at": utcnow(), **details})

            async def on_settled(result, error):
                nonlocal last_done, run
                async with checkpoint_lock:
                    if error:
                        remember_worker_error(error)
                    # Each completion reads authoritative state; workers' own
                    # returned snapshots can already be stale under concurrency.
                    run = await api_json(client, "GET", f"/api/runs/{state['run_id']}")
                    pending = run["status"] not in {"completed", "partial", "failed", "cancelled"} and not run["status"].startswith("paused")
                    pending = pending and not state.get("session_failed")
                    state["elapsed_seconds"] = elapsed_before + time.perf_counter() - started
                    state["status"] = run["status"]
                    atomic_json(checkpoint, state)
                    done = int(run.get("progress", {}).get("documents_done", 0))
                    if done != last_done or not pending:
                        atomic_json(args.output, snapshot(store, state, selections, started, elapsed_before))
                        print(f"{done}/{len(selections)} documents; {run['status']}; tasks {run.get('progress', {}).get('tasks_done', 0)}", flush=True)
                        last_done = done
                    return pending

            try:
                errors = await run_worker_pool(app.state.work_once, state["run_id"], args.concurrency, on_settled)
                for error in errors:
                    remember_worker_error(error)
                # All leased work has settled; only this read determines the
                # final status, never an intermediate worker response.
                run = await api_json(client, "GET", f"/api/runs/{state['run_id']}")
                state["status"] = run["status"]
            finally:
                state["elapsed_seconds"] = elapsed_before + time.perf_counter() - started
                atomic_json(checkpoint, state)
                report = snapshot(store, state, selections, started, elapsed_before)
                atomic_json(args.output, report)
            return 0 if report["metrics"]["coverage_detail"]["selected_input_complete"] else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["lexical", "ollama", "cloudflare", "openai", "runpod"], default="lexical")
    parser.add_argument("--model", default="")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks" / "lexical-full.json")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "benchmark")
    parser.add_argument("--max-documents", type=int)
    parser.add_argument("--max-blocks-per-document", type=int)
    parser.add_argument("--sample-policy", choices=["spread", "prefix"], default="spread")
    parser.add_argument("--lens-ids", nargs="+", default=DEFAULT_LENSES)
    parser.add_argument("--persona", choices=["research", "briefing", "teaching"], default="research")
    parser.add_argument("--question", default="What assumptions, frames, and competing interpretations appear in these public institutional documents?")
    parser.add_argument("--budget-usd", type=float, default=2.0)
    parser.add_argument("--input-price-per-million", type=float)
    parser.add_argument("--output-price-per-million", type=float)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="Concurrent shared-scheduler tasks (1-8); Ollama requires 1.")
    args = parser.parse_args(argv)
    if args.max_documents is not None and not 1 <= args.max_documents <= 20:
        parser.error("--max-documents must be between 1 and 20.")
    if args.max_blocks_per_document is not None and args.max_blocks_per_document < 1:
        parser.error("--max-blocks-per-document must be positive.")
    if args.budget_usd < 0:
        parser.error("--budget-usd must be non-negative.")
    if not 1 <= args.concurrency <= 8:
        parser.error("--concurrency must be between 1 and 8.")
    if args.provider == "ollama" and args.concurrency > 1:
        parser.error("Ollama benchmarks require --concurrency 1 to avoid local model-memory contention.")
    return args


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run_benchmark(args))
    except KeyboardInterrupt:
        print("Interrupted. Resume using the same arguments plus --resume.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Benchmark failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
