"""Small synthetic transport fixtures; no empirical model-quality claims."""
import asyncio
import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

from fastapi import FastAPI
import pytest

from doxa.contextual import UNIT_BUILDER_VERSION, _criteria, _make_assessment, build_units
from doxa.models import AssessmentDetail
from doxa.showcase_v3 import restore_showcase_v3
from doxa.store import Store, utcnow


_package_spec = importlib.util.spec_from_file_location("doxa_package_v3_fixture", Path(__file__).resolve().parents[1] / "scripts" / "package_v3.py")
_package_module = importlib.util.module_from_spec(_package_spec)
_package_spec.loader.exec_module(_package_module)
package_run = _package_module.package_run


TEXT = "🧭 The committee said: ‘Increase the library budget.’ The council has not adopted that proposal."
BLOCKS = [{"id": "shared-block", "text": TEXT, "page": None, "paragraph": 1}]
SHA = hashlib.sha256(json.dumps(BLOCKS, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def version_id(source_id):
    return hashlib.sha256(f"{source_id}:{SHA}".encode()).hexdigest()[:32]


def insert_document(store, project, identity, source_id):
    key = f"documents/{identity}/blocks.json"
    store.put_json(key, BLOCKS)
    store.execute(
        "INSERT INTO documents(id,project_id,batch_id,title,author,filename,mime_type,sha256,status,word_count,blocks_key,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [identity, project, "fixture-batch", "Transport fixture " + source_id, "Fixture institution",
         source_id + ".txt", "text/plain", SHA, "ready", len(TEXT.split()), key, utcnow(), json.dumps({"source_id": source_id})])
    return {**store.one("SELECT * FROM documents WHERE id=?", [identity]), "metadata": {"source_id": source_id}}


def make_archive(directory, *, status="completed"):
    old = Store(directory / "source-workspace")
    old.init()
    old.execute("INSERT INTO projects(id,name,created_at) VALUES('original','Synthetic source fixture',?)", [utcnow()])
    documents = [insert_document(old, "original", "old-" + identity, "origin-" + identity) for identity in ("a", "b")]
    packs, criteria = _criteria(["discourse"], {})
    config = {"methodology_version": "3.0", "schema_version": "3.0", "provider": "fixture",
              "model": "scripted-persistence-only", "lens_ids": ["discourse"], "lens_snapshots": packs,
              "unit_max_chars": 1200, "context_sentences": 2, "unit_builder_version": UNIT_BUILDER_VERSION,
              "source_snapshot": [{"id": doc["id"], "sha256": SHA} for doc in documents]}
    old.execute(
        "INSERT INTO runs(id,project_id,status,config_json,created_at,started_at,completed_at,usage_json,progress_json,errors_json,model_profile_json,spent_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ["original-run", "original", status, json.dumps(config), utcnow(), utcnow(), utcnow(),
         json.dumps({"input_tokens": 200, "output_tokens": 100, "requests": 2, "cost_usd": 0.42, "estimated": False}),
         json.dumps({"documents_total": 2, "complete": status == "completed"}),
         json.dumps([] if status == "completed" else [{"code": "fixture_incomplete", "message": "Synthetic missing outcome"}]),
         json.dumps({"provider": "fixture", "model": "scripted-persistence-only"}), 0.42])
    for doc_index, document in enumerate(documents):
        doc = {**document, "blocks": copy.deepcopy(BLOCKS), "source_version_id": version_id(document["metadata"]["source_id"])}
        unit = build_units(doc)[0]
        accepted = []
        for index, (key, criterion) in enumerate(criteria.items()):
            if status == "partial" and doc_index == 1 and index == 3:
                continue
            result = ["meets_criterion", "uncertain", "not_applicable", "does_not_meet"][index]
            detail = None
            if result in {"meets_criterion", "uncertain"}:
                detail = AssessmentDetail(
                    unit_id=unit["id"], criterion_key=key, evidence_sentence_ids=[unit["sentences"][0]["id"]],
                    title="Scripted persistence fixture", explanation="This fixture checks saved-result transport and source provenance.",
                    alternative="No model or human annotation judgment is represented.", target="The library budget",
                    proposition="Increase the library budget.", speaker="The committee", attribution="reported_speaker",
                    speaker_stance="supports", document_stance="unclear", hypothetical=False,
                    certainty="supported" if result == "meets_criterion" else "insufficient_context")
            assessment = _make_assessment(doc, unit, criterion, result, detail)
            accepted.append(key)
            old.execute(
                "INSERT INTO v3_assessments(id,project_id,run_id,document_id,unit_id,lens_id,criterion_id,criterion_kind,result,attribution,speech_role,speaker_stance,document_stance,hypothetical,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [assessment["id"], "original", "original-run", doc["id"], unit["id"], assessment["lens_id"], assessment["criterion_id"],
                 assessment["criterion_kind"], result, assessment["attribution"], assessment["speech_role"], assessment["speaker_stance"],
                 assessment["document_stance"], 0, json.dumps(assessment), utcnow()])
        state = "partial" if status == "partial" and doc_index == 1 else "accepted"
        payload = {"unit_id": unit["id"], "status": state, "accepted_criteria": accepted,
                   "failed_criteria": [] if state == "accepted" else ["discourse/rhetoric"], "pending_criteria": [],
                   "errors": [] if state == "accepted" else [{"code": "fixture_incomplete"}]}
        old.execute(
            "INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,characters,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
            ["original-run", doc["id"], unit["id"], state, unit["source_characters"], json.dumps(payload), utcnow()])
    package = directory / "archive"
    try:
        manifest = package_run(old, "original-run", package, part_documents=1)
    finally:
        old.close()
    return package, manifest


