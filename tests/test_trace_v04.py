import asyncio
import json

from doxa.contextual import analyze_units
from doxa.lineage import PrivateJournal
from doxa.providers import BaseProvider


class Provider(BaseProvider):
    name = "test"
    mode = "local"

    def __init__(self, config=None):
        super().__init__(config or {})
        self.calls = 0

    async def generate(self, messages, schema, **kwargs):
        self.calls += 1
        payload = json.loads(messages[-1]["content"])
        content = {"decisions": [{"unit_id": unit["unit_id"], "results": [0] * len(payload["criterion_order"])} for unit in payload["units"]]}
        return {"content": content, "finish_reason": "stop", "usage": self._usage(100, 20),
                "provider_observation": self._response_observation(json.dumps(content))}


DOC = {"id": "d", "sha256": "frozen", "author": "Source author", "blocks": [{"id": "b", "text": "PRIVATE_SOURCE_SENTINEL The committee opens the library."}]}
LENS = {"id": "custom", "name": "Library", "description": "Research fixture", "version": "test",
        "criteria": [{"id": "library", "label": "Library", "description": "A public library is discussed.", "kind": "descriptive"}]}


def test_default_trace_metadata_no_source_or_raw_io_and_visible_trace_not_cot():
    provider = Provider()
    events = []
    result = asyncio.run(analyze_units(DOC, [LENS], provider, progress=events.append))
    assert result["coverage"]["complete"]
    assert [entry["status"] for entry in result["attempt_journal"]] == ["started", "completed"]
    assert "PRIVATE_SOURCE_SENTINEL" not in json.dumps(result["attempt_journal"])
    last = result["attempt_journal"][-1]
    assert len(last["messages_sha256"]) == 64 and len(last["schema_sha256"]) == 64
    assert last["validation"]["accepted_pairs"] == 1 and last["elapsed_seconds"] >= 0
    assert result["request_capture"]["hidden_reasoning"] == "never_recorded"
    assert any(event["stage"] == "request_attempt" for event in events)


def test_visible_io_requires_explicit_private_sink_and_stays_out_of_public_journal(tmp_path):
    missing = asyncio.run(analyze_units(DOC, [LENS], Provider(), config={"request_journal_mode": "visible_io"}))
    assert missing["errors"][0]["code"] == "invalid_input"
    sink = PrivateJournal(tmp_path / "private")
    config = {"request_journal_mode": "visible_io", "_record_private_io": sink}
    result = asyncio.run(analyze_units(DOC, [LENS], Provider(config), config=config))
    captured = list((tmp_path / "private").glob("*.json"))
    assert len(captured) == 1
    assert "PRIVATE_SOURCE_SENTINEL" in captured[0].read_text()
    assert "PRIVATE_SOURCE_SENTINEL" not in json.dumps(result["attempt_journal"])


def test_only_optin_method_metadata_changes_cache_identity_and_journal_mode_does_not():
    first_config = {"method_config_version": "0.4", "source_metadata_hash": "original"}
    first = asyncio.run(analyze_units(DOC, [LENS], Provider(), config=first_config))
    cached = first["assessments"]
    provider = Provider()
    hit = asyncio.run(analyze_units(DOC, [LENS], provider, config={**first_config, "accepted_assessments": cached, "concurrency": 8}))
    assert not provider.calls and hit["model_profile"]["cache_hit_decisions"] == 1
    peer_changed = asyncio.run(analyze_units(DOC, [LENS], Provider(), config={**first_config, "source_metadata_hash": "changed", "source_snapshot": [{"id": "peer", "sha256": "changed"}], "fork_provenance": {"parent": "old"}, "reused_from_run_id": "old", "accepted_assessments": cached}))
    assert peer_changed["model_profile"]["cache_hit_decisions"] == 1
    changed = asyncio.run(analyze_units({**DOC, "author": "Changed author"}, [LENS], Provider(), config={**first_config, "source_metadata_hash": "changed", "accepted_assessments": cached}))
    assert changed["model_profile"]["cache_hit_decisions"] == 0
    legacy = asyncio.run(analyze_units(DOC, [LENS], Provider()))
    legacy_metadata = asyncio.run(analyze_units(DOC, [LENS], Provider(), config={"source_metadata_hash": "ignored-by-historical-method"}))
    assert legacy["model_profile"]["analysis_config_hash"] == legacy_metadata["model_profile"]["analysis_config_hash"]
