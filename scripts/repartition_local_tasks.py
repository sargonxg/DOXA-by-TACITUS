#!/usr/bin/env python3
"""Repartition a STOPPED local v3 run into one unchanged source unit per task.

Default is a dry run. Stop all workers for this isolated local workspace before
using --apply. Original tasks and request-ledger state are archived privately;
model decisions, reviews, source anchors, model/prompt versions and request rows
are retained. This maintenance tool never calls inference or downloads models.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from doxa.contextual import build_units  # noqa: E402
from doxa.store import Store, utcnow  # noqa: E402

VERSION = "local-one-unit-tasks-1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def repartition_local_tasks(store: Store, run_id: str, *, apply: bool = False) -> dict:
    if store.remote:
        raise ValueError("Repartition only an isolated local SQLite workspace with stopped workers")
    run = store.one("SELECT * FROM runs WHERE id=?", [run_id])
    if not run:
        raise ValueError("Run does not exist")
    config = json.loads(run["config_json"])
    if config.get("provider") != "ollama" or config.get("task_index_basis") != "source_units_v3":
        raise ValueError("Only local Ollama contextual source-unit tasks can be repartitioned")
    tasks = store.query("SELECT * FROM tasks WHERE run_id=? ORDER BY ordinal,id", [run_id])
    unfinished = [task for task in tasks if task["status"] != "completed"]
    if not unfinished or all(task["end_block"] - task["start_block"] == 1 for task in unfinished):
        return {"changed": False, "run_id": run_id, "reason": "Unfinished tasks already contain at most one unit"}
    expected_sources = {row["id"]: row["sha256"] for row in config.get("source_snapshot", [])}
    units_by_document = {}
    for identity in {task["document_id"] for task in tasks}:
        document = store.one("SELECT * FROM documents WHERE id=? AND project_id=?", [identity, run["project_id"]])
        if not document or document["sha256"] != expected_sources.get(identity):
            raise ValueError("A source changed from its frozen run snapshot")
        document["metadata"] = json.loads(document["metadata_json"] or "{}")
        source = document["metadata"].get("source_id") or document["metadata"].get("source_identity") or document.get("source_url") or identity
        document["source_version_id"] = hashlib.sha256(f"{source}:{document['sha256']}".encode()).hexdigest()[:32]
        document["blocks"] = store.get_json(document["blocks_key"])
        units = build_units(document, max_unit_chars=config.get("unit_max_chars", 1200))
        if config.get("verification_selection"):
            selected = set(config["verification_selection"]["unit_ids"].get(identity, []))
            units = [unit for unit in units if unit["id"] in selected]
        ledger = {row["unit_id"] for row in store.query("SELECT unit_id FROM v3_unit_statuses WHERE run_id=? AND document_id=?", [run_id, identity])}
        if ledger != {unit["id"] for unit in units}:
            raise ValueError("Rebuilt canonical source-unit IDs differ from the frozen ledger")
        units_by_document[identity] = units
    covered = set()
    for task in tasks:
        units = units_by_document[task["document_id"]]
        if not 0 <= task["start_block"] < task["end_block"] <= len(units):
            raise ValueError("Task source-unit range is invalid")
        for unit in units[task["start_block"]:task["end_block"]]:
            pair = (task["document_id"], unit["id"])
            if pair in covered:
                raise ValueError("Existing tasks overlap source units")
            covered.add(pair)
    if len(covered) != sum(map(len, units_by_document.values())):
        raise ValueError("Existing task ranges do not cover every frozen source unit")
    replacements, mapping, unit_tasks = [], {}, {}
    ordinal = max((task["ordinal"] for task in tasks), default=-1) + 1
    for task in unfinished:
        mapping[task["id"]] = []
        for index in range(task["start_block"], task["end_block"]):
            unit = units_by_document[task["document_id"]][index]
            identity = digest([VERSION, run_id, task["document_id"], unit["id"]])[:32]
            replacements.append({"id": identity, "run_id": run_id, "document_id": task["document_id"],
                                 "ordinal": ordinal, "start_block": index, "end_block": index + 1})
            mapping[task["id"]].append(identity)
            unit_tasks[(task["document_id"], unit["id"])] = identity
            ordinal += 1
    reservations = store.query("SELECT * FROM budget_reservations WHERE run_id=?", [run_id])
    if any(float(row["amount"]) != 0 or float(row.get("charged_usd") or 0) != 0 for row in reservations):
        raise ValueError("Local maintenance refuses a run with paid/reserved nonzero request amounts")
    reassigned = []
    for row in store.query("SELECT id,document_id,unit_id,task_id FROM v3_assessments WHERE run_id=?", [run_id]):
        if row["task_id"] in mapping:
            new_task = unit_tasks.get((row["document_id"], row["unit_id"]))
            if not new_task:
                raise ValueError("An existing assessment falls outside its original task range")
            reassigned.append((row["id"], new_task))
    legacy = []
    for row in store.query("SELECT id,document_id,task_id,payload_json FROM findings WHERE run_id=?", [run_id]):
        if row["task_id"] in mapping:
            unit_id = json.loads(row["payload_json"]).get("unit_id")
            new_task = unit_tasks.get((row["document_id"], unit_id))
            if not new_task:
                raise ValueError("A legacy finding lacks a recoverable v3 unit ID")
            legacy.append((row["id"], new_task))
    report = {"version": VERSION, "run_id": run_id, "changed": True, "applied": False,
              "old_unfinished_tasks": len(unfinished), "new_unit_tasks": len(replacements),
              "cached_assessments_retained": len(reassigned), "request_rows_retained": len(reservations),
              "source_units_total": len(covered), "pipeline_version": config.get("pipeline_version"),
              "notice": "Operational repartition only; source units/context, prompts, models, raw decisions and reviews are unchanged."}
    if not apply:
        return report
    archive_id = digest([tasks, replacements, reservations])[:32]
    archive_key = f"runs/{run_id}/task-repartition-{archive_id}.json"
    archive = {**report, "created_at": utcnow(), "old_tasks": tasks, "replacement_tasks": replacements,
               "mapping": mapping, "request_ledger_before": reservations}
    store.put_json(archive_key, archive)
    assert store._db is not None
    connection = store._db
    with store.lock:
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = [dict(row) for row in connection.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY ordinal,id", [run_id])]
            if current != tasks:
                raise ValueError("Task state changed; stop every worker before repartitioning")
            for task in unfinished:
                connection.execute("DELETE FROM tasks WHERE id=?", [task["id"]])
            for task in replacements:
                connection.execute("INSERT INTO tasks(id,run_id,document_id,ordinal,start_block,end_block) VALUES(?,?,?,?,?,?)", list(task.values()))
            for identity, task_id in reassigned:
                connection.execute("UPDATE v3_assessments SET task_id=? WHERE id=?", [task_id, identity])
            for identity, task_id in legacy:
                connection.execute("UPDATE findings SET task_id=? WHERE id=?", [task_id, identity])
            for (document_id, unit_id), task_id in unit_tasks.items():
                connection.execute("UPDATE v3_unit_statuses SET task_id=? WHERE run_id=? AND document_id=? AND unit_id=?", [task_id, run_id, document_id, unit_id])
            for reservation in reservations:
                if reservation["status"] != "reserved":
                    continue
                usage = {**json.loads(reservation["usage_json"] or "{}"), "cost_usd": 0.0, "estimated": True,
                         "tokens_unknown": True, "interrupted_for_operational_repartition": True,
                         "cost_basis": "local_compute_excluded"}
                connection.execute("UPDATE budget_reservations SET status='settled',charged_usd=0,usage_json=?,settled_at=? WHERE id=? AND status='reserved'", [json.dumps(usage), utcnow(), reservation["id"]])
            config.setdefault("operational_repartitions", []).append({"version": VERSION, "archive_key": archive_key,
                                                                       "old_tasks": len(unfinished), "new_tasks": len(replacements), "at": utcnow()})
            connection.execute("UPDATE runs SET status='queued',cancel_requested=0,completed_at=NULL,errors_json='[]',config_json=? WHERE id=?", [json.dumps(config), run_id])
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    report.update(applied=True, archive_key=archive_key)
    store.put_json(archive_key, {**archive, "applied": True, "result": report})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    store = Store(args.data_dir)
    try:
        print(json.dumps(repartition_local_tasks(store, args.run_id, apply=args.apply), indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()
