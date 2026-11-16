"""Restore a genuine benchmark, rechecking every source and quotation.

Historical usage is provenance. Importing results makes no inference calls and
does not debit the current workspace's budget.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .store import Store, utcnow


def restore_showcase(store: Store, project_id: str, documents: list[dict], path: Path) -> str:
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    snapshot = json.loads(raw)
    source_docs = snapshot.get("documents", [])
    source_runs = snapshot.get("runs", [])
    if len(source_docs) != 20 or len(source_runs) != 1:
        raise ValueError("Showcase requires twenty documents and one recorded run")
    source_run = source_runs[0]
    if source_run.get("status") not in {"completed", "partial"}:
        raise ValueError("Only a finished benchmark snapshot can be imported")
    if snapshot.get("metrics", {}).get("coverage_detail", {}).get("sampled"):
        raise ValueError("A sampled benchmark cannot replace the complete showcase")
    by_source = {d.get("metadata", {}).get("source_id"): d for d in documents}
    source_ids = [doc.get("metadata", {}).get("source_id") for doc in source_docs]
    if len(by_source) != 20 or None in by_source or len(set(source_ids)) != 20 or set(source_ids) != set(by_source) or len({doc["id"] for doc in source_docs}) != 20:
        raise ValueError("Showcase requires twenty distinct matching source identities")
    mapping, blocks = {}, {}
    for old in source_docs:
        source_id = old.get("metadata", {}).get("source_id")
        current = by_source.get(source_id)
        if not source_id or current is None:
            raise ValueError("Showcase manifest does not match imported documents")
        current_blocks = store.get_json(current["blocks_key"])
        if old.get("blocks") != current_blocks:
            raise ValueError("Showcase source blocks changed; recompute the benchmark")
        mapping[old["id"]] = current
        blocks[old["id"]] = {b["id"]: b for b in current_blocks}
    for finding in snapshot.get("findings", []):
        block = blocks.get(finding["document_id"], {}).get(finding.get("block_id"))
        start, end = finding.get("start"), finding.get("end")
        if not block or not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(block["text"]) or block["text"][start:end] != finding.get("quote"):
            raise ValueError("Showcase finding failed exact source validation")
    digest = hashlib.sha256(raw).hexdigest()
    for previous in store.query("SELECT id,config_json FROM runs WHERE project_id=?", [project_id]):
        saved = json.loads(previous["config_json"]).get("saved_snapshot", {})
        if saved.get("sha256") == digest and saved.get("import_complete"):
            return previous["id"]
    run_id = hashlib.sha256(f"{project_id}:{digest}".encode()).hexdigest()[:32]
    config = dict(source_run["config"])
    config["source_snapshot"] = [{"id": d["id"], "sha256": d["sha256"]} for d in documents]
    if config.get("source_manifest_key"):
        config["source_manifest_key"] = f"runs/{run_id}/source-manifest.json"
        store.put_json(config["source_manifest_key"], [{key: d.get(key) for key in ["id", "sha256", "title", "author", "institution", "document_type", "published_at"]} for d in documents])
    config["saved_snapshot"] = {"sha256": digest, "generated_at": snapshot.get("generated_at"), "original_run_id": source_run["id"],
                                "historical_usage": True, "import_complete": False, "notice": "Previously computed output. Loading this snapshot makes no inference calls."}
    store.execute("INSERT INTO runs(id,project_id,status,config_json,created_at,started_at,completed_at,progress_json,usage_json,errors_json,model_profile_json) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                  [run_id, project_id, "importing", json.dumps(config), utcnow(), source_run.get("started_at"), source_run.get("completed_at"),
                   json.dumps(source_run.get("progress", {})), json.dumps(source_run.get("usage", {})), json.dumps(source_run.get("errors", [])), json.dumps(source_run.get("model_profile", {}))])
    by_document = defaultdict(list)
    for result in snapshot.get("benchmark", {}).get("task_results", []):
        by_document[result["document_id"]].append(result)
    task_by_doc = {}
    for ordinal, old in enumerate(source_docs):
        current = mapping[old["id"]]
        task_id = hashlib.sha256(f"{run_id}:{old['id']}".encode()).hexdigest()[:32]
        task_by_doc[old["id"]] = task_id
        parts = by_document[old["id"]]
        coverage = {"total_blocks": len(old["blocks"]), "processed_blocks": sum(p.get("coverage", {}).get("processed_blocks", 0) for p in parts),
                    "source_characters": sum(len(b["text"]) for b in old["blocks"]),
                    "processed_characters": sum(p.get("coverage", {}).get("processed_characters", 0) for p in parts),
                    "semantic_analysis_performed": config.get("provider") != "lexical"}
        coverage["complete"] = coverage["total_blocks"] == coverage["processed_blocks"]
        key = f"runs/{run_id}/saved/{task_id}.json"
        store.put_json(key, {"coverage": coverage, "saved_snapshot": config["saved_snapshot"]})
        store.execute("INSERT INTO tasks(id,run_id,document_id,ordinal,start_block,end_block,status,result_key,coverage_json) VALUES(?,?,?,?,0,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                      [task_id, run_id, current["id"], ordinal, len(old["blocks"]), "completed" if coverage["complete"] else "failed", key, json.dumps(coverage)])
    statements = []
    for original in snapshot.get("findings", []):
        finding = dict(original)
        document = mapping[original["document_id"]]
        finding_id = hashlib.sha256(f"{run_id}:{original['id']}".encode()).hexdigest()[:32]
        finding.update({"id": finding_id, "project_id": project_id, "run_id": run_id, "document_id": document["id"],
                        "review_status": "unreviewed", "review_note": "", "document_title": document["title"], "author": document["author"]})
        statements.append({"sql": "INSERT INTO findings(id,project_id,run_id,document_id,task_id,lens_id,category,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                           "params": [finding_id, project_id, run_id, document["id"], task_by_doc[original["document_id"]], finding["lens_id"], finding["category"], json.dumps(finding, ensure_ascii=False), utcnow()]})
    for offset in range(0, len(statements), 50):
        store.batch(statements[offset:offset+50])
    config["saved_snapshot"]["import_complete"] = True
    store.execute("UPDATE runs SET config_json=?,status=? WHERE id=?", [json.dumps(config), source_run["status"], run_id])
    return run_id
