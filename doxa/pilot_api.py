"""Native guided-pilot workflows; no invented validation, invoices or cloud tenancy."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from .store import utcnow

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pilot_briefs (
 project_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, revision INTEGER NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pilot_transforms (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_document_ids_json TEXT NOT NULL,
 public_json TEXT NOT NULL, vault_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pilot_transforms_project ON pilot_transforms(project_id,created_at);
CREATE TABLE IF NOT EXISTS pilot_reports (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, manifest_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
"""


def ensure_schema(store):
    if store.remote:
        return
    store.batch([{"sql": statement.strip(), "params": []}
                 for statement in SCHEMA_SQL.split(";") if statement.strip()])
    from .privacy_lifecycle import ensure_schema as ensure_privacy
    ensure_privacy(store)


def project_work_directory(store, project_id):
    """Make crashed temporary work attributable to the owning project."""
    base = store.root / "project-work"
    path = (base / project_id).resolve()
    if not path.is_relative_to(base.resolve()) or path == base.resolve():
        raise ValueError("Invalid project identity")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def inference_destination(config):
    from .providers import configured_endpoint
    provider = config.get("provider", "lexical")
    endpoint = configured_endpoint(config)
    if endpoint is None:
        return "local"
    parsed = urlsplit(endpoint)
    loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost", "ollama", "host.docker.internal"}
    if not loopback and parsed.scheme != "https":
        raise ValueError("Managed source transfer requires HTTPS outside the local inference endpoint")
    if provider == "ollama":
        return "local" if loopback else "remote_ollama"
    if provider in {"openai", "openai-compatible", "vllm"}:
        mode = config.get("inference_mode") or os.environ.get("DOXA_OPENAI_MODE") or ("local" if provider == "vllm" else "remote")
        # A loopback compatibility gateway can forward to a remote model. It
        # needs both a local endpoint and explicit local inference mode.
        return "local" if loopback and mode == "local" else "openai"
    return provider


def active_policy(store, project_id, *, default_for_service=False):
    if store.remote:
        return None
    if not store.one("SELECT name FROM sqlite_master WHERE type='table' AND name='privacy_policies'"):
        return None
    configured_mode = os.environ.get("DOXA_ACCESS_MODE", "managed" if os.environ.get("DOXA_ACCESS_CONFIG_FILE") else "local")
    if not default_for_service and not getattr(store, "_managed_pilot", configured_mode == "managed") and not store.one(
            "SELECT project_id FROM privacy_policies WHERE project_id=?", [project_id]):
        return None  # Preserve explicitly consented legacy local workflows.
    from .privacy_lifecycle import get_policy
    return get_policy(store, project_id)


def enforce_analysis_policy(store, project_id, config, documents=None):
    """Rechecked before each budget reservation as well as before scheduling."""
    selected = active_policy(store, project_id, default_for_service=bool(config.get("service_profile_id") or config.get("service_brief")))
    if selected is None:
        return
    policy = selected["policy"]
    destination = inference_destination(config)
    if destination not in policy["allowed_inference_destinations"]:
        raise ValueError("This project's privacy policy does not permit the selected inference destination")
    if destination != "local" and not config.get("allow_remote_text"):
        raise ValueError("Explicit source-text transfer consent is required for remote inference")
    if config.get("request_journal_mode", "metadata") == "visible_io" and policy.get("journal_mode", "metadata") != "visible_io":
        raise ValueError("Request capture does not match this project's privacy policy")
    if policy.get("identity_mode") == "pseudonymized":
        if config.get("learning_snapshot_id"):
            raise ValueError("Pseudonymized processing cannot send historical reviewed-example memory; disable memory for this run")
        for document in documents or []:
            metadata = document.get("metadata")
            if metadata is None:
                metadata = json.loads(document.get("metadata_json") or "{}")
            transform_id = metadata.get("privacy_transform_id")
            record = store.one("SELECT public_json FROM pilot_transforms WHERE id=? AND project_id=?", [transform_id, project_id]) if transform_id else None
            if not record:
                raise ValueError("Pseudonymized processing requires explicitly created transformed sources; originals were not scheduled")
            captured = json.loads(record["public_json"])
            if document["id"] not in captured["created_document_ids"]:
                raise ValueError("Source is not a registered output of its privacy transformation")
            index = captured["created_document_ids"].index(document["id"])
            expected = captured["preview"]["fields"]
            for field in ("title", "author", "institution", "document_type", "published_at", "license"):
                if str(document.get(field) or "") != expected[f"source.{index}.{field}"]:
                    raise ValueError("Transformed source metadata changed; create a new privacy transformation before analysis")
            blocks = document.get("blocks")
            if blocks is None:
                blocks = store.get_json(document["blocks_key"])
            field_prefix = f"source.{index}.block."
            expected_blocks = [expected[field_prefix + str(ordinal)] for ordinal in range(len(blocks))
                               if field_prefix + str(ordinal) in expected]
            total = sum(field.startswith(field_prefix) for field in expected)
            if total != len(blocks) or [block["text"] for block in blocks] != expected_blocks:
                raise ValueError("Transformed source text does not match its frozen privacy record")


