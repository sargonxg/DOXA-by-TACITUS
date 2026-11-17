#!/usr/bin/env python3
"""Fresh isolated local restore proof; execution requires a final package SHA gate."""

from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import shutil
import socket
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "6ea29f063abcdda5425d91277c9137cbb66f461fa2fff26f1f3543c51fb02ff3"
GIB = 1024**3


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def now():
    return datetime.now(timezone.utc).isoformat()


def execute(args):
    if not __debug__:
        raise RuntimeError("Run verification without Python -O; proof assertions must remain enabled")
    started = time.perf_counter()
    receipt = {
        "schema_version": "evidence-replay-1.0",
        "verification_kind": "actual_isolated_saved_news_restore",
        "generated_at": now(),
        "status": "running",
        "interpretation_accuracy": None,
        "independent_human_reference_labels": False,
    }
    package = Path(args.package).resolve()
    package = package / "showcase.json" if package.is_dir() else package
    if not re.fullmatch("[a-f0-9]{64}", args.expected_package_sha256 or ""):
        raise ValueError("Explicit final package SHA256 is required before any import")
    if package.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Manifest size exceeds loader limit")
    if file_hash(package) != args.expected_package_sha256:
        raise ValueError("Package has not matched the explicitly authorized final manifest SHA256")
    manifest = json.loads(package.read_text())
    original = manifest.get("run", {})
    config = original.get("config", {})
    if original.get("status") not in ("completed", "partial"):
        raise ValueError("Wait for a rebuilt terminal completed/partial package before any source import")
    if manifest.get("counts", {}).get("documents") != 5000 or len(config.get("source_snapshot", [])) != 5000:
        raise ValueError("Package is not the complete canonical5000-source selection")
    source_manifest = ROOT / "corpus/news/manifest.jsonl"
    if file_hash(source_manifest) != CANONICAL:
        raise ValueError("Canonical source manifest changed")
    declared = manifest.get("benchmark", {}).get("collection", {}).get("manifest_sha256")
    if declared and declared != CANONICAL:
        raise ValueError("Saved package declares a different source cohort")
    source_rows = [json.loads(line) for line in source_manifest.read_text().splitlines() if line.strip()]
    canonical_sources = {r["id"]: r["content_sha256"] for r in source_rows}
    if len(source_rows) != 5000 or len(canonical_sources) != 5000:
        raise ValueError("Canonical source identities are not distinct5000")
    source_bytes = sum(
        (source_manifest.parent / r["metadata_path"]).stat().st_size
        if (source_manifest.parent / r["metadata_path"]).exists()
        else len(canonical(r)) + int(r.get("body_characters", 0)) * 4
        for r in source_rows
    )
    raw_bytes = sum(part["uncompressed_bytes"] for part in manifest["parts"])
    estimated_bytes = math.ceil(raw_bytes * 2 + source_bytes * 2 + 512 * 1024**2)
    available = shutil.disk_usage("/tmp").free
    if estimated_bytes > args.max_temp_gib * GIB or available < estimated_bytes + args.min_free_gib * GIB:
        raise ValueError("Insufficient bounded scratch headroom; no import started")
    receipt.update(
        package_manifest_sha256=args.expected_package_sha256,
        canonical_source_manifest_sha256=CANONICAL,
        original_run_id=original["id"],
        package_run_status=original["status"],
        expected_counts=manifest["counts"],
        expected_unit_states=manifest["unit_states"],
        expected_outcomes=manifest["outcomes"],
        preflight={
            "archive_raw_bytes": raw_bytes,
            "source_metadata_bytes": source_bytes,
            "estimated_temp_bytes": estimated_bytes,
            "free_bytes": available,
            "max_temp_bytes": int(args.max_temp_gib * GIB),
            "minimum_remaining_free_bytes": int(args.min_free_gib * GIB),
        },
    )
    calls = Counter()

    def blocked(kind):
        def call(*_a, **_k):
            calls[kind] += 1
            raise AssertionError("Restore attempted prohibited " + kind)

        return call

    def blocked_async(kind):
        async def call(*_a, **_k):
            calls[kind] += 1
            raise AssertionError("Restore attempted prohibited " + kind)

        return call

    socket.socket.connect = blocked("network_socket_connect")
    socket.socket.connect_ex = blocked("network_socket_connect_ex")
    socket.create_connection = blocked("network_connection")
    socket.getaddrinfo = blocked("network_resolution")
    os.environ["DOXA_CLOUD_STATE_URL"] = ""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.path.insert(0, str(ROOT))
    import httpx

    httpx.Client.request = blocked("httpx_request")
    httpx.AsyncClient.request = blocked_async("httpx_async_request")
    import doxa.providers as providers

    providers.get_provider = blocked_async("provider_factory")
    for name in (
        "BaseProvider",
        "LexicalProvider",
        "OllamaProvider",
        "OpenAICompatibleProvider",
        "CloudflareProvider",
        "RunpodProvider",
    ):
        cls = getattr(providers, name)
        cls.generate = blocked_async("provider_generate")
        cls.health = blocked_async("provider_health")
    from doxa.cpu_models import CPUModels

    CPUModels._load = blocked("cpu_model_load")
    CPUModels.embed = blocked("cpu_embedding")
    CPUModels.embed_windows = blocked("cpu_embedding_windows")
    CPUModels.score_rubrics = blocked("cpu_nli")
    from doxa.store import Store, utcnow
    from doxa.news import import_news, restore_news_showcase
    from doxa.showcase_v3 import read_manifest, _records

    checked, checked_path, checked_hash = read_manifest(package)
    assert checked_hash == args.expected_package_sha256 and checked == manifest
    scratch = Path(tempfile.mkdtemp(prefix="doxa-news-restore-", dir="/tmp"))
    store = None
    phase = "initializing"
    object_bytes = 0
    max_temp = 0
    max_wal = 0
    min_free = available
    batches = 0
    last_heartbeat = 0

    def storage_sample(force=False):
        nonlocal max_temp, max_wal, min_free, last_heartbeat
        dbbytes = sum(
            (scratch / name).stat().st_size
            for name in ("doxa.sqlite3", "doxa.sqlite3-wal", "doxa.sqlite3-shm")
            if (scratch / name).exists()
        )
        wal = (scratch / "doxa.sqlite3-wal").stat().st_size if (scratch / "doxa.sqlite3-wal").exists() else 0
        max_temp = max(max_temp, dbbytes + object_bytes)
        max_wal = max(max_wal, wal)
        free = shutil.disk_usage(scratch).free
        min_free = min(min_free, free)
        if free < args.min_free_gib * GIB or dbbytes + object_bytes > args.max_temp_gib * GIB:
            raise RuntimeError(
                "Scratch headroom bound reached; preserving original inputs and cleaning only isolated verification data"
            )
        if force or time.perf_counter() - last_heartbeat > 20:
            print(
                json.dumps(
                    {
                        "phase": phase,
                        "temp_bytes": dbbytes + object_bytes,
                        "wal_bytes": wal,
                        "free_bytes": free,
                        "elapsed_seconds": round(time.perf_counter() - started, 2),
                    }
                ),
                flush=True,
            )
            last_heartbeat = time.perf_counter()

    def counts():
        return {
            table: store.one("SELECT COUNT(*) n FROM " + table)["n"]
            for table in (
                "documents",
                "runs",
                "tasks",
                "v3_sources",
                "v3_run_sources",
                "v3_assessments",
                "v3_unit_statuses",
                "budget_ledger",
                "budget_reservations",
                "reviews",
                "v3_review_events",
            )
        }

    def data_fingerprint():
        h = hashlib.sha256()
        tables = [
            r["name"]
            for r in store.query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            if not r["name"].startswith("document_search_")
        ]
        for table in tables:
            if not re.fullmatch("[A-Za-z_][A-Za-z0-9_]*", table):
                raise ValueError("Unexpected logical table identifier")
            fields = store.query("PRAGMA table_info(" + table + ")")
            keys = [r["name"] for r in sorted(fields, key=lambda r: r["pk"]) if r["pk"]]
            synthetic = not keys
            order = ["rowid"] if synthetic else keys
            select = "rowid AS _verification_rowid,*" if synthetic else "*"
            h.update(table.encode())
            last = None
            while True:
                clause = (
                    ""
                    if last is None
                    else " WHERE (" + ",".join(order) + ")>(" + ",".join("?" for _ in order) + ")"
                )
                rows = store.query(
                    "SELECT "
                    + select
                    + " FROM "
                    + table
                    + clause
                    + " ORDER BY "
                    + ",".join(order)
                    + " LIMIT 256",
                    last or [],
                )
                if not rows:
                    break
                for row in rows:
                    h.update(canonical(row))
                    h.update(b"\n")
                last = [rows[-1]["_verification_rowid"]] if synthetic else [rows[-1][key] for key in keys]
        objects = 0
        for path in sorted((scratch / "objects").rglob("*")):
            if path.is_file():
                h.update(b"OBJECT\0")
                h.update(str(path.relative_to(scratch / "objects")).encode())
                h.update(bytes.fromhex(file_hash(path)))
                objects += 1
        return {
            "sha256": h.hexdigest(),
            "logical_tables": tables,
            "source_objects": objects,
            "scope": "All columns of logical application tables plus ordered FTS rows and every source object; SQLite internal and FTS shadow tables excluded.",
        }

    try:
        store = Store(scratch)
        assert not store.remote
        store.init()
        connection = store._db
        connection.execute("PRAGMA cache_size=-16384")
        connection.execute("PRAGMA wal_autocheckpoint=1000")
        connection.execute("PRAGMA journal_size_limit=16777216")
        original_batch = store.batch
        original_put = store.put_bytes

        def guarded_batch(statements):
            nonlocal batches
            if batches % 64 == 0:
                storage_sample()
            result = original_batch(statements)
            batches += 1
            if batches % 512 == 0:
                connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
                storage_sample()
            return result

        def guarded_put(key, value, content_type="application/octet-stream"):
            nonlocal object_bytes
            path = store.object_path(key)
            previous = path.stat().st_size if path.exists() else 0
            original_put(key, value, content_type)
            object_bytes += len(value) - previous

        store.batch = guarded_batch
        store.put_bytes = guarded_put
        project = "restore-check-" + args.expected_package_sha256[:20]
        store.execute(
            "INSERT INTO projects(id,name,description,created_at) VALUES(?,?,?,?)",
            [
                project,
                "Isolated canonical news restore verification",
                "Temporary software verification only; no inference",
                utcnow(),
            ],
        )
        before = counts()
        assert before["runs"] == before["budget_ledger"] == before["budget_reservations"] == 0
        phase = "importing_canonical_sources"
        timer = time.perf_counter()
        source_import = import_news(store, project, ROOT, use_saved_showcase=False)
        source_seconds = time.perf_counter() - timer
        assert source_import["documents"] == 5000 and source_import["saved_results"] is False
        mapping = {}
        sha_by_source = {}
        for row in store.query("SELECT id,sha256,metadata_json FROM documents WHERE project_id=?", [project]):
            sid = json.loads(row["metadata_json"])["source_id"]
            assert sid not in mapping
            mapping[sid] = row["id"]
            sha_by_source[sid] = row["sha256"]
        assert sha_by_source == canonical_sources and len(mapping) == 5000
        source_only_counts = counts()
        assert (
            source_only_counts["runs"]
            == source_only_counts["budget_reservations"]
            == source_only_counts["budget_ledger"]
            == 0
        )
        phase = "first_restore"
        storage_sample(True)
        timer = time.perf_counter()
        run_id = restore_news_showcase(store, project, mapping, package)
        first_seconds = time.perf_counter() - timer
        restored = store.one("SELECT * FROM runs WHERE id=?", [run_id])
        restored_config = json.loads(restored["config_json"])
        snapshot = restored_config["saved_snapshot"]
        assert restored["status"] == original["status"]
        assert snapshot["sha256"] == args.expected_package_sha256 and snapshot["import_complete"] is True
        assert snapshot["original_run_id"] == original["id"] and snapshot["historical_usage"] is True
        assert snapshot["historical_spent_usd"] == original.get("spent_usd", 0) and snapshot[
            "historical_reserved_usd"
        ] == original.get("reserved_usd", 0)
        assert restored["spent_usd"] == restored["reserved_usd"] == 0
        for field in ("progress", "usage", "model_profile"):
            assert json.loads(restored[field + "_json"]) == original.get(field, {})
        first_counts = counts()
        assert (
            first_counts["documents"]
            == first_counts["v3_sources"]
            == first_counts["v3_run_sources"]
            == first_counts["tasks"]
            == 5000
            and first_counts["runs"] == 1
        )
        assert first_counts["v3_assessments"] == manifest["counts"]["assessments"]
        assert (
            dict(
                (r["status"], r["n"])
                for r in store.query(
                    "SELECT status,COUNT(*) n FROM v3_unit_statuses WHERE run_id=? GROUP BY status", [run_id]
                )
            )
            == manifest["unit_states"]
        )
        assert (
            dict(
                (r["result"], r["n"])
                for r in store.query(
                    "SELECT result,COUNT(*) n FROM v3_assessments WHERE run_id=? GROUP BY result", [run_id]
                )
            )
            == manifest["outcomes"]
        )
        assert (
            store.one(
                "SELECT COUNT(*) n FROM tasks WHERE attempts!=0 OR lease_token IS NOT NULL OR lease_until IS NOT NULL"
            )["n"]
            == 0
        )
        assert first_counts["budget_reservations"] == first_counts["budget_ledger"] == 0
        phase = "checking_all_restored_payloads"
        timer = time.perf_counter()
        assessments_checked = references_checked = units_checked = source_characters_checked = (
            source_words_checked
        ) = 0
        allowed = {
            "id",
            "project_id",
            "run_id",
            "document_id",
            "task_id",
            "evidence_refs",
            "review_status",
            "review_note",
            "review_overlay",
            "source_assessment_id",
            "historical_review_status",
            "historical_review_note",
            "historical_review_overlay",
        }
        for ordinal, record in enumerate(_records(manifest, checked_path)):
            doc_id = mapping[record["source_id"]]
            doc = store.one("SELECT blocks_key FROM documents WHERE id=?", [doc_id])
            blocks = {b["id"]: b["text"] for b in store.get_json(doc["blocks_key"])}
            source_characters_checked += sum(len(text) for text in blocks.values())
            source_words_checked += sum(len(text.split()) for text in blocks.values())
            actual = {
                json.loads(row["payload_json"])["source_assessment_id"]: row
                for row in store.query(
                    "SELECT * FROM v3_assessments WHERE run_id=? AND document_id=?", [run_id, doc_id]
                )
            }
            assert len(actual) == len(record["assessments"])
            for expected in record["assessments"]:
                row = actual[expected["id"]]
                value = json.loads(row["payload_json"])
                assert all(value.get(key) == data for key, data in expected.items() if key not in allowed)
                assert (
                    value["project_id"] == project
                    and value["run_id"] == run_id
                    and value["document_id"] == doc_id
                )
                assert row["review_status"] == "unreviewed" and row["review_note"] == ""
                assert (
                    value["review_status"] == "unreviewed"
                    and value["review_note"] == ""
                    and "review_overlay" not in value
                )
                history = {
                    key: data for key, data in expected.items() if key.startswith("historical_review_")
                }
                for key in ("review_status", "review_note", "review_overlay"):
                    if expected.get(key):
                        history["historical_" + key] = expected[key]
                assert all(value.get(key) == data for key, data in history.items())
                for key in (
                    "unit_id",
                    "lens_id",
                    "criterion_id",
                    "criterion_kind",
                    "result",
                    "attribution",
                    "speech_role",
                    "speaker_stance",
                    "document_stance",
                ):
                    if key in value:
                        assert row[key] == value[key]
                assert bool(row["hypothetical"]) == value.get("hypothetical", False)
                for ref, oldref in zip(value["evidence_refs"], expected["evidence_refs"]):
                    assert (
                        all(ref.get(key) == data for key, data in oldref.items() if key != "document_id")
                        and ref["document_id"] == doc_id
                    )
                    assert blocks[ref["block_id"]][ref["start"] : ref["end"]] == ref["quote"]
                    references_checked += 1
                assert len(value["evidence_refs"]) == len(expected["evidence_refs"])
                assessments_checked += 1
            actual_units = {
                row["unit_id"]: row
                for row in store.query(
                    "SELECT * FROM v3_unit_statuses WHERE run_id=? AND document_id=?", [run_id, doc_id]
                )
            }
            assert len(actual_units) == len(record["unit_statuses"])
            for expected in record["unit_statuses"]:
                row = actual_units[expected["unit_id"]]
                value = json.loads(row["payload_json"])
                assert row["status"] == expected["status"] and row["characters"] == expected["characters"]
                assert all(
                    value.get(key) == data
                    for key, data in expected.items()
                    if key not in {"document_id", "run_id"}
                )
                units_checked += 1
            if ordinal % 250 == 0:
                storage_sample()
        assert (
            assessments_checked == manifest["counts"]["assessments"]
            and references_checked == manifest["counts"]["audited_references"]
        )
        assert (
            source_characters_checked == manifest["counts"]["source_characters"]
            and source_words_checked == manifest["counts"]["words"]
        )
        payload_seconds = time.perf_counter() - timer
        phase = "idempotent_second_restore"
        storage_sample(True)
        before_digest = data_fingerprint()
        timer = time.perf_counter()
        again = restore_news_showcase(store, project, mapping, package)
        second_seconds = time.perf_counter() - timer
        after_digest = data_fingerprint()
        assert again == run_id and counts() == first_counts and before_digest == after_digest
        assert (
            store.query("SELECT * FROM budget_ledger") == []
            and store.query("SELECT * FROM budget_reservations") == []
        )
        assert not calls and "torch" not in sys.modules and "transformers" not in sys.modules
        assert file_hash(package) == args.expected_package_sha256 and file_hash(source_manifest) == CANONICAL
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        checkpoint = [list(row) for row in connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()]
        storage_sample(True)
        receipt.update(
            status="passed",
            restored_run_id=run_id,
            canonical_sources=5000,
            source_import_seconds=round(source_seconds, 6),
            first_restore_seconds=round(first_seconds, 6),
            payload_verification_seconds=round(payload_seconds, 6),
            second_restore_seconds=round(second_seconds, 6),
            restored_counts=first_counts,
            exact_payload_checks={
                "assessments": assessments_checked,
                "evidence_references": references_checked,
                "source_units": units_checked,
                "source_characters": source_characters_checked,
                "source_words": source_words_checked,
                "historical_review_metadata_checked": assessments_checked,
                "all_original_outcomes_and_raw_fields_preserved": True,
            },
            preserved={
                "package_run_status": True,
                "historical_progress": True,
                "historical_usage": True,
                "model_profile": True,
                "historical_spend": True,
                "canonical_source_hashes": True,
            },
            live_spend_usd=0,
            live_reserved_usd=0,
            new_budget_reservations=0,
            new_provider_requests=0,
            prohibited_call_attempts=dict(calls),
            network_calls_blocked=True,
            model_calls_blocked=True,
            torch_imported=False,
            transformers_imported=False,
            idempotent={
                "same_run_id": True,
                "row_counts_unchanged": True,
                "restored_logical_data_and_objects_unchanged": True,
                "fingerprint_sha256": before_digest["sha256"],
                "fingerprinted_logical_tables": before_digest["logical_tables"],
                "fingerprinted_source_objects": before_digest["source_objects"],
                "fingerprint_scope": before_digest["scope"],
            },
            sqlite_quick_check="ok",
            final_checkpoint=checkpoint,
            peak_process_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            storage={
                "sampled_peak_temp_file_bytes": max_temp,
                "sampled_peak_wal_bytes": max_wal,
                "minimum_free_bytes_observed": min_free,
                "bounded_batches": batches,
                "max_batch_statements": 50,
                "max_loader_batch_bytes": 768 * 1024,
            },
            notes=[
                "Actual full-data restore through import_news + restore_news_showcase into a fresh isolated local Store.",
                "Historical provider requests/tokens/spend are copied as provenance; no new model request or budget ledger entry is made.",
                "One restored scheduler task is created per source; original historical progress and all source-unit statuses/outcomes remain preserved.",
                "Full serialized payloads and exact source references were checked; this is software fidelity, not independent interpretation accuracy.",
                "Second restore validates the frozen archive again and leaves all persisted verification data unchanged.",
            ],
        )
        if args.report_job_receipt:
            phase = "persistent_full_corpus_report_job"
            from scripts.verify_report_jobs import verify_report_jobs

            report = verify_report_jobs(
                store, project, run_id, args.report_job_receipt,
                isolated=True, expected_counts={"documents": 5000, "assessments": assessments_checked},
                min_free_gib=args.min_free_gib,
            )
            receipt["persistent_report_job"] = {
                "status": report["status"], "receipt": str(args.report_job_receipt),
                "scope": "same fresh isolated restored workspace; original baseline untouched",
            }
            assert not calls and file_hash(package) == args.expected_package_sha256
    except BaseException as error:
        receipt.update(
            status="failed",
            failed_phase=phase,
            error_type=type(error).__name__,
            prohibited_call_attempts=dict(calls),
        )
        raise
    finally:
        if store is not None:
            store.close()
        shutil.rmtree(scratch)
        receipt["temporary_store_cleaned_up"] = not scratch.exists()
        receipt["elapsed_seconds"] = round(time.perf_counter() - started, 6)
        receipt["generated_at"] = now()
        target = Path(args.receipt)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        temp.replace(target)
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "canonical_sources": receipt.get("canonical_sources"),
                "package_run_status": receipt["package_run_status"],
                "receipt": str(Path(args.receipt)),
                "elapsed_seconds": receipt["elapsed_seconds"],
            }
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=ROOT / "benchmarks/v3-news/showcase.json")
    parser.add_argument("--receipt", type=Path, default=ROOT / "benchmarks/v04-evidence-replay.json")
    parser.add_argument("--report-job-receipt", type=Path, help="Also verify a complete persistent XLSX report job on the isolated restored workspace")
    parser.add_argument("--expected-package-sha256")
    parser.add_argument("--max-temp-gib", type=float, default=8)
    parser.add_argument("--min-free-gib", type=float, default=1)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "prepared_only",
                    "inputs_loaded": False,
                    "instruction": "Pass --execute and the explicit terminal package manifest SHA256 to run the isolated full-data proof.",
                }
            )
        )
        return
    if not 1 <= args.max_temp_gib <= 12 or not 0.5 <= args.min_free_gib <= 4:
        parser.error("Use bounded temporary storage and free-space reserve values")
    execute(args)


if __name__ == "__main__":
    main()
