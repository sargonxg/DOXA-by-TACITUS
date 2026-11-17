#!/usr/bin/env python3
"""Opt-in durable report proof on a caller-owned isolated restore, never originals."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import resource
import shutil
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from doxa.recorded_export import file_hash, ids_hash  # noqa: E402
from doxa.report_jobs import ReportJobs  # noqa: E402
from doxa.store import utcnow  # noqa: E402
from doxa.workspace import Workspace, WorkspaceQuery  # noqa: E402


def _stream_hash(stream):
    digest, count = hashlib.sha256(), 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
        count += len(chunk)
    return digest.hexdigest(), count


def _workbook(path, expected):
    """Independently count literal rows and reject formulas/external connections."""
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    counts = {}
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None or any("externallinks" in name.lower() or "connections" in name.lower() for name in archive.namelist()):
            raise ValueError("Workbook ZIP integrity or external-link check failed")
        members = archive.infolist()
        counts.update(member_count=len(members),
                      max_member_uncompressed_bytes=max((item.file_size for item in members), default=0),
                      total_uncompressed_bytes=sum(item.file_size for item in members),
                      zip64_member_count=sum(item.extract_version >= 45 for item in members))
        for filename, label in (("xl/worksheets/sheet2.xml", "Documents"), ("xl/worksheets/sheet3.xml", "Findings")):
            rows, logical, digest = 0, 0, hashlib.sha256()
            with archive.open(filename) as stream:
                for _event, element in ET.iterparse(stream, events=("end",)):
                    if element.tag.rsplit("}", 1)[-1] != "row":
                        continue
                    if element.find(".//s:f", namespace) is not None:
                        raise ValueError("Unexpected workbook formula")
                    if rows:
                        cells = element.findall("s:c", namespace)
                        strings = ["".join(cell.itertext()) for cell in cells]
                        if strings and strings[-1].startswith("1/"):
                            logical += 1
                            digest.update((strings[0] + "\n").encode())
                    rows += 1
                    element.clear()
            if logical != expected["logical_rows"][label]:
                raise ValueError("Workbook logical row count differs")
            counts[label] = {"logical_rows": logical, "physical_rows": max(0, rows - 1), "ordered_ids_sha256": digest.hexdigest()}
    return counts


def verify_report_jobs(store, project_id, run_id, receipt_path, *, isolated=False, expected_counts=None, min_free_gib=1, max_seconds=1200):
    """Run after restore assertions, before caller cleanup; returns a sanitized receipt.

    Pass ``isolated=True`` only for a disposable fresh verification Store. Original
    documents, runs, assessments, review records and ledgers are never changed.
    """
    if not isolated or store.remote:
        raise ValueError("Report verification requires an explicitly isolated native scratch Store")
    from doxa.privacy_lifecycle import ensure_schema as ensure_privacy_schema
    ensure_privacy_schema(store)
    started = time.perf_counter()
    receipt = {"schema_version": "doxa-durable-report-proof-1", "generated_at": utcnow(), "status": "running",
               "scope": "isolated native Store; not a production D1 capacity test", "format": "xlsx", "interpretation_accuracy": None,
               "new_inference_requests": 0, "fresh_model_loads": 0}
    query_id = job_id = None
    samples = {"minimum_free_bytes": shutil.disk_usage(store.root).free, "max_owned_report_bytes": 0}
    last_sample = 0
    def sample(identity=None, *, force=False):
        nonlocal last_sample
        current = time.perf_counter()
        if not force and current - last_sample < 2:
            return
        last_sample = current
        free = shutil.disk_usage(store.root).free
        samples["minimum_free_bytes"] = min(samples["minimum_free_bytes"], free)
        if free < min_free_gib * 1024**3:
            raise ValueError("Report verification scratch headroom reached")
        if time.perf_counter() - started > max_seconds:
            raise ValueError("Report verification maximum elapsed time reached")
        if identity:
            roots = [store.root / "report-work" / identity, store.object_path("report-jobs/" + identity)]
            seen, size = set(), 0
            for root in roots:
                for path in root.rglob("*") if root.exists() else []:
                    if path.is_file():
                        stat = path.stat()
                        inode = (stat.st_dev, stat.st_ino)
                        if inode not in seen:
                            size += stat.st_size
                            seen.add(inode)
            samples["max_owned_report_bytes"] = max(samples["max_owned_report_bytes"], size)
    class AuditedJobs(ReportJobs):
        def _check(self, identity, lease):
            super()._check(identity, lease)
            sample(identity)
    manager = AuditedJobs(store)
    try:
        sample()
        query = Workspace(store).create(project_id, WorkspaceQuery(run_id=run_id, duplicate_policy="sources",
                         results=["meets_criterion", "does_not_meet", "uncertain", "not_applicable"]))
        query_id = query["query_id"]
        scope = Workspace(store).require_query(query_id)
        clause, params, modern = Workspace(store)._scope(project_id, scope["scope"])
        if not modern:
            raise ValueError("Report proof requires a recorded contextual run")
        table = Workspace(store)._table(modern)
        expected_typed = store.query("SELECT a.criterion_kind,a.result,COUNT(*) count FROM " + table + " WHERE " + clause + " GROUP BY a.criterion_kind,a.result ORDER BY a.criterion_kind,a.result", params)
        with store.lock:
            expected_ids = ids_hash(row[0] for row in store._db.execute("SELECT a.id FROM " + table + " WHERE " + clause + " ORDER BY a.id", params))
            where, document_params = Workspace(store)._document_scope(project_id, scope["scope"])
            expected_source_ids = ids_hash(row[0] for row in store._db.execute("SELECT d.id FROM documents d WHERE " + " AND ".join(where) + " ORDER BY d.id", document_params))
        expected_sources = query["metrics"]["documents"]
        expected_assessments = query["metrics"]["findings"]
        if expected_counts and any(expected_counts.get(key, value) != value for key, value in (("documents", expected_sources), ("assessments", expected_assessments))):
            raise ValueError("Restored report selection differs from expected package counts")
        job = manager.create(project_id, {"query_id": query_id, "format": "xlsx", "profile": "briefing", "publication_type": "archive"})
        job_id = job["id"]
        calls = 0
        while job["status"] in {"queued", "running"}:
            sample(job_id)
            manager.work_once(job_id, deadline_seconds=420)
            calls += 1
            job = manager.get(job_id)
            print(json.dumps({"phase": "durable_report_job", "status": job["status"], "progress": job["progress"], "elapsed_seconds": round(time.perf_counter() - started, 2)}), flush=True)
        if job["status"] != "completed":
            raise ValueError("Durable report job failed: " + json.dumps(job.get("error")))
        artifact_rows = store.query("SELECT * FROM report_job_artifacts WHERE job_id=? ORDER BY format", [job_id])
        files = {}
        for item in artifact_rows:
            path = store.object_path(item["object_key"])
            if path.stat().st_size != item["bytes"] or file_hash(path) != item["sha256"]:
                raise ValueError("Ready artifact SHA/size mismatch")
            files[item["format"]] = path
        manifest = json.loads(files["manifest"].read_text())
        if manifest["counts"]["documents"] != expected_sources or manifest["counts"]["assessments"] != expected_assessments or manifest["assessment_ids_sha256"] != expected_ids or manifest["source_ids_sha256"] != expected_source_ids or manifest["typed_counts"] != expected_typed:
            raise ValueError("Report manifest selection/outcomes differ from source database")
        with gzip.open(files["snapshot"], "rb") as stream:
            digest, size = _stream_hash(stream)
        if digest != manifest["snapshot"]["uncompressed_sha256"] or size != manifest["snapshot"]["uncompressed_bytes"]:
            raise ValueError("Full report snapshot raw integrity failed")
        workbook = _workbook(files["xlsx"], manifest["workbook"])
        if workbook["Findings"]["ordered_ids_sha256"] != manifest["assessment_record_order_sha256"] or workbook["Documents"]["ordered_ids_sha256"] != manifest["source_ids_sha256"]:
            raise ValueError("Actual workbook identities differ from frozen source records")
        with zipfile.ZipFile(files["inputs"]) as archive:
            if archive.testzip() is not None:
                raise ValueError("Frozen input ZIP integrity failed")
        sample(job_id, force=True)
        receipt.update(status="pass", query_id=query_id, scope_hash=job["scope_hash"], snapshot_version=job["snapshot_version"], job_id=job_id,
                       counts=manifest["counts"], typed_counts=expected_typed, assessment_ids_sha256=expected_ids,
                       source_ids_sha256=manifest["source_ids_sha256"], snapshot=manifest["snapshot"], workbook=workbook,
                       durable_work_calls=calls, artifact_files=[{key: row[key] for key in ("format", "filename", "bytes", "sha256")} for row in artifact_rows],
                       no_fresh_inference_path=True, bounded_capture_parts=store.one("SELECT COUNT(*) n FROM report_job_parts WHERE job_id=?", [job_id])["n"])
        return receipt
    except BaseException as error:
        receipt.update(status="failed", error_type=type(error).__name__, error=str(error)[:500])
        raise
    finally:
        if job_id:
            for table in ("report_job_artifacts", "report_job_parts"):
                store.execute("DELETE FROM " + table + " WHERE job_id=?", [job_id])
            store.execute("DELETE FROM report_jobs WHERE id=?", [job_id])
            # This proof removes only the ownership row for its own temporary
            # output. Source/import and unrelated report ownership stays intact.
            store.execute("DELETE FROM privacy_artifacts WHERE project_id=? AND path=?",
                          [project_id, "objects/report-jobs/" + job_id])
            shutil.rmtree(store.root / "report-work" / job_id, ignore_errors=True)
            shutil.rmtree(store.object_path("report-jobs/" + job_id), ignore_errors=True)
        if query_id:
            store.execute("DELETE FROM v3_query_snapshots WHERE id=?", [query_id])
        receipt.update(temporary_report_inputs_and_artifacts_cleaned=True, elapsed_seconds=round(time.perf_counter()-started, 6),
                       process_lifetime_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                       child_lifetime_peak_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*1024, storage=samples,
                       storage_measurement="Distinct-inode logical file sizes; native hardlinks counted once. Filesystem free space sampled independently.")
        target = Path(receipt_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
