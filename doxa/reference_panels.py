"""Import an externally collected, rights-recorded reference sampling frame.

The operator supplies sampling/group/rights records, never model outcomes or
gold labels. Exact units and any model decisions are loaded from the scoped run.
An attested probability design is a recorded claim, not an automatic audit.
"""
from __future__ import annotations

from collections import Counter
import copy
import json
import math
import re

from .lineage import content_sha256
from .store import utcnow


def _text(value, name, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Panel {name} requires bounded nonempty text")
    return value


def _sha(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError(f"Panel {name} requires a SHA-256 record")
    return value


def validate_manifest(value):
    allowed = {"profile_id", "domain", "population_description", "sampling_design", "sampling_frame_sha256",
               "inference_cluster_key", "seed", "cases"}
    if not isinstance(value, dict) or set(value) != allowed:
        raise ValueError("Panel manifest requires only its declared collection fields")
    if not isinstance(value["profile_id"], str) or value["profile_id"] not in {"company", "public", "personal"}:
        raise ValueError("Choose Company, Public or Personal")
    _text(value["domain"], "domain", 500)
    _text(value["population_description"], "population description", 4000)
    _sha(value["sampling_frame_sha256"], "sampling frame")
    if not isinstance(value["sampling_design"], str) or value["sampling_design"] not in {"probability_sample", "enriched_diagnostic"}:
        raise ValueError("Declare a probability sample or enriched diagnostic collection")
    if not isinstance(value["inference_cluster_key"], str) or value["inference_cluster_key"] not in {"story_group", "organization_group", "source_family_group"}:
        raise ValueError("Declare the inference cluster key")
    if type(value["seed"]) is not int or not 0 <= value["seed"] < 2 ** 63:
        raise ValueError("Panel seed must be a bounded nonnegative integer")
    if not isinstance(value["cases"], list) or not 5 <= len(value["cases"]) <= 1000:
        raise ValueError("Import 5–1000 primary source/thread clusters per panel")
    seen, sources, probabilities, inference_groups = set(), set(), set(), {}
    for case in value["cases"]:
        fields = {"document_id", "unit_id", "lens_id", "criterion_id", "primary_cluster_id", "split_group_id",
                  "inference_cluster_id", "inclusion_probability", "rights_record_sha256"}
        if not isinstance(case, dict) or set(case) != fields:
            raise ValueError("Panel cases accept source/unit/criterion, grouping, probability and rights records; no submitted labels")
        for field in fields - {"inclusion_probability", "rights_record_sha256"}:
            _text(case[field], field)
        _sha(case["rights_record_sha256"], "rights record")
        probability = case["inclusion_probability"]
        if isinstance(probability, bool) or not isinstance(probability, (float, int)) or not math.isfinite(probability) or not 0 < probability <= 1:
            raise ValueError("Panel inclusion probabilities must lie in (0,1]")
        if case["primary_cluster_id"] in seen or case["document_id"] in sources:
            raise ValueError("Use one primary case per source/thread cluster and imported source")
        seen.add(case["primary_cluster_id"])
        sources.add(case["document_id"])
        probabilities.add(probability)
        prior_group = inference_groups.setdefault(case["inference_cluster_id"], case["split_group_id"])
        if prior_group != case["split_group_id"]:
            raise ValueError("Each inference cluster must remain wholly inside one company/thread/story split group")
    if value["sampling_design"] == "probability_sample" and len(probabilities) != 1:
        raise ValueError("This release supports equal-probability panels; unequal sampling needs a separately validated weighted estimator")
    if len({case["split_group_id"] for case in value["cases"]}) < 5:
        raise ValueError("At least five independent company/thread/story split groups are needed for three partitions")
    return copy.deepcopy(value)


def assign_splits(manifest):
    """Rank complete declared groups before model/reference labels are read.

    The split is 40/20/40 of groups (rounded), never random correlated passages.
    Source counts can differ when groups have unequal size and are disclosed.
    """
    groups = sorted({case["split_group_id"] for case in manifest["cases"]},
                    key=lambda group: content_sha256([manifest["seed"], group]))
    development, calibration = max(1, int(0.4 * len(groups))), max(1, int(0.2 * len(groups)))
    return {group: "development" if index < development else "calibration" if index < development + calibration else "heldout"
            for index, group in enumerate(groups)}


def import_reference_panel(store, project_id, run_id, manifest, *, actor_id):
    from . import evaluation
    from .contextual import build_units
    from .method_config import frozen_source_metadata
    if store.remote:
        raise ValueError("Reference panel import requires native storage")
    manifest = validate_manifest(manifest)
    evaluation.ensure_schema(store)
    if not store.one("SELECT id FROM projects WHERE id=?", [project_id]):
        raise ValueError("Project not found")
    with store.transaction(write=True):
        run = store.one("SELECT * FROM runs WHERE id=? AND project_id=?", [run_id, project_id])
        if not run:
            raise ValueError("Run not found in this project")
        config = json.loads(run["config_json"])
        if config.get("service_profile_id") and config["service_profile_id"] != manifest["profile_id"]:
            raise ValueError("Panel profile differs from the run's frozen service profile")
        identity = content_sha256(["reference-panel-0.6", project_id, run_id, content_sha256(config), manifest])[:32]
        existing = store.one("SELECT payload_json FROM v4_evaluations WHERE project_id=? AND id=?", [project_id, identity])
        if existing:
            return {**json.loads(existing["payload_json"]), "idempotent": True}
        partitions, prepared, hashes = assign_splits(manifest), [], set()
        run_selection = {item["id"]: item for item in config.get("source_snapshot", [])}
        for selected in manifest["cases"]:
            document_id = selected["document_id"]
            row = store.one("SELECT * FROM documents WHERE project_id=? AND id=?", [project_id, document_id])
            if not row or not row.get("blocks_key"):
                raise ValueError("Panel source is outside this project or has no frozen text")
            member = store.one("SELECT sha256 FROM v3_run_sources WHERE run_id=? AND document_id=?", [run_id, document_id])
            payload = store.one("SELECT payload_json FROM v3_assessments WHERE project_id=? AND run_id=? AND document_id=? AND unit_id=? AND lens_id=? AND criterion_id=?",
                                [project_id, run_id, document_id, selected["unit_id"], selected["lens_id"], selected["criterion_id"]])
            if not member and document_id not in run_selection and not payload:
                raise ValueError("Panel source is not a recorded member of this run")
            expected = (member or run_selection.get(document_id) or {}).get("sha256")
            if expected and row["sha256"] != expected:
                raise ValueError("Panel source version changed after the run")
            if row["sha256"] in hashes:
                raise ValueError("Exact duplicate source versions cannot be separate primary sampling clusters")
            hashes.add(row["sha256"])
            source = {key: row.get(key) for key in ("id", "sha256", "title", "author", "institution", "document_type", "published_at", "source_url")}
            source["metadata"] = json.loads(row.get("metadata_json") or "{}")
            metadata_origin = "source_metadata_at_panel_creation_not_contemporaneous_run_capture"
            if config.get("method_config_version") == "0.4" or config.get("source_manifest_key"):
                source.update(frozen_source_metadata(store, run, document_id))
                metadata_origin = "frozen_run_metadata"
            blocks = store.get_json(row["blocks_key"])
            if sum(len(block["text"]) for block in blocks) > 2_000_000:
                raise ValueError("A panel source exceeds the 2-million-character interactive bound; use a scoped source version")
            units = build_units({**source, "blocks": blocks}, max_unit_chars=config.get("unit_max_chars", 1200),
                                context_sentences=config.get("context_sentences", 2),
                                **({"context_policy": config["context_policy"]} if config.get("context_policy") else {}))
            if selected["unit_id"] not in {unit["id"] for unit in units}:
                raise ValueError("Panel unit does not resolve to this exact frozen source and context policy")
            rubric = evaluation.freeze_rubric(config, selected["lens_id"])
            criterion = next((item for item in rubric["pack"]["criteria"] if item["id"] == selected["criterion_id"]), None)
            if criterion is None:
                raise ValueError("Panel criterion is absent from the frozen rubric")
            model = json.loads(payload["payload_json"]) if payload else {
                "document_id": document_id, "unit_id": selected["unit_id"], "source_hash": source["sha256"],
                "lens_id": selected["lens_id"], "criterion_id": selected["criterion_id"], "result": None,
                "execution_state": "unprocessed", "detail_supplied": False}
            case = {"id": content_sha256([identity, selected])[:32], "evaluation_id": identity,
                    "source": source, "source_hash": source["sha256"], "source_metadata_origin": metadata_origin,
                    "story_group": selected["primary_cluster_id"], "split_group_id": selected["split_group_id"],
                    "cluster_ids": {manifest["inference_cluster_key"]: selected["inference_cluster_id"]},
                    "split": partitions[selected["split_group_id"]], "assessment_id": model.get("id"),
                    "unit_id": selected["unit_id"], "lens_id": selected["lens_id"], "criterion_id": selected["criterion_id"],
                    "criterion_kind": criterion.get("kind"), "model_assessment": model,
                    "rubric": rubric, "criterion": criterion, "extracted_blocks_sha256": content_sha256(blocks),
                    "inclusion_probability": selected["inclusion_probability"], "rights_record_sha256": selected["rights_record_sha256"],
                    "reference_status": "unreviewed", "reference_labels": None}
            case["case_sha256"] = content_sha256(case)
            prepared.append(case)
        counts = Counter(case["split"] for case in prepared)
        summary = {"id": identity, "project_id": project_id, "run_id": run_id, "schema_version": "0.6",
                   "kind": "independent_domain_panel", "profile_id": manifest["profile_id"], "domain": manifest["domain"],
                   "population_description": manifest["population_description"], "seed": manifest["seed"],
                   "case_count": len(prepared), "development_count": counts["development"], "calibration_count": counts["calibration"],
                   "heldout_count": counts["heldout"], "created_by": actor_id, "created_at": utcnow(),
                   "parent": {"run_id": run_id, "run_status": run["status"], "config_sha256": content_sha256(config)},
                   "sampling": {"policy": "external_rights_recorded_equal_probability_primary_cluster_manifest" if manifest["sampling_design"] == "probability_sample" else "external_enriched_diagnostic_manifest",
                                "representative_population_sample": manifest["sampling_design"] == "probability_sample",
                                "sampling_design_independently_verified": False,
                                "sampling_frame_sha256": manifest["sampling_frame_sha256"], "manifest_sha256": content_sha256(manifest),
                                "inference_cluster_key": manifest["inference_cluster_key"], "split_policy": "40/20/40_hash_ranked_whole_groups",
                                "split_group_count": len(partitions), "actual_cases_by_split": dict(counts),
                                "notice": "Operator attests collection rights and sampling/group records; DOXA validates scoped identities, not their real-world truth. Unequal group sizes change case counts."},
                   "quality_status": "reference_pending", "independent_reference_labels": False, "interpretation_accuracy": None,
                   "protocol": {"required_independent_reviewers": 2, "model_blinded_first_pass": True,
                                "adjudication_separate": True, "retain_initial_disagreements": True},
                   "exposure_ledger": {"status": "operator_must_document_model_and_test_exposure", "parent_outputs_may_be_known": True}}
        summary["scaffold_sha256"] = content_sha256({"summary": summary, "case_hashes": [case["case_sha256"] for case in prepared]})
        store.execute("INSERT INTO v4_evaluations(id,project_id,run_id,payload_json,created_at) VALUES(?,?,?,?,?)",
                      [identity, project_id, run_id, evaluation._json(summary), summary["created_at"]])
        store.batch([{"sql": "INSERT INTO v4_evaluation_cases(id,evaluation_id,project_id,document_id,story_group,split,payload_json) VALUES(?,?,?,?,?,?,?)",
                      "params": [case["id"], identity, project_id, case["source"]["id"], case["story_group"], case["split"], evaluation._json(case)]} for case in prepared])
        return summary
