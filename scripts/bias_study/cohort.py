"""Freeze a source-first topic cohort and read its original criterion decisions."""

from collections import Counter, defaultdict
import hashlib
import json
import re

from doxa.contextual import _criteria
from doxa.recorded_starter import canonical_hash
from doxa.showcase_v3 import _parts, _validate_record, read_manifest
from scripts.build_recorded_starter_v06 import (
    EXPECTED_CORPUS_SHA256,
    EXPECTED_PARENT_SHA256,
    archive_records,
    file_hash,
)

SEED = "doxa-muslim-coverage-1"
YEARS = tuple(str(y) for y in range(2020, 2026))
PER_YEAR = 10
TERM = re.compile(r"\b(?:muslims?|islamophobi(?:a|c))\b", re.IGNORECASE)
LENSES = ("anti-muslim-hostility", "anti-muslim-racism")
STATES = ("flagged", "incomplete", "uncertain", "no_flag", "inapplicable")
STATE_LABELS = {
    "flagged": "Flag recorded",
    "incomplete": "Missing decision",
    "uncertain": "Unresolved reading",
    "no_flag": "Checked without a flag",
    "inapplicable": "Outside the criterion",
}
RESULT_LABELS = {
    "meets_criterion": "Flag recorded",
    "does_not_meet": "Criterion not met",
    "uncertain": "Uncertain",
    "not_applicable": "Outside the criterion",
}
TITLES = {
    "targeted_hostility": "Hostility directed at Muslims",
    "stereotyping_and_intent": "Stereotyping intended to encourage hatred",
    "discrimination_claim": "Reports or statements of unequal treatment",
    "racialization": "Treating Muslims as a single suspect group",
    "exclusion": "Exclusion linked to Muslim identity",
    "institutional_pattern": "Possible repeated institutional practice",
}


def choose_sources(root):
    corpus = root / "corpus/news"
    if file_hash(corpus / "manifest.jsonl") != EXPECTED_CORPUS_SHA256:
        raise ValueError("Source manifest changed")
    with (corpus / "holdout.jsonl").open() as stream:
        held = {item["story_group_id"] for item in map(json.loads, stream)}
    scaffold = root / "benchmarks/evaluation-v04/scaffold.json"
    locked = [c for c in json.loads(scaffold.read_text())["cases"] if c["split"] == "heldout"]
    held.update(c["story_group"] for c in locked)
    locked_ids = {c["source"]["source_id"] for c in locked}
    by_year = defaultdict(list)
    with (corpus / "manifest.jsonl").open() as stream:
        for row in map(json.loads, stream):
            year = row["published_at"][:4]
            if (
                year not in YEARS
                or row["split"] != "development"
                or row["license"] != "CC-BY-3.0"
                or row["synthetic"] is not False
                or row["language"] != "en"
                or row["story_group_id"] in held
                or row["id"] in locked_ids
            ):
                continue
            source = json.loads((corpus / row["metadata_path"]).read_text())
            if any(TERM.search(b["text"]) for b in source["blocks"]):
                priority = hashlib.sha256(f"{SEED}|{row['id']}".encode()).hexdigest()
                by_year[year].append((priority, row))
    selected, groups, hashes = {}, set(), set()
    for year in YEARS:
        n = 0
        for _, row in sorted(by_year[year], key=lambda pair: (pair[0], pair[1]["id"])):
            if row["story_group_id"] in groups or row["content_sha256"] in hashes:
                continue
            groups.add(row["story_group_id"])
            hashes.add(row["content_sha256"])
            selected[row["id"]] = row
            n += 1
            if n == PER_YEAR:
                break
        if n != PER_YEAR:
            raise ValueError(f"Too few eligible distinct articles in {year}")
    selection = {
        "seed": SEED,
        "body_pattern": TERM.pattern,
        "per_year": PER_YEAR,
        "candidates_by_year": {y: len(by_year[y]) for y in YEARS},
        "manifest_sha256": EXPECTED_CORPUS_SHA256,
        "scaffold_sha256": file_hash(scaffold),
        "holdout_sha256": file_hash(corpus / "holdout.jsonl"),
        "selected_ids_sha256": canonical_hash(sorted(selected)),
        "uses_model_outcomes": False,
    }
    return selected, selection


def article_state(outcomes, units):
    """One article per criterion. A known presence survives missing peer decisions."""
    if len(outcomes) > units:
        raise ValueError("More decisions than units")
    if "meets_criterion" in outcomes:
        return "flagged"
    if not units or len(outcomes) != units:
        return "incomplete"
    if "uncertain" in outcomes:
        return "uncertain"
    if "does_not_meet" in outcomes:
        return "no_flag"
    if set(outcomes) != {"not_applicable"}:
        raise ValueError("Unexpected criterion outcome")
    return "inapplicable"


class FrozenBlocks:
    def __init__(self, blocks):
        self.blocks = blocks

    def get_json(self, _key):
        return self.blocks


