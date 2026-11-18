"""Versioned independent-review scaffold; no invented reference labels.

The scaffold records sampling and provenance, not accuracy. Two distinct
reviewer identities are necessary but do not prove real-world independence.
"""
from __future__ import annotations

from collections import defaultdict
import copy
import json
import uuid
import hashlib
import re

from .lineage import content_sha256
from .store import utcnow

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS v4_evaluations(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,run_id TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v4_evaluation_cases(
 id TEXT PRIMARY KEY,evaluation_id TEXT NOT NULL,project_id TEXT NOT NULL,document_id TEXT NOT NULL,
 story_group TEXT NOT NULL,split TEXT NOT NULL,payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS v4_eval_scope ON v4_evaluation_cases(project_id,evaluation_id,split);
CREATE TABLE IF NOT EXISTS v4_reference_reviews(
 id TEXT PRIMARY KEY,evaluation_id TEXT NOT NULL,case_id TEXT NOT NULL,project_id TEXT NOT NULL,
 reviewer_id TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(evaluation_id,case_id,reviewer_id)
);
CREATE TABLE IF NOT EXISTS v4_reference_adjudications(
 id TEXT PRIMARY KEY,evaluation_id TEXT NOT NULL,case_id TEXT NOT NULL,project_id TEXT NOT NULL,
 actor_id TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(evaluation_id,case_id)
);
"""
RESULTS = {"meets_criterion", "does_not_meet", "uncertain", "not_applicable"}
ATTRIBUTIONS = {"author", "quoted_speaker", "reported_speaker", "unknown"}
STANCES = {"supports", "opposes", "mixed", "neutral", "unclear", "not_applicable"}
ANNOTATION_FIELDS = {"result", "attribution", "speaker_stance", "document_stance", "evidence_adequacy",
                     "criterion_application", "context_sufficient", "acceptable_alternatives", "note"}
# Keep the v0.4 offline generator's field set stable. The optional contract is
# additive for newly entered human references, never synthesized model gold.
EXTENDED_ANNOTATION_FIELDS = {"evidence_spans", "target", "proposition", "reporting_chain",
                              "communicative_act", "perspective", "missing_context_reasons"}
PERCEPTION_ANNOTATION_FIELDS = {"reader_perception"}
PERCEPTION_DIMENSIONS = {"clarity", "directness", "perceived_respect", "perceived_hostility"}
COMMUNICATIVE_ACTS = {"request", "proposal", "commitment", "refusal", "disagreement", "assertion",
                     "question", "acknowledgement", "greeting", "mixed", "other", "uncertain", "not_applicable"}
REPORTING_ROLES = {"author", "narrator", "message_sender", "quoted_speaker", "reported_speaker", "unknown"}
MISSING_CONTEXT_REASONS = {"missing_prior_message", "missing_following_message", "missing_section", "speaker_unknown",
                           "target_unknown", "translation_uncertain", "extraction_incomplete", "comparator_missing",
                           "ambiguous_reference", "other"}


def annotation_contract():
    return {"version": "0.5", "optional_fields": sorted(EXTENDED_ANNOTATION_FIELDS | PERCEPTION_ANNOTATION_FIELDS),
            "reader_perception": {"version": "0.6", "scale_version": "reader-perception-ordinal-1.0",
                                  "dimensions": sorted(PERCEPTION_DIMENSIONS), "scale": "1=very low, 5=very high, ordinal",
                                  "fields": ["scale_version", "reader_context", "ratings"],
                                  "minimum_readers_per_context": 3,
                                  "identity": "Reader identity comes from the authenticated review envelope, never a submitted list of invented participants."},
            "unannotated_value": None, "coordinate_unit": "unicode_code_points",
            "evidence_spans": {"maximum": 32, "fields": ["document_id", "block_id", "start", "end", "quote"],
                               "scope": "Exact frozen source text within the displayed scored unit or neighbor context; offsets are relative to block text."},
            "reporting_chain": {"maximum": 12, "fields": ["speaker", "role", "evidence_span_indices"],
                                "roles": sorted(REPORTING_ROLES), "evidence_links": "Zero-based indices into the independently entered evidence_spans."},
            "communicative_acts": sorted(COMMUNICATIVE_ACTS),
            "missing_context_reasons": sorted(MISSING_CONTEXT_REASONS),
            "perspective": "Reviewer-declared reading context or framework perspective; not inferred private identity or beliefs.",
            "origin": "human_reference_annotation", "automatic_reference_labels": False}


def ensure_schema(store):
    if getattr(store, "_v4_evaluation_schema_ready", False):
        return
    row = store.one("SELECT COUNT(*) AS total FROM sqlite_master WHERE type='table' AND name IN ('v4_evaluations','v4_evaluation_cases','v4_reference_reviews','v4_reference_adjudications')")
    if row and row["total"] == 4:
        store._v4_evaluation_schema_ready = True
        return
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            store.execute(statement)
    store._v4_evaluation_schema_ready = True


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _source_group(source):
    metadata = source.get("metadata") or {}
    return str(source.get("story_group_id") or metadata.get("story_group_id") or
               metadata.get("duplicate_group") or source.get("sha256") or source.get("content_sha256") or source["id"])


def _source_split(source):
    metadata = source.get("metadata") or {}
    split = str(source.get("split") or metadata.get("split") or "development").lower()
    return "heldout" if split in {"holdout", "heldout", "held_out", "test", "validation"} else "development"


def freeze_rubric(config, lens_id):
    """Freeze review instructions and distinguish embedded versus later capture."""
    snapshots = config.get("lens_snapshots") or []
    expected = config.get("lens_snapshots_sha256")
    verified = None
    if snapshots and expected:
        actual = hashlib.sha256(json.dumps(snapshots, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if actual != expected:
            raise ValueError("Embedded frozen rubric snapshot hash changed")
        verified = True
    pack = next((pack for pack in snapshots if pack.get("id") == lens_id), None)
    if pack is not None:
        origin = "run_embedded_frozen_rubric"
    else:
        from .lenses import get_lens, validate_custom_lens
        pack = validate_custom_lens(config["custom_lens"]) if lens_id == "custom" and config.get("custom_lens") else get_lens(lens_id)
        origin = "versioned_registry_captured_at_evaluation_creation"
    pack = copy.deepcopy(pack)
    return {"pack": pack, "rubric_sha256": content_sha256(pack), "origin": origin,
            "run_lens_snapshots_sha256": expected if snapshots else None,
            "embedded_collection_hash_verified": verified,
            "contemporaneous_request_capture": False,
            "notice": "Frozen run configuration is distinct from an actual request journal." if origin == "run_embedded_frozen_rubric" else "Later review-rubric capture; original request rubric content was not embedded. Historical content equality is unverified."}


def build_scaffold(candidates, *, development_count=100, heldout_count=100, seed=20261117,
                   parent=None, exposure_ledger=None) -> dict:
    """Select deterministic bounded cases, respecting the existing source split.

    candidates yields {assessment,source}; at most four times requested_count
    are retained per criterion/outcome bucket, allowing leakage exclusions.
    If those bounds cannot supply the target the scaffold fails explicitly.
    """
    if type(development_count) is not int or type(heldout_count) is not int or not (1 <= development_count <= 1000 and 1 <= heldout_count <= 1000):
        raise ValueError("Each review split must contain 1–1000 cases")
    if type(seed) is not int or not 0 <= seed < 2 ** 63:
        raise ValueError("Evaluation seed must be a non-negative bounded integer")
    exposed = set((exposure_ledger or {}).get("exposed_story_groups", []))
    pools, group_splits, identities = defaultdict(list), defaultdict(set), set()
    for entry in candidates:
        assessment, source = entry["assessment"], entry["source"]
        if assessment.get("result") not in RESULTS or not assessment.get("unit_id") or not assessment.get("criterion_id"):
            continue
        digest = source.get("sha256") or source.get("content_sha256")
        if not digest:
            raise ValueError("Every review source needs its frozen content hash")
        split, group = _source_split(source), _source_group(source)
        group_splits[group].add(split)
        if split == "heldout" and group in exposed:
            continue
        identity = content_sha256([source["id"], digest, assessment["unit_id"], assessment.get("lens_id"), assessment["criterion_id"]])
        if identity in identities:
            continue
        identities.add(identity)
        case = {"id": identity[:32], "source": copy.deepcopy(source), "source_hash": digest,
                "story_group": group, "split": split, "assessment_id": assessment.get("id"),
                "unit_id": assessment["unit_id"], "lens_id": assessment.get("lens_id"),
                "criterion_id": assessment["criterion_id"], "criterion_kind": assessment.get("criterion_kind"),
                "model_assessment": copy.deepcopy(assessment), "reference_status": "unreviewed",
                "reference_labels": None}
        case["case_sha256"] = content_sha256(case)
        rank = content_sha256([seed, identity])
        key = (split, case["lens_id"] or "", case["criterion_id"], assessment["result"])
        pool = pools[key]
        pool.append((rank, case))
        pool.sort(key=lambda item: item[0])
        del pool[4 * max(development_count, heldout_count):]
    overlapping = {group for group, splits in group_splits.items() if len(splits) > 1}
    selected = []
    for split, count in (("development", development_count), ("heldout", heldout_count)):
        buckets = [[case for _, case in pools[key] if case["story_group"] not in overlapping]
                   for key in sorted(pools) if key[0] == split]
        chosen = []
        while len(chosen) < count and any(buckets):
            for bucket in buckets:
                if bucket and len(chosen) < count:
                    chosen.append(bucket.pop(0))
        if len(chosen) != count:
            raise ValueError(f"Insufficient eligible {split} cases: {len(chosen)}/{count}; no duplicate or fabricated cases were added")
        selected.extend(chosen)
    value = {"schema_version": "0.4", "kind": "independent_reference_scaffold", "seed": seed,
        "parent": parent or {}, "case_count": len(selected), "development_count": development_count,
        "heldout_count": heldout_count, "cases": selected, "quality_status": "reference_pending",
        "independent_reference_labels": False, "interpretation_accuracy": None,
        "sampling": {"policy": "deterministic criterion/outcome round-robin within pre-existing source splits",
                     "representative_population_sample": False, "excluded_cross_split_story_groups": sorted(overlapping),
                     "heldout_exposure_exclusions": sorted(exposed)},
        "protocol": {"required_independent_reviewers": 2, "model_blinded_first_pass": True,
            "adjudication_separate": True, "retain_initial_disagreements": True,
            "independence_notice": "Distinct authenticated identities and declarations do not prove real-world reviewer independence.",
            "gold_policy": "No model-generated or unreviewed labels are reference truth."},
        "exposure_ledger": exposure_ledger or {"status": "not_recorded", "exposed_story_groups": []}}
    value["scaffold_sha256"] = content_sha256(value)
    return value


def _require(store, project_id, evaluation_id):
    row = store.one("SELECT * FROM v4_evaluations WHERE id=? AND project_id=?", [evaluation_id, project_id])
    if not row:
        raise ValueError("Evaluation not found in this project")
    return json.loads(row["payload_json"])


def create_scaffold(store, project_id, run_id, *, development_count=100, heldout_count=100, seed=20261117, actor_id):
    ensure_schema(store)
    row = store.one("SELECT * FROM runs WHERE id=? AND project_id=?", [run_id, project_id])
    if not row:
        raise ValueError("Run not found in this project")
    config = json.loads(row["config_json"])
    rubrics = {item["lens_id"]: freeze_rubric(config, item["lens_id"]) for item in store.query("SELECT DISTINCT lens_id FROM v3_assessments WHERE project_id=? AND run_id=?", [project_id, run_id])}
    from .method_config import frozen_source_metadata
    manifest_available = bool(config.get("source_manifest_key") or config.get("method_config_version") == "0.4")
    sources = {}
    selected_ids = {item.get("id") for item in config.get("source_snapshot", [])}
    for source in store.query("SELECT id,sha256,title,author,institution,document_type,published_at,source_url,metadata_json FROM documents WHERE project_id=?", [project_id]):
        source["metadata"] = json.loads(source.pop("metadata_json") or "{}")
        if manifest_available and source["id"] in selected_ids:
            source.update(frozen_source_metadata(store, row, source["id"]))
        sources[source["id"]] = source

    def candidates():
        offset = 0
        while True:
            rows = store.query("SELECT id,document_id,payload_json FROM v3_assessments WHERE project_id=? AND run_id=? ORDER BY id LIMIT 250 OFFSET ?", [project_id, run_id, offset])
            for assessment in rows:
                payload = json.loads(assessment["payload_json"])
                payload.setdefault("id", assessment["id"])
                yield {"assessment": payload, "source": sources[assessment["document_id"]]}
            if len(rows) < 250:
                break
            offset += len(rows)
    scaffold = build_scaffold(candidates(), development_count=development_count, heldout_count=heldout_count,
        seed=seed, parent={"run_id": run_id, "run_status": row["status"], "config_sha256": content_sha256(config),
                           "evaluation_rubrics_sha256": content_sha256(rubrics)},
        exposure_ledger=config.get("exposure_ledger"))
    identity, now = scaffold["scaffold_sha256"][:32], utcnow()
    existing = store.one("SELECT id FROM v4_evaluations WHERE id=? AND project_id=?", [identity, project_id])
    if not existing:
        summary = {key: value for key, value in scaffold.items() if key != "cases"}
        summary.update({"id": identity, "project_id": project_id, "run_id": run_id, "created_by": actor_id, "created_at": now})
        statements = [{"sql": "INSERT INTO v4_evaluations(id,project_id,run_id,payload_json,created_at) VALUES(?,?,?,?,?)",
                       "params": [identity, project_id, run_id, _json(summary), now]}]
        store.batch(statements)
    # A retry repairs an interrupted case import; each case is deterministic and
    # immutable rather than treating an existing summary as a complete import.
    statements = []
    for case in scaffold["cases"]:
        scoped_id = content_sha256([identity, case["id"]])[:32]
        case = {**case, "id": scoped_id, "evaluation_id": identity}
        case["rubric"] = rubrics[case["lens_id"]]
        case["criterion"] = next(criterion for criterion in case["rubric"]["pack"]["criteria"] if criterion["id"] == case["criterion_id"])
        blocks = store.one("SELECT blocks_key FROM documents WHERE project_id=? AND id=?", [project_id, case["source"]["id"]])
        if not blocks or not blocks.get("blocks_key"):
            raise ValueError("Review cases require immutable source blocks")
        case["extracted_blocks_sha256"] = content_sha256(store.get_json(blocks["blocks_key"]))
        # Case hash freezes the exact form consumed by the reviewer.
        case["case_sha256"] = content_sha256({key: value for key, value in case.items() if key != "case_sha256"})
        statements.append({"sql": "INSERT INTO v4_evaluation_cases(id,evaluation_id,project_id,document_id,story_group,split,payload_json) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                           "params": [case["id"], identity, project_id, case["source"]["id"], case["story_group"], case["split"], _json(case)]})
    for start in range(0, len(statements), 25):
        store.batch(statements[start:start + 25])
    return {**_require(store, project_id, identity), "status": "reference_pending"}


def export_review_form(store, project_id, evaluation_id, *, split="development", include_model=False):
    ensure_schema(store)
    evaluation = _require(store, project_id, evaluation_id)
    if split not in {"development", "calibration", "heldout"} or type(include_model) is not bool:
        raise ValueError("Choose a valid review split and explicit Boolean visibility")
    if split == "heldout" and include_model:
        raise ValueError("Locked heldout review forms cannot expose model decisions")
    if evaluation.get("kind") == "independent_domain_panel" and split == "calibration" and include_model:
        pending = store.one("SELECT c.id FROM v4_evaluation_cases c LEFT JOIN v4_reference_adjudications a ON a.case_id=c.id AND a.evaluation_id=c.evaluation_id WHERE c.project_id=? AND c.evaluation_id=? AND c.split='calibration' AND a.id IS NULL LIMIT 1", [project_id, evaluation_id])
        if pending:
            raise ValueError("Locked calibration forms cannot expose model decisions before independent adjudication is complete")
    cases = []
    for row in store.query("SELECT payload_json FROM v4_evaluation_cases WHERE project_id=? AND evaluation_id=? AND split=? ORDER BY id", [project_id, evaluation_id, split]):
        case = json.loads(row["payload_json"])
        assessment = case.pop("model_assessment")
        # Source text, context and criterion are allowed; model-selected outcome,
        # explanation, role, stance and alternative remain hidden in first pass.
        blocks_row = store.one("SELECT blocks_key FROM documents WHERE project_id=? AND id=?", [project_id, case["source"]["id"]])
        blocks = store.get_json(blocks_row["blocks_key"]) if blocks_row and blocks_row.get("blocks_key") else []
        if content_sha256(blocks) != case.get("extracted_blocks_sha256"):
            raise ValueError("Review source blocks changed after the evaluation was frozen")
        run = store.one("SELECT config_json FROM runs WHERE id=? AND project_id=?", [evaluation["run_id"], project_id])
        config = json.loads(run["config_json"]) if run else {}
        from .contextual import build_units
        document = {**case["source"], "blocks": blocks,
                    "source_version_id": assessment.get("source_version_id") or case["source"]["id"]}
        units = build_units(document, max_unit_chars=config.get("unit_max_chars", 1200), context_sentences=config.get("context_sentences", 2),
                            **({"context_policy": config["context_policy"]} if config.get("context_policy") else {}))
        unit = next((unit for unit in units if unit["id"] == case["unit_id"]), None)
        if unit is None:
            raise ValueError("Review case no longer resolves to its frozen source unit")
        case["scored_unit"] = unit
        if "rubric" not in case or "criterion" not in case:
            case["rubric"] = {"origin": "not_recorded", "notice": "Create a new evaluation with explicitly frozen review instructions."}
        case["reference_labels"] = {key: None for key in sorted(ANNOTATION_FIELDS | EXTENDED_ANNOTATION_FIELDS | PERCEPTION_ANNOTATION_FIELDS)}
        if include_model:
            case["model_assessment"] = assessment
        cases.append(case)
    return {"schema_version": "0.4", "evaluation_id": evaluation_id, "scaffold_sha256": evaluation["scaffold_sha256"],
            "split": split, "model_blinded": not include_model, "cases": cases,
            "annotation_contract": annotation_contract(),
            "review_template": {"evaluation_id": evaluation_id, "rows": [
                {"case_id": case["id"], "case_sha256": case["case_sha256"], "source_hash": case["source_hash"],
                 "independent_blinded_review": False, "labels": case["reference_labels"]} for case in cases]},
            "reviewer_declaration_required": "I reviewed the source independently before seeing model decisions or another review."}


def _labels(value):
    if not isinstance(value, dict) or set(value) - (ANNOTATION_FIELDS | EXTENDED_ANNOTATION_FIELDS | PERCEPTION_ANNOTATION_FIELDS):
        raise ValueError("Reference labels contain unknown fields")
    for key, allowed in (("result", RESULTS), ("attribution", ATTRIBUTIONS), ("speaker_stance", STANCES), ("document_stance", STANCES),
                         ("evidence_adequacy", {"adequate", "inadequate", "uncertain"}),
                         ("criterion_application", {"supported", "unsupported", "uncertain"})):
        if value.get(key) not in allowed:
            raise ValueError(f"A valid explicit {key} reference label is required")
    if type(value.get("context_sufficient")) is not bool:
        raise ValueError("Context sufficiency must be an explicit Boolean")
    if not isinstance(value.get("acceptable_alternatives", []), list) or any(item not in RESULTS for item in value.get("acceptable_alternatives", [])):
        raise ValueError("Acceptable alternatives must use the explicit outcome enum")
    if not isinstance(value.get("note", ""), str) or len(value.get("note", "")) > 8000:
        raise ValueError("Reference note exceeds its bound")
    for key, maximum in (("target", 2000), ("proposition", 4000), ("perspective", 2000)):
        if value.get(key) is not None and (not isinstance(value[key], str) or len(value[key]) > maximum):
            raise ValueError(f"Reference {key} must be bounded text or null")
    perception = value.get("reader_perception")
    if perception is not None:
        if not isinstance(perception, dict) or set(perception) != {"scale_version", "reader_context", "ratings"}:
            raise ValueError("Reader perception requires a scale version, explicit reading context and ordinal ratings")
        if perception["scale_version"] != "reader-perception-ordinal-1.0":
            raise ValueError("Unsupported reader perception scale")
        if not isinstance(perception["reader_context"], str) or not perception["reader_context"].strip() or len(perception["reader_context"]) > 2000:
            raise ValueError("Reader perception needs bounded audience/goal/relationship context")
        ratings = perception["ratings"]
        if not isinstance(ratings, dict) or not ratings or set(ratings) - PERCEPTION_DIMENSIONS:
            raise ValueError("Reader perception ratings must use declared dimensions")
        if any(type(rating) is not int or not 1 <= rating <= 5 for rating in ratings.values()):
            raise ValueError("Reader perception ratings must be ordinal integers from 1 to 5")
    if value.get("communicative_act") is not None and (not isinstance(value["communicative_act"], str) or value["communicative_act"] not in COMMUNICATIVE_ACTS):
        raise ValueError("Reference communicative act must use the declared enum")
    reasons = value.get("missing_context_reasons")
    if reasons is not None and (not isinstance(reasons, list) or len(reasons) > len(MISSING_CONTEXT_REASONS)
                                or any(not isinstance(item, str) or item not in MISSING_CONTEXT_REASONS for item in reasons)
                                or len(set(reasons)) != len(reasons)):
        raise ValueError("Missing-context reasons must be a bounded list of distinct declared reasons")
    spans = value.get("evidence_spans")
    if spans is not None:
        if not isinstance(spans, list) or len(spans) > 32:
            raise ValueError("Reference evidence spans must contain at most 32 entries")
        for span in spans:
            if not isinstance(span, dict) or set(span) != {"document_id", "block_id", "start", "end", "quote"}:
                raise ValueError("Reference evidence spans require explicit document, block, offsets and quotation")
            if any(not isinstance(span[key], str) or not span[key] or len(span[key]) > 200 for key in ("document_id", "block_id")):
                raise ValueError("Reference evidence identifiers must be bounded strings")
            if type(span["start"]) is not int or type(span["end"]) is not int or not 0 <= span["start"] < span["end"]:
                raise ValueError("Reference evidence offsets must be explicit increasing integers")
            if not isinstance(span["quote"], str) or not span["quote"] or len(span["quote"]) > 16000:
                raise ValueError("Reference evidence quotation must be nonempty bounded text")
    chain = value.get("reporting_chain")
    if chain is not None:
        if not isinstance(chain, list) or len(chain) > 12:
            raise ValueError("Reference reporting chain must contain at most 12 entries")
        for node in chain:
            if not isinstance(node, dict) or set(node) != {"speaker", "role", "evidence_span_indices"}:
                raise ValueError("Reporting-chain entries require speaker, role and evidence links")
            if not isinstance(node["speaker"], str) or not node["speaker"].strip() or len(node["speaker"]) > 300 or not isinstance(node["role"], str) or node["role"] not in REPORTING_ROLES:
                raise ValueError("Reporting-chain speaker and role are invalid")
            links = node["evidence_span_indices"]
            if not isinstance(links, list) or len(links) > 32 or any(type(index) is not int or index < 0 or index >= len(spans or []) for index in links):
                raise ValueError("Reporting-chain links must resolve to independently entered evidence spans")
            if not links and node["role"] != "unknown":
                raise ValueError("An identified reporting-chain role requires a source evidence link")
    return json.loads(_json(value))


def _validate_annotation_context(store, project_id, evaluation, case, labels):
    """Validate independent annotations against the exact frozen, visible case."""
    if not any(labels.get(key) is not None for key in EXTENDED_ANNOTATION_FIELDS | PERCEPTION_ANNOTATION_FIELDS):
        return
    source_id = case["source"]["id"]
    source = store.one("SELECT sha256,blocks_key FROM documents WHERE id=? AND project_id=?", [source_id, project_id])
    if not source or source["sha256"] != case["source_hash"] or not source.get("blocks_key"):
        raise ValueError("Reference annotation source is outside this project or its frozen version changed")
    blocks = store.get_json(source["blocks_key"])
    if content_sha256(blocks) != case.get("extracted_blocks_sha256"):
        raise ValueError("Reference source blocks changed after the evaluation was frozen")
    run = store.one("SELECT config_json FROM runs WHERE id=? AND project_id=?", [evaluation["run_id"], project_id])
    if not run:
        raise ValueError("Reference annotation run no longer exists in this project")
    config = json.loads(run["config_json"])
    from .contextual import build_units
    document = {**case["source"], "blocks": blocks,
                "source_version_id": case["model_assessment"].get("source_version_id") or source_id}
    unit = next((item for item in build_units(document, max_unit_chars=config.get("unit_max_chars", 1200),
                                             context_sentences=config.get("context_sentences", 2),
                                             **({"context_policy": config["context_policy"]} if config.get("context_policy") else {})) if item["id"] == case["unit_id"]), None)
    if unit is None:
        raise ValueError("Reference annotation no longer resolves to the frozen scored unit")
    visible = defaultdict(list)
    for sentence in [*unit.get("structural_context", []), *unit.get("context_before", []), *unit["sentences"], *unit.get("context_after", [])]:
        visible[sentence["block_id"]].append((sentence["start"], sentence["end"]))
    merged = {}
    for block_id, intervals in visible.items():
        merged[block_id] = []
        for start, end in sorted(intervals):
            if merged[block_id] and start <= merged[block_id][-1][1]:
                merged[block_id][-1] = (merged[block_id][-1][0], max(end, merged[block_id][-1][1]))
            else:
                merged[block_id].append((start, end))
    block_text = {block["id"]: block["text"] for block in blocks}
    for span in labels.get("evidence_spans") or []:
        if span["document_id"] != source_id or span["block_id"] not in block_text:
            raise ValueError("Reference evidence belongs to a different source or block")
        if not any(start <= span["start"] < span["end"] <= end for start, end in merged.get(span["block_id"], [])):
            raise ValueError("Reference evidence is outside the displayed frozen case context")
        if block_text[span["block_id"]][span["start"]:span["end"]] != span["quote"]:
            raise ValueError("Reference quotation does not match the exact frozen Unicode source")


def import_reviews(store, project_id, evaluation_id, rows, *, reviewer_id, actor_kind="human"):
    ensure_schema(store)
    evaluation = _require(store, project_id, evaluation_id)
    if evaluation.get("kind") == "independent_domain_panel" and not evaluation.get("preregistration"):
        raise ValueError("A domain panel must be preregistered before any initial review")
    if actor_kind != "human" or not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ValueError("Independent references require an identified human reviewer")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError("Import 1–1000 reference reviews")
    statements, duplicate = [], 0
    seen = set()
    for value in rows:
        if not isinstance(value, dict) or not isinstance(value.get("case_id"), str) or not value["case_id"] or len(value["case_id"]) > 200:
            raise ValueError("Reference review rows require a bounded case identifier")
        identity = value.get("case_id")
        if identity in seen:
            raise ValueError("Duplicate case in review import")
        seen.add(identity)
        row = store.one("SELECT payload_json FROM v4_evaluation_cases WHERE id=? AND project_id=? AND evaluation_id=?", [identity, project_id, evaluation_id])
        if not row:
            raise ValueError("Reference case is outside this evaluation")
        case = json.loads(row["payload_json"])
        if value.get("case_sha256") != case["case_sha256"] or value.get("source_hash") != case["source_hash"]:
            raise ValueError("Reference case/source hash changed")
        if value.get("independent_blinded_review") is not True:
            raise ValueError("A blinded independent-review declaration is required")
        validated_labels = _labels(value.get("labels"))
        _validate_annotation_context(store, project_id, evaluation, case, validated_labels)
        payload = {"case_id": identity, "case_sha256": case["case_sha256"], "source_hash": case["source_hash"],
                   "labels": validated_labels, "independent_blinded_review": True,
                   "reviewer_id": reviewer_id, "origin": "human_reference_review"}
        existing = store.one("SELECT payload_json FROM v4_reference_reviews WHERE evaluation_id=? AND case_id=? AND reviewer_id=?", [evaluation_id, identity, reviewer_id])
        if existing:
            if json.loads(existing["payload_json"]) != payload:
                raise ValueError("A prior independent review is immutable; adjudicate separately")
            duplicate += 1
            continue
        statements.append({"sql": "INSERT INTO v4_reference_reviews(id,evaluation_id,case_id,project_id,reviewer_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
            "params": [uuid.uuid4().hex, evaluation_id, identity, project_id, reviewer_id, _json(payload), utcnow()]})
    for start in range(0, len(statements), 25):
        store.batch(statements[start:start + 25])
    return {"imported": len(statements), "idempotent_duplicates": duplicate, "quality_status": "reference_pending",
            "notice": "Distinct identities and declarations are recorded; real-world independence is not automatically verified."}


def adjudicate_case(store, project_id, evaluation_id, case_id, labels, *, actor_id, actor_kind="human", note=""):
    ensure_schema(store)
    evaluation = _require(store, project_id, evaluation_id)
    if actor_kind != "human" or not actor_id:
        raise ValueError("An identified human adjudicator is required")
    reviews = store.query("SELECT reviewer_id,payload_json FROM v4_reference_reviews WHERE evaluation_id=? AND case_id=? AND project_id=?", [evaluation_id, case_id, project_id])
    if len({row["reviewer_id"] for row in reviews}) < 2:
        raise ValueError("Two distinct independent reviews are required before adjudication")
    if evaluation.get("preregistration") and actor_id in {row["reviewer_id"] for row in reviews}:
        raise ValueError("The preregistered protocol requires a separate adjudicator who did not enter an initial review")
    row = store.one("SELECT payload_json FROM v4_evaluation_cases WHERE id=? AND project_id=? AND evaluation_id=?", [case_id, project_id, evaluation_id])
    if not row:
        raise ValueError("Reference case is outside this evaluation")
    validated_labels = _labels(labels)
    _validate_annotation_context(store, project_id, evaluation, json.loads(row["payload_json"]), validated_labels)
    payload = {"labels": validated_labels, "note": str(note)[:8000], "reviewer_ids": sorted(row["reviewer_id"] for row in reviews),
               "review_sha256": sorted(content_sha256(json.loads(row["payload_json"])) for row in reviews),
               "origin": "human_reference_adjudication"}
    existing = store.one("SELECT payload_json FROM v4_reference_adjudications WHERE evaluation_id=? AND case_id=? AND project_id=?", [evaluation_id, case_id, project_id])
    if existing:
        if json.loads(existing["payload_json"]) != payload:
            raise ValueError("Reference adjudication is immutable; create a new versioned evaluation")
        return {"idempotent": True, **payload}
    store.execute("INSERT INTO v4_reference_adjudications(id,evaluation_id,case_id,project_id,actor_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                  [uuid.uuid4().hex, evaluation_id, case_id, project_id, actor_id, _json(payload), utcnow()])
    return payload


def reference_report(store, project_id, evaluation_id):
    ensure_schema(store)
    evaluation = _require(store, project_id, evaluation_id)
    rows = store.query("SELECT c.id,c.split,c.story_group,c.payload_json,a.actor_id AS adjudicator_id,a.payload_json AS reference_json FROM v4_evaluation_cases c LEFT JOIN v4_reference_adjudications a ON a.case_id=c.id AND a.evaluation_id=c.evaluation_id WHERE c.project_id=? AND c.evaluation_id=? ORDER BY c.id", [project_id, evaluation_id])
    reviewed = [row for row in rows if row.get("reference_json")]
    heldout = [row for row in rows if row["split"] == "heldout"]
    heldout_complete = bool(heldout) and all(row.get("reference_json") for row in heldout)
    measurable = [row for row in reviewed if row["split"] != "heldout" or heldout_complete]
    counts, reference_hashes = {}, []
    for field in ("result", "attribution", "speaker_stance", "document_stance"):
        pairs = [(json.loads(row["payload_json"])["model_assessment"].get(field), json.loads(row["reference_json"])["labels"][field]) for row in measurable]
        counts[field] = {"compared": len(pairs), "exact_matches": sum(a == b for a, b in pairs),
                         "agreement_fraction": sum(a == b for a, b in pairs) / len(pairs) if pairs else None}
    for row in reviewed:
        reference_hashes.append([row["id"], content_sha256(json.loads(row["reference_json"]))])
    structured = {field: sum(json.loads(row["reference_json"])["labels"].get(field) is not None for row in reviewed)
                  for field in sorted(EXTENDED_ANNOTATION_FIELDS)}
    from .evaluation_metrics import criterion_metrics
    initial = defaultdict(list)
    for item in store.query("SELECT case_id,reviewer_id,payload_json FROM v4_reference_reviews WHERE project_id=? AND evaluation_id=? ORDER BY reviewer_id", [project_id, evaluation_id]):
        initial[item["case_id"]].append({"reviewer_id": item["reviewer_id"], "labels": json.loads(item["payload_json"])["labels"]})
    protocol = (evaluation.get("preregistration") or {}).get("protocol")
    records = []
    for row in rows:
        case = json.loads(row["payload_json"])
        references = initial[row["id"]]
        cluster_key = (protocol or {}).get("cluster_key", "story_group")
        metadata = case["source"].get("metadata") or {}
        cluster = (case.get("cluster_ids") or {}).get(cluster_key) or (case["story_group"] if cluster_key == "story_group" else metadata.get(cluster_key))
        records.append({"id": row["id"], "split": row["split"], "cluster": cluster,
                        "lens_id": case["lens_id"], "criterion_id": case["criterion_id"],
                        "prediction": case["model_assessment"],
                        "reference": json.loads(row["reference_json"])["labels"] if row.get("reference_json") else None,
                        "reviews": references,
                        "separate_adjudicator": bool(row.get("adjudicator_id")) and row["adjudicator_id"] not in {item["reviewer_id"] for item in references}})
    development_metrics = criterion_metrics([item for item in records if item["split"] == "development"])
    calibration_metrics = criterion_metrics([item for item in records if item["split"] == "calibration"])
    heldout_metrics = criterion_metrics([item for item in records if item["split"] == "heldout"], protocol=protocol,
                                        heldout_complete=True) if heldout_complete else None
    perceptions = defaultdict(list)
    for record in records:
        if record["split"] == "heldout" and not heldout_complete:
            continue
        for review in record["reviews"]:
            rating = review["labels"].get("reader_perception")
            if rating:
                perceptions[(record["id"], rating["scale_version"], rating["reader_context"])].append({"reviewer_id": review["reviewer_id"], "ratings": rating["ratings"]})
    perception_rows = []
    for (case_id, scale, context), ratings in sorted(perceptions.items()):
        dimensions = {}
        for dimension in sorted(PERCEPTION_DIMENSIONS):
            observed = [entry["ratings"][dimension] for entry in ratings if dimension in entry["ratings"]]
            dimensions[dimension] = {"reader_count": len(observed), "ordinal_distribution": {str(point): observed.count(point) for point in range(1, 6)},
                                     "minimum_reader_count_met": len(observed) >= 3}
        perception_rows.append({"case_id": case_id, "scale_version": scale, "reader_context": context,
                                "distinct_authenticated_reader_ids": len({entry["reviewer_id"] for entry in ratings}), "dimensions": dimensions})
    gates = [item["release_gate"] for item in (heldout_metrics or {}).get("criterion_metrics", [])]
    numerical_pass = bool(gates) and all(item["supported_finding_thresholds_met"] for item in gates)
    return {"evaluation_id": evaluation_id, "scaffold_sha256": evaluation["scaffold_sha256"],
            "quality_status": "reference_reviewed" if reviewed and len(reviewed) == len(rows) else "review_in_progress" if reviewed else "reference_pending",
            "total_cases": len(rows), "adjudicated_cases": len(reviewed),
            "reference_set_sha256": content_sha256(reference_hashes) if reference_hashes else None,
            "independent_reference_labels": bool(reviewed), "interpretation_accuracy": None,
            "heldout_metrics_locked": not heldout_complete,
            "adjudicated_by_split": {split: sum(row["split"] == split for row in reviewed) for split in ("development", "calibration", "heldout")},
            "diagnostic_label_agreement": counts,
            "reference_metrics": {"development": development_metrics, "calibration": calibration_metrics, "heldout": heldout_metrics,
                                  "heldout_lock": "No heldout reference metrics are exposed until every heldout case is adjudicated."},
            "reader_perception": {"cases": perception_rows, "status": "not_measured" if not perception_rows else "context_specific_reader_distributions",
                                  "actual_emotional_state_inferred": False,
                                  "notice": "Ordinal reader perceptions remain separate by reading context. Distinct authenticated IDs do not prove distinct people, qualifications or representative audiences."},
            "preregistration": evaluation.get("preregistration"),
            "release_readiness": {"status": "quantitative_thresholds_met_external_review_required" if numerical_pass else "reference_pending" if not reviewed else "independent_release_gates_not_met",
                                  "quantitative_supported_finding_gates_met": numerical_pass,
                                  "automated_verdicts_enabled": False, "expert_review_required": True,
                                  "notice": "Numerical thresholds never verify reviewer qualifications, sampling rights, independence or customer-delivery sign-off."},
            "structured_annotation_progress": {"contract_version": "0.5", "adjudicated_cases": len(reviewed),
                                                "cases_with_field_annotated": structured,
                                                "notice": "Field completion is not annotation quality, independence, calibration or population accuracy."},
            "notice": "Selected-case agreement is diagnostic, not universal accuracy. The collection design is operator-attested and needs external audit; initial disagreements remain visible." if evaluation.get("kind") == "independent_domain_panel" else "Selected-case agreement with human adjudication is diagnostic, not population accuracy. Sampling is enriched; original reviewer disagreements are retained."}


def preregister_protocol(store, project_id, evaluation_id, value, *, actor_id):
    """Freeze the intended inferential design before any initial label is entered.

    A protocol cannot turn the existing outcome-balanced scaffold into a
    probability sample. Its provenance stays diagnostic even after review.
    """
    ensure_schema(store)
    with store.transaction(write=True):
        evaluation = _require(store, project_id, evaluation_id)
        allowed = {"profile_id", "domain", "language", "sampling_design", "population_description", "confidence_method",
                   "cluster_key", "bootstrap_replicates", "seed", "sampling_frame_sha256", "reviewer_protocol_sha256"}
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError("Protocol contains unsupported fields")
        if not isinstance(value.get("profile_id"), str) or value["profile_id"] not in {"company", "public", "personal"}:
            raise ValueError("Protocol requires a declared Company, Public or Personal profile")
        for field, maximum in (("domain", 500), ("language", 100), ("population_description", 4000)):
            if not isinstance(value.get(field), str) or not value[field].strip() or len(value[field]) > maximum:
                raise ValueError(f"Protocol {field} requires bounded explicit text")
        for field in ("sampling_frame_sha256", "reviewer_protocol_sha256"):
            if not isinstance(value.get(field), str) or not re.fullmatch(r"[a-f0-9]{64}", value[field]):
                raise ValueError(f"Protocol requires an exact {field}")
        if not isinstance(value.get("sampling_design"), str) or value["sampling_design"] not in {"enriched_diagnostic", "probability_sample"}:
            raise ValueError("Declare the sampling design")
        if value["sampling_design"] == "probability_sample" and not evaluation.get("sampling", {}).get("representative_population_sample"):
            raise ValueError("An outcome-balanced diagnostic scaffold cannot be relabeled as a probability sample")
        if evaluation.get("kind") == "independent_domain_panel":
            if (value["profile_id"] != evaluation["profile_id"] or value["domain"] != evaluation["domain"]
                    or value["sampling_frame_sha256"] != evaluation["sampling"]["sampling_frame_sha256"]
                    or value.get("cluster_key") != evaluation["sampling"]["inference_cluster_key"]):
                raise ValueError("Preregistered profile, domain, frame hash and cluster key must match the imported panel")
        if not isinstance(value.get("confidence_method"), str) or value["confidence_method"] not in {"exact_binomial", "cluster_bootstrap"}:
            raise ValueError("Preregister exact_binomial or cluster_bootstrap")
        if not isinstance(value.get("cluster_key"), str) or value["cluster_key"] not in {"story_group", "organization_group", "source_family_group"}:
            raise ValueError("Preregister an independent story, organization or source-family cluster key")
        repetitions = value.get("bootstrap_replicates", 1999)
        seed = value.get("seed", 20261118)
        if type(repetitions) is not int or not 999 <= repetitions <= 4999 or type(seed) is not int or not 0 <= seed < 2 ** 63:
            raise ValueError("Preregister 999–4999 bootstrap replicates and a bounded integer seed")
        protocol = {**copy.deepcopy(value), "bootstrap_replicates": repetitions, "seed": seed,
                    "version": "doxa-reference-protocol-0.6", "gate_policy_version": "doxa-reference-gates-0.6"}
        existing = evaluation.get("preregistration")
        if existing:
            if existing["protocol"] != protocol:
                raise ValueError("Preregistration is immutable; create a separate evaluation")
            return existing
        if store.one("SELECT id FROM v4_reference_reviews WHERE project_id=? AND evaluation_id=? LIMIT 1", [project_id, evaluation_id]):
            raise ValueError("Preregistration must precede every initial reference review")
        record = {"protocol": protocol, "protocol_sha256": content_sha256(protocol), "registered_by": actor_id,
                  "registered_at": utcnow(), "scaffold_sha256": evaluation["scaffold_sha256"],
                  "external_independence_verified": False,
                  "notice": "Registration records the plan and authenticated actor; it does not verify external reviewer qualifications or sampling rights."}
        evaluation["preregistration"] = record
        store.execute("UPDATE v4_evaluations SET payload_json=? WHERE id=? AND project_id=?", [_json(evaluation), evaluation_id, project_id])
        return record


def evaluation_overview(store, project_id):
    ensure_schema(store)
    return {"evaluations": [json.loads(row["payload_json"]) for row in store.query("SELECT payload_json FROM v4_evaluations WHERE project_id=? ORDER BY created_at,id", [project_id])],
            "default_quality_status": "reference_pending", "fabricated_reference_labels": False}


def locked_reference_source(store, project_id, document_id, story_group):
    ensure_schema(store)
    return bool(store.one("SELECT id FROM v4_evaluation_cases WHERE project_id=? AND split IN ('heldout','calibration') AND (document_id=? OR story_group=?) LIMIT 1", [project_id, document_id, story_group]))