def enforce_export_policy(store, project_id):
    selected = active_policy(store, project_id)
    if selected and not selected["policy"].get("allow_exports", True):
        raise ValueError("Exports are disabled by this project's privacy policy")


def stored_brief(store, project_id):
    from .service_profiles import validate_brief
    row = store.one("SELECT * FROM pilot_briefs WHERE project_id=?", [project_id])
    if row:
        return {**json.loads(row["payload_json"]), "revision": row["revision"], "updated_at": row["updated_at"]}
    return {**validate_brief({"profile_id": "public"}), "revision": 0, "updated_at": None}


def freeze_brief(store, project_id, profile_id, revision):
    brief = stored_brief(store, project_id)
    if brief["revision"] == 0 or brief["profile_id"] != profile_id or brief["revision"] != revision:
        raise ValueError("The saved service brief changed; review and save the current brief before starting analysis")
    return brief


def select_report_sources(store, project_id, body, services):
    identities = body.get("document_ids")
    if (not isinstance(identities, list) or not 1 <= len(identities) <= 100
            or any(not isinstance(identity, str) for identity in identities)
            or len(set(identities)) != len(identities)):
        raise HTTPException(422, "Explicitly select between 1 and 100 distinct sources")
    documents, total = [], 0
    for identity in identities:
        document = services["require_document"](identity, True)
        if document["project_id"] != project_id:
            raise HTTPException(404, "Source does not belong to this project")
        if document["status"] != "ready":
            raise HTTPException(422, "Select successfully extracted sources")
        total += sum(len(block["text"]) for block in document.get("blocks", []))
        if total > 2_000_000:
            raise HTTPException(413, "This interactive operation accepts at most two million source characters; narrow the selection")
        documents.append(document)
    return store, documents


def guided_quote_scope(snapshot):
    """Derive delivery scope from captured sources and the saved inquiry."""
    from .lenses import get_lens
    documents = snapshot["documents"]
    return {"profile_id": snapshot["brief"]["profile_id"], "documents": len(documents),
            "source_ids": [document["id"] for document in documents],
            "characters": sum(len(block["text"]) for document in documents for block in document["blocks"]),
            "criteria": sum(len(get_lens(lens_id)["criteria"]) for lens_id in snapshot["brief"]["lens_ids"]),
            "ocr_pages": sum(int(document.get("page_count") or 0) for document in documents
                if "ocr" in str((document.get("metadata") or {}).get("extractor", "")))}


def admit_guided_quote(store, project_id, quote_id, snapshot):
    from .pilot_commerce import CommerceError, admit_quote_work
    try:
        return admit_quote_work(store, project_id, quote_id, **guided_quote_scope(snapshot))
    except CommerceError as error:
        raise HTTPException(error.status_code, str(error)) from error


