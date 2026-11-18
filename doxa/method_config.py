"""Immutable analytical identity, distinct from mutable execution policy.

Unknown public settings are analytical by default: adding a new setting cannot
silently make two different methods compare equal. No credentials are retained.
Historical hashes are not migrated or rewritten by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from datetime import datetime, timezone
import uuid

METHOD_CONFIG_VERSION = "0.4"
OPERATIONAL_KEYS = frozenset({
    "quote_id", "commercial_pricing",
    "budget_usd", "monthly_budget_usd", "budget_changes", "budget_history",
    "concurrency", "workers", "worker_count", "task_timeout_seconds", "task_lease_seconds",
    "task_timeout_source", "task_execution_history", "timing_history", "execution_history",
    "operational_history", "sessions", "run_sessions", "session_id", "started_at", "finished_at",
    "task_timing_history", "source_manifest_key", "provider_preflight",
    "max_unit_retries", "accepted_assessments", "task_id", "run_id", "project_id",
    "profile_hash", "analysis_profile_hash", "analysis_config_hash", "method_fingerprint",
    "method_config", "execution_policy", "execution_policy_hash", "run_session",
    "request_journal_mode", "journal_mode", "request_journal_dir", "journal_dir",
    "private_journal_limit_bytes", "profile_wall_limit_seconds",
    "allow_remote_text", "allow_unknown_cost", "document_ids", "unit_ids",
    "verification_of", "verification_selection", "created_at", "updated_at",
    "execution_actor_id", "managed_access",
})
MEMORY_KEYS = frozenset({"learning_snapshot_id", "approved_examples", "approved_example_ids"})
SECRET_KEYS = frozenset({"api_key", "api_token", "authorization", "headers", "password", "secret", "token", "credentials"})


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _safe(value):
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()
                if not str(key).startswith("_") and str(key).lower() not in SECRET_KEYS}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    # JSON encoding below rejects nonserializable values and nonfinite numbers.
    return value


@dataclass(frozen=True)
class _FrozenRecord:
    """Serialized storage prevents mutation through nested dictionaries/lists."""
    _json: str

    def as_dict(self) -> dict:
        return json.loads(self._json)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self._json.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MethodConfig(_FrozenRecord):
    pass


@dataclass(frozen=True)
class ExecutionPolicy(_FrozenRecord):
    pass


@dataclass(frozen=True)
class RunSession(_FrozenRecord):
    pass


def source_metadata_hash(source_manifest) -> str:
    """Hash exact frozen prompt-visible metadata, including ordering and nulls."""
    return content_hash(_safe(source_manifest))


def method_config(config: dict, source_manifest=None, *, exclude_memory: bool = False) -> MethodConfig:
    if not isinstance(config, dict):
        raise ValueError("Method configuration must be an object")
    excluded = OPERATIONAL_KEYS | (MEMORY_KEYS if exclude_memory else frozenset())
    settings = {key: value for key, value in _safe(config).items() if key not in excluded}
    if source_manifest is not None:
        digest = source_metadata_hash(source_manifest)
        if settings.get("source_metadata_hash") not in {None, digest}:
            raise ValueError("Frozen source metadata hash does not match the supplied manifest")
        settings["source_metadata_hash"] = digest
    return MethodConfig(canonical_json({"schema_version": METHOD_CONFIG_VERSION, "settings": settings}))


def method_fingerprint(config: dict, *, exclude_memory: bool = False) -> str:
    return method_config(config, exclude_memory=exclude_memory).fingerprint


def structural_fingerprint(config: dict) -> str:
    """Comparison identity excluding ONLY memory and operational policy."""
    return method_fingerprint(config, exclude_memory=True)


def cache_method_fingerprint(config: dict, document: dict) -> str:
    """Identity of one document's actual analytical inputs, not its cohort.

    A changed peer source must not invalidate an unchanged document's validated
    outcomes. Global method identity stays strict and separately recorded.
    Unknown analytical settings remain included; only explicit scope/provenance
    keys are removed. Source units/evidence are validated independently.
    """
    scope = {"source_snapshot", "source_metadata_hash", "source_manifest_sha256", "manifest_sha256",
             "collection_sha256", "source_cohort_hash", "cohort_sha256", "fork_provenance", "parent_run_id"}
    settings = {key: value for key, value in config.items() if key not in scope and not key.startswith("reused_")}
    bibliography = {key: document.get(key) for key in ("id", "title", "author", "institution", "document_type", "published_at")}
    return content_hash({"schema_version": "document-cache-0.4", "method": method_config(settings).as_dict(),
                         "prompt_visible_bibliography": bibliography,
                         "source_hash": document.get("sha256") or document.get("content_sha256"),
                         "source_version_id": document.get("source_version_id") or document.get("id")})


def execution_policy(config: dict) -> ExecutionPolicy:
    safe = _safe(config)
    fields = {key: value for key, value in safe.items() if key in OPERATIONAL_KEYS
              and key not in {"accepted_assessments", "method_config", "execution_policy", "run_session",
                              "profile_hash", "analysis_profile_hash", "analysis_config_hash", "method_fingerprint"}}
    return ExecutionPolicy(canonical_json({"schema_version": METHOD_CONFIG_VERSION, "settings": fields}))


def new_run_session(config: dict, *, session_id: str | None = None, started_at: str | None = None) -> RunSession:
    return RunSession(canonical_json({"schema_version": METHOD_CONFIG_VERSION,
        "session_id": session_id or uuid.uuid4().hex,
        "started_at": started_at or datetime.now(timezone.utc).isoformat(),
        "method_fingerprint": method_fingerprint(config),
        "execution_policy": execution_policy(config).as_dict(),
        "execution_policy_hash": execution_policy(config).fingerprint}))


def frozen_source_metadata(store, run: dict, document_id: str) -> dict:
    """Resolve bibliography actually frozen for this run, never library edits."""
    config = run.get("config") or json.loads(run.get("config_json") or "{}")
    document = store.one("SELECT sha256 FROM documents WHERE id=? AND project_id=?", [document_id, run["project_id"]])
    if not document:
        raise ValueError("Frozen source document is unavailable in this project")
    if config.get("method_config_version") == METHOD_CONFIG_VERSION:
        row = store.one("SELECT payload_json,metadata_sha256 FROM run_source_metadata WHERE run_id=? AND document_id=?", [run["id"], document_id])
        if not row:
            raise ValueError("Frozen source metadata is missing; create a new run")
        value = json.loads(row["payload_json"])
        if source_metadata_hash(value) != row["metadata_sha256"]:
            raise ValueError("Frozen source metadata integrity check failed")
    else:
        key = config.get("source_manifest_key")
        if not key:
            raise ValueError("Historical frozen source metadata was not recorded; create a new run")
        try:
            manifest = store.get_json(key)
            value = next(item for item in manifest if item.get("id") == document_id)
        except (OSError, KeyError, TypeError, ValueError, StopIteration) as error:
            raise ValueError("Historical frozen source metadata is unavailable; create a new run") from error
        required = {"id", "sha256", "title", "author", "institution", "document_type", "published_at"}
        if not required.issubset(value):
            raise ValueError("Historical frozen source metadata is incomplete; create a new run")
        value = {key: value[key] for key in required}
    if value.get("id") != document_id or value.get("sha256") != document["sha256"]:
        raise ValueError("Frozen source metadata does not match the immutable source")
    return json.loads(canonical_json(value))
