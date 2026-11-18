#!/usr/bin/env python3
"""Offline synthetic TXT persistence/export benchmark in a disposable workspace.

No inference, PDF/OCR throughput or production capacity claim is made. The
scheduler probe is independently bounded; all generated documents are persisted
and exported. No existing application data directory can be selected.
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
import resource
import shutil
import sys
import tempfile
import time
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GUARD_COUNTS = Counter()


def _offline_guard(event, _arguments):
    if event in {"socket.connect", "socket.connect_ex", "socket.sendto", "socket.getaddrinfo"}:
        GUARD_COUNTS["blocked_network_attempts"] += 1
        raise RuntimeError("This benchmark forbids network connections")


# The spawned renderer imports this module too, so it receives the same guard.
if __name__ in {"__main__", "__mp_main__"}:
    sys.addaudithook(_offline_guard)


def clean_environment():
    for name in list(os.environ):
        if name.startswith(("DOXA_", "OLLAMA_", "CLOUDFLARE_", "RUNPOD_", "OPENAI_", "ANTHROPIC_", "HF_", "HUGGINGFACE_", "GOOGLE_", "GCP_")):
            os.environ.pop(name, None)
    os.environ.update(PYTHON_DOTENV_DISABLED="1", DOXA_LOCAL_PROFILE="low-memory",
                      HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(data)
    return result.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def fixture_text(index):
    return (f"Synthetic infrastructure fixture {index:05d}.\nInventory key: key{index:05d}. "
            "Café Unicode text retained exactly.\nThis generated record tests persistence and export only.\n")


def hardware():
    def read(path):
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None
    try:
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError, AttributeError):
        memory = None
    return {"platform": platform.platform(), "python": platform.python_version(),
            "machine": platform.machine(), "logical_cpus": os.cpu_count(), "host_memory_bytes": memory,
            "cgroup_memory_max": read("/sys/fs/cgroup/memory.max"),
            "cgroup_cpu_max": read("/sys/fs/cgroup/cpu.max"),
            "note": "Observed host/container, not an 8 GB laptop or cloud service qualification."}


def owned_bytes(folder):
    seen, total = set(), 0
    for path in folder.rglob("*"):
        if path.is_file():
            info = path.stat()
            identity = (info.st_dev, info.st_ino)
            if identity not in seen:
                seen.add(identity)
                total += info.st_size
    return total


def source_fingerprint(store, project_id):
    result = hashlib.sha256()
    for row in store.query("SELECT id,sha256,status,blocks_key,metadata_json,created_at FROM documents WHERE project_id=? ORDER BY id", [project_id]):
        result.update(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
        result.update(bytes.fromhex(digest(store.object_path(row["blocks_key"]))))
    return result.hexdigest()


def workbook_rows(path):
    counts, ids = {}, {}
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Workbook ZIP integrity failed")
        if any("externallinks" in name.lower() or "connections" in name.lower() for name in archive.namelist()):
            raise RuntimeError("Unexpected external workbook connection")
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        for filename, name in (("xl/worksheets/sheet2.xml", "Documents"), ("xl/worksheets/sheet3.xml", "Findings")):
            rows, logical, seen = 0, 0, []
            with archive.open(filename) as stream:
                for _event, element in ET.iterparse(stream, events=("end",)):
                    if element.tag.rsplit("}", 1)[-1] != "row":
                        continue
                    if element.find(".//s:f", ns) is not None:
                        raise RuntimeError("Unexpected formula in source/result rows")
                    if rows:
                        cells = ["".join(cell.itertext()) for cell in element.findall("s:c", ns)]
                        if cells and cells[-1].startswith("1/"):
                            logical += 1
                            seen.append(cells[0])
                    rows += 1
                    element.clear()
            counts[name] = {"physical_rows": max(0, rows - 1), "logical_rows": logical}
            ids[name] = seen
        info = archive.infolist()
        counts["zip_members"] = len(info)
        counts["uncompressed_bytes"] = sum(item.file_size for item in info)
    return counts, ids


async def execute(args, folder):
    clean_environment()
    import httpx
    from doxa.api import create_app
    from doxa.extraction import extract_text_document
    from doxa.providers import get_provider as real_provider
    from doxa.report_jobs import ReportJobs
    from doxa.workspace import Workspace
    from doxa import __version__

    async def only_lexical(config):
        if config.get("provider") not in {"lexical", "lexical-baseline"}:
            GUARD_COUNTS["blocked_model_provider_attempts"] += 1
            raise RuntimeError("This benchmark permits only the no-model lexical engine")
        return await real_provider(config)

    app = create_app(folder / "workspace", start_workers=False, use_saved_showcase=False)
    receipt = {"schema_version": "doxa-synthetic-scale-0.6", "application_version": __version__,
               "benchmark_kind": "synthetic_native_txt_persistence_and_complete_export", "synthetic": True,
               "documents": args.documents, "scheduler_probe_documents": min(args.scheduler_documents, args.documents),
               "hardware": hardware(), "script_sha256": digest(__file__), "interpretation_accuracy": None,
               "independent_reference_labels": False, "fresh_model_loads": 0,
               "isolation": "New caller-owned TemporaryDirectory only; no existing source workspace is opened.",
               "limitations": ["Generated short TXT fixtures exercise native canonical storage, indexing, pagination and export, not multipart upload, binary documents, PDF or OCR throughput.",
                               "Only the explicitly counted lexical scheduler subset is executed; the complete source export covers every generated document.",
                               "Lexical processing is not LLM inference or contextual prejudice analysis; no accuracy, inference throughput or production capacity claim follows.",
                               "Timing and RSS include benchmark verification overhead. Parent and child high-water RSS are separate, not a simultaneous process-tree peak."]}
    timings, checks, storage = {}, {}, {}
    started = time.perf_counter()
    with patch("doxa.providers.get_provider", only_lexical):
        async with app.router.lifespan_context(app):
            store = app.state.store
            assert not store.remote and store.root.is_relative_to(folder)
            services = app.state.activity_services
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 0))
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                async def api(method, url, **kwargs):
                    response = await client.request(method, url, **kwargs)
                    if response.status_code >= 400:
                        raise RuntimeError(f"{method} {url}: HTTP {response.status_code}")
                    return response.json()
                project = await api("POST", "/api/projects", json={"name": "Synthetic 10k infrastructure fixture",
                    "description": "Generated TXT records, no real people, independent labels, model outputs or interpretation-quality evidence."})
                project_id = project["id"]
                batch = services["new_batch"](project_id, "Synthetic TXT canonical persistence")
                path = folder / "generated-source.txt"
                ids, source_bytes, words = [], 0, 0
                def save(index):
                    text = fixture_text(index)
                    path.write_text(text, encoding="utf-8")
                    filename = f"synthetic-{index:05d}.txt"
                    extracted = extract_text_document(path, filename)
                    extracted.update(filename=filename, sha256=hashlib.sha256(text.encode()).hexdigest(),
                                     title=f"Synthetic inventory item {index:05d}", author="Synthetic generator (not a person)",
                                     document_type="synthetic_txt_fixture", synthetic=True, license="CC0-1.0")
                    extracted["metadata"].update(source_identity=f"synthetic-scale/{filename}",
                        fixture_source_text=text, fixture_source_kind="synthetic infrastructure TXT", source_generator="benchmark_scale_v06-1")
                    return services["save_document"](project_id, batch["id"], extracted)
                phase = time.perf_counter()
                for index in range(1, args.documents + 1):
                    document = save(index)
                    ids.append(document["id"])
                    source_bytes += len(fixture_text(index).encode())
                    words += document["word_count"]
                    if index % 1000 == 0:
                        print(json.dumps({"stage": "persist", "documents": index}), flush=True)
                timings["persist_txt"] = time.perf_counter() - phase
                storage["after_persistence_bytes"] = owned_bytes(folder)
                before = source_fingerprint(store, project_id)
                phase = time.perf_counter()
                for index in range(1, args.documents + 1):
                    assert save(index)["id"] == ids[index - 1]
                timings["replay_all_txt"] = time.perf_counter() - phase
                checks["all_source_ids_and_blocks_unchanged_after_replay"] = source_fingerprint(store, project_id) == before
                checks["exact_document_count_after_replay"] = store.one("SELECT COUNT(*) n FROM documents WHERE project_id=?", [project_id])["n"] == args.documents
                checks["canonical_import_commits_complete"] = store.one("SELECT COUNT(*) n FROM import_commits WHERE project_id=? AND status='ready'", [project_id])["n"] == args.documents
                phase = time.perf_counter()
                paged, offset, pages = [], 0, 0
                while True:
                    page = await api("GET", f"/api/projects/{project_id}/documents", params={"offset": offset, "limit": 100})
                    if not page:
                        break
                    paged.extend(row["id"] for row in page)
                    offset += len(page)
                    pages += 1
                timings["http_pagination"] = time.perf_counter() - phase
                checks["http_pagination_exact_membership"] = len(paged) == len(set(paged)) == args.documents and set(paged) == set(ids)
                receipt["pagination_pages"] = pages
                search = await api("POST", f"/api/v3/projects/{project_id}/query", json={"run_id": "none", "q": "key00001", "duplicate_policy": "sources"})
                checks["fts_exact_probe"] = search["metrics"]["documents"] == 1
                selected = ids[:min(args.scheduler_documents, len(ids))]
                run = await api("POST", f"/api/projects/{project_id}/runs", json={"provider": "lexical", "model": "lexical-baseline", "lens_ids": ["discourse"], "document_ids": selected, "budget_usd": 0})
                first = store.one("SELECT id FROM tasks WHERE run_id=? ORDER BY ordinal,id LIMIT 1", [run["id"]])
                store.execute("UPDATE tasks SET status='running',lease_token='synthetic-expired-lease',lease_until=0,attempts=1 WHERE id=?", [first["id"]])
                phase = time.perf_counter()
                while True:
                    state = await app.state.work_once(run["id"])
                    if not state["pending"]:
                        settled = state["run"]
                        break
                timings["lexical_scheduler_probe"] = time.perf_counter() - phase
                task_counts = store.one("SELECT COUNT(*) total,SUM(status='completed') completed,SUM(status='failed') failed FROM tasks WHERE run_id=?", [run["id"]])
                recovered = store.one("SELECT status,attempts FROM tasks WHERE id=?", [first["id"]])
                checks["lexical_scheduler_subset_complete"] = settled["status"] == "completed" and task_counts["completed"] == len(selected) and not task_counts["failed"]
                checks["expired_analysis_lease_recovered"] = recovered["status"] == "completed" and recovered["attempts"] == 2
                terminal = [dict(row) for row in store.query("SELECT * FROM tasks WHERE run_id=? ORDER BY id", [run["id"]])]
                await app.state.work_once(run["id"])
                checks["terminal_analysis_replay_no_new_work"] = terminal == store.query("SELECT * FROM tasks WHERE run_id=? ORDER BY id", [run["id"]])
                receipt["scheduler_tasks"] = task_counts
                receipt["lexical_usage"] = settled.get("usage", {})
                checks["no_lexical_findings_in_fixture"] = store.one("SELECT COUNT(*) n FROM findings WHERE project_id=?", [project_id])["n"] == 0
                frozen = await api("POST", f"/api/v3/projects/{project_id}/query", json={"run_id": "none", "duplicate_policy": "sources"})
                checks["frozen_query_contains_every_source"] = frozen["metrics"]["documents"] == args.documents
                query = Workspace(store).require_query(frozen["query_id"])
                expected_ids = Workspace(store).document_ids(query)
                source_before_report = source_fingerprint(store, project_id)
                manager = app.state.report_jobs
                job = manager.create(project_id, {"query_id": frozen["query_id"], "format": "xlsx", "profile": "research", "publication_type": "archive"})
                store.execute("UPDATE report_jobs SET status='running',lease_token='synthetic-dead-report-worker',lease_until=0,attempts=1 WHERE id=?", [job["id"]])
                phase = time.perf_counter()
                first_work = await asyncio.to_thread(manager.work_once, job["id"], deadline_seconds=80)
                checkpoint = json.loads(store.one("SELECT state_json FROM report_jobs WHERE id=?", [job["id"]])["state_json"])
                checks["persistent_report_checkpoint_frozen"] = first_work["status"] == "queued" and checkpoint.get("frozen") is True
                from doxa.store import Store
                store.close()
                store = Store(folder / "workspace")
                store.init()
                app.state.store = store
                manager = ReportJobs(store)
                receipt["fresh_store_open_after_report_checkpoint"] = True
                for _attempt in range(10):
                    state = await asyncio.to_thread(manager.work_once, job["id"], deadline_seconds=420)
                    if state["status"] == "completed":
                        break
                    if state["status"] in {"failed", "cancelled"}:
                        raise RuntimeError("Persistent source report failed: " + str(manager.get(job["id"])["error"]))
                else:
                    raise RuntimeError("Persistent source report exceeded its bounded replay attempts")
                timings["persistent_xlsx_capture_render"] = time.perf_counter() - phase
                final = manager.get(job["id"])
                checks["expired_report_lease_recovered"] = store.one("SELECT attempts FROM report_jobs WHERE id=?", [job["id"]])["attempts"] >= 3
                artifacts_before = final["artifacts"]
                assert manager.work_once(job["id"])["status"] == "completed"
                checks["terminal_report_redelivery_same_artifacts"] = manager.get(job["id"])["artifacts"] == artifacts_before
                artifacts = store.query("SELECT * FROM report_job_artifacts WHERE job_id=? ORDER BY format", [job["id"]])
                entry = next(row for row in artifacts if row["format"] == "xlsx")
                workbook = store.object_path(entry["object_key"])
                info, workbook_ids = workbook_rows(workbook)
                checks["workbook_all_source_rows"] = info["Documents"]["logical_rows"] == args.documents and workbook_ids["Documents"] == expected_ids
                checks["workbook_no_model_assessments"] = info["Findings"]["logical_rows"] == 0
                checks["source_content_unchanged_after_reporting"] = source_fingerprint(store, project_id) == source_before_report
                checks["sqlite_integrity"] = store.one("PRAGMA quick_check")["quick_check"] == "ok"
                checks["no_provider_reservations"] = store.one("SELECT COUNT(*) n FROM budget_reservations")["n"] == 0
                checks["no_assessments_or_human_reviews"] = store.one("SELECT COUNT(*) n FROM v3_assessments")["n"] == 0 and store.one("SELECT COUNT(*) n FROM v3_review_events")["n"] == 0
                checks["no_model_requests"] = settled.get("usage", {}).get("requests", 0) == 0
                checks["no_network_or_forbidden_provider_attempts"] = not GUARD_COUNTS
                receipt.update(query_id=frozen["query_id"], scope_hash=frozen["scope_hash"], report_job_id=job["id"],
                    source_documents_sha256=hashlib.sha256("\n".join(expected_ids).encode()).hexdigest(),
                    input_text_bytes=source_bytes, input_words=words, workbook=info,
                    artifacts=[{"format": row["format"], "bytes": row["bytes"], "sha256": row["sha256"]} for row in artifacts],
                    inference_requests=0, new_budget_reservations=0, network_attempts_blocked=dict(GUARD_COUNTS),
                    completed_contextual_documents=0, contextual_assessments=0)
                if args.workbook:
                    args.workbook.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(workbook, args.workbook)
                    receipt["retained_workbook"] = {"file": str(args.workbook), "bytes": args.workbook.stat().st_size, "sha256": digest(args.workbook)}
                storage["before_cleanup_bytes"] = owned_bytes(folder)
                receipt["storage"] = storage
                receipt["checks"] = checks
                receipt["passed"] = all(checks.values())
    receipt["timing_seconds"] = {**timings, "total_before_cleanup": time.perf_counter() - started}
    factor = 1 if sys.platform == "darwin" else 1024
    receipt["process_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * factor
    receipt["child_lifetime_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * factor
    receipt["torch_imported"] = "torch" in sys.modules
    receipt["transformers_imported"] = "transformers" in sys.modules
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=10000)
    parser.add_argument("--scheduler-documents", type=int, default=128)
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks/v06-scale-10000.json")
    parser.add_argument("--workbook", type=Path)
    args = parser.parse_args()
    if not 1 <= args.documents <= 20000 or not 1 <= args.scheduler_documents <= 1000:
        parser.error("documents must be 1–20000 and scheduler-documents 1–1000")
    begun = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="doxa-v06-scale-") as directory:
        folder = Path(directory)
        report = asyncio.run(execute(args, folder))
    report["temporary_workspace_removed"] = not folder.exists()
    report["elapsed_seconds"] = time.perf_counter() - begun
    report["checks"]["temporary_workspace_removed"] = report["temporary_workspace_removed"]
    report["passed"] = all(report["checks"].values())
    if args.workbook and report["passed"]:
        manifest = {"schema_version": "doxa-synthetic-scale-export-0.6", "synthetic": True,
                    "description": "Complete metadata/source-text fixture record XLSX, zero model assessments; not a bias-analysis result.",
                    "script": "scripts/benchmark_scale_v06.py", "script_sha256": digest(__file__),
                    "documents": args.documents, "contextual_assessments": 0, "model_requests": 0,
                    "query_id": report["query_id"], "scope_hash": report["scope_hash"],
                    "files": [{"file": args.workbook.name, "bytes": args.workbook.stat().st_size, "sha256": digest(args.workbook)}]}
        atomic_json(args.workbook.with_suffix(".manifest.json"), manifest)
    atomic_json(args.output, report)
    print(json.dumps({"passed": report["passed"], "documents": report["documents"], "scheduler_probe_documents": report["scheduler_probe_documents"], "elapsed_seconds": report["elapsed_seconds"], "checks": report["checks"]}, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