def capture_guided_report(store, project_id, body, services, *, actor_id, permissions):
    """Freeze the same evidence contract for legacy and durable publication.

    Actor and permissions are server-owned arguments, never request-body fields.
    No rendering, model requests or durable writes happen during capture.
    """
    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from error
    from .privacy_lifecycle import get_policy
    from .service_profiles import observe_communication
    call(enforce_export_policy, store, project_id)
    formats = body.get("formats", ["pdf", "html", "xlsx", "json"])
    if (not isinstance(formats, list) or not formats or len(formats) > 4
            or any(fmt not in {"pdf", "html", "xlsx", "json"} for fmt in formats)
            or len(set(formats)) != len(formats)):
        raise HTTPException(422, "Choose distinct pdf, html, xlsx or json formats")
    commentary = body.get("expert_commentary", [])
    if not isinstance(commentary, list) or len(commentary) > 200:
        raise HTTPException(422, "Supply at most 200 evidence-linked comments")
    acknowledged = body.get("review_ack", False)
    if type(acknowledged) is not bool:
        raise HTTPException(422, "Review acknowledgement must be Boolean")
    if acknowledged and "review" not in permissions:
        raise HTTPException(403, "A reviewer permission is required to sign commentary")
    notes = []
    for item in commentary:
        if not isinstance(item, dict) or set(item) - {"document_id", "block_id", "start", "end", "quote", "note"}:
            raise HTTPException(422, "Comments accept source anchors and note text; actor and review authority are assigned by the server")
        notes.append({**item, "actor_id": actor_id,
                      "review_status": "operator_reviewed" if acknowledged else "unreviewed"})
    with store.transaction():
        _store, documents = select_report_sources(store, project_id, body, services)
        frozen = stored_brief(store, project_id)
        observations = [{"document_id": document["id"], **call(observe_communication, document["blocks"], profile_id=frozen["profile_id"])}
                        for document in documents]
        findings = []
        provenance = None
        run_id = body.get("run_id")
        if run_id:
            run = services["require_run"](run_id, project_id)
            if run["config"].get("service_brief") != frozen:
                raise HTTPException(409, "The selected run used a different or historical inquiry brief; use its frozen casebook/report workflow or start a run with this saved brief")
            from .method_config import frozen_source_metadata
            for document in documents:
                prior = call(frozen_source_metadata, store, run, document["id"])
                for key in ("id", "sha256", "title", "author", "institution", "document_type", "published_at"):
                    if prior.get(key) != document.get(key):
                        raise HTTPException(409, "Source metadata changed after analysis; use the run's frozen evidence report or a new analysis")
            selected_ids = [document["id"] for document in documents]
            marks = ",".join("?" for _identity in selected_ids)
            values = store.query(f"SELECT payload_json FROM v3_assessments WHERE project_id=? AND run_id=? AND document_id IN({marks}) ORDER BY id LIMIT 2001",
                                 [project_id, run["id"], *selected_ids])
            if len(values) > 2000:
                raise HTTPException(413, "This guided briefing accepts at most 2,000 saved decisions; use a narrower selection or the full evidence report jobs")
            findings = [json.loads(item["payload_json"]) for item in values]
            source_coverage = []
            for document_id in selected_ids:
                units = store.one("SELECT COUNT(*) total,COALESCE(SUM(CASE WHEN status='accepted' THEN 1 ELSE 0 END),0) accepted FROM v3_unit_statuses WHERE run_id=? AND document_id=?", [run["id"], document_id])
                source_coverage.append({"document_id": document_id, "units_total": units["total"], "units_accepted": units["accepted"],
                                        "decisions_retained": sum(item["document_id"] == document_id for item in findings)})
            provenance = {"run_id": run["id"], "provider": run["config"].get("provider"),
                "model": run["config"].get("model") or run.get("model_profile", {}).get("model") or None,
                "model_revision": run["config"].get("model_revision"), "method_fingerprint": run["config"].get("method_fingerprint"),
                "question": run["config"].get("question"), "perspective": run["config"].get("persona"),
                "service_brief_fingerprint": frozen["fingerprint"], "selected_document_ids": selected_ids,
                "selection": "selected_sources_and_declared_lenses", "run_status": run["status"],
                "coverage": {"original_run": {key: run.get("progress", {})[key]
                    for key in ("documents_total", "documents_complete", "blocks_total", "blocks_done", "tasks_failed")
                    if key in run.get("progress", {})}, "selected_sources": source_coverage}}
        snapshot = {"brief": frozen, "documents": documents, "observations": observations,
                    "findings": findings, "expert_commentary": notes, "generated_at": utcnow(),
                    "run_provenance": provenance,
                    "privacy_disclosure": {"policy": get_policy(store, project_id)["policy"],
                        "originals_remain_accessible_to_project_readers": True,
                        "interpretation_reference_status": "reference_pending"}}
    admit_guided_quote(store, project_id, body.get("quote_id"), snapshot)
    from .pilot_reports import freeze_snapshot
    call(freeze_snapshot, snapshot, trusted_reviews=acknowledged)
    return snapshot, formats, acknowledged