@pytest.fixture
def restoration(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    package, manifest = make_archive(tmp_path)
    store = Store(tmp_path / "destination")
    store.init()
    store.execute("INSERT INTO projects(id,name,created_at) VALUES('destination','Synthetic destination',?)", [utcnow()])
    documents = [insert_document(store, "destination", "new-" + identity, "origin-" + identity) for identity in ("a", "b")]
    yield store, documents, package, manifest
    store.close()


def counts(store):
    return {table: store.one(f"SELECT COUNT(*) n FROM {table}")["n"]
            for table in ("runs", "tasks", "v3_assessments", "v3_unit_statuses", "budget_ledger", "budget_reservations")}


def write_manifest(package, manifest):
    (package / "showcase.json").write_text(json.dumps(manifest), encoding="utf-8")


def rewrite_record(package, manifest, transform, *, part_index=0):
    part = manifest["parts"][part_index]
    path = package / part["file"]
    records = [json.loads(line) for line in gzip.decompress(path.read_bytes()).decode().splitlines()]
    transform(records[0])
    raw = ("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)).encode()
    path.write_bytes(gzip.compress(raw, mtime=0))
    part.update({"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size,
                 "uncompressed_bytes": len(raw)})
    write_manifest(package, manifest)


def test_restore_remaps_projects_and_preserves_distinct_same_hash_sources_without_inference(restoration, monkeypatch):
    store, documents, package, manifest = restoration
    store.execute("INSERT INTO budget_ledger(month,reserved_usd,spent_usd) VALUES(?,0.2,0.3)", [utcnow()[:7]])
    ledger_before = store.query("SELECT * FROM budget_ledger")
    monkeypatch.setattr("doxa.providers.get_provider", lambda *_args, **_kwargs: pytest.fail("Restoration must not request inference"))
    run_id = restore_showcase_v3(store, "destination", documents, package)
    assert isinstance(run_id, str) and run_id
    run = store.one("SELECT * FROM runs WHERE id=?", [run_id])
    config = json.loads(run["config_json"])
    assert run["project_id"] == "destination" and run["status"] == "completed"
    assert config["saved_snapshot"]["historical_usage"] is True
    assert config["saved_snapshot"]["import_complete"] is True
    assert config["saved_snapshot"]["original_run_id"] == "original-run"
    assert {row["id"] for row in config["source_snapshot"]} == {"new-a", "new-b"}
    assert run["spent_usd"] == run["reserved_usd"] == 0
    assert json.loads(run["usage_json"])["cost_usd"] == 0.42
    assert store.query("SELECT * FROM budget_ledger") == ledger_before
    assert store.one("SELECT COUNT(*) n FROM budget_reservations")["n"] == 0
    rows = store.query("SELECT * FROM v3_assessments")
    assert len(rows) == manifest["counts"]["assessments"] == 8
    assert {row["document_id"] for row in rows} == {"new-a", "new-b"}
    assert {row["project_id"] for row in rows} == {"destination"}
    versions = set()
    for row in rows:
        item = json.loads(row["payload_json"])
        assert item["run_id"] == run_id and item["project_id"] == "destination"
        assert item["document_id"] == row["document_id"]
        versions.add(item["source_version_id"])
        assert item["source_hash"] == SHA and item["result"] == row["result"]
        for ref in item["evidence_refs"]:
            assert TEXT[ref["start"]:ref["end"]] == ref["quote"]
            assert ref["document_id"] == row["document_id"]
    assert versions == {version_id("origin-a"), version_id("origin-b")}
    assert store.one("SELECT COUNT(*) n FROM v3_run_sources WHERE run_id=?", [run_id])["n"] == 2
    assert {row["status"] for row in store.query("SELECT status FROM v3_unit_statuses")} == {"accepted"}


def test_idempotent_manifest_path_and_directory_import_keep_local_review(restoration):
    store, documents, package, _ = restoration
    first = restore_showcase_v3(store, "destination", documents, package / "showcase.json")
    row = store.one("SELECT id FROM v3_assessments ORDER BY id LIMIT 1")
    store.execute("UPDATE v3_assessments SET review_status='revised',review_note='Local review survives' WHERE id=?", [row["id"]])
    before = counts(store)
    raw_documents = store.query("SELECT * FROM documents ORDER BY id")
    assert restore_showcase_v3(store, "destination", raw_documents, package) == first
    assert counts(store) == before
    assert store.one("SELECT review_status,review_note FROM v3_assessments WHERE id=?", [row["id"]]) == {
        "review_status": "revised", "review_note": "Local review survives"}


@pytest.mark.parametrize("status", ["queued", "running", "paused", "importing", "cancelled", "failed"])
def test_only_finished_completed_or_partial_packages_can_restore(restoration, status):
    store, documents, package, manifest = restoration
    manifest["run"]["status"] = status
    write_manifest(package, manifest)
    before = counts(store)
    with pytest.raises(ValueError):
        restore_showcase_v3(store, "destination", documents, package)
    assert counts(store) == before


def test_finished_partial_run_remains_partial_and_preserves_missing_outcomes(restoration, tmp_path):
    store, documents, _, _ = restoration
    package, manifest = make_archive(tmp_path / "partial-fixture", status="partial")
    run_id = restore_showcase_v3(store, "destination", documents, package)
    run = store.one("SELECT * FROM runs WHERE id=?", [run_id])
    assert run["status"] == "partial"
    assert len(store.query("SELECT id FROM v3_assessments")) == manifest["counts"]["assessments"] == 7
    assert {row["status"] for row in store.query("SELECT status FROM v3_unit_statuses")} == {"accepted", "partial"}
    assert json.loads(run["errors_json"])[0]["code"] == "fixture_incomplete"


def test_part_checksum_corruption_is_rejected_before_persistence(restoration):
    store, documents, package, manifest = restoration
    path = package / manifest["parts"][0]["file"]
    path.write_bytes(path.read_bytes() + b"tampered")
    before = counts(store)
    with pytest.raises(ValueError):
        restore_showcase_v3(store, "destination", documents, package)
    assert counts(store) == before


def test_part_paths_cannot_escape_the_declared_package_directory(restoration):
    store, documents, package, manifest = restoration
    original = package / manifest["parts"][0]["file"]
    external = package.parent / "outside-package.jsonl.gz"
    external.write_bytes(original.read_bytes())
    manifest["parts"][0]["file"] = "../" + external.name
    write_manifest(package, manifest)
    before = counts(store)
    with pytest.raises(ValueError):
        restore_showcase_v3(store, "destination", documents, package)
    assert counts(store) == before


def test_completed_unit_requires_every_declared_criterion_outcome(restoration):
    store, documents, package, manifest = restoration
    removed = []
    rewrite_record(package, manifest, lambda record: removed.append(record["assessments"].pop()))
    # Keep global totals honest: the error is a missing required decision, not
    # merely a stale manifest count or a broken gzip checksum.
    manifest["counts"]["assessments"] -= 1
    manifest["counts"]["audited_references"] -= len(removed[0]["evidence_refs"])
    manifest["outcomes"][removed[0]["result"]] -= 1
    manifest["citation_audit"]["references"] = manifest["counts"]["audited_references"]
    write_manifest(package, manifest)
    before = counts(store)
    with pytest.raises(ValueError):
        restore_showcase_v3(store, "destination", documents, package)
    assert counts(store) == before


@pytest.mark.parametrize("field", ["source_hash", "source_identity", "quote", "offset", "unit", "sentence", "result", "kind", "criterion", "duplicate_source"])
def test_rechecks_semantics_even_if_attacker_recomputes_part_checksum(restoration, field):
    store, documents, package, manifest = restoration
    def tamper(record):
        first = record["assessments"][0]
        if field == "source_hash":
            record["sha256"] = "0" * 64
        elif field == "source_identity":
            record["source_id"] = "not-in-imported-sources"
        elif field == "quote":
            first["evidence_refs"][0]["quote"] = "This was not in the source."
        elif field == "offset":
            first["evidence_refs"][0]["start"] += 1
        elif field == "unit":
            first["unit_id"] = "u_not_a_source_unit"
        elif field == "sentence":
            first["evidence_refs"][0]["sentence_id"] = "s_not_a_source_sentence"
        elif field == "result":
            first["result"] = "probably_biased"
        elif field == "kind":
            first["criterion_kind"] = "indicator"
        elif field == "criterion":
            first["criterion_id"] = "nonexistent"
            first["criterion_key"] = "discourse/nonexistent"
        else:
            record["source_id"] = "origin-b"
    rewrite_record(package, manifest, tamper)
    before = counts(store)
    with pytest.raises(ValueError):
        restore_showcase_v3(store, "destination", documents, package)
    assert counts(store) == before


def test_changed_destination_text_cannot_hide_behind_unchanged_declared_hash(restoration):
    store, documents, package, _ = restoration
    changed = copy.deepcopy(BLOCKS)
    changed[0]["text"] = TEXT.replace("library", "theatre")
    store.put_json(documents[0]["blocks_key"], changed)
    before = counts(store)
    with pytest.raises(ValueError):
        restore_showcase_v3(store, "destination", documents, package)
    assert counts(store) == before


def test_duplicate_imported_source_identity_is_rejected(restoration):
    store, documents, package, _ = restoration
    documents[1]["metadata"]["source_id"] = documents[0]["metadata"]["source_id"]
    # Stored metadata is authoritative; changing only a caller's unpacked copy
    # must not override a persisted source identity.
    store.execute("UPDATE documents SET metadata_json=? WHERE id=?",
                  [json.dumps(documents[1]["metadata"]), documents[1]["id"]])
    before = counts(store)
    with pytest.raises(ValueError):
        restore_showcase_v3(store, "destination", documents, package)
    assert counts(store) == before


def test_committed_batch_with_lost_acknowledgement_resumes_without_duplicate_or_review_loss(restoration, monkeypatch):
    store, documents, package, _ = restoration
    original_batch = store.batch
    failed = False
    def lost_acknowledgement(statements):
        nonlocal failed
        result = original_batch(statements)
        if not failed and any("INSERT INTO v3_assessments" in statement["sql"] for statement in statements):
            failed = True
            raise RuntimeError("Simulated lost acknowledgement after committed assessment batch")
        return result
    with monkeypatch.context() as context:
        context.setattr(store, "batch", lost_acknowledgement)
        with pytest.raises(RuntimeError, match="Simulated lost acknowledgement"):
            restore_showcase_v3(store, "destination", documents, package)
    assert failed
    run = store.one("SELECT * FROM runs")
    assert run["status"] == "importing"
    assert json.loads(run["config_json"])["saved_snapshot"]["import_complete"] is False
    row = store.one("SELECT id FROM v3_assessments LIMIT 1")
    assert row
    store.execute("UPDATE v3_assessments SET review_status='confirmed',review_note='Preserve adjudication' WHERE id=?", [row["id"]])
    restored = restore_showcase_v3(store, "destination", documents, package)
    assert restored == run["id"]
    assert store.one("SELECT COUNT(*) n FROM runs")["n"] == 1
    assert store.one("SELECT COUNT(*) n FROM v3_assessments")["n"] == 8
    assert store.one("SELECT COUNT(*) n FROM v3_unit_statuses")["n"] == 2
    assert store.one("SELECT status FROM runs WHERE id=?", [restored])["status"] == "completed"
    assert store.one("SELECT review_status,review_note FROM v3_assessments WHERE id=?", [row["id"]]) == {
        "review_status": "confirmed", "review_note": "Preserve adjudication"}


def demo_endpoint(store, root, load_demo):
    from doxa.v3_api import register
    app = FastAPI()
    register(app, {"root": root, "db": lambda: store,
                   "require_project": lambda identity: store.one("SELECT * FROM projects WHERE id=?", [identity]),
                   "load_demo": load_demo, "use_saved_showcase": True})
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/v3/projects/{project_id}/demo")


def test_demo_endpoint_prefers_finished_contextual_institutions_without_legacy_inference(restoration, monkeypatch, tmp_path):
    store, documents, package, _ = restoration
    root = tmp_path / "app-root"
    shutil.copytree(package, root / "benchmarks" / "v3-institutions")
    monkeypatch.setattr("doxa.showcase_v3.import_institution_sources", lambda *_: (documents, {"id": "fixture-batch"}))
    monkeypatch.setattr("doxa.providers.get_provider", lambda *_args, **_kwargs: pytest.fail("Saved demo must not request inference"))
    async def forbidden_legacy(_project):
        pytest.fail("A finished contextual package must be preferred over legacy fallback")
    result = asyncio.run(demo_endpoint(store, root, forbidden_legacy)("destination", {"collection": "institutions"}))
    assert result["saved_results"] is True and result["documents"] == 2
    assert result["collection"] == "institutions"
    assert "No inference was requested" in result["notice"]
    assert store.one("SELECT status FROM runs WHERE id=?", [result["run"]["id"]])["status"] == "completed"


def test_demo_endpoint_paused_package_uses_explicit_historical_fallback_notice(restoration, monkeypatch, tmp_path):
    store, _, package, manifest = restoration
    root = tmp_path / "app-root"
    manifest["run"]["status"] = "paused"
    write_manifest(package, manifest)
    shutil.copytree(package, root / "benchmarks" / "v3-institutions")
    monkeypatch.setattr("doxa.showcase_v3.import_institution_sources", lambda *_: pytest.fail("Paused package must be skipped before importing contextual sources"))
    calls = []
    async def legacy(project):
        calls.append(project)
        return {"documents": 2, "saved_results": False, "notice": "Legacy fixture only."}
    result = asyncio.run(demo_endpoint(store, root, legacy)("destination", {"collection": "institutions"}))
    assert calls == ["destination"] and result["saved_results"] is False
    assert "paused" in result["notice"] and "Historical institutional demonstration" in result["notice"]
    assert store.one("SELECT COUNT(*) n FROM runs")["n"] == 0


def test_news_saved_wrapper_restores_generic_package_with_current_document_mapping(restoration):
    from doxa.news import restore_news_showcase
    store, documents, package, _ = restoration
    mapping = {doc["metadata"]["source_id"]: doc["id"] for doc in documents}
    run_id = restore_news_showcase(store, "destination", mapping, package / "showcase.json")
    assert store.one("SELECT status FROM runs WHERE id=?", [run_id])["status"] == "completed"
    assert {row["document_id"] for row in store.query("SELECT document_id FROM v3_assessments")} == set(mapping.values())


@pytest.fixture
def empty_store(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    store = Store(tmp_path / "empty-workspace")
    store.init()
    store.execute("INSERT INTO projects(id,name,created_at) VALUES('destination','Synthetic source-import fixtures',?)", [utcnow()])
    yield store
    store.close()


def canonical_blocks_hash(blocks):
    return hashlib.sha256(json.dumps(blocks, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_twenty_institution_imports_reuse_existing_legacy_source_without_collapsing_independent_sources(empty_store, tmp_path):
    from doxa.showcase_v3 import import_institution_sources
    store = empty_store
    source_dir = tmp_path / "fixture-root" / "corpus" / "documents"
    source_dir.mkdir(parents=True)
    for index in range(20):
        identity = f"fixture-institution-{index:02d}"
        source = {"id": identity, "title": "Synthetic transport fixture " + str(index),
                  "blocks": BLOCKS, "content_sha256": SHA, "word_count": len(TEXT.split())}
        (source_dir / f"{identity}.json").write_text(json.dumps(source), encoding="utf-8")
    legacy = insert_document(store, "destination", "preexisting-legacy", "fixture-institution-00")
    first, batch = import_institution_sources(store, "destination", tmp_path / "fixture-root")
    second, second_batch = import_institution_sources(store, "destination", tmp_path / "fixture-root")
    assert len(first) == len({doc["id"] for doc in first}) == 20
    assert first == second and batch == second_batch
    assert legacy["id"] in {doc["id"] for doc in first}
    assert store.one("SELECT COUNT(*) n FROM documents")["n"] == 20
    assert store.one("SELECT COUNT(*) n FROM batch_members WHERE batch_id=?", [batch["id"]])["n"] == 20


def news_sources():
    result = []
    for index in range(2):
        blocks = [{"id": f"fixture-{index}-block", "text": f"Synthetic news import fixture {index}. Its claims are transport test data.", "paragraph": 1, "page": None}]
        result.append({"id": f"fixture-news-{index}", "title": f"Synthetic source {index}", "author": "Fixture author",
                       "institution": "Synthetic transport fixture", "published_at": "2024-01-15", "modified_at": "2024-01-15",
                       "blocks": blocks, "content_sha256": canonical_blocks_hash(blocks),
                       "source_url": f"https://example.invalid/news/{index}", "license": "TEST-FIXTURE-ONLY",
                       "word_count": sum(len(block["text"].split()) for block in blocks), "synthetic": True})
    return result


def write_news_sources(root, sources, *, claimed_manifest_hash=None):
    base = root / "corpus" / "news"
    metadata = base / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    entries = []
    for source in sources:
        (metadata / f"{source['id']}.json").write_text(json.dumps(source), encoding="utf-8")
        entries.append({"id": source["id"], "metadata_path": f"metadata/{source['id']}.json",
                        "content_sha256": claimed_manifest_hash or source["content_sha256"]})
    (base / "manifest.jsonl").write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")


def test_news_revisions_are_immutable_current_cohort_selects_new_version_and_repeat_is_idempotent(empty_store, tmp_path):
    from doxa.news import import_news
    store, root = empty_store, tmp_path / "news-root"
    sources = news_sources()
    write_news_sources(root, sources)
    original = import_news(store, "destination", root, use_saved_showcase=False)
    old_rows = {json.loads(row["metadata_json"])["source_id"]: row for row in store.query("SELECT * FROM documents")}
    old = old_rows["fixture-news-0"]
    old_blocks = store.get_json(old["blocks_key"])
    old_index = store.query("SELECT body FROM document_search WHERE id=?", [old["id"]]) if store.fts else []
    assert original["documents"] == 2 and original["saved_results"] is False
    sources[0]["blocks"][0]["text"] += " A later revision adds this sentence."
    sources[0]["content_sha256"] = canonical_blocks_hash(sources[0]["blocks"])
    sources[0]["word_count"] = sum(len(block["text"].split()) for block in sources[0]["blocks"])
    sources[0]["modified_at"] = "2024-02-01"
    write_news_sources(root, sources)
    revised = import_news(store, "destination", root, use_saved_showcase=False)
    assert revised["batch"]["id"] != original["batch"]["id"]
    assert store.one("SELECT COUNT(*) n FROM documents")["n"] == 3
    current_ids = {row["document_id"] for row in store.query("SELECT document_id FROM batch_members WHERE batch_id=?", [revised["batch"]["id"]])}
    assert len(current_ids) == 2 and old["id"] not in current_ids
    assert old_rows["fixture-news-1"]["id"] in current_ids
    assert store.get_json(old["blocks_key"]) == old_blocks
    assert store.one("SELECT * FROM documents WHERE id=?", [old["id"]]) == old
    if store.fts:
        assert store.query("SELECT body FROM document_search WHERE id=?", [old["id"]]) == old_index
    assert import_news(store, "destination", root, use_saved_showcase=False) == revised
    assert store.one("SELECT COUNT(*) n FROM documents")["n"] == 3
    assert store.one("SELECT COUNT(*) n FROM batch_members WHERE batch_id=?", [revised["batch"]["id"]])["n"] == 2


@pytest.mark.parametrize("mismatch", ["manifest_vs_sidecar", "claimed_vs_blocks"])
def test_news_import_rejects_manifest_or_body_checksum_mismatch(empty_store, tmp_path, mismatch):
    from doxa.news import import_news
    sources, root = news_sources(), tmp_path / "tampered-news"
    if mismatch == "manifest_vs_sidecar":
        write_news_sources(root, sources, claimed_manifest_hash="0" * 64)
    else:
        sources[0]["content_sha256"] = "0" * 64
        write_news_sources(root, sources)
    with pytest.raises(ValueError):
        import_news(empty_store, "destination", root, use_saved_showcase=False)
    assert empty_store.one("SELECT COUNT(*) n FROM documents")["n"] == 0


@pytest.mark.parametrize("state", ["failed", "pending"])
def test_partial_archives_preserve_conservative_unit_status_with_already_accepted_subset(restoration, tmp_path, state):
    store, documents, _, _ = restoration
    package, manifest = make_archive(tmp_path / "retained-subset", status="partial")
    def retain_subset(record):
        status = record["unit_statuses"][0]
        status["status"] = state
        if state == "pending":
            status["pending_criteria"] = status["failed_criteria"]
            status["failed_criteria"] = []
    manifest["unit_states"] = {"accepted": 1, state: 1}
    rewrite_record(package, manifest, retain_subset, part_index=1)
    run_id = restore_showcase_v3(store, "destination", documents, package)
    assert store.one("SELECT status FROM runs WHERE id=?", [run_id])["status"] == "partial"
    assert store.one("SELECT status FROM v3_unit_statuses WHERE document_id='new-b'")["status"] == state
    assert store.one("SELECT COUNT(*) n FROM v3_assessments WHERE document_id='new-b'")["n"] == 3
    assert store.one("SELECT COUNT(*) n FROM v3_unit_statuses WHERE status='accepted'")["n"] == 1
