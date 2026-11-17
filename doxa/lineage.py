"""Explicit origins for observations, templates, model output and annotations.

Lineage is an integrity record, not a claim of causal explanation or accuracy.
The API intentionally has no hidden-reasoning or credential fields.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

KINDS = frozenset({"actual_request_metadata", "actual_request_visible_io", "raw_response",
    "persisted_assessment", "editorial_annotation", "reconstructed_template", "source_artifact", "report_export"})
FORBIDDEN = frozenset({"authorization", "api_key", "api_token", "password", "secret", "credentials",
                       "reasoning_content", "chain_of_thought", "hidden_chain_of_thought", "thinking"})


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_sha256(value) -> str:
    raw = value if isinstance(value, bytes) else _json(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate(value):
    if isinstance(value, dict):
        if any(str(key).lower() in FORBIDDEN for key in value):
            raise ValueError("Credentials and hidden reasoning cannot be stored as lineage")
        for item in value.values():
            _validate(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate(item)


def lineage_record(kind: str, *, payload_sha256: str | None = None, parents=(), origin_model=None,
                   origin_template=None, source=None, actor=None, recorded_at=None, metadata=None) -> dict:
    if kind not in KINDS:
        raise ValueError("Unknown lineage origin kind")
    if payload_sha256 is not None and (len(payload_sha256) != 64 or any(c not in "0123456789abcdef" for c in payload_sha256)):
        raise ValueError("Payload SHA256 must be lowercase hexadecimal")
    value = {"schema_version": "0.4", "kind": kind, "content_sha256": payload_sha256,
             "availability": "reconstructed_not_actual_request" if kind == "reconstructed_template" else "observed_request" if kind.startswith("actual_request") else "recorded_artifact",
             "parents": list(parents), "origin_model": origin_model, "origin_template": origin_template,
             "source": source, "actor": actor,
             "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(), "metadata": metadata or {}}
    _validate(value)
    # Return a detached JSON object so callers cannot mutate supplied parents.
    value = json.loads(_json(value))
    return {**value, "lineage_sha256": content_sha256(value)}


def lineage_status(record=None) -> dict:
    if record is None:
        return {"status": "not_recorded", "actual_request_available": False,
                "notice": "No contemporaneous request journal exists; a reconstructed template is not the actual request."}
    return {"status": record.get("availability", "unknown"),
            "actual_request_available": record.get("kind") == "actual_request_visible_io",
            "kind": record.get("kind"), "lineage_sha256": record.get("lineage_sha256")}


class PrivateJournal:
    """Explicit local-only, private visible I/O sink. Never used automatically.

    The caller chooses retention/deletion. Raw HTTP bodies, headers, credentials
    and separate provider reasoning fields are never accepted by this sink.
    """
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)

    def __call__(self, value: dict) -> dict:
        allowed = {"request_id", "messages", "schema", "provider_request", "visible_response", "metadata"}
        if set(value) - allowed:
            raise ValueError("Private journal accepts only visible request/response fields")
        _validate(value)
        raw = _json(value).encode()
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("One private journal entry exceeds 4 MiB")
        identity = uuid.uuid4().hex
        path = self.directory / f"{identity}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
        return {"id": identity, "sha256": hashlib.sha256(raw).hexdigest(), "visibility": "private_local"}
