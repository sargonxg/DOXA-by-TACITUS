#!/usr/bin/env python3
"""Build or verify the antisemitism case from its frozen public archive."""

import argparse
import csv
from io import StringIO
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.antisemitism_case.evidence import coverage, freeze, readings  # noqa: E402
from scripts.antisemitism_case.pixel_art import render as pixel_art  # noqa: E402
from scripts.antisemitism_case.publication import render  # noqa: E402
from scripts.bias_study.graph import build_graph, graph_counts  # noqa: E402
from scripts.build_recorded_starter_v06 import file_hash  # noqa: E402

PUBLIC = ROOT / "frontend/public/studies/antisemitism"
MANIFEST = ROOT / "examples/antisemitism-case.manifest.json"
PDF = "antisemitism-case.pdf"


def decision_csv(study):
    text = StringIO(newline="")
    writer = csv.writer(text, lineterminator="\n")
    fields = ("id", "unit_id", "lens_id", "criterion_id", "criterion_kind", "result", "attribution",
              "detail_supplied", "review_status")
    writer.writerow(("source_id", "source_version_id", *fields))
    for doc in study["documents"]:
        for row in doc["decisions"]:
            writer.writerow((doc["id"], doc["source_version_id"], *(row[field] for field in fields)))
    return text.getvalue().encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Replace the checked-in publication.")
    parser.add_argument("--offline-zip", type=Path, help="Package all case files for offline reading.")
    args = parser.parse_args()
    study = freeze(ROOT)
    notes = readings(study)
    counts = coverage(study)
    graph = build_graph(study, notes)
    node_counts = graph_counts(graph)
    if node_counts["Decision"] != counts["retained"] or counts["retained"] != counts["expected"]:
        raise ValueError("The complete teaching set must retain one decision per unit and criterion")
    artwork = pixel_art()
    outputs = {
        **artwork,
        "index.html": render(study, graph, notes, counts, artwork).encode("utf-8"),
        "decisions.csv": decision_csv(study),
        "evidence.jsonld": (json.dumps(graph, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"),
    }
    if args.write:
        from weasyprint import HTML

        pdf = HTML(string=render(study, graph, notes, counts, artwork, printing=True)).write_pdf(pdf_tags=True)
        PUBLIC.mkdir(parents=True, exist_ok=True)
        for name, data in {**outputs, PDF: pdf}.items():
            (PUBLIC / name).write_bytes(data)
    else:
        for name, data in outputs.items():
            if (PUBLIC / name).read_bytes() != data:
                raise ValueError(f"Published {name} differs from the frozen rebuild")
    manifest = {
        "study_version": study["study_version"],
        "selection": study["selection"],
        "parent": study["parent"],
        "coverage": counts,
        "graph_counts": node_counts,
        "sources": {doc["id"]: doc["archive_locator"] for doc in study["documents"]},
        "case_ids": {note["slug"]: note["decision"]["id"] for note in notes},
        "fresh_inference_requests": study["fresh_inference_requests"],
        "independent_human_labels": study["independent_human_labels"],
        "files": [
            {"file": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": file_hash(path)}
            for path in (PUBLIC / name for name in sorted([*outputs, PDF]))
        ],
    }
    if args.write:
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif json.loads(MANIFEST.read_text(encoding="utf-8")) != manifest:
        raise ValueError("Published checksums differ from the verified case files")
    if args.offline_zip:
        with ZipFile(args.offline_zip, "w", ZIP_DEFLATED) as archive:
            archive.writestr("START-HERE.txt", "Open index.html with all sibling files in the same directory.\n"
                             "Use the four worked readings to review passages under the frozen frameworks.\n"
                             "The article links open the original Global Voices texts when online.\n")
            archive.writestr("SHA256SUMS.txt", "".join(f"{row['sha256']}  {Path(row['file']).name}\n"
                                                      for row in manifest["files"]))
            for row in manifest["files"]:
                archive.write(ROOT / row["file"], Path(row["file"]).name)
    print(json.dumps({"coverage": counts, "graph": node_counts}, indent=2))


if __name__ == "__main__":
    main()