def register(app, services):
    def db():
        store = services["db"]()
        if store.remote:
            raise HTTPException(501, "Managed-pilot privacy workflows require native dedicated storage; the Cloudflare workspace remains shared/private")
        return store

    def project(identity):
        services["require_project"](identity)
        return db()

    def actor(request):
        principal = getattr(request.state, "doxa_principal", None)
        return getattr(principal, "id", None) or "local-operator (self-declared)"

    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from error

    def source_selection(project_id, body, *, maximum=100, blocks=True):
        store = project(project_id)
        identities = body.get("document_ids")
        if not isinstance(identities, list) or not identities or len(identities) > maximum:
            raise HTTPException(422, f"Explicitly select between 1 and {maximum} sources")
        if any(not isinstance(identity, str) for identity in identities) or len(set(identities)) != len(identities):
            raise HTTPException(422, "Source identities must be distinct strings")
        documents, total = [], 0
        for identity in identities:
            document = services["require_document"](identity, blocks)
            if document["project_id"] != project_id:
                raise HTTPException(404, "Source does not belong to this project")
            if document["status"] != "ready":
                raise HTTPException(422, "Select successfully extracted sources")
            total += sum(len(block["text"]) for block in document.get("blocks", []))
            if total > 2_000_000:
                raise HTTPException(413, "This interactive operation accepts at most two million source characters; narrow the selection")
            documents.append(document)
        return store, documents

    @app.get("/api/pilot/profiles")
    def profiles():
        from .service_profiles import list_profiles
        return list_profiles()

    @app.get("/api/pilot/projects/{project_id}/brief")
    def brief(project_id: str):
        return stored_brief(project(project_id), project_id)

    @app.put("/api/pilot/projects/{project_id}/brief")
    def save_brief(project_id: str, body: dict):
        from .service_profiles import validate_brief
        store = project(project_id)
        value = call(validate_brief, {key: value for key, value in body.items() if key != "expected_revision"})
        if (value["profile_id"] == "personal" and app.state.access_control.managed
                and os.environ.get("DOXA_PILOT_WORKSPACE_KIND", "organization") != "personal"):
            raise HTTPException(409, "Personal review requires its own dedicated personal installation; organization administrators can access this installation's projects")
        expected = body.get("expected_revision")
        with store.transaction(write=True):
            existing = store.one("SELECT revision FROM pilot_briefs WHERE project_id=?", [project_id])
            revision = existing["revision"] if existing else 0
            if expected is not None and (type(expected) is not int or expected != revision):
                raise HTTPException(409, "The service brief changed; reload before saving")
            store.execute("INSERT INTO pilot_briefs(project_id,payload_json,revision,updated_at) VALUES(?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET payload_json=excluded.payload_json,revision=excluded.revision,updated_at=excluded.updated_at",
                          [project_id, json.dumps(value), revision + 1, utcnow()])
        return stored_brief(store, project_id)

    @app.get("/api/pilot/projects/{project_id}/privacy-policy")
    def privacy_policy(project_id: str):
        from .privacy_lifecycle import get_policy
        value = call(get_policy, project(project_id), project_id)
        value["enforcement"].update({"profiled_default_policy_active": True,
                                     "legacy_local_requires_saved_policy": not app.state.access_control.managed})
        return value

    @app.put("/api/pilot/projects/{project_id}/privacy-policy")
    def save_policy(project_id: str, body: dict, request: Request):
        from .privacy_lifecycle import set_policy
        store = project(project_id)
        if not isinstance(body.get("policy"), dict):
            raise HTTPException(422, "Supply a policy object and its expected_revision")
        # Serialize against policy writers; a revised policy is checked again
        # before every subsequent inference reservation.
        with store.transaction(write=True):
            current = privacy_policy(project_id)
            if type(body.get("expected_revision")) is not int or body["expected_revision"] != current["revision"]:
                raise HTTPException(409, "The privacy policy changed; reload before saving")
            return call(set_policy, store, project_id, body["policy"], actor(request))

    @app.post("/api/pilot/projects/{project_id}/estimate")
    def estimate(project_id: str, body: dict):
        from .service_profiles import estimate_analysis
        store, documents = source_selection(project_id, body)
        return call(estimate_analysis, documents, stored_brief(store, project_id), prices=body.get("prices"))

    @app.post("/api/pilot/projects/{project_id}/signals")
    def signals(project_id: str, body: dict):
        from .service_profiles import observe_communication
        store, documents = source_selection(project_id, body)
        selected = stored_brief(store, project_id)
        values, remaining = [], 10000
        for document in documents:
            result = call(observe_communication, document["blocks"], profile_id=selected["profile_id"],
                          max_observations=max(1, min(5000, remaining)))
            if remaining == 0:
                result["observations"] = []
                result["coverage"]["observations_returned"] = 0
                result["coverage"]["observations_omitted"] = result["coverage"]["rule_matches"]
                result["coverage"]["observations_complete"] = result["coverage"]["rule_matches"] == 0
            remaining -= len(result["observations"])
            values.append({"document_id": document["id"], "title": document["title"], **result})
        return {"profile_id": selected["profile_id"], "sources": values,
                "observation_limit": 10000, "observations_returned": 10000 - remaining,
                "method": "deterministic_linguistic_observations", "interpretation_accuracy": "not_measured",
                "expert_review_required": True, "model_requests": 0}

    @app.post("/api/pilot/projects/{project_id}/drafts", status_code=201)
    def save_selected_writing(project_id: str, body: dict):
        from .mail import selected_writing_blocks
        from .privacy_lifecycle import track_artifact
        store = project(project_id)
        text, title = body.get("text"), body.get("title", "Selected writing")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise HTTPException(422, "Writing title must contain 1–200 characters")
        identity = uuid.uuid4().hex
        blocks = call(selected_writing_blocks, text, identity)
        key = f"originals/{project_id}/selected-{identity}/selected-writing.txt"
        track_artifact(store, project_id, "objects/" + key, "selected_writing")
        store.put_bytes(key, text.encode("utf-8"), "text/plain; charset=utf-8")
        batch = services["new_batch"](project_id, "Selected writing")
        return services["save_document"](project_id, batch["id"], {
            "title": title.strip(), "author": "Selected writer (user-declared)", "document_type": "selected_writing",
            "filename": "selected-writing.txt", "mime_type": "text/plain", "blocks": blocks,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "metadata": {"source_identity": f"selected-writing:{identity}", "mail_context_version": "selected-writing-1",
                         "intake": "user_selected_text", "speech_roles": "heuristic_explicit_markers_only",
                         "retention": "saved_source_not_transient", "extraction_warnings": [
                             "No authenticated mail headers or recipient context were supplied; quotation roles use explicit text markers."]}}, object_key=key)

    @app.post("/api/pilot/projects/{project_id}/privacy-preview")
    def privacy_preview(project_id: str, body: dict):
        from .privacy import pseudonymize
        project(project_id)
        text = body.get("text")
        if not isinstance(text, str) or not text or len(text) > 100_000:
            raise HTTPException(422, "Preview between 1 and 100,000 text characters")
        value = call(pseudonymize, {"body": text}, context_id=project_id,
                     custom_entities=body.get("custom_entities", []))
        public = value.public_record()
        return {**public, "transformed_text": public["fields"]["body"], "persisted": False,
                "boundary": "server_preview_before_inference_not_before_upload",
                "analysis_inputs_changed": False}

    @app.post("/api/pilot/projects/{project_id}/privacy/transforms", status_code=201)
    def transform_sources(project_id: str, body: dict):
        from .privacy import PrivateMapVault, PrivacyLimits, pseudonymize
        store, documents = source_selection(project_id, body)
        fields = {}
        for index, document in enumerate(documents):
            fields[f"source.{index}.identity"] = document["id"]
            for name in ("title", "author", "institution", "filename", "source_url", "license", "document_type", "published_at"):
                fields[f"source.{index}.{name}"] = str(document.get(name) or "")
            for ordinal, block in enumerate(document["blocks"]):
                fields[f"source.{index}.block.{ordinal}"] = block["text"]
                if block.get("message_key"):
                    fields[f"source.{index}.sender.{ordinal}"] = str(block.get("mail_sender") or "Unknown")
        transformed = call(pseudonymize, fields, context_id=project_id,
                           custom_entities=body.get("custom_entities", []), limits=PrivacyLimits(max_fields=1024))
        transform_id = transformed.transform_id
        previous = store.one("SELECT public_json FROM pilot_transforms WHERE id=? AND project_id=?", [transform_id, project_id])
        if previous:
            record = json.loads(previous["public_json"])
            return {**record, "created_documents": [services["require_document"](identity)
                    for identity in record["created_document_ids"]], "originals_preserved": True,
                    "analysis_inputs_changed": False, "duplicate": True}
        vault = call(PrivateMapVault.from_env, store.root / "private-identity-maps" / project_id)
        saved = call(vault.save, transformed)
        public = transformed.public_record()
        transformed_fields = public["fields"]
        vault_id = saved["id"] if isinstance(saved, dict) else saved
        from .privacy_lifecycle import track_artifact
        clone_ids = [uuid.uuid4().hex for _document in documents]
        for clone_id in clone_ids:
            # Register before the clone transaction, so a later rollback cannot
            # orphan a source object that was already written to disk.
            track_artifact(store, project_id, f"objects/documents/{clone_id}", "transformed_source")
        created, batch = [], services["new_batch"](project_id, "Pseudonymized source versions")
        try:
            with store.transaction(write=True):
                # A concurrent identical request may have committed while the
                # encrypted mapping and ownership records were being prepared.
                duplicate = store.one("SELECT public_json FROM pilot_transforms WHERE id=? AND project_id=?", [transform_id, project_id])
                if duplicate:
                    record = json.loads(duplicate["public_json"])
                    vault.delete(vault_id)
                    return {**record, "created_documents": [services["require_document"](identity)
                            for identity in record["created_document_ids"]], "originals_preserved": True,
                            "analysis_inputs_changed": False, "duplicate": True}
                for index, original in enumerate(documents):
                    prefix = f"source.{index}."
                    blocks = []
                    for ordinal, block in enumerate(original["blocks"]):
                        entry = {"id": f"private-{ordinal}", "text": transformed_fields[prefix + f"block.{ordinal}"],
                                 "page": block.get("page"), "paragraph": block.get("paragraph")}
                        # Keep parse roles, never copy unmasked sender/header metadata.
                        for name in ("mail_role", "eligible_for_author_counts"):
                            if name in block:
                                entry[name] = block[name]
                        if block.get("message_key"):
                            entry.update({"message_key": hashlib.sha256(f"{transform_id}:{block['message_key']}".encode()).hexdigest()[:24],
                                          "mail_sender": transformed_fields[prefix + f"sender.{ordinal}"],
                                          "mail_source_field": "pseudonymized_block",
                                          "source_char_start": 0, "source_char_end": len(entry["text"]),
                                          "source_chain": {"message_sender_claim": transformed_fields[prefix + f"sender.{ordinal}"],
                                                           "body_role": entry.get("mail_role", "unknown"),
                                                           "verification": "pseudonymized_unverified_source_claim"}})
                        blocks.append(entry)
                    clone = {name: transformed_fields[prefix + name] for name in ("title", "author", "institution", "license", "document_type")}
                    clone.update({"filename": f"pseudonymized-{index + 1}.txt", "mime_type": "text/plain", "blocks": blocks,
                                  "published_at": transformed_fields[prefix + "published_at"] or None,
                                  "source_url": "", "metadata": {"privacy_transform_id": transform_id,
                                    "privacy_mode": "pseudonymized", "source_identity": f"privacy:{transform_id}:{index}",
                                    "original_document_id": original["id"], "original_sha256": original["sha256"],
                                    "quotation_provenance": "transformed_text_not_unchanged_original",
                                    "detector_limitations": "Rules and supplied terms can miss identifiers and contextual re-identification"}})
                    if any(block.get("message_key") for block in blocks):
                        clone["metadata"]["mail_context_version"] = "pseudonymized-mail-1"
                    created.append(services["save_document"](project_id, batch["id"], clone, document_id=clone_ids[index]))
                record = {"id": transform_id, "project_id": project_id, "source_document_ids": [item["id"] for item in documents],
                          "created_document_ids": [item["id"] for item in created], "preview": public,
                          "boundary": "server_side_before_inference_originals_already_uploaded"}
                store.execute("INSERT INTO pilot_transforms(id,project_id,source_document_ids_json,public_json,vault_id,created_at) VALUES(?,?,?,?,?,?)",
                              [transform_id, project_id, json.dumps(record["source_document_ids"]), json.dumps(record), str(vault_id), utcnow()])
        except BaseException:
            # Precommitted ownership covers files left by a failed clone write.
            raise
        return {**record, "created_documents": created, "originals_preserved": True,
                "analysis_inputs_changed": False, "next_step": "Explicitly select the new source versions for analysis"}

    @app.get("/api/pilot/projects/{project_id}/privacy/transforms/{transform_id}")
    def transform_detail(project_id: str, transform_id: str):
        store = project(project_id)
        row = store.one("SELECT public_json FROM pilot_transforms WHERE id=? AND project_id=?", [transform_id, project_id])
        if not row:
            raise HTTPException(404, "Transformation not found")
        return json.loads(row["public_json"])

    @app.post("/api/pilot/projects/{project_id}/privacy/transforms/{transform_id}/reveal")
    def reveal_transform(project_id: str, transform_id: str):
        from .privacy import PrivateMapVault
        from .privacy_lifecycle import get_policy
        store = project(project_id)
        if not get_policy(store, project_id)["policy"]["allow_identity_reveal"]:
            raise HTTPException(403, "Identity-map reveal is disabled by project policy")
        row = store.one("SELECT vault_id,public_json FROM pilot_transforms WHERE id=? AND project_id=?", [transform_id, project_id])
        if not row:
            raise HTTPException(404, "Transformation not found")
        vault = call(PrivateMapVault.from_env, store.root / "private-identity-maps" / project_id)
        record = json.loads(row["public_json"])
        return call(vault.load, row["vault_id"], expected_context_id=project_id,
                    expected_transform_id=record["preview"]["transform_id"])

    @app.post("/api/pilot/projects/{project_id}/reports", status_code=201)
    async def create_report(project_id: str, body: dict, request: Request):
        from .pilot_reports import build_pilot_report
        from .privacy_lifecycle import track_artifact
        from .access import current_principal
        store = project(project_id)
        snapshot, formats, acknowledged = capture_guided_report(
            store, project_id, body, services, actor_id=actor(request),
            permissions=current_principal().permissions)
        frozen = snapshot["brief"]
        documents = snapshot["documents"]
        notes = snapshot["expert_commentary"]
        identity = uuid.uuid4().hex
        prefix = f"pilot-reports/{project_id}/{identity}"
        track_artifact(store, project_id, "objects/" + prefix, "guided_report")
        artifacts = []
        if not hasattr(app.state, "pilot_report_slots"):
            app.state.pilot_report_slots = asyncio.Semaphore(1)
        async with app.state.pilot_report_slots:
            for fmt in formats:
                admit_guided_quote(store, project_id, body.get("quote_id"), snapshot)
                from .local_profile import run_heavy
                result = await asyncio.to_thread(run_heavy, call, build_pilot_report, snapshot, fmt, trusted_reviews=acknowledged)
                admit_guided_quote(store, project_id, body.get("quote_id"), snapshot)
                filename = f"doxa-{frozen['profile_id']}-{identity[:8]}.{result['extension']}"
                key = prefix + "/" + filename
                from .privacy_lifecycle import run_activity
                await asyncio.to_thread(run_activity, store, project_id, "pilot_report_output",
                                        store.put_bytes, key, result["content"], result["media_type"])
                artifacts.append({"format": fmt, "filename": filename, "bytes": len(result["content"]),
                    "sha256": hashlib.sha256(result["content"]).hexdigest(), "object_key": key,
                    "media_type": result["media_type"], "manifest": result["manifest"],
                    "download_url": f"/api/pilot/projects/{project_id}/reports/{identity}/artifacts/{fmt}"})
        admit_guided_quote(store, project_id, body.get("quote_id"), snapshot)
        record = {"id": identity, "project_id": project_id, "quote_id": body.get("quote_id"), "profile_id": frozen["profile_id"],
                  "review_status": "operator_signed" if acknowledged and notes else "provisional",
                  "reference_status": "reference_pending", "created_at": utcnow(), "artifacts": artifacts,
                  "source_ids": [document["id"] for document in documents], "brief_revision": frozen["revision"]}
        store.execute("INSERT INTO pilot_reports(id,project_id,manifest_json,created_at) VALUES(?,?,?,?)",
                      [identity, project_id, json.dumps(record), record["created_at"]])
        return record

    @app.get("/api/pilot/projects/{project_id}/reports")
    def report_history(project_id: str):
        store = project(project_id)
        return [json.loads(row["manifest_json"]) for row in store.query(
            "SELECT manifest_json FROM pilot_reports WHERE project_id=? ORDER BY created_at DESC LIMIT 100", [project_id])]

    @app.api_route("/api/pilot/projects/{project_id}/reports/{report_id}/artifacts/{format}", methods=["GET", "HEAD"])
    def download_report(project_id: str, report_id: str, format: str):
        from fastapi.responses import FileResponse
        store = project(project_id)
        call(enforce_export_policy, store, project_id)
        row = store.one("SELECT manifest_json FROM pilot_reports WHERE id=? AND project_id=?", [report_id, project_id])
        if not row:
            raise HTTPException(404, "Report not found")
        artifact = next((item for item in json.loads(row["manifest_json"])["artifacts"] if item["format"] == format), None)
        if not artifact:
            raise HTTPException(404, "Report format not found")
        path = store.object_path(artifact["object_key"])
        if not path.is_file() or path.stat().st_size != artifact["bytes"]:
            raise HTTPException(409, "Report integrity check failed; the saved artifact was changed or is unavailable")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != artifact["sha256"]:
                raise HTTPException(409, "Report integrity check failed; the saved artifact was changed or is unavailable")
        return FileResponse(path, media_type=artifact["media_type"],
                            filename=artifact["filename"], headers={"ETag": '"' + artifact["sha256"] + '"'})

    async def erase_until_waiting(identity):
        from .privacy_lifecycle import process_erase_job
        try:
            while True:
                result = await asyncio.to_thread(process_erase_job, db(), identity, max_objects=100)
                if result["status"] not in {"queued", "running"}:
                    return
                await asyncio.sleep(0)
        except (OSError, ValueError):
            # The durable job retains a generic failure; do not log private
            # filesystem paths or source material from an exception message.
            return

    def resume_erase_jobs():
        for row in db().query("SELECT id FROM privacy_erase_jobs WHERE status IN ('queued','running','waiting')"):
            app.state.tasks["erase:" + row["id"]] = asyncio.create_task(erase_until_waiting(row["id"]))

    app.state.resume_pilot_erasure_jobs = resume_erase_jobs

    @app.get("/api/pilot/projects/{project_id}/erase-jobs")
    def project_erasures(project_id: str):
        from .privacy_lifecycle import get_erase_job
        store = db()
        return [get_erase_job(store, row["id"]) for row in store.query(
            "SELECT id FROM privacy_erase_jobs WHERE project_id=? ORDER BY created_at DESC", [project_id])]

    @app.post("/api/pilot/projects/{project_id}/erase-jobs", status_code=202)
    async def erase_project(project_id: str, body: dict, request: Request):
        from .privacy_lifecycle import create_erase_job, matches_erasure_key
        store = db()
        previous = store.one("SELECT id,idempotency_key FROM privacy_erase_jobs WHERE project_id=?", [project_id])
        if previous:
            if not matches_erasure_key(previous["idempotency_key"], body.get("idempotency_key")):
                raise HTTPException(409, "This project already has an erasure request")
            return erase_status(previous["id"])
        row = store.one("SELECT name FROM projects WHERE id=?", [project_id])
        if not row:
            raise HTTPException(404, "Project not found")
        if body.get("confirm_project_name") != row["name"]:
            raise HTTPException(422, "Confirm the exact project name to erase its sources and derived data")
        key = body.get("idempotency_key")
        if not isinstance(key, str) or not 8 <= len(key) <= 128:
            raise HTTPException(422, "Supply an idempotency key of 8–128 characters")
        job = await asyncio.to_thread(call, create_erase_job, store, project_id, actor(request), key)
        app.state.tasks["erase:" + job["id"]] = asyncio.create_task(erase_until_waiting(job["id"]))
        return job

    @app.get("/api/pilot/erase-jobs/{erase_job_id}")
    def erase_status(erase_job_id: str):
        from .privacy_lifecycle import get_erase_job
        return call(get_erase_job, db(), erase_job_id)

    @app.post("/api/pilot/erase-jobs/{erase_job_id}/resume", status_code=202)
    async def resume_erasure(erase_job_id: str):
        job = erase_status(erase_job_id)
        app.state.tasks["erase:" + erase_job_id] = asyncio.create_task(erase_until_waiting(erase_job_id))
        return job
