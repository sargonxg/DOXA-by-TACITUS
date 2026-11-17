"""Idempotent import of the licensed, frozen news cohort and saved v3 outputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .store import Store, utcnow


def import_news(store: Store, project_id: str, root: Path, *, use_saved_showcase: bool = True) -> dict:
    # The collector is a reproducible source adapter, never invoked by this import.
    manifest = root / "corpus" / "news" / "manifest.jsonl"
    if not manifest.exists():
        raise ValueError("The frozen 5,000-article collection is not available yet. Run scripts/fetch_news_corpus.py first.")
    hasher = hashlib.sha256()
    with manifest.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    batch_id = hashlib.sha256(f"{project_id}:news:{digest}".encode()).hexdigest()[:32]
    store.execute("INSERT INTO batches(id,project_id,name,created_at) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING", [batch_id, project_id, "Global Voices · 5,000 real news articles", utcnow()])
    def source_documents():
        base = manifest.parent.resolve()
        with manifest.open(encoding="utf-8") as stream:
            for line in stream:
                entry = json.loads(line)
                sidecar = (base / entry["metadata_path"]).resolve()
                if not sidecar.is_relative_to(base):
                    raise ValueError("News source metadata escapes the frozen corpus directory")
                source = json.loads(sidecar.read_text(encoding="utf-8"))
                if source.get("id") != entry.get("id") or source.get("content_sha256") != entry.get("content_sha256"):
                    raise ValueError("News source metadata differs from its frozen manifest identity/hash")
                yield source

    mapping = {}
    for source in source_documents():
        source_id = source["id"]
        blocks = source["blocks"]
        content_hash = hashlib.sha256(json.dumps(blocks, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if content_hash != source.get("content_sha256") or source_id in mapping:
            raise ValueError("News source identity or extracted-block checksum is invalid")
        identity = hashlib.sha256(f"{project_id}:{source_id}".encode()).hexdigest()[:32]
        existing = store.one("SELECT id,sha256 FROM documents WHERE id=? AND project_id=?", [identity, project_id])
        if existing and existing["sha256"] != source["content_sha256"]:
            # Source revisions are immutable. Keep the original document and
            # its indexes; only this cohort's membership selects the new version.
            identity = hashlib.sha256(f"{project_id}:{source_id}:{content_hash}".encode()).hexdigest()[:32]
            existing = store.one("SELECT id,sha256 FROM documents WHERE id=? AND project_id=?", [identity, project_id])
            if existing and existing["sha256"] != content_hash:
                raise ValueError("News source version identity has a conflicting content hash")
        mapping[source_id] = identity
        if not existing:
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
    run_id, saved_notice = None, "No finished contextual package is available; the source collection was imported without inference."
    if use_saved_showcase and benchmark.exists():
        from .showcase_v3 import ShowcaseNotFinished
        try:
            run_id = restore_news_showcase(store, project_id, mapping, benchmark)
        except ShowcaseNotFinished as error:
            saved_notice = str(error) + ". Sources were imported without loading active/paused results."
    elif not use_saved_showcase:
        saved_notice = "Saved showcase restoration is disabled; sources were imported without inference."
    return {"documents": len(mapping), "batch": {"id": batch_id, "name": "Global Voices · 5,000 real news articles"},
            "run": {"id": run_id} if run_id else None, "saved_results": bool(run_id), "collection": "news",
            "notice": ("Licensed source snapshots with previously computed contextual results. No inference was requested; inspect coverage and review state." if run_id else saved_notice)}


def restore_news_showcase(store: Store, project_id: str, mapping: dict, path: Path) -> str:
    from .showcase_v3 import restore_showcase_v3
    documents = []
    identities = list(mapping.values())
    for start in range(0, len(identities), 80):
        part = identities[start:start + 80]
        documents.extend(store.query("SELECT id,sha256 FROM documents WHERE project_id=? AND id IN (" + ",".join("?" for _ in part) + ")", [project_id, *part]))
    return restore_showcase_v3(store, project_id, documents, path)
