"""Cross-format verification of the published antisemitism teaching set."""

from collections import Counter
import csv
import json
from pathlib import Path
import sys

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.bias_study.graph import case_trace, graph_counts, graph_index  # noqa: E402

PUBLIC = ROOT / "frontend/public/studies/antisemitism"


def test_public_case_preserves_the_archived_framework_divergence():
    manifest = json.loads((ROOT / "examples/antisemitism-case.manifest.json").read_text())
    assert manifest["selection"]["uses_model_outcomes"] is True
    assert manifest["fresh_inference_requests"] == 0 and manifest["independent_human_labels"] is False
    graph = json.loads((PUBLIC / "evidence.jsonld").read_text())
    index = graph_index(graph)
    assert graph_counts(graph) == manifest["graph_counts"]
    assert graph_counts(graph)["Decision"] == manifest["coverage"]["expected"] == 81
    notes = {node["recordId"]: node for node in index.values() if "doxa:EditorialNote" in node["@type"]}
    plzen = case_trace(notes["plzen-poster"], index)
    assert plzen["decision"]["outcome"] == "meets_criterion"
    assert plzen["decision"]["attribution"] == "reported_speaker"
    assert plzen["criterion"]["kind"] == "indicator"
    assert plzen["peer"]["outcome"] == "does_not_meet" and "explanation" not in plzen["peer"]
    assert case_trace(notes["australia-policy"], index)["criterion"]["kind"] == "boundary"
    assert case_trace(notes["survivor-remembrance"], index)["peer"]["outcome"] == "not_applicable"
    for title, note in notes.items():
        trace = case_trace(note, index)
        assert trace["decision"]["recordId"] == manifest["case_ids"][title]

    with (PUBLIC / "decisions.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 81
    assert Counter(row["result"] for row in rows) == {
        "meets_criterion": 2, "does_not_meet": 75, "not_applicable": 4,
    }
    assert {row["id"] for row in rows} == {
        node["recordId"] for node in index.values() if "doxa:Decision" in node["@type"]
    }
    same_unit = [row for row in rows if row["unit_id"] == plzen["unit"]["recordId"]]
    assert len(same_unit) == 9
    assert Counter(row["lens_id"] for row in same_unit) == {"ihra": 3, "jda": 3, "nexus": 3}


def test_reader_and_tagged_pdf_include_context_comparison_and_art():
    reader = (PUBLIC / "index.html").read_text()
    pdf = PdfReader(PUBLIC / "antisemitism-case.pdf")
    assert pdf.root_object["/MarkInfo"]["/Marked"]
    pages = [" ".join(page.extract_text().split()) for page in pdf.pages]
    text = " ".join(pages)
    for phrase in ["God cursed them", "0–197, 197–388", "nine archived decisions", "Research red flags",
                   "A survivor’s name in a remembrance"]:
        assert phrase in text and phrase in reader
    assert all(len(page) > 700 for page in pages)
    for name in ("evidence-desk.png", "source.png", "voice.png", "criterion.png"):
        assert f"src='{name}'" in reader
        assert (PUBLIC / name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
