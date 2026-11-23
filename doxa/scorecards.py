"""Source-level measurement over a frozen contextual run and workspace query."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import json
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .lenses import SOURCE_SCOPE
from .method_cards import freeze_method_cards
from .method_config import canonical_json, content_hash
from .store import utcnow
from .workspace import Workspace

VERSION = "doxa-scorecard-1.0"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS v3_scorecards (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS v3_scorecards_project ON v3_scorecards(project_id,created_at);
"""
MIN_COVERAGE = 0.8
OUTCOMES = ("positive", "negative", "uncertain", "inapplicable", "incomplete")
EXPLICIT = {"meets_criterion", "does_not_meet", "uncertain", "not_applicable"}


class ScorecardInput(BaseModel):
    query_id: str = Field(min_length=1, max_length=128)
    comparison_id: str | None = Field(default=None, min_length=1, max_length=128)
    basis: str = "model"


def freeze_index(spec, config):
    cards = config["method_cards_snapshot"]["cards"]
    eligible = {card["id"] for card in cards if card["lens_id"] in {"political-framing", "custom"}
                and card["construct"] == "descriptive_discourse_pattern" and not card["comparator_required"]}
    name = spec.get("name")
    weights = spec.get("weights")
    if set(spec) - {"name", "weights"} or not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
        raise ValueError("Give the project-specific index a name of 1–160 characters.")
    if not isinstance(weights, dict) or len(weights) < 2 or set(weights) - eligible:
        raise ValueError("Weight at least two selected single-source political or custom descriptive criteria.")
    if any(type(weight) is not int or not 1 <= weight <= 99 for weight in weights.values()) or sum(weights.values()) != 100:
        raise ValueError("Index weights must be positive whole percentages totaling 100.")
    return {"version": VERSION, "name": name.strip(), "weights": dict(weights), "minimum_coverage": MIN_COVERAGE}


def _witness(row, payload, blocks, review):
    block_id, start, end, quote = (payload.get(field) for field in ("block_id", "start", "end", "quote"))
    block = blocks.get(block_id) if isinstance(block_id, str) else None
    if (not block or type(start) is not int or type(end) is not int or
            not 0 <= start < end <= len(block["text"]) or block["text"][start:end] != quote):
        return None
    return {"assessment_id": row["id"], "unit_id": row["unit_id"], "block_id": block_id, "start": start, "end": end,
            "quote": quote, "page": block.get("page"), "paragraph": block.get("paragraph"),
            "cue_start_ms": block.get("cue_start_ms"), "cue_end_ms": block.get("cue_end_ms"),
            "explanation": str(payload.get("explanation", ""))[:500],
            "alternative": str(payload.get("alternative", ""))[:350],
            "review_state": row["review_status"], "review_event_id": review.get("review_id")}


def _decision(row, blocks, basis, comparison_required):
    payload, review = json.loads(row["payload_json"]), {}
    result = payload.get("result", row["result"])
    if basis == "review_overlay":
        review = json.loads(row["review_json"] or "{}")
        if row["review_status"] == "rejected":
            result = "uncertain"
        elif review.get("adjudicated") and isinstance(review.get("resolved"), dict):
            payload = review["resolved"]
            result = payload.get("result")
    witness = _witness(row, payload, blocks, review)
    # The single-source adapter supplies no second source to this rule.
    if comparison_required and result in {"meets_criterion", "does_not_meet"}:
        result = "uncertain"
    if result == "meets_criterion" and not witness:
        result = "uncertain"
    return result, witness


def _source_outcome(planned, counts, complete, *, source_scope=False):
    if not complete or planned == 0 or sum(counts.values()) != planned:
        return "incomplete"
    if counts["meets_criterion"]:
        return "positive"
    if counts["uncertain"]:
        return "uncertain"
    if counts["does_not_meet"]:
        return "negative"
    return "uncertain" if source_scope else "inapplicable"


def _summary(cells):
    counts = Counter(cell["status"] for cell in cells)
    determinate = counts["positive"] + counts["negative"]
    eligible = determinate + counts["incomplete"] + counts["uncertain"]
    retained = sum(cell["retained"] for cell in cells)
    planned = sum(cell["planned"] for cell in cells)
    return {**{outcome: counts[outcome] for outcome in OUTCOMES}, "determinate": determinate,
            "eligible": eligible, "planned": planned, "retained": retained,
            "rate": counts["positive"] / determinate if determinate else None,
            "coverage": determinate / eligible if eligible else None,
            "unresolved_range": [counts["positive"] / eligible, (eligible - counts["negative"]) / eligible] if eligible else None,
            "processing_coverage": retained / planned if planned else None}


