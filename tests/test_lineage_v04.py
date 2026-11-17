import json
import os

import pytest

from doxa.lineage import PrivateJournal, content_sha256, lineage_record, lineage_status


def test_lineage_distinguishes_unknown_reconstruction_and_actual_io():
    assert lineage_status()["status"] == "not_recorded"
    reconstructed = lineage_record("reconstructed_template", payload_sha256=content_sha256({"prompt": "current"}))
    assert not lineage_status(reconstructed)["actual_request_available"]
    actual = lineage_record("actual_request_visible_io", parents=[{"kind": "source_artifact", "id": "d", "sha256": "a" * 64}])
    assert lineage_status(actual)["actual_request_available"]
    with pytest.raises(ValueError):
        lineage_record("raw_response", metadata={"reasoning_content": "private"})
    with pytest.raises(ValueError):
        lineage_record("raw_response", metadata={"api_key": "secret"})


def test_explicit_private_sink_has_private_permissions_and_no_hidden_reasoning(tmp_path):
    sink = PrivateJournal(tmp_path / "private")
    receipt = sink({"request_id": "r", "messages": [{"role": "user", "content": "Source"}], "schema": {},
                    "visible_response": "{}", "metadata": {"status": "completed"}})
    path = tmp_path / "private" / (receipt["id"] + ".json")
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    assert json.loads(path.read_text())["visible_response"] == "{}"
    with pytest.raises(ValueError):
        sink({"request_id": "r", "raw_http_headers": {"Authorization": "secret"}})
