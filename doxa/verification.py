"""Deterministic independent-model review selection and descriptive comparison.

This module does not call models, alter findings, settle budgets or adjudicate
interpretations. A verifier is a second opinion, never a reference truth label.
"""
from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
import hashlib
import json
import math

from .store import Store

SELECTION_VERSION = "indicator-plus-stratified-other-3.0"
MAX_UNITS = 250_000
MAX_DISAGREEMENTS = 200
COMPARE_FIELDS = ("result", "attribution", "speaker_stance", "document_stance", "speech_role", "hypothetical")


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _run(store: Store, project_id: str, run_id: str) -> dict:
    row = store.one("SELECT * FROM runs WHERE id=? AND project_id=?", [run_id, project_id])
    if not row:
        raise ValueError("Run does not belong to this project")
    row["config"] = json.loads(row["config_json"] or "{}")
    row["model_profile"] = json.loads(row.get("model_profile_json") or "{}")
    version = str(row["config"].get("methodology_version", row["config"].get("schema_version", "")))
    if not version.startswith("3") or row["config"].get("provider") == "lexical":
        raise ValueError("Independent unit verification requires a contextual v3 model run")
    return row


def _profile(run: dict) -> dict:
    config = run["config"]
    return {"run_id": run["id"], "status": run["status"], "provider": config.get("provider"),
            "model": config.get("model"), "model_profile": run["model_profile"],
            "config_sha256": _hash(config), "methodology_version": config.get("methodology_version", config.get("schema_version")),
            "unit_max_chars": config.get("unit_max_chars"),
            "source_manifest_key": config.get("source_manifest_key"),
            "lens_ids": config.get("lens_ids", []), "persona": config.get("persona"),
            "inquiry": config.get("inquiry"), "language": config.get("language")}


def select_verification_units(store: Store, project_id: str, run_id: str,
                              negative_fraction: float = 0.05, seed: int = 20261116) -> dict:
    """Select every flagged indicator unit plus a document-stratified other sample.

The other sample uses a global ceil(fraction*N) target and proportional largest
remainders. Ceil per document would turn a 5% sample into 50–100% for short news.
"""
    if (isinstance(negative_fraction, bool) or not isinstance(negative_fraction, (int, float))
            or not 0 <= negative_fraction <= 1 or not math.isfinite(negative_fraction)):
        raise ValueError("negative_fraction must be a finite number between zero and one")
    if not isinstance(seed, int) or isinstance(seed, bool) or not -(2 ** 63) <= seed < 2 ** 63:
        raise ValueError("seed must be a signed 64-bit integer")
    run = _run(store, project_id, run_id)
    ledger = store.query("SELECT document_id,unit_id,status FROM v3_unit_statuses WHERE run_id=? ORDER BY document_id,unit_id LIMIT 250001", [run_id])
    if not ledger:
        raise ValueError("Primary run has no contextual source-unit ledger")
    if len(ledger) > MAX_UNITS:
        raise ValueError("Verification selection exceeds the 250000-unit baseline cap")
    all_units = {(row["document_id"], row["unit_id"]): row["status"] for row in ledger}
    flagged_rows = store.query("SELECT DISTINCT document_id,unit_id,json_extract(payload_json,'$.result') result FROM v3_assessments WHERE project_id=? AND run_id=? AND COALESCE(json_extract(payload_json,'$.criterion_kind'),criterion_kind)='indicator' AND json_extract(payload_json,'$.result') IN ('meets_criterion','uncertain')", [project_id, run_id])
    flagged = {(row["document_id"], row["unit_id"]) for row in flagged_rows}
    if flagged - all_units.keys():
        raise ValueError("An indicator assessment has no matching frozen source unit")
    groups = defaultdict(list)
    for pair, status in all_units.items():
        if status == "accepted" and pair not in flagged:
            groups[pair[0]].append(pair[1])
    others = sum(map(len, groups.values()))
    fraction = Fraction(str(negative_fraction))
    target = math.ceil(fraction * others)
    allocations = {document_id: math.floor(fraction * len(units)) for document_id, units in groups.items()}
    remaining = target - sum(allocations.values())
    stratum_order = sorted(groups, key=lambda identity: (
        -(fraction * len(groups[identity]) - allocations[identity]),
        _hash([SELECTION_VERSION, seed, run_id, identity])))
    for identity in stratum_order[:remaining]:
        allocations[identity] += 1
    sampled = set()
    for identity, units in groups.items():
        ranked = sorted(units, key=lambda unit_id: _hash([SELECTION_VERSION, seed, run_id, identity, unit_id]))
        sampled.update((identity, unit_id) for unit_id in ranked[:allocations[identity]])
    selected = sorted(flagged | sampled)
    unit_ids = defaultdict(list)
    for identity, unit_id in selected:
        unit_ids[identity].append(unit_id)
    counts = {"primary_units_total": len(all_units),
              "primary_units_accepted": sum(status == "accepted" for status in all_units.values()),
              "primary_units_incomplete": sum(status != "accepted" for status in all_units.values()),
              "indicator_flagged_units": len(flagged),
              "indicator_meets_units": len({(row["document_id"], row["unit_id"]) for row in flagged_rows if row["result"] == "meets_criterion"}),
              "indicator_uncertain_units": len({(row["document_id"], row["unit_id"]) for row in flagged_rows if row["result"] == "uncertain"}),
              "other_accepted_units": others, "sampled_other_units": len(sampled),
              "other_documents_eligible": len(groups), "other_documents_sampled": len({pair[0] for pair in sampled}),
              "selected_units": len(selected), "documents_selected": len(unit_ids),
              "other_sample_fraction_actual": len(sampled) / others if others else 0.0}
    profile = _profile(run)
    result = {"selection_version": SELECTION_VERSION, "primary_run_id": run_id,
              "document_ids": sorted(unit_ids), "unit_ids": dict(unit_ids),
              "selection_counts": counts, "primary_run_profile": profile,
              "negative_fraction": float(negative_fraction), "seed": seed,
              "stratification": "Proportional per-document allocation with seeded largest-remainder ties; seeded within-document selection.",
              "primary_run_complete": run["status"] == "completed" and counts["primary_units_incomplete"] == 0,
              "notice": "Frozen selection snapshot. Other accepted units are not human-validated negatives; model agreement is not accuracy."}
    result["selection_id"] = _hash(result)
    # Preserve the compact spelling requested by integrations while documenting
    # snake_case keys as the canonical public contract.
    result["selectioncounts"] = counts
    result["primaryrunprofile"] = profile
    return result


