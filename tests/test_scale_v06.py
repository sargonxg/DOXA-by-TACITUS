"""Small real replay of the larger offline infrastructure benchmark contract."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_scale_script_uses_disposable_storage_blocks_remote_settings_and_exports_every_source(tmp_path):
    receipt = tmp_path / "receipt.json"
    workbook = tmp_path / "sources.xlsx"
    environment = dict(os.environ)
    sentinel = "fake-test-credential-never-print"
    environment.update(DOXA_CLOUD_STATE_URL="https://must-not-contact.invalid", DOXA_ACCESS_MODE="managed",
                       DOXA_ACCESS_CONFIG_FILE="/must-not-read-private-config", OPENAI_API_KEY=sentinel)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/benchmark_scale_v06.py"),
        "--documents", "12", "--scheduler-documents", "3", "--output", str(receipt), "--workbook", str(workbook)],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-3000:] + result.stdout[-3000:]
    data = json.loads(receipt.read_text())
    assert data["passed"] and all(data["checks"].values())
    assert data["documents"] == 12 and data["scheduler_probe_documents"] == 3
    assert data["workbook"]["Documents"]["logical_rows"] == 12
    assert data["workbook"]["Findings"]["logical_rows"] == data["contextual_assessments"] == 0
    assert data["fresh_store_open_after_report_checkpoint"] and data["temporary_workspace_removed"]
    assert data["inference_requests"] == data["new_budget_reservations"] == data["fresh_model_loads"] == 0
    assert not data["torch_imported"] and not data["transformers_imported"]
    assert data["network_attempts_blocked"] == {}
    assert sentinel not in result.stdout + result.stderr + receipt.read_text()
    manifest = json.loads(workbook.with_suffix(".manifest.json").read_text())
    assert manifest["synthetic"] and manifest["documents"] == 12
    assert manifest["files"][0]["sha256"] == hashlib.sha256(workbook.read_bytes()).hexdigest()