def freeze(root):
    selected, selection = choose_sources(root)
    return freeze_selected(root, selected, selection, LENSES, "muslim-coverage-1")


def freeze_selected(root, selected, selection, lens_ids, edition, *, wanted_parts=None):
    """Validate frozen source versions and project only the declared rubric."""
    parent, path, digest = read_manifest(root / "benchmarks/v3-news/showcase.json")
    if digest != EXPECTED_PARENT_SHA256:
        raise ValueError("Recorded parent changed")
    _parts(parent, path)
    run = parent["run"]
    config = run["config"]
    _, criteria = _criteria(config["lens_snapshots"], config)
    lenses = [lens for lens in config["lens_snapshots"] if lens["id"] in lens_ids]
    if {lens["id"] for lens in lenses} != set(lens_ids):
        raise ValueError("Frozen rubric does not cover every requested lens")
    documents = []
    for record, location in archive_records(parent, path, wanted_parts=wanted_parts):
        source_id = record["source_id"]
        row = selected.get(source_id)
        if not row:
            continue
        corpus = root / "corpus/news"
        source = json.loads((corpus / row["metadata_path"]).read_text())
        if (
            source["content_sha256"] != row["content_sha256"]
            or source["id"] != source_id
            or file_hash(corpus / row["text_path"]) != row["txt_sha256"]
        ):
            raise ValueError("Source metadata or attributed text changed")
        doc_id = record["document_id"]
        _validate_record(
            FrozenBlocks(source["blocks"]),
            record,
            {
                **source,
                "id": doc_id,
                "sha256": source["content_sha256"],
                "blocks_key": "frozen",
                "metadata": {**source, "source_id": source_id},
            },
            {"id": doc_id, "sha256": source["content_sha256"]},
            run,
            criteria,
        )
        decisions = [a for a in record["assessments"] if a["lens_id"] in lens_ids]
        documents.append(
            {
                **{
                    k: source[k]
                    for k in (
                        "id",
                        "title",
                        "author",
                        "authors",
                        "translators",
                        "published_at",
                        "attribution",
                        "source_url",
                        "license",
                        "license_url",
                        "content_sha256",
                        "blocks",
                        "story_group_id",
                        "word_count",
                        "txt_sha256",
                    )
                },
                "document_id": doc_id,
                "source_version_id": record["source_version_id"],
                "archive_locator": location,
                "parent_record_sha256": canonical_hash(record),
                "unit_statuses": record["unit_statuses"],
                "decisions": decisions,
            }
        )
    if len(documents) != len(selected) or {d["id"] for d in documents} != set(selected):
        raise ValueError("The archive did not account for the entire frozen topic cohort")
    documents.sort(key=lambda d: (d["published_at"], d["id"]))
    return {
        "study_version": edition,
        "selection": selection,
        "lenses": lenses,
        "parent": {
            "id": run["id"],
            "status": run["status"],
            "archive_sha256": digest,
            "model": config["model"],
            "provider": config["provider"],
            "config_sha256": canonical_hash(config),
            "question": config["question"],
            "persona": config["persona"],
            "progress": run["progress"],
        },
        "documents": documents,
        "independent_human_labels": False,
        "fresh_inference_requests": 0,
    }


def summarize(study):
    articles = study["documents"]
    criteria = {}
    for lens in study["lenses"]:
        for criterion in lens["criteria"]:
            key = criterion["id"]
            if key in criteria:
                raise ValueError("Duplicate short criterion identity")
            criteria[key] = {
                "lens": lens["id"],
                "label": TITLES[key],
                "definition": criterion["description"],
                "states": dict.fromkeys(STATES, 0),
            }
    outcomes, voices, doc_states = Counter(), Counter(), {}
    flagged, unit_count, n_refs, missing, uncertain_docs = 0, 0, 0, 0, 0
    for doc in articles:
        units = len(doc["unit_statuses"])
        unit_count += units
        by_criterion = defaultdict(list)
        for a in doc["decisions"]:
            by_criterion[a["criterion_id"]].append(a["result"])
            outcomes[a["result"]] += 1
            n_refs += len(a["evidence_refs"])
            if a["result"] == "meets_criterion":
                voices["hypothetical" if a["hypothetical"] else a["attribution"]] += 1
        states = {key: article_state(by_criterion[key], units) for key in criteria}
        doc_states[doc["id"]] = states
        for key, state in states.items():
            criteria[key]["states"][state] += 1
        missing += units * len(criteria) - len(doc["decisions"])
        uncertain_docs += any("uncertain" in values for values in by_criterion.values())
        flagged += "flagged" in states.values()
    return {
        "articles": len(articles),
        "flagged_articles": flagged,
        "units": unit_count,
        "decisions": sum(outcomes.values()),
        "outcomes": dict(outcomes),
        "missing": missing,
        "uncertain_articles": uncertain_docs,
        "references": n_refs,
        "flag_voices": dict(voices),
        "criteria": criteria,
        "article_states": doc_states,
    }
