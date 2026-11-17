"""Idempotent import of the licensed, frozen news cohort and saved v3 outputs."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from .store import Store, utcnow


def import_news(store: Store, project_id: str, root: Path) -> dict:
    # The collector is a reproducible source adapter, never invoked by this import.
    manifest = root / "corpus" / "news" / "manifest.jsonl"
    if not manifest.exists():
        raise ValueError("The frozen 5,000-article collection is not available yet. Run scripts/fetch_news_corpus.py first.")
    from scripts.fetch_news_corpus import iter_news_documents
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    batch_id = hashlib.sha256(f"{project_id}:news:{digest}".encode()).hexdigest()[:32]
    store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING", [batch_id, project_id, "Global Voices · 5,000 real news articles", utcnow()])
    mapping = {}
    for source in iter_news_documents(root / "corpus" / "news"):
        source_id = source["id"]
        identity = hashlib.sha256(f"{project_id}:{source_id}".encode()).hexdigest()[:32]
        mapping[source_id] = identity
        existing = store.one("SELECT id FROM documents WHERE id=? AND project_id=?", [identity, project_id])
        if not existing:
            blocks = source["blocks"]
            key = f"documents/{identity}/blocks.json"
            store.put_json(key, blocks)
            metadata = {key: value for key, value in source.items() if key != "blocks"}
            metadata["source_id"] = source_id
            values = {"id": identity, "project_id": project_id, "batch_id": batch_id, "title": source["title"],
                      "author": source.get("author") or "Unknown", "institution": source.get("institution") or "Global Voices",
                      "document_type": "news_article", "published_at": source.get("published_at"), "filename": source_id + ".txt",
                      "mime_type": "text/plain", "sha256": source["content_sha256"], "status": "ready", "word_count": source["word_count"],
                      "page_count": 0, "source_url": source["source_url"], "license": source["license"], "blocks_key": key,
                      "created_at": utcnow(), "metadata_json": json.dumps(metadata, ensure_ascii=False)}
            store.execute("INSERT INTO documents(" + ",".join(values) + ") VALUES(" + ",".join("?" for _ in values) + ")", list(values.values()))
            if store.fts:
                store.index_document(identity, project_id, source["title"], blocks)
            store.execute("INSERT INTO v3_sources(document_id,project_id,source_identity,content_hash,canonical_url,relative_path,version,created_at) VALUES(?,?,?,?,?,?,?,?)", [identity, project_id, source_id, source["content_sha256"], source["source_url"], source_id + ".txt", source.get("modified_at") or "", utcnow()])
        store.execute("INSERT INTO batch_members(batch_id,document_id) VALUES(?,?) ON CONFLICT(batch_id,document_id) DO NOTHING", [batch_id, identity])
    benchmark = root / "benchmarks" / "v3-news" / "showcase.json"
    run_id = restore_news_showcase(store, project_id, mapping, benchmark) if benchmark.exists() else None
    return {"documents": len(mapping), "batch": {"id": batch_id, "name": "Global Voices · 5,000 real news articles"},
            "run": {"id": run_id} if run_id else None, "saved_results": bool(run_id), "collection": "news",
            "notice": "Licensed source snapshots. Loading recorded results makes no inference calls; inspect model coverage and review state."}


def restore_news_showcase(store: Store, project_id: str, mapping: dict, path: Path) -> str:
    manifest = json.loads(path.read_text())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    run_id = hashlib.sha256(f"{project_id}:v3-news:{digest}".encode()).hexdigest()[:32]
    existing = store.one("SELECT id,config_json FROM runs WHERE id=?", [run_id])
    if existing and json.loads(existing["config_json"]).get("saved_snapshot", {}).get("import_complete"):
        return run_id
    run = manifest["run"]
    config = {**run["config"], "methodology_version": "3.0", "source_snapshot": []}
    config["saved_snapshot"] = {"sha256": digest, "original_run_id": run["id"], "historical_usage": True, "import_complete": False}
    store.execute("INSERT INTO runs(id,project_id,status,config_json,created_at,started_at,completed_at,progress_json,usage_json,model_profile_json) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                  [run_id, project_id, "importing", json.dumps(config), utcnow(), run.get("started_at"), run.get("completed_at"), json.dumps(run.get("progress", {})), json.dumps(run.get("usage", {})), json.dumps(run.get("model_profile", {}))])
    for part in manifest["parts"]:
        file = path.parent / part["file"]
        if not file.resolve().is_relative_to(path.parent.resolve()) or hashlib.sha256(file.read_bytes()).hexdigest() != part["sha256"]:
            raise ValueError("News benchmark part failed its checksum")
        with gzip.open(file, "rt", encoding="utf-8") as stream:
            for line in stream:
                record = json.loads(line)
                document_id = mapping.get(record["source_id"])
                if not document_id:
                    raise ValueError("News benchmark contains an unknown source")
                document = store.one("SELECT sha256,blocks_key FROM documents WHERE id=?", [document_id])
                if document["sha256"] != record["sha256"]:
                    raise ValueError("News source changed since the saved inference")
                blocks = {b["id"]: b for b in store.get_json(document["blocks_key"])}
                statements = []
                for assessment in record.get("assessments", []):
                    for ref in assessment.get("evidence_refs", []):
                        block = blocks.get(ref["block_id"])
                        if not block or not 0 <= ref["start"] < ref["end"] <= len(block["text"]) or ("quote" in ref and block["text"][ref["start"]:ref["end"]] != ref["quote"]):
                            raise ValueError("Saved assessment failed exact evidence validation")
                    item = {**assessment, "id": hashlib.sha256(f"{run_id}:{assessment['id']}".encode()).hexdigest()[:32], "run_id": run_id, "project_id": project_id, "document_id": document_id}
                    item["evidence_refs"] = [{**ref, "document_id": document_id} for ref in item.get("evidence_refs", [])]
                    columns = ["id", "project_id", "run_id", "document_id", "unit_id", "lens_id", "criterion_id", "criterion_kind", "result", "attribution", "speech_role", "speaker_stance", "document_stance", "hypothetical"]
                    values = [item.get(key, "unknown") for key in columns]
                    values[-1] = int(bool(item.get("hypothetical")))
                    statements.append({"sql": "INSERT INTO v3_assessments(" + ",".join(columns) + ",payload_json,created_at) VALUES(" + ",".join("?" for _ in range(len(columns) + 2)) + ") ON CONFLICT(id) DO NOTHING", "params": [*values, json.dumps(item, ensure_ascii=False), utcnow()]})
                for status in record.get("unit_statuses", []):
                    statements.append({"sql": "INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,document_id,unit_id) DO NOTHING", "params": [run_id, document_id, status["unit_id"], status["status"], status.get("characters", 0), json.dumps(status), utcnow()]})
                for offset in range(0, len(statements), 50):
                    store.batch(statements[offset:offset + 50])
                config["source_snapshot"].append({"id": document_id, "sha256": document["sha256"]})
    if len(config["source_snapshot"]) != len(mapping):
        raise ValueError("Saved news run does not cover the exact imported collection")
    config["saved_snapshot"]["import_complete"] = True
    store.execute("UPDATE runs SET config_json=?,status=? WHERE id=?", [json.dumps(config), run["status"], run_id])
    return run_id