def _index(spec, summaries, whole_scope, settled):
    if not spec:
        return None
    by_key = {item["key"]: item for item in summaries}
    weights = spec["weights"]
    components = [{"key": key, "weight": weight, "contribution": None if row.get("rate") is None else weight * row["rate"],
                   "coverage": row.get("coverage")} for key, weight in weights.items() if (row := by_key.get(key))]
    eligible = len(components) == len(weights) and all(
        component["coverage"] is not None and component["coverage"] >= spec["minimum_coverage"] for component in components)
    status = ("scope_changed" if not whole_scope or len(components) != len(weights) else "analysis_in_progress"
              if not settled else "scored" if eligible else "insufficient_coverage")
    result = {**spec, "status": status, "components": components, "value": None, "range": None, "equal_weight": None}
    if status == "scored":
        weighted = [(weight, by_key[key]) for key, weight in weights.items()]
        result.update(value=sum(item["contribution"] for item in components),
                      range=[sum(weight * row["unresolved_range"][bound] for weight, row in weighted) for bound in (0, 1)],
                      equal_weight=100 * sum(row["rate"] for _, row in weighted) / len(weights))
    return result


def _comparison(store, project_id, comparison_id, run_id, sources, cells, summaries):
    if not comparison_id:
        return None
    from .comparisons import get_comparison
    packet = get_comparison(store, project_id, comparison_id)
    if (packet.get("analysis") or {}).get("run_id") not in {None, run_id}:
        raise ValueError("Choose a matched comparison from this analysis run.")
    frozen_hashes = {item["document_id"]: item["source_sha256"] for item in packet["sources"]}
    source_hashes = {source["id"]: source["sha256"] for source in sources}
    excluded = list(packet["excluded"])
    pairs = []
    for pair in packet["pairs"]:
        left, right = pair["left_document_id"], pair["right_document_id"]
        if any(frozen_hashes.get(identity) != source_hashes.get(identity) or identity not in source_hashes for identity in (left, right)):
            excluded.append({"document_id": left, "paired_document_id": right, "reason": "outside_scorecard_scope_or_changed"})
        else:
            pairs.append(pair)
    rows = summarize_pairs(pairs, cells, summaries)
    return {"id": packet["id"], "question": packet["question"], "packet_sha256": packet["snapshot_sha256"],
            "matching": packet["specification"], "pairs": pairs, "excluded": excluded, "criteria": rows,
            "interpretation": "Differences describe the declared matched sample. An omission inquiry needs an inventory of relevant information available at publication."}


def summarize_pairs(pairs, cells, summaries):
    indexed = {(cell["document_id"], cell["criterion"]): cell for cell in cells}
    rows = []
    for item in summaries:
        resolved = []
        for pair in pairs:
            left, right = (indexed[(pair[side + "_document_id"], item["key"])] for side in ("left", "right"))
            if all(cell["status"] in {"positive", "negative"} and cell["evidence"] for cell in (left, right)):
                resolved.append({"left_document_id": left["document_id"], "right_document_id": right["document_id"],
                                 "left_status": left["status"], "right_status": right["status"]})
        delta = sum(int(pair["left_status"] == "positive") - int(pair["right_status"] == "positive") for pair in resolved)
        rows.append({"key": item["key"], "label": item["label"], "resolved": len(resolved), "total": len(pairs),
                     "difference_pp": 100 * delta / len(resolved) if resolved else None, "pairs": resolved})
    return rows


def _date(value):
    try:
        return date.fromisoformat(str(value or "")[:10]).isoformat()
    except ValueError:
        return None


def build_snapshot(*, project_id, query_id, snapshot_version, run, basis, sources, exclusions,
                   cards, cells, whole_scope, now=None, comparison=None, identity=None):
    """Construct one result used by the API, interactive views, and artifacts."""
    summaries = []
    grouped = defaultdict(list)
    for cell in cells:
        grouped[cell["criterion"]].append(cell)
    source_dates = {source["id"]: source["published_at"] for source in sources}
    timeline = []
    for card in cards:
        key = card["id"]
        values = grouped[key]
        summaries.append({"key": key, "label": card["label"], "kind": card["criterion_kind"],
                          "card_sha256": card["card_sha256"], **_summary(values)})
        by_month = defaultdict(list)
        for cell in values:
            day = source_dates[cell["document_id"]]
            by_month[day[:7] if day else "undated"].append(cell)
        timeline.extend({"period": month, "criterion": key, **_summary(group)} for month, group in sorted(by_month.items()))
    config = json.loads(run["config_json"])
    result = {"id": identity or uuid.uuid4().hex, "version": VERSION, "project_id": project_id, "query_id": query_id,
              "snapshot_version": snapshot_version, "created_at": now or utcnow(), "basis": basis,
              "question": config.get("question"), "run": {"id": run["id"], "status": run["status"], "model": config.get("model"),
                  "model_revision": config.get("model_revision"), "provider": config.get("provider"),
                  "method_fingerprint": config.get("method_fingerprint"), "execution_profile": config.get("execution_profile")},
              "sources": sources, "excluded": exclusions, "method_cards": cards, "matrix": cells, "criteria": summaries,
              "timeline": timeline, "comparison": comparison,
              "index": _index(config.get("index_spec"), summaries, whole_scope, run["status"] in {"completed", "partial", "failed", "cancelled"}),
              "method": {"unit": "one deduplicated source per criterion",
                         "rate": "positive sources / determinate sources",
                         "coverage": "determinate sources / (selected sources minus fully inapplicable sources)",
                         "processing": "retained explicit decisions / planned source-unit decisions"}}
    return {**result, "snapshot_sha256": content_hash(result)}


