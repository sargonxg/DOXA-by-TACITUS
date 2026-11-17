"""The optional large-restore hook is itself checked against compact real exports."""
import json

import pytest

from scripts.verify_report_jobs import verify_report_jobs
from test_recorded_export import recorded_workspace as recorded_workspace


def test_proof_verifies_all_ids_outcomes_actual_workbook_and_cleans_only_own_job(recorded_workspace, tmp_path):
    store, _ = recorded_workspace
    before = {name: store.query("SELECT * FROM " + name + " ORDER BY 1") for name in ("runs", "documents", "v3_assessments", "v3_unit_statuses", "budget_ledger")}
    target = tmp_path / "receipt.json"
    receipt = verify_report_jobs(store, "project", "run", target, isolated=True, expected_counts={"documents": 2, "assessments": 5}, min_free_gib=0)
    assert receipt["status"] == "pass" and receipt["counts"]["documents"] == 2 and receipt["counts"]["assessments"] == 5
    assert receipt["workbook"]["Findings"]["logical_rows"] == 5
    assert {item["result"] for item in receipt["typed_counts"]} == {"meets_criterion", "does_not_meet", "uncertain", "not_applicable"}
    assert receipt["temporary_report_inputs_and_artifacts_cleaned"]
    assert json.loads(target.read_text())["status"] == "pass"
    assert store.one("SELECT COUNT(*) n FROM report_jobs")["n"] == 0
    assert store.one("SELECT COUNT(*) n FROM v3_query_snapshots")["n"] == 0
    assert {name: store.query("SELECT * FROM " + name + " ORDER BY 1") for name in before} == before


def test_proof_requires_explicit_isolated_store_before_writes(recorded_workspace, tmp_path):
    store, _ = recorded_workspace
    with pytest.raises(ValueError, match="isolated"):
        verify_report_jobs(store, "project", "run", tmp_path / "receipt.json")
    assert store.one("SELECT COUNT(*) n FROM report_jobs")["n"] == 0
