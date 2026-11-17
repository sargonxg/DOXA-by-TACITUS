import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from doxa.contextual import build_units
from doxa.store import Store, utcnow

spec = importlib.util.spec_from_file_location("repartition_local_tasks", Path(__file__).resolve().parents[1] / "scripts/repartition_local_tasks.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def prepared(tmp_path):
    store = Store(tmp_path)
    store.init()
    store.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Local',?)", [utcnow()])
    blocks = [{"id": f"b{i}", "text": (f"Sentence {i} records a public source observation. " * 4).strip()} for i in range(3)]
    store.put_json("documents/d/blocks.json", blocks)
    store.execute("INSERT INTO documents(id,project_id,batch_id,title,filename,mime_type,sha256,status,blocks_key,metadata_json,created_at) VALUES('d','p','batch','Source','d.txt','text/plain','sha','ready','documents/d/blocks.json',?,?)", [json.dumps({"source_id": "original"}), utcnow()])
    document = {"id": "d", "sha256": "sha", "blocks": blocks,
                "source_version_id": hashlib.sha256(b"original:sha").hexdigest()[:32]}
    units = build_units(document, max_unit_chars=200)
    config = {"provider": "ollama", "model": "qwen3.5:4b", "methodology_version": "3.0",
              "pipeline_version": "contextual-decisions-3.2.1", "task_index_basis": "source_units_v3",
              "unit_max_chars": 200, "source_snapshot": [{"id": "d", "sha256": "sha"}]}
    store.execute("INSERT INTO runs(id,project_id,status,config_json,created_at) VALUES('run','p','running',?,?)", [json.dumps(config), utcnow()])
    store.execute("INSERT INTO tasks(id,run_id,document_id,ordinal,start_block,end_block,status,error) VALUES('old','run','d',0,0,?,'failed','timeout')", [len(units)])
    for unit in units:
        store.execute("INSERT INTO v3_unit_statuses(run_id,document_id,unit_id,status,payload_json,created_at) VALUES('run','d',?,'pending','{}',?)", [unit["id"], utcnow()])
    raw = json.dumps({"result": "does_not_meet", "unit_id": units[0]["id"], "task_id": "old"})
    store.execute("INSERT INTO v3_assessments(id,project_id,run_id,document_id,task_id,unit_id,lens_id,criterion_id,criterion_kind,result,payload_json,review_json,review_status,created_at) VALUES('a','p','run','d','old',?,'ihra','c','indicator','does_not_meet',?,?,'revised',?)", [units[0]["id"], raw, json.dumps({"note": "preserved human review"}), utcnow()])
    for identity, status, usage in [('paid-none', 'settled', {'input_tokens': 40, 'output_tokens': 20, 'cost_usd': 0.0}), ('inflight', 'reserved', {})]:
        store.execute("INSERT INTO budget_reservations(id,run_id,task_id,month,amount,status,charged_usd,usage_json,created_at) VALUES(?,'run','old','2026-11',0,?,0,?,?)", [identity, status, json.dumps(usage), utcnow()])
    yield store, units, raw
    store.close()


def test_dry_run_then_atomic_repartition_preserves_sources_reviews_and_request_history(prepared):
    store, units, raw = prepared
    dry = module.repartition_local_tasks(store, "run")
    assert not dry["applied"] and dry["new_unit_tasks"] == len(units)
    assert store.one("SELECT id FROM tasks")["id"] == "old"
    result = module.repartition_local_tasks(store, "run", apply=True)
    tasks = store.query("SELECT * FROM tasks ORDER BY ordinal")
    assert len(tasks) == len(units) and all(t["end_block"] - t["start_block"] == 1 for t in tasks)
    assessment = store.one("SELECT * FROM v3_assessments WHERE id='a'")
    assert assessment["payload_json"] == raw and assessment["task_id"] == tasks[0]["id"]
    assert assessment["review_status"] == "revised" and "preserved human review" in assessment["review_json"]
    assert store.one("SELECT COUNT(*) n FROM budget_reservations")["n"] == 2
    recorded = store.one("SELECT * FROM budget_reservations WHERE id='paid-none'")
    assert json.loads(recorded["usage_json"])["input_tokens"] == 40
    interrupted = store.one("SELECT * FROM budget_reservations WHERE id='inflight'")
    assert interrupted["status"] == "settled" and json.loads(interrupted["usage_json"])["tokens_unknown"]
    archive = store.get_json(result["archive_key"])
    assert archive["applied"] and archive["old_tasks"][0]["id"] == "old"
    assert archive["request_ledger_before"][1]["task_id"] == "old"
    config = json.loads(store.one("SELECT config_json FROM runs")["config_json"])
    assert config["pipeline_version"] == "contextual-decisions-3.2.1"
    assert not module.repartition_local_tasks(store, "run", apply=True)["changed"]


def test_changed_source_or_canonical_unit_mismatch_aborts_before_task_replacement(prepared):
    store, units, raw = prepared
    store.execute("UPDATE documents SET sha256='changed' WHERE id='d'")
    with pytest.raises(ValueError, match="source changed"):
        module.repartition_local_tasks(store, "run", apply=True)
    assert store.one("SELECT id FROM tasks")["id"] == "old"
    store.execute("UPDATE documents SET sha256='sha' WHERE id='d'")
    store.execute("DELETE FROM v3_unit_statuses WHERE unit_id=?", [units[0]["id"]])
    with pytest.raises(ValueError, match="canonical source-unit"):
        module.repartition_local_tasks(store, "run", apply=True)


def test_nonzero_request_cost_refuses_local_maintenance(prepared):
    store, _, _ = prepared
    store.execute("UPDATE budget_reservations SET amount=0.1 WHERE id='inflight'")
    with pytest.raises(ValueError, match="nonzero"):
        module.repartition_local_tasks(store, "run", apply=True)
    assert store.one("SELECT id FROM tasks")["id"] == "old"