def _source_record(row, frozen, metadata):
    manifest = metadata.get("extraction_manifest") or {}
    return {"id": row["id"], "title": frozen.get("title") or row["title"],
            "document_type": frozen.get("document_type") or row["document_type"], "sha256": row["sha256"],
            "published_at": _date(frozen.get("published_at") if frozen else row["published_at"]),
            "source_url": frozen.get("source_url") or row["source_url"], "license": frozen.get("license") or row["license"],
            "extraction_version": manifest.get("extraction_version_id"), "extraction_complete": manifest.get("source_coverage_complete", True),
            "extraction_warnings": (metadata.get("extraction_warnings") or [])[:12]}


def _populate(store, project_id, scoped_ids, run_id, basis, cards):
    run_sources = store.query(
        "SELECT d.*,rs.sha256 AS run_sha256,m.payload_json AS frozen_json FROM v3_run_sources rs "
        "JOIN documents d ON d.id=rs.document_id LEFT JOIN run_source_metadata m ON m.run_id=rs.run_id AND m.document_id=d.id "
        "WHERE rs.run_id=? AND d.project_id=? AND d.id IN (SELECT value FROM json_each(?)) ORDER BY d.id",
        [run_id, project_id, json.dumps(scoped_ids)])
    unit_rows = store.query("SELECT document_id,unit_id FROM v3_unit_statuses WHERE run_id=? "
                            "AND document_id IN (SELECT value FROM json_each(?))", [run_id, json.dumps(scoped_ids)])
    units = defaultdict(set)
    for row in unit_rows:
        units[row["document_id"]].add(row["unit_id"])
    if len(unit_rows) * len(cards) > 100_000:
        raise ValueError("Narrow the saved query to score at most 100,000 planned decisions.")
    keys = {card["id"] for card in cards}
    recorded = defaultdict(list)
    for item in store.query("SELECT id,document_id,unit_id,lens_id,criterion_id,result,review_status,payload_json,review_json "
                            "FROM v3_assessments WHERE project_id=? AND run_id=? AND document_id IN (SELECT value FROM json_each(?)) "
                            "AND lens_id||'/'||criterion_id IN (SELECT value FROM json_each(?)) ORDER BY id",
                            [project_id, run_id, json.dumps(scoped_ids), json.dumps(sorted(keys))]):
        recorded[(item["document_id"], item["lens_id"] + "/" + item["criterion_id"])].append(item)
    sources, cells, excluded = [], [], []
    seen = {}
    for row in run_sources:
        source_id = row["id"]
        frozen = json.loads(row["frozen_json"] or "{}")
        metadata = frozen.get("metadata") or json.loads(row["metadata_json"] or "{}")
        current = json.loads(row["metadata_json"] or "{}")
        extraction = metadata.get("extraction_manifest") or {}
        current_extraction = current.get("extraction_manifest") or {}
        unchanged = (row["run_sha256"] == row["sha256"] and extraction.get("extraction_version_id") ==
                     current_extraction.get("extraction_version_id"))
        group = metadata.get("story_group_id") or metadata.get("duplicate_group")
        duplicate_keys = [("raw", row["run_sha256"])]
        if extraction.get("blocks_sha256"):
            duplicate_keys.append(("extraction", extraction["blocks_sha256"]))
        if group:
            duplicate_keys.append(("declared", str(group)))
        duplicate = next((seen[key] for key in duplicate_keys if key in seen), None)
        if duplicate:
            excluded.append({"document_id": source_id, "title": row["title"], "reason": "duplicate", "represented_by": duplicate})
            continue
        seen.update({key: source_id for key in duplicate_keys})
        source = _source_record({**row, "sha256": row["run_sha256"]}, frozen, metadata)
        sources.append(source)
        blocks = {item["id"]: item for item in store.get_json(row["blocks_key"])} if unchanged and row["blocks_key"] else {}
        planned_units = units[source_id]
        for card in cards:
            key = card["id"]
            counts, evidence, accepted = Counter(), {}, set()
            for item in recorded[(source_id, key)]:
                unit_id = item["unit_id"]
                if unit_id not in planned_units or unit_id in accepted:
                    continue
                result, witness = _decision(item, blocks, basis, key == "political-framing/comparator_dependent_asymmetry")
                if result not in EXPLICIT:
                    continue
                accepted.add(unit_id)
                counts[result] += 1
                if witness and result not in evidence:
                    evidence[result] = witness
            source_scope = card.get("applicability") == SOURCE_SCOPE
            status = _source_outcome(len(planned_units), counts, unchanged and source["extraction_complete"],
                                     source_scope=source_scope)
            preferred = {"positive": "meets_criterion", "negative": "does_not_meet", "uncertain": "uncertain"}.get(status)
            witness = evidence.get(preferred) or next(iter(evidence.values()), None)
            cell = {"document_id": source_id, "criterion": key, "status": status, "planned": len(planned_units),
                    "retained": len(accepted), "evidence": witness, "decisions": dict(counts)}
            if source_scope and status == "uncertain" and counts["not_applicable"] == len(planned_units):
                cell["scope_note"] = "Every selected source is eligible. Review the unit decisions that marked this source inapplicable."
            cells.append(cell)
    active = {row["id"] for row in run_sources}
    excluded.extend({"document_id": value, "reason": "source_without_frozen_run_input"} for value in scoped_ids if value not in active)
    return sources, excluded, cells


