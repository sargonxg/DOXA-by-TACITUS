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
    if split not in {"development", "heldout"} or type(include_model) is not bool:
        raise ValueError("Choose a valid review split and explicit Boolean visibility")
    if split == "heldout" and include_model:
        raise ValueError("Locked heldout review forms cannot expose model decisions")
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
        units = build_units(document, max_unit_chars=config.get("unit_max_chars", 1200), context_sentences=config.get("context_sentences", 2))
        unit = next((unit for unit in units if unit["id"] == case["unit_id"]), None)
        if unit is None:
            raise ValueError("Review case no longer resolves to its frozen source unit")
        case["scored_unit"] = unit
        if "rubric" not in case or "criterion" not in case:
            case["rubric"] = {"origin": "not_recorded", "notice": "Create a new evaluation with explicitly frozen review instructions."}
        case["reference_labels"] = {key: None for key in sorted(ANNOTATION_FIELDS)}
        if include_model:
            case["model_assessment"] = assessment
        cases.append(case)
    return {"schema_version": "0.4", "evaluation_id": evaluation_id, "scaffold_sha256": evaluation["scaffold_sha256"],
            "split": split, "model_blinded": not include_model, "cases": cases,
            "review_template": {"evaluation_id": evaluation_id, "rows": [
                {"case_id": case["id"], "case_sha256": case["case_sha256"], "source_hash": case["source_hash"],
                 "independent_blinded_review": False, "labels": case["reference_labels"]} for case in cases]},
            "reviewer_declaration_required": "I reviewed the source independently before seeing model decisions or another review."}


def _labels(value):
    if not isinstance(value, dict) or set(value) - ANNOTATION_FIELDS:
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
    return json.loads(_json(value))


def import_reviews(store, project_id, evaluation_id, rows, *, reviewer_id, actor_kind="human"):
    ensure_schema(store)
    _require(store, project_id, evaluation_id)
    if actor_kind != "human" or not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ValueError("Independent references require an identified human reviewer")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError("Import 1–1000 reference reviews")
    statements, duplicate = [], 0
    seen = set()
    for value in rows:
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
        payload = {"case_id": identity, "case_sha256": case["case_sha256"], "source_hash": case["source_hash"],
                   "labels": _labels(value.get("labels")), "independent_blinded_review": True,
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
    _require(store, project_id, evaluation_id)
    if actor_kind != "human" or not actor_id:
        raise ValueError("An identified human adjudicator is required")
    reviews = store.query("SELECT reviewer_id,payload_json FROM v4_reference_reviews WHERE evaluation_id=? AND case_id=? AND project_id=?", [evaluation_id, case_id, project_id])
    if len({row["reviewer_id"] for row in reviews}) < 2:
        raise ValueError("Two distinct independent reviews are required before adjudication")
    payload = {"labels": _labels(labels), "note": str(note)[:8000], "reviewer_ids": sorted(row["reviewer_id"] for row in reviews),
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
    rows = store.query("SELECT c.id,c.split,c.payload_json,a.payload_json AS reference_json FROM v4_evaluation_cases c LEFT JOIN v4_reference_adjudications a ON a.case_id=c.id AND a.evaluation_id=c.evaluation_id WHERE c.project_id=? AND c.evaluation_id=? ORDER BY c.id", [project_id, evaluation_id])
    reviewed = [row for row in rows if row.get("reference_json")]
    heldout = [row for row in rows if row["split"] == "heldout"]
    heldout_complete = bool(heldout) and all(row.get("reference_json") for row in heldout)
    measurable = [row for row in reviewed if row["split"] == "development" or heldout_complete]
    counts, reference_hashes = {}, []
    for field in ("result", "attribution", "speaker_stance", "document_stance"):
        pairs = [(json.loads(row["payload_json"])["model_assessment"].get(field), json.loads(row["reference_json"])["labels"][field]) for row in measurable]
        counts[field] = {"compared": len(pairs), "exact_matches": sum(a == b for a, b in pairs),
                         "agreement_fraction": sum(a == b for a, b in pairs) / len(pairs) if pairs else None}
    for row in reviewed:
        reference_hashes.append([row["id"], content_sha256(json.loads(row["reference_json"]))])
    return {"evaluation_id": evaluation_id, "scaffold_sha256": evaluation["scaffold_sha256"],
            "quality_status": "reference_reviewed" if reviewed and len(reviewed) == len(rows) else "review_in_progress" if reviewed else "reference_pending",
            "total_cases": len(rows), "adjudicated_cases": len(reviewed),
            "reference_set_sha256": content_sha256(reference_hashes) if reference_hashes else None,
            "independent_reference_labels": bool(reviewed), "interpretation_accuracy": None,
            "heldout_metrics_locked": not heldout_complete,
            "adjudicated_by_split": {split: sum(row["split"] == split for row in reviewed) for split in ("development", "heldout")},
            "diagnostic_label_agreement": counts,
            "notice": "Selected-case agreement with human adjudication is diagnostic, not population accuracy. Sampling is enriched; original reviewer disagreements are retained."}


def evaluation_overview(store, project_id):
    ensure_schema(store)
    return {"evaluations": [json.loads(row["payload_json"]) for row in store.query("SELECT payload_json FROM v4_evaluations WHERE project_id=? ORDER BY created_at,id", [project_id])],
            "default_quality_status": "reference_pending", "fabricated_reference_labels": False}


def locked_reference_source(store, project_id, document_id, story_group):
    ensure_schema(store)
    return bool(store.one("SELECT id FROM v4_evaluation_cases WHERE project_id=? AND split='heldout' AND (document_id=? OR story_group=?) LIMIT 1", [project_id, document_id, story_group]))
