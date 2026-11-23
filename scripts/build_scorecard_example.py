#!/usr/bin/env python3
"""Render the authored Civic Square teaching example without inference."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from doxa.extraction import extract_document
from doxa.method_cards import freeze_method_cards
from doxa.method_config import content_hash
from doxa.recorded_export import file_hash
from doxa.scorecard_reports import render_scorecard
from doxa.scorecards import build_snapshot, freeze_index, summarize_pairs

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "examples/scorecard-inputs"
OUTPUT = ROOT / "frontend/public/studies/scorecard"
MANIFEST = ROOT / "examples/scorecard.manifest.json"
LABELS = {
    "committee-briefing": [("positive", 0, "Cites two recorded ambulance delays."),
                           ("positive", 1, "Calls the exclusion zone a proportionate safety measure."),
                           ("positive", 1, "Recommends adopting the two-block exclusion zone."),
                           ("negative", 0, "States evidence of delays directly.")],
    "rights-briefing": [("negative", 0, "Discusses an existing plan and a gap in analysis."),
                        ("negative", 1, "Characterizes the zone as a burden on assembly."),
                        ("negative", 1, "Recommends stewards and measurement."),
                        ("negative", 0, "Questions whether the current plan fails.")],
    "committee-speech": [("positive", 0, "Cites dispatch logs documenting emergency delays."),
                         ("positive", 1, "Calls the zone a practical safeguard."),
                         ("positive", 1, "Recommends keeping the two-block exclusion zone."),
                         ("negative", 0, "Presents the recorded delay explicitly.")],
    "rights-speech": [("positive", 0, "Cites one documented ambulance delay."),
                      ("negative", 1, "Questions the committee's characterization."),
                      ("negative", 0, "Recommends a staffed emergency lane."),
                      ("uncertain", 1, "Restore order can imply an earlier failure, but the text questions what order means.")],
}


def build_example():
    inquiry = json.loads((INPUTS / "inquiry.json").read_text())
    method = freeze_method_cards(inquiry["lens_ids"], inquiry["custom_lens"])
    spec = freeze_index(inquiry["index_spec"], {"method_cards_snapshot": method})
    sources, cells = [], []
    with (INPUTS / "metadata.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            path = INPUTS / row["filename"]
            source_id = path.stem
            extracted = extract_document(path)
            manifest = extracted["extraction_manifest"]
            sources.append({"id": source_id, **row, "sha256": manifest["raw_sha256"], "source_url": "",
                            "extraction_version": manifest["extraction_version_id"],
                            "extraction_complete": True, "extraction_warnings": []})
            for card, (status, block_index, explanation) in zip(method["cards"], LABELS[source_id], strict=True):
                block = extracted["blocks"][block_index]
                identity = content_hash([source_id, card["id"]])
                result = {"positive": "meets_criterion", "negative": "does_not_meet", "uncertain": "uncertain"}[status]
                cells.append({"document_id": source_id, "criterion": card["id"], "status": status, "planned": 1,
                              "retained": 1, "decisions": {result: 1}, "evidence": {
                                  "assessment_id": identity, "unit_id": "teaching-" + source_id,
                                  "quote": block["text"], "block_id": block["id"], "start": 0, "end": len(block["text"]),
                                  "page": None, "paragraph": block["paragraph"], "cue_start_ms": block.get("cue_start_ms"),
                                  "cue_end_ms": block.get("cue_end_ms"), "explanation": explanation,
                                  "alternative": "Consider whether the key phrase describes traffic or the demonstrators."
                                  if status == "uncertain" else "", "review_state": "authored teaching label", "review_event_id": None}})
    config = {"question": inquiry["question"], "index_spec": spec, "provider": "authored teaching fixture",
              "method_fingerprint": content_hash(method)}
    pairs = [{"left_document_id": "committee-" + kind, "right_document_id": "rights-" + kind,
              "date_gap_days": gap, "matched_on": {"event": "Civic Square hearing", "topic": "exclusion zone", "genre": genre}}
             for kind, gap, genre in (("briefing", 1, "policy briefing"), ("speech", 0, "speech transcript"))]
    comparison = {"id": "civic-square-comparison", "question": "How do the committee and the rights council frame the same proposal?",
                  "packet_sha256": content_hash(pairs), "matching": {"matching_fields": ["event", "topic", "genre"], "max_date_gap_days": 7},
                  "pairs": pairs, "excluded": [], "criteria": summarize_pairs(pairs, cells, method_rows(method)),
                  "interpretation": "The difference describes these two fictional matched pairs."}
    return build_snapshot(project_id="civic-square-teaching", query_id="civic-square-four-sources", snapshot_version="1",
                          run={"id": "authored-civic-square-v1", "status": "completed", "config_json": json.dumps(config)},
                          basis="authored teaching labels", sources=sources, exclusions=[], cards=method["cards"],
                          cells=cells, whole_scope=True, now="2026-11-22T00:00:00Z", comparison=comparison,
                          identity="civic-square-scorecard-v1")


def method_rows(method):
    return [{"key": card["id"], "label": card["label"]} for card in method["cards"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    snapshot = build_example()
    if snapshot["index"]["value"] != 60 or snapshot["comparison"]["criteria"][0]["difference_pp"] != 50:
        raise ValueError("The teaching contrast changed")
    encoded = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        (OUTPUT / "scorecard.json").write_text(encoded, encoding="utf-8")
        for format in ("pdf", "html", "xlsx", "csv", "jsonl"):
            render_scorecard(snapshot, OUTPUT, format)
        with ZipFile(OUTPUT / "starter.zip", "w", compression=ZIP_DEFLATED) as archive:
            for path in sorted(INPUTS.iterdir()):
                archive.writestr(ZipInfo(path.name, (2026, 11, 22, 0, 0, 0)), path.read_bytes(), compress_type=ZIP_DEFLATED)
        files = [{"file": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": file_hash(path)}
                 for path in sorted(OUTPUT.iterdir())]
        MANIFEST.write_text(json.dumps({"id": snapshot["id"], "origin": "authored fictional labels", "files": files}, indent=2) + "\n")
    elif encoded != (OUTPUT / "scorecard.json").read_text(encoding="utf-8"):
        raise ValueError("The checked-in scorecard differs from the authored fixture")
    print(f"{len(snapshot['sources'])} sources, {len(snapshot['matrix'])} cells, score {snapshot['index']['value']:.1f}")


if __name__ == "__main__":
    main()