def create_scorecard(store, project_id, body):
    if store.remote:
        raise ValueError("Scorecards require native workspace storage.")
    if body.basis not in {"model", "review_overlay"}:
        raise ValueError("Choose model results or model results with review corrections.")
    workspace = Workspace(store)
    with store.transaction(write=True):
        saved = workspace.require_query(body.query_id, current=False)
        if saved["project_id"] != project_id:
            raise HTTPException(404, "Saved workspace query not found")
        if saved["snapshot_version"] != workspace.revision(project_id):
            raise HTTPException(409, "Workspace changed. Refresh the query before saving a scorecard.")
        selection = workspace.document_ids(saved)
        if not 1 <= len(selection) <= 500:
            raise ValueError("Select 1–500 sources for a scorecard.")
        run = store.one("SELECT id,status,config_json FROM runs WHERE id=? AND project_id=?",
                        [saved["scope"].get("run_id"), project_id])
        config = json.loads(run["config_json"]) if run else {}
        if not run or not str(config.get("methodology_version", "")).startswith("3") or config.get("provider") == "lexical":
            raise ValueError("Choose a contextual analysis run for the scorecard.")
        snapshot = config.get("method_cards_snapshot") or freeze_method_cards(config["lens_ids"], config.get("custom_lens"))
        scope = saved["scope"]
        cards = [card for card in snapshot["cards"] if (not scope.get("lens_ids") or card["lens_id"] in scope["lens_ids"])
                 and (not scope.get("criterion_ids") or card["criterion_id"] in scope["criterion_ids"])]
        if not cards:
            raise ValueError("The selected criteria were not included in this analysis.")
        source_ids = [row["document_id"] for row in store.query("SELECT document_id FROM v3_run_sources WHERE run_id=?", [run["id"]])]
        sources, exclusions, cells = _populate(store, project_id, selection, run["id"], body.basis, cards)
        if not sources:
            raise ValueError("The run has no frozen source inputs in this selection.")
        comparison = _comparison(store, project_id, body.comparison_id, run["id"], sources, cells,
                                 [{"key": card["id"], "label": card["label"]} for card in cards])
        result = build_snapshot(project_id=project_id, query_id=body.query_id, snapshot_version=saved["snapshot_version"],
                                run=run, basis=body.basis, sources=sources, exclusions=exclusions, cards=cards,
                                cells=cells, whole_scope=set(selection) == set(source_ids), comparison=comparison)
        encoded = canonical_json(result)
        if len(encoded.encode()) > 8 * 1024 * 1024:
            raise ValueError("Scorecard exceeds 8 MiB. Narrow the saved query.")
        store.execute("INSERT INTO v3_scorecards(id,project_id,created_at,payload_json) VALUES(?,?,?,?)",
                      [result["id"], project_id, result["created_at"], encoded])
    return result


def get_scorecard(store, project_id, scorecard_id):
    if store.remote:
        raise HTTPException(404, "Scorecard not found")
    row = store.one("SELECT payload_json FROM v3_scorecards WHERE id=? AND project_id=?", [scorecard_id, project_id])
    if not row:
        raise HTTPException(404, "Scorecard not found")
    value = json.loads(row["payload_json"])
    if content_hash({key: item for key, item in value.items() if key != "snapshot_sha256"}) != value["snapshot_sha256"]:
        raise HTTPException(409, "Saved scorecard integrity check failed")
    return value
