"""Frozen report-source contracts; persistence fixtures are not quality labels."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

from fastapi import HTTPException
import pytest

from doxa import report_source
from doxa.store import utcnow
from doxa.workspace import Workspace, WorkspaceQuery, unpack_assessment
from test_recorded_export import logical_source_state, recorded_workspace as recorded_workspace, xlsx_values


RESULTS = ["meets_criterion", "does_not_meet", "uncertain", "not_applicable"]
TABLES = {"documents": "documents", "assessments": "v3_assessments",
          "units": "v3_unit_statuses", "reviews": "v3_review_events"}


def config_for(store, *, source_only=False):
    query = WorkspaceQuery(run_id="none" if source_only else "run", duplicate_policy="sources",
                           results=[] if source_only else RESULTS,
                           document_ids=["source-a", "source-b"] if source_only else [])
    envelope = Workspace(store).create("project", query)
    return report_source.source_config(store, "project", envelope["query_id"], "briefing")


def add_review(store, identity="review-1", *, long_context=""):
    corrections = {"explanation": "Review-adjusted interpretation."}
    resolved = {**corrections, "context": long_context}
    store.execute("INSERT INTO v3_review_events(id,project_id,assessment_id,actor_kind,actor_id,adjudicated,corrections_json,resolved_json,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  [identity, "project", "assessment-0", "human", "synthetic-reviewer", 1,
                   json.dumps(corrections), json.dumps(resolved), "Synthetic review provenance", utcnow()])


def capture_all(store, config):
    result = {}
    with report_source.source_reader(store) as reader:
        for stage in report_source.STAGES:
            rows, cursor = [], ""
            while True:
                page, next_cursor = report_source.capture_page(reader, config, stage, cursor)
                if not page:
                    assert next_cursor == cursor
                    break
                assert next_cursor > cursor
                rows.extend(page)
                cursor = next_cursor
            result[stage] = rows
    return result


def store_parts(store, config):
    parts = []
    with report_source.source_reader(store) as reader:
        for stage in report_source.STAGES:
            cursor, ordinal = "", 0
            while True:
                rows, next_cursor = report_source.capture_page(reader, config, stage, cursor)
                if not rows:
                    break
                payload = report_source.encode_part(stage, rows)
                key = f"test-report-inputs/{config['query_id']}/{stage}-{ordinal}.json.gz"
                store.put_bytes(key, payload, "application/gzip")
                parts.append({"stage": stage, "ordinal": ordinal, "object_key": key, "bytes": len(payload),
                              "sha256": hashlib.sha256(payload).hexdigest(), "row_count": len(rows),
                              "last_key": next_cursor})
                cursor, ordinal = next_cursor, ordinal + 1
    return parts


def test_config_and_capture_preserve_declared_full_scope_raw_payload_and_reviews(recorded_workspace):
    store, root = recorded_workspace
    add_review(store)
    original = store.one("SELECT * FROM v3_assessments WHERE id='assessment-0'")
    payload = json.loads(original["payload_json"])
    payload["future_evidence_field"] = {"keep": "Raw model extension"}
    store.execute("UPDATE v3_assessments SET payload_json=? WHERE id='assessment-0'", [json.dumps(payload)])
    config = config_for(store)
    before = logical_source_state(root)
    assert config["expected_documents"] == 2 and config["expected_assessments"] == 5
    assert set(config["scope"]["results"]) == set(RESULTS)
    assert config["scope"]["duplicate_policy"] == "sources"
    assert config["metadata"]["query_id"] == config["query_id"]
    assert config["metadata"]["scope_hash"] == config["scope_hash"]
    assert config["metadata"]["snapshot_version"] == config["snapshot_version"]
    assert config["metadata"]["runs"][0]["status"] == "partial"
    assert config["metadata"]["export_provenance"]["inference_requests_made"] == 0
    captured = capture_all(store, config)
    assert {row["id"] for row in captured["documents"]} == {"source-a", "source-b"}
    assert len(captured["units"]) == 6 and len(captured["reviews"]) == 1
    assert {row["id"] for row in captured["assessments"]} == {f"assessment-{index}" for index in range(5)}
    assert {row["result"] for row in captured["assessments"]} == set(RESULTS)
    assert {row["criterion_kind"] for row in captured["assessments"]} == {"indicator", "boundary", "descriptive"}
    raw = next(row for row in captured["assessments"] if row["id"] == "assessment-0")
    assert json.loads(raw["payload_json"])["explanation"] == "Raw interpretation."
    assert json.loads(raw["payload_json"])["future_evidence_field"] == {"keep": "Raw model extension"}
    assert unpack_assessment(raw)["explanation"] == "Review-adjusted interpretation."
    assert raw["review_json"] == original["review_json"]
    assert captured["reviews"][0] == store.one("SELECT * FROM v3_review_events WHERE id='review-1'")
    assert logical_source_state(root) == before


def test_revision_guard_refuses_stale_config_and_capture(recorded_workspace):
    store, _ = recorded_workspace
    config = config_for(store)
    Workspace(store).touch("project")
    with pytest.raises(report_source.SourceChanged):
        report_source.revision_guard(store, config)
    with report_source.source_reader(store) as reader:
        with pytest.raises(report_source.SourceChanged):
            report_source.capture_page(reader, config, "documents")
    with pytest.raises(HTTPException) as stale:
        report_source.source_config(store, "project", config["query_id"], "briefing")
    assert stale.value.status_code == 409


def test_remote_capture_rejects_revision_change_between_membership_and_payload_reads(recorded_workspace):
    store, _ = recorded_workspace
    config = config_for(store)
    class ConcurrentRemote:
        remote = True
        fts = store.fts
        def query(self, sql, params=()):
            rows = store.query(sql, params)
            if sql.startswith("SELECT d.* FROM documents"):
                Workspace(store).touch("project")
            return rows
        def one(self, sql, params=()):
            rows = self.query(sql, params)
            return rows[0] if rows else None
    with report_source.source_reader(ConcurrentRemote()) as reader:
        with pytest.raises(report_source.SourceChanged, match="workspace changed"):
            report_source.capture_page(reader, config, "documents")


def test_assessment_keyset_plan_has_no_per_page_full_run_sort(recorded_workspace):
    store, _ = recorded_workspace
    config = config_for(store)
    with report_source.source_reader(store) as reader:
        _columns, table, clause, params, key, size = report_source._spec(reader, config, "assessments")
        plan = reader.query("EXPLAIN QUERY PLAN SELECT " + key + ",(" + size + ") FROM " + table + " WHERE " + clause + " AND " + key + ">? ORDER BY " + key + " LIMIT 100", [*params, ""])
    details = " ".join(row["detail"] for row in plan)
    assert "v3_assessments_report_cursor" in details
    assert "TEMP B-TREE" not in details


def test_assessment_capture_is_byte_bounded_and_pages_without_omissions(recorded_workspace):
    store, _ = recorded_workspace
    seed = store.one("SELECT * FROM v3_assessments WHERE id='assessment-0'")
    for index in range(55):
        row = dict(seed)
        row["id"], row["unit_id"] = f"page-{index:03d}", f"page-unit-{index:03d}"
        payload = json.loads(row["payload_json"])
        payload.update(id=row["id"], unit_id=row["unit_id"], context="Bounded context. " * 1800)
        row["payload_json"] = json.dumps(payload)
        columns = list(row)
        store.execute("INSERT INTO v3_assessments(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")", [row[key] for key in columns])
    config = config_for(store)
    seen, cursor, pages = set(), "", 0
    with report_source.source_reader(store) as reader:
        while True:
            rows, next_cursor = report_source.capture_page(reader, config, "assessments", cursor)
            if not rows:
                break
            assert 0 < len(rows) <= 100
            assert len(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()) <= report_source.PAGE_BYTES
            assert next_cursor > cursor
            identities = {row["id"] for row in rows}
            assert not seen.intersection(identities)
            seen.update(identities)
            cursor, pages = next_cursor, pages + 1
    assert len(seen) == config["expected_assessments"] == 60 and pages > 1


def test_structured_review_pages_account_for_actual_payload_bytes(recorded_workspace):
    store, _ = recorded_workspace
    for index in range(24):
        add_review(store, f"review-{index:03d}", long_context="Large review context. " * 2000)
    config = config_for(store)
    cursor, seen = "", set()
    with report_source.source_reader(store) as reader:
        while True:
            rows, next_cursor = report_source.capture_page(reader, config, "reviews", cursor)
            if not rows:
                break
            assert len(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()) <= report_source.PAGE_BYTES
            assert not seen.intersection(row["id"] for row in rows)
            seen.update(row["id"] for row in rows)
            cursor = next_cursor
    assert len(seen) == 24


def test_capture_rejects_record_exceeding_individual_bound(recorded_workspace, monkeypatch):
    store, _ = recorded_workspace
    config = config_for(store)
    monkeypatch.setattr(report_source, "MAX_RECORD_BYTES", 100)
    with report_source.source_reader(store) as reader:
        with pytest.raises(ValueError, match="record exceeds"):
            report_source.capture_page(reader, config, "assessments")


def test_materialize_retains_raw_rows_overlays_and_review_provenance(recorded_workspace, tmp_path):
    store, root = recorded_workspace
    add_review(store)
    config = config_for(store)
    expected = capture_all(store, config)
    before = logical_source_state(root)
    parts = store_parts(store, config)
    checks = []
    database, metadata_path = report_source.materialize(store, config, list(reversed(parts)), tmp_path / "frozen", lambda: checks.append(True))
    assert checks and logical_source_state(root) == before
    assert json.loads(metadata_path.read_text()) == config["metadata"]
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        for stage, table in TABLES.items():
            actual = [dict(row) for row in connection.execute("SELECT * FROM " + table)]
            assert sorted(actual, key=lambda row: json.dumps(row, sort_keys=True)) == sorted(expected[stage], key=lambda row: json.dumps(row, sort_keys=True))
    store.execute("UPDATE v3_assessments SET review_note='Changed after capture' WHERE id='assessment-0'")
    Workspace(store).touch("project")
    with report_source.frozen_view(database, json.loads(metadata_path.read_text())) as frozen:
        records = list(frozen["records"])
        assert {row["result"] for row in records} == set(RESULTS)
        record = next(row for row in records if row["id"] == "assessment-0")
        assert record["review_note"] == "" and record["explanation"] == "Review-adjusted interpretation."


def test_materialize_refuses_corrupted_frozen_part(recorded_workspace, tmp_path):
    store, _ = recorded_workspace
    config = config_for(store)
    parts = store_parts(store, config)
    store.put_bytes(parts[0]["object_key"], b"Corrupted input")
    with pytest.raises(ValueError, match="checksum"):
        report_source.materialize(store, config, parts, tmp_path / "corrupt")


@pytest.mark.parametrize("source_only", [False, True])
def test_offline_xlsx_and_snapshot_render_from_frozen_inputs_only(recorded_workspace, tmp_path, monkeypatch, source_only):
    store, root = recorded_workspace
    if not source_only:
        add_review(store)
    config = config_for(store, source_only=source_only)
    parts = store_parts(store, config)
    database, metadata = report_source.materialize(store, config, parts, tmp_path / "inputs")
    frozen_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    store.close()
    (root / "doxa.sqlite3").rename(root / "source-unavailable.sqlite3")
    def forbidden_network(*_args, **_kwargs):
        pytest.fail("Frozen rendering must not perform inference or network access")
    monkeypatch.setattr("socket.create_connection", forbidden_network)
    output = tmp_path / "output"
    artifacts = report_source.render(database, metadata, output, "xlsx")
    assert {item["format"] for item in artifacts} == {"snapshot", "xlsx", "manifest"}
    for artifact in artifacts:
        path = Path(artifact["path"])
        assert path.stat().st_size == artifact["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == frozen_hash
    manifest = json.loads((output / "manifest.json").read_text())
    with gzip.open(output / "snapshot.json.gz", "rt", encoding="utf-8") as stream:
        snapshot = json.load(stream)
    assert manifest["query_id"] == snapshot["query_id"] == config["query_id"]
    assert manifest["scope_hash"] == snapshot["scope_hash"] == config["scope_hash"]
    assert manifest["inference_requests_made"] == 0
    assert {row["id"] for row in snapshot["documents"]} == {"source-a", "source-b"}
    assert manifest["counts"]["documents"] == 2
    values = xlsx_values(output / "report.xlsx")
    assert "source-a" in values and "source-b" in values
    if source_only:
        assert snapshot["runs"] == [] and snapshot["findings"] == [] and snapshot["reviews"] == []
        assert manifest["counts"]["assessments"] == 0 and manifest["typed_counts"] == []
        assert not any(f"assessment-{index}" in values for index in range(5))
    else:
        assert {row["result"] for row in snapshot["findings"]} == set(RESULTS)
        assert len(snapshot["reviews"]) == 1 and manifest["counts"]["assessments"] == 5
        expected = Counter((row["criterion_kind"], row["result"]) for row in snapshot["findings"])
        assert {(row["criterion_kind"], row["result"]): row["count"] for row in manifest["typed_counts"]} == expected
        record = next(row for row in snapshot["findings"] if row["id"] == "assessment-0")
        assert record["explanation"] == "Review-adjusted interpretation."
