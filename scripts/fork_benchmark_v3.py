#!/usr/bin/env python3
"""Explicitly fork a stopped v3 run onto a corrected immutable source cohort.

This command never starts inference. Keep the original worker pool stopped until
the new checkpoint has been published. Historical request accounting is copied
as provenance without debiting the shared monthly ledger a second time.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def scoped_id(run_id, original_id):
    return hashlib.sha256(f"{run_id}:{original_id}".encode()).hexdigest()[:32]


def stopped_run(store, run_id):
    from scripts.package_v3 import unpack_run
    row = store.one("SELECT * FROM runs WHERE id=?", [run_id])
    if not row:
        raise ValueError("Original run does not exist")
    run = unpack_run(row)
    if run["status"] not in {"cancelled", "partial", "completed", "failed", "paused_budget"}:
        raise ValueError("Stop the original worker pool and cancel or settle its run before forking")
    if store.one("SELECT id FROM tasks WHERE run_id=? AND status='running' LIMIT 1", [run_id]):
        raise ValueError("Original run still has running task leases")
    if not math.isclose(float(run["reserved_usd"]), 0, abs_tol=1e-9) or store.one("SELECT id FROM budget_reservations WHERE run_id=? AND status='reserved' LIMIT 1", [run_id]):
        raise ValueError("Original provider requests must all be settled before forking")
    if str(run["config"].get("methodology_version")) != "3.0" or run["config"].get("verification_selection"):
        raise ValueError("Only a full-source contextual primary run can be forked")
    if run["config"].get("task_index_basis") != "source_units_v3":
        raise ValueError("Fork requires the source-unit scheduler contract")
    return run


def source_rows(store, project_id, snapshots):
    result = {}
    for snapshot in snapshots:
        row = store.one("SELECT * FROM documents WHERE id=? AND project_id=?", [snapshot["id"], project_id])
        if row is None or row["sha256"] != snapshot["sha256"]:
            raise ValueError("A frozen source is missing, foreign, or has changed its declared checksum")
        row["metadata"] = json.loads(row["metadata_json"] or "{}")
        identity = row["metadata"].get("source_id")
        if not identity or identity in result:
            raise ValueError("The cohort requires distinct explicit source identities")
        result[identity] = row
    return result


def remap(value, identities):
    """Rewrite identifier fields without modifying any quotation or prose."""
    if isinstance(value, list):
        return [remap(item, identities) for item in value]
    if isinstance(value, dict):
        return {key: identities.get(item, item) if key in {"id", "run_id", "task_id", "assessment_id", "finding_id", "previous_review_id"} and isinstance(item, str)
                else remap(item, identities) for key, item in value.items()}
    return value


def batches(store, statements):
    for offset in range(0, len(statements), 30):
        store.batch(statements[offset:offset + 30])


def insert_statement(table, row):
    columns = list(row)
    return {"sql": f"INSERT INTO {table}(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ") ON CONFLICT(id) DO NOTHING",
            "params": [row[column] for column in columns]}


def validate_source(store, run, document, criteria):
    from doxa.showcase_v3 import _validate_record
    from scripts.package_v3 import _record
    record, _ = _record(store, run, document)
    # News checksums identify the exact canonical block JSON, including source
    # locators. A metadata correction may create a new immutable source version.
    if document.get("document_type") == "news_article" and record["blocks_sha256"] != document["sha256"]:
        raise ValueError("Stored news blocks no longer match their frozen source checksum")
    _validate_record(store, record, document, {"id": document["id"], "sha256": document["sha256"]}, run, criteria)
    return record


async def fork_run(client, store, old_run_id: str, document_ids: list[str], *, budget_usd: float, reason: str):
    """Create a same-project fork; callers must explicitly stop the worker pool.

    The selected logical source identities must remain identical. Output reuse
    requires identical document IDs, source hashes and source-derived anchors.
    This function does not cancel, rewrite or resume the original run.
    """
    from doxa.api import RunInput
    from doxa.contextual import PROMPT_VERSION, UNIT_BUILDER_VERSION, _criteria, _hash, build_units
    from doxa.store import utcnow
    from doxa.workspace import Workspace
    from scripts.benchmark import api_json

    old = stopped_run(store, old_run_id)
    if store.remote:
        raise ValueError("Operational forks require an isolated local benchmark Store")
    if not reason.strip() or not math.isfinite(budget_usd) or not old["spent_usd"] <= budget_usd <= 10000:
        raise ValueError("An explicit reason and cumulative budget covering inherited spend are required")
    config = old["config"]
    if config.get("pipeline_version") != PROMPT_VERSION or config.get("unit_builder_version") != UNIT_BUILDER_VERSION:
        raise ValueError("Core method changed; source-cohort correction cannot migrate analytical methods")
    packs, criteria = _criteria(config.get("lens_snapshots") or config["lens_ids"], config)
    if config.get("lens_snapshots") and config["lens_snapshots"] != packs:
        raise ValueError("Selected rubric changed; start an independently analyzed run")
    if not config.get("lens_snapshots"):
        from scripts.benchmark_v3 import file_hash
        receipt_path = store.root / "frozen-engine-compatibility.json"
        if not receipt_path.exists():
            raise ValueError("A historical run without rubric snapshots requires its pre-extension frozen-method receipt")
        receipt = json.loads(receipt_path.read_text())
        actual = {"contextual": file_hash(ROOT / "doxa/contextual.py"), "models": file_hash(ROOT / "doxa/models.py"),
                  "selected_lenses": digest(packs)}
        if receipt.get("components") != actual:
            raise ValueError("Historical frozen core/selected-rubric receipt does not match")
    old_sources = source_rows(store, old["project_id"], config["source_snapshot"])
    if len(document_ids) != len(set(document_ids)):
        raise ValueError("New cohort contains duplicate source versions")
    selected = []
    for identity in document_ids:
        row = store.one("SELECT id,sha256 FROM documents WHERE id=? AND project_id=? AND status='ready'", [identity, old["project_id"]])
        if row is None:
            raise ValueError("New source is missing, foreign, or not ready")
        selected.append(row)
    new_sources = source_rows(store, old["project_id"], selected)
    if set(old_sources) != set(new_sources):
        raise ValueError("A cohort correction must retain every logical source identity")
    reusable = [identity for identity in new_sources if old_sources[identity]["id"] == new_sources[identity]["id"]
                and old_sources[identity]["sha256"] == new_sources[identity]["sha256"]]
    changed = sorted(set(new_sources) - set(reusable))
    for identity in reusable:
        validate_source(store, old, old_sources[identity], criteria)
    historical = store.query("SELECT * FROM budget_reservations WHERE run_id=?", [old_run_id])
    if any(row["status"] != "settled" for row in historical) or not math.isclose(
            sum(row["charged_usd"] or 0 for row in historical), old["spent_usd"], abs_tol=1e-8):
        raise ValueError("Original conservative spend must reconcile with settled request accounting")

    operation = digest({"original": old_run_id, "selection": selected, "budget": budget_usd, "reason": reason})
    checkpoint_key = "v3-source-fork:" + operation
    existing = store.one("SELECT value_json FROM settings WHERE key=?", [checkpoint_key])
    if existing:
        new_id = json.loads(existing["value_json"])["run_id"]
        new_row = store.one("SELECT * FROM runs WHERE id=?", [new_id])
        if not new_row or new_row["status"] not in {"paused_fork_importing", "paused_fork_failed"}:
            raise ValueError("This fork already completed; resume its published checkpoint instead")
        from scripts.package_v3 import unpack_run
        new = unpack_run(new_row)
    else:
        body = {key: config[key] for key in RunInput.model_fields if key in config and key not in {"document_ids", "verification_of"}}
        body.update({"methodology_version": "3.0", "document_ids": document_ids, "budget_usd": budget_usd,
                     "learning_snapshot_id": config.get("learning_snapshot_id")})
        new = await api_json(client, "POST", f"/api/projects/{old['project_id']}/runs", json=body)
        new_id = new["id"]
        store.execute("UPDATE runs SET status='paused_fork_importing' WHERE id=?", [new_id])
        store.execute("INSERT INTO settings(key,value_json) VALUES(?,?)", [checkpoint_key, json.dumps({"run_id": new_id})])
    try:
        for key in ("provider", "model", "model_revision", "unit_max_chars", "context_sentences", "task_index_basis", "pipeline_version", "unit_builder_version", "lens_ids", "question", "persona", "learning_snapshot_id", "custom_lens"):
            if config.get(key) != new["config"].get(key):
                raise ValueError("New run differs from the frozen method: " + key)
        if new["config"].get("lens_snapshots") != packs:
            raise ValueError("New selected rubric snapshots differ from the frozen criteria")
        task_map, reused = {}, Counter()
        now = utcnow()
        for identity in reusable:
            document = new_sources[identity]
            old_tasks = store.query("SELECT * FROM tasks WHERE run_id=? AND document_id=?", [old_run_id, document["id"]])
            new_tasks = store.query("SELECT * FROM tasks WHERE run_id=? AND document_id=?", [new_id, document["id"]])
            by_range = {(row["start_block"], row["end_block"]): row for row in new_tasks}
            for task in old_tasks:
                peer = by_range.get((task["start_block"], task["end_block"]))
                if peer is None:
                    raise ValueError("Source task boundaries changed")
                task_map[task["id"]] = peer["id"]

        for identity in reusable:
            document = new_sources[identity]
            record = validate_source(store, old, document, criteria)
            rows = store.query("SELECT * FROM v3_assessments WHERE run_id=? AND document_id=?", [old_run_id, document["id"]])
            identities = {old_run_id: new_id, **task_map}
            for row in rows:
                core_id = "a_" + _hash(row["unit_id"], row["lens_id"] + "/" + row["criterion_id"])
                identities[row["id"]] = scoped_id(new_id, core_id)
            statements = []
            for row in rows:
                if row["task_id"] not in task_map:
                    raise ValueError("Accepted outcome has no matching frozen task")
                original_id = row["id"]
                payload = remap(json.loads(row["payload_json"]), identities)
                payload["reused_from_assessment_id"] = original_id
                payload["reused_from_run_id"] = old_run_id
                row = remap(row, identities)
                row["payload_json"] = json.dumps(payload, ensure_ascii=False)
                row["review_json"] = json.dumps(remap(json.loads(row["review_json"] or "{}"), identities), ensure_ascii=False)
                statements.append(insert_statement("v3_assessments", row))
                reused["assessments"] += 1
            batches(store, statements)

            # Retain the immutable review audit as well as its current overlay.
            for old_row in rows:
                events = store.query("SELECT * FROM v3_review_events WHERE assessment_id=?", [old_row["id"]])
                event_ids = {event["id"]: scoped_id(new_id, event["id"]) for event in events}
                for event in events:
                    event = remap(event, {**identities, **event_ids})
                    for field in ("corrections_json", "resolved_json"):
                        event[field] = json.dumps(remap(json.loads(event[field]), identities), ensure_ascii=False)
                    store.batch([insert_statement("v3_review_events", event)])

            valid_pairs = {(item["unit_id"], item["criterion_key"]) for item in record["assessments"]}
            text_by_block = {block["id"]: block["text"] for block in store.get_json(document["blocks_key"])}
            for finding in store.query("SELECT * FROM findings WHERE run_id=? AND document_id=?", [old_run_id, document["id"]]):
                item = json.loads(finding["payload_json"])
                text = text_by_block.get(item.get("block_id"), "")
                start, end = item.get("start"), item.get("end")
                if (item.get("unit_id"), item.get("criterion_key")) not in valid_pairs or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text) or text[start:end] != item.get("quote"):
                    raise ValueError("Compatibility finding does not match a validated source outcome")
                identity_fields = [new_id, document["id"], item.get("block_id"), start, end, item.get("lens_id"), item.get("category"), item.get("criterion")]
                finding_id = hashlib.sha256(json.dumps(identity_fields).encode()).hexdigest()[:32]
                local_ids = {**identities, finding["id"]: finding_id}
                finding = remap(finding, local_ids)
                finding["payload_json"] = json.dumps(remap(item, local_ids), ensure_ascii=False)
                store.batch([insert_statement("findings", finding)])
                for review in store.query("SELECT * FROM reviews WHERE finding_id=?", [item["id"]]):
                    review["id"] = scoped_id(new_id, review["id"])
                    review = remap(review, local_ids)
                    store.batch([insert_statement("reviews", review)])

            for status in record["unit_statuses"]:
                current = store.one("SELECT task_id FROM v3_unit_statuses WHERE run_id=? AND document_id=? AND unit_id=?", [new_id, document["id"], status["unit_id"]])
                if not current:
                    raise ValueError("Rebuilt unit identity differs from the frozen source")
                store.execute("UPDATE v3_unit_statuses SET status=?,characters=?,payload_json=?,created_at=? WHERE run_id=? AND document_id=? AND unit_id=?",
                              [status["status"], status["characters"], json.dumps(remap(status, identities)), now, new_id, document["id"], status["unit_id"]])
                reused["accepted_units"] += status["status"] == "accepted"

            blocks = store.get_json(document["blocks_key"])
            source_identity = document["metadata"].get("source_id") or document["source_url"] or document["id"]
            units = build_units({**document, "source_version_id": hashlib.sha256(f"{source_identity}:{document['sha256']}".encode()).hexdigest()[:32], "blocks": blocks},
                                max_unit_chars=config["unit_max_chars"], context_sentences=config.get("context_sentences", 2))
            statuses = {status["unit_id"]: status["status"] for status in record["unit_statuses"]}
            for task in store.query("SELECT * FROM tasks WHERE run_id=? AND document_id=?", [old_run_id, document["id"]]):
                selected_units = units[task["start_block"]:task["end_block"]]
                accepted = [unit for unit in selected_units if statuses[unit["id"]] == "accepted"]
                complete = task["status"] == "completed" and len(accepted) == len(selected_units)
                coverage = {"total_blocks": len(selected_units), "processed_blocks": len(accepted), "total_units": len(selected_units),
                            "accepted_units": len(accepted), "source_characters": sum(unit["source_characters"] for unit in selected_units),
                            "processed_characters": sum(unit["source_characters"] for unit in accepted), "complete": complete,
                            "semantic_analysis_performed": True, "reused_from_run_id": old_run_id}
                key = f"runs/{new_id}/reused/{task_map[task['id']]}.json" if complete else None
                if key:
                    store.put_json(key, {"coverage": coverage, "usage": {}, "errors": [], "model_profile": old["model_profile"],
                                         "reused_from": {"run_id": old_run_id, "task_id": task["id"], "result_key": task["result_key"]}})
                store.execute("UPDATE tasks SET status=?,result_key=?,coverage_json=?,usage_json='{}',error=NULL,lease_token=NULL,lease_until=NULL WHERE id=? AND run_id=?",
                              ["completed" if complete else "pending", key, json.dumps(coverage), task_map[task["id"]], new_id])
                reused["completed_tasks"] += complete
            reused["documents"] += 1

        # Copy accounting provenance only. No settlement function is called and
        # budget_ledger is never written by this helper.
        for row in historical:
            usage = json.loads(row["usage_json"] or "{}")
            usage.update({"historical_reused": True, "new_provider_request": False,
                          "reused_from_run_id": old_run_id, "reused_from_reservation_id": row["id"]})
            row.update({"id": scoped_id(new_id, row["id"]), "run_id": new_id,
                        "task_id": task_map.get(row["task_id"], "historical:" + row["task_id"]),
                        "usage_json": json.dumps(usage)})
            store.batch([insert_statement("budget_reservations", row)])
        provenance = {"schema_version": "3.0", "operation_id": operation, "original_run_id": old_run_id, "reason": reason,
                      "created_at": now, "changed_source_ids": changed, "reused": dict(reused),
                      "historical_usage": old["usage"], "historical_budget_accounted_usd": old["spent_usd"],
                      "new_monthly_debit_usd": 0, "notice": "Historical request provenance is inherited, not new inference or a second monthly debit. Changed sources have no reused outcomes."}
        new_config = new["config"]
        new_config.update({"reused_from_run_id": old_run_id, "fork_provenance": provenance})
        tasks = store.query("SELECT document_id,status,start_block,end_block,coverage_json FROM tasks WHERE run_id=?", [new_id])
        by_document = defaultdict(list)
        for task in tasks:
            by_document[task["document_id"]].append(task["status"])
        total = sum(task["end_block"] - task["start_block"] for task in tasks)
        done = sum(json.loads(task["coverage_json"] or "{}").get("processed_blocks", 0) for task in tasks)
        complete = bool(tasks) and all(task["status"] == "completed" for task in tasks) and done == total
        progress = {"documents_total": len(by_document), "documents_done": sum(all(state == "completed" for state in states) for states in by_document.values()),
                    "documents_complete": sum(all(state == "completed" for state in states) for states in by_document.values()),
                    "documents_settled": sum(all(state == "completed" for state in states) for states in by_document.values()),
                    "tasks_total": len(tasks), "tasks_done": sum(task["status"] == "completed" for task in tasks), "tasks_failed": 0,
                    "blocks_total": total, "blocks_done": done, "semantic_blocks_done": done, "lexical_blocks_done": 0,
                    "semantic_analysis_performed": True, "analysis_kind": "model_analysis", "complete": complete}
        store.execute("UPDATE runs SET config_json=?,spent_usd=?,reserved_usd=0,usage_json=?,model_profile_json=?,progress_json=?,status=?,completed_at=? WHERE id=?",
                      [json.dumps(new_config), old["spent_usd"], json.dumps(old["usage"]), json.dumps(old["model_profile"]), json.dumps(progress),
                       "completed" if complete else "queued", now if complete else None, new_id])
        Workspace(store).touch(old["project_id"])
        return {"run_id": new_id, "project_id": old["project_id"], **provenance}
    except BaseException:
        store.execute("UPDATE runs SET status='paused_fork_failed' WHERE id=?", [new_id])
        raise


def publish_checkpoint(store, fork, selection, old_state, *, output: Path, data_dir: Path):
    from doxa.lenses import get_lens
    from scripts.benchmark_v3 import atomic_json, file_hash, progress_summary
    from scripts.package_v3 import unpack_run
    run = unpack_run(store.one("SELECT * FROM runs WHERE id=?", [fork["run_id"]]))
    config = {key: run["config"][key] for key in ("provider", "model", "methodology_version", "allow_remote_text", "lens_ids", "question", "persona", "budget_usd")}
    components = {"contextual": file_hash(ROOT / "doxa/contextual.py"), "models": file_hash(ROOT / "doxa/models.py"),
                  "selected_lenses": digest([get_lens(identity) for identity in config["lens_ids"]])}
    engine = digest({name: file_hash(ROOT / name) for name in ("doxa/contextual.py", "doxa/models.py", "doxa/lenses.py")})
    by_source = {json.loads(row["metadata_json"])["source_id"]: row for row in source_rows(store, run["project_id"], run["config"]["source_snapshot"]).values()}
    snapshots = [{"id": by_source[item["source_id"]]["id"], "sha256": by_source[item["source_id"]]["sha256"], "source_id": item["source_id"]} for item in selection["sources"]]
    state = {"schema_version": "3.0", "signature": digest({"config": {k: v for k, v in config.items() if k != "budget_usd"}, "selection": selection, "engine": engine}),
             "selection": selection, "data_dir": str(data_dir.resolve()), "engine_fingerprint": engine,
             "project_id": run["project_id"], "run_id": run["id"], "source_snapshots": snapshots,
             "provider_preflight": run["config"].get("provider_preflight", {}), "sessions": [], "elapsed_seconds": 0,
             "historical_sessions": old_state.get("sessions", []), "historical_elapsed_seconds": old_state.get("elapsed_seconds", 0),
             "fork_provenance": fork}
    receipt = data_dir / "frozen-engine-compatibility.json"
    if receipt.exists():
        atomic_json(data_dir / "fork-provenance" / f"{fork['original_run_id']}-engine.json", json.loads(receipt.read_text()))
    atomic_json(receipt, {"engine_fingerprint": engine, "components": components, "lens_ids": config["lens_ids"]})
    atomic_json(output.with_suffix(output.suffix + ".checkpoint.json"), state)
    atomic_json(output, progress_summary(store, state, run))
    return state


def validate_original_checkpoint(store, old, state, data_dir):
    config = {key: old["config"][key] for key in ("provider", "model", "methodology_version", "allow_remote_text", "lens_ids", "question", "persona")}
    if state.get("data_dir") != str(data_dir.resolve()) or state.get("run_id") != old["id"] or state.get("project_id") != old["project_id"]:
        raise ValueError("Original checkpoint does not match the frozen run/project/data-dir")
    # Prove the ORIGINAL signature using its ORIGINAL selection. A changed
    # canonical collection must never be smuggled into an old full-news run.
    expected = digest({"config": config, "selection": state["selection"], "engine": state["engine_fingerprint"]})
    if state.get("signature") != expected:
        raise ValueError("Original benchmark checkpoint signature is invalid")
    frozen = {(row["id"], row["sha256"]) for row in old["config"]["source_snapshot"]}
    recorded = {(row["id"], row["sha256"]) for row in state.get("source_snapshots", [])}
    if frozen != recorded:
        raise ValueError("Original checkpoint source versions differ from the frozen run")
    sources = source_rows(store, old["project_id"], old["config"]["source_snapshot"])
    declared = {row["source_id"]: row["content_sha256"] for row in state["selection"]["sources"]}
    if declared != {identity: row["sha256"] for identity, row in sources.items()}:
        raise ValueError("Original checkpoint source selection differs from its immutable versions")


async def run_cli(args):
    import httpx
    from doxa.api import create_app
    from doxa.news import import_news
    from scripts.benchmark_v3 import source_selection
    if not args.confirm_workers_stopped or os.environ.get("DOXA_CLOUD_STATE_URL"):
        raise ValueError("Explicit stopped-worker acknowledgement and a local benchmark data-dir are required")
    old_state = json.loads(args.old_checkpoint.read_text())
    target = args.output.with_suffix(args.output.suffix + ".checkpoint.json")
    if target.resolve() == args.old_checkpoint.resolve() or target.exists() or args.output.exists():
        raise ValueError("Choose a fresh output/checkpoint; preserve the original benchmark files")
    app = create_app(args.data_dir, start_workers=False, use_saved_showcase=False)
    async with app.router.lifespan_context(app):
        store = app.state.store
        old = stopped_run(store, args.old_run)
        validate_original_checkpoint(store, old, old_state, args.data_dir)
        selection = source_selection("news")
        imported = import_news(store, old_state["project_id"], ROOT, use_saved_showcase=False)
        rows = store.query("SELECT d.id,d.metadata_json FROM documents d JOIN batch_members b ON b.document_id=d.id WHERE b.batch_id=?", [imported["batch"]["id"]])
        mapping = {json.loads(row["metadata_json"])["source_id"]: row["id"] for row in rows}
        document_ids = [mapping[item["source_id"]] for item in selection["sources"]]
        headers = {"x-doxa-service-token": os.environ["DOXA_INTERNAL_TOKEN"]} if os.environ.get("DOXA_INTERNAL_TOKEN") else {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 0)), base_url="http://127.0.0.1", headers=headers) as client:
            fork = await fork_run(client, store, args.old_run, document_ids, budget_usd=args.budget, reason=args.reason)
        publish_checkpoint(store, fork, selection, old_state, output=args.output, data_dir=args.data_dir)
        print(json.dumps({"run_id": fork["run_id"], "original_run_id": args.old_run, "changed_source_ids": fork["changed_source_ids"],
                          "reused": fork["reused"], "historical_budget_accounted_usd": fork["historical_budget_accounted_usd"], "inference_started": False}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--old-run", required=True)
    parser.add_argument("--old-checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--confirm-workers-stopped", action="store_true")
    args = parser.parse_args(argv)
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=True)
    import asyncio
    try:
        asyncio.run(run_cli(args))
    except Exception as error:
        print(f"Fork stopped ({type(error).__name__}); no inference was started. Inspect local diagnostics.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