def _raw_expression(alias: str, field: str) -> str:
    # payload_json preserves original model decisions after reviewed SQL columns
    # are updated. Never compare human corrections as if they were model output.
    return f"json_extract({alias}.payload_json,'$.{field}')"


def _summary(payload: dict) -> dict:
    result = {key: payload.get(key) for key in (*COMPARE_FIELDS, "speaker", "target", "proposition", "certainty")}
    for key in ("title", "explanation", "alternative"):
        value = payload.get(key)
        result[key] = str(value)[:1000] if value is not None else None
    result["evidence_refs"] = [{key: reference.get(key) for key in ("sentence_id", "block_id", "start", "end")}
                               for reference in (payload.get("evidence_refs") or [])[:8] if isinstance(reference, dict)]
    return result


def compare_runs(store: Store, project_id: str, primary_id: str, verifier_id: str) -> dict:
    """Describe paired raw-model differences; preserve both runs and human review."""
    if primary_id == verifier_id:
        raise ValueError("Primary and verifier must be distinct runs")
    primary, verifier = _run(store, project_id, primary_id), _run(store, project_id, verifier_id)
    config = verifier["config"]
    if config.get("verification_of") != primary_id:
        raise ValueError("Verifier must explicitly reference this primary run")
    selection = config.get("verification_selection")
    if not isinstance(selection, dict) or not isinstance(selection.get("unit_ids"), dict):
        raise ValueError("Verifier must freeze its primary source-unit selection")
    if selection.get("primary_run_id") != primary_id:
        raise ValueError("Frozen selection references a different primary run")
    selected = set()
    for identity, units in selection["unit_ids"].items():
        if not isinstance(identity, str) or not isinstance(units, list) or any(not isinstance(unit, str) for unit in units):
            raise ValueError("Invalid frozen unit selection")
        selected.update((identity, unit) for unit in units)
    if len(selected) > MAX_UNITS:
        raise ValueError("Frozen verification selection exceeds the baseline unit cap")
    for run_id in (primary_id, verifier_id):
        duplicate = store.one("SELECT document_id,unit_id,lens_id,criterion_id,COUNT(*) n FROM v3_assessments WHERE project_id=? AND run_id=? GROUP BY document_id,unit_id,lens_id,criterion_id HAVING COUNT(*)>1 LIMIT 1", [project_id, run_id])
        if duplicate:
            raise ValueError("Duplicate assessment keys prevent a reliable paired comparison")
    first_units = store.query("SELECT document_id,unit_id,status FROM v3_unit_statuses WHERE run_id=?", [primary_id])
    second_units = store.query("SELECT document_id,unit_id,status FROM v3_unit_statuses WHERE run_id=?", [verifier_id])
    primary_units = {(row["document_id"], row["unit_id"]): row["status"] for row in first_units}
    verifier_units = {(row["document_id"], row["unit_id"]): row["status"] for row in second_units}
    if selected - primary_units.keys():
        raise ValueError("Frozen verification selection includes units outside the primary ledger")
    if verifier_units.keys() - selected:
        raise ValueError("Verifier processed units outside its frozen selection")
    # A source unit can have accepted rows before the full unit finishes. Include
    # those paired rows, but expose incomplete unit coverage independently.
    unexpected = store.one("SELECT a.document_id,a.unit_id FROM v3_assessments a LEFT JOIN v3_unit_statuses u ON u.run_id=a.run_id AND u.document_id=a.document_id AND u.unit_id=a.unit_id WHERE a.project_id=? AND a.run_id=? AND u.unit_id IS NULL LIMIT 1", [project_id, verifier_id])
    if unexpected:
        raise ValueError("Verifier assessment has no frozen source-unit ledger row")
    source_pairs = {row["id"]: row.get("sha256") for row in primary["config"].get("source_snapshot", [])}
    verifier_sources = {row["id"]: row.get("sha256") for row in config.get("source_snapshot", [])}
    comparability = []
    if any(identity not in source_pairs or source_pairs[identity] != digest for identity, digest in verifier_sources.items()):
        comparability.append("Source content snapshots differ")
    for key in ("unit_max_chars", "language", "persona", "inquiry", "lens_ids", "custom_lens"):
        if primary["config"].get(key) != config.get(key):
            comparability.append(f"Frozen analytical setting differs: {key}")
    if selection.get("primary_run_profile", {}).get("config_sha256") not in {None, _hash(primary["config"])}:
        comparability.append("Primary configuration changed after selection")
    join = " FROM v3_assessments p JOIN v3_assessments v ON p.document_id=v.document_id AND p.unit_id=v.unit_id AND p.lens_id=v.lens_id AND p.criterion_id=v.criterion_id WHERE p.project_id=? AND p.run_id=? AND v.project_id=? AND v.run_id=?"
    params = [project_id, primary_id, project_id, verifier_id]
    differences = {field: f"({_raw_expression('p', field)} IS NOT {_raw_expression('v', field)})" for field in COMPARE_FIELDS}
    any_difference = "(" + " OR ".join(differences.values()) + ")"
    columns = ["COUNT(*) paired_assessments", "COUNT(DISTINCT p.document_id||char(31)||p.unit_id) paired_units"]
    columns.extend(f"COALESCE(SUM({expression}),0) {field}_mismatches" for field, expression in differences.items())
    columns.append(f"COALESCE(SUM({any_difference}),0) any_mismatches")
    counts = store.one("SELECT " + ",".join(columns) + join, params)
    rows = store.query("SELECT p.id primary_assessment_id,v.id verifier_assessment_id,p.document_id,p.unit_id,p.lens_id,p.criterion_id,p.criterion_kind,p.payload_json primary_payload,v.payload_json verifier_payload" + join + " AND " + any_difference + " ORDER BY p.document_id,p.unit_id,p.lens_id,p.criterion_id LIMIT 200", params)
    disagreements = []
    for row in rows:
        left, right = json.loads(row.pop("primary_payload")), json.loads(row.pop("verifier_payload"))
        changed = {field: {"primary": left.get(field), "verifier": right.get(field)}
                   for field in COMPARE_FIELDS if left.get(field) != right.get(field)}
        disagreements.append({**row, "differences": changed, "primary": _summary(left), "verifier": _summary(right)})
    verifier_count = store.one("SELECT COUNT(*) n FROM v3_assessments WHERE project_id=? AND run_id=?", [project_id, verifier_id])["n"]
    expected = store.one("SELECT COUNT(*) n FROM v3_assessments p JOIN v3_unit_statuses vu ON vu.run_id=? AND vu.document_id=p.document_id AND vu.unit_id=p.unit_id WHERE p.project_id=? AND p.run_id=?", [verifier_id, project_id, primary_id])["n"]
    counts["primary_assessments_in_verifier_ledger"] = expected
    counts["unpaired_primary_assessments_in_verifier_ledger"] = expected - counts["paired_assessments"]
    if verifier_count != counts["paired_assessments"]:
        comparability.append("Some verifier criterion decisions have no primary pair")
    complete = (bool(selected) and selected == verifier_units.keys()
                and all(verifier_units[pair] == "accepted" and primary_units[pair] == "accepted" for pair in selected)
                and counts["unpaired_primary_assessments_in_verifier_ledger"] == 0
                and verifier_count == counts["paired_assessments"])
    return {"primary_run_id": primary_id, "verifier_run_id": verifier_id,
            "primary_run_profile": _profile(primary), "verifier_run_profile": _profile(verifier),
            "selection_id": selection.get("selection_id"), "selection_counts": selection.get("selection_counts", {}),
            "selected_units": len(selected), "verifier_units_present": len(verifier_units),
            "verifier_units_accepted": sum(status == "accepted" for status in verifier_units.values()),
            "verifier_units_missing": len(selected - verifier_units.keys()),
            "verifier_assessments": verifier_count, "unpaired_verifier_assessments": verifier_count - counts["paired_assessments"],
            "comparison_available": counts["paired_assessments"] > 0, "comparison_complete_on_selected_units": complete,
            "paired_assessment_fraction_in_verifier_ledger": counts["paired_assessments"] / expected if expected else None,
            "counts": counts, "disagreements": disagreements, "disagreement_limit": MAX_DISAGREEMENTS,
            "disagreements_truncated": counts["any_mismatches"] > len(disagreements),
            "comparable_settings": not comparability, "comparability_notices": comparability,
            "interpretation_accuracy": None, "independent_reference_labels": False, "automatic_adjudication": False,
            "notice": "Literal differences between raw model outputs, not accuracy or error counts. Human corrections remain separate; disagreement requires review. Unselected/incomplete units are not negative evidence."}
