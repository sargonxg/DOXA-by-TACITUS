"""Cross-format evidence integrity and article-level roll-up of the public study."""

import io
import itertools
import json
import sys
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.bias_study.cohort import article_state, summarize  # noqa: E402
from scripts.bias_study.readings import cases  # noqa: E402

PUBLIC = ROOT / "frontend/public/studies/muslim-coverage"


def test_article_outcomes_keep_gaps_and_uncertainty():
    assert article_state([], 0) == "incomplete"
    assert article_state(["not_applicable", "does_not_meet"], 2) == "no_flag"
    assert article_state(["not_applicable"], 1) == "inapplicable"
    assert article_state(["does_not_meet", "uncertain"], 2) == "uncertain"
    assert article_state(["uncertain"], 2) == "incomplete"
    assert article_state(["meets_criterion", "uncertain"], 3) == "flagged"


def test_shipped_study_counts_sources_decisions_scoped_citations_and_downloads():
    manifest = json.loads((ROOT / "examples/muslim-coverage.manifest.json").read_text())
    with ZipFile(PUBLIC / "sources.zip") as archive:
        study = json.loads(archive.read("study.json"))
        assert archive.testzip() is None
        assert len([name for name in archive.namelist() if name.endswith(".txt")]) == 60
    docs = study["documents"]
    assert len({doc["content_sha256"] for doc in docs}) == len({doc["story_group_id"] for doc in docs}) == 60
    assert [
        len(list(group)) for _, group in itertools.groupby(docs, key=lambda d: d["published_at"][:4])
    ] == [10] * 6
    counts = summarize(study)
    assert sum(counts["outcomes"].values()) == counts["decisions"] == 822
    assert counts["flagged_articles"] == 14
    assert counts["missing"] == 0 and counts["uncertain_articles"] == 8
    assert counts["flag_voices"] == {"reported_speaker": 44, "quoted_speaker": 1, "author": 4}
    assert all(sum(c["states"].values()) == 60 for c in counts["criteria"].values())
    assert {k: v for k, v in counts.items() if k != "article_states"} == manifest["counts"]
    html = (PUBLIC / "index.html").read_text()
    pdf = PdfReader(io.BytesIO((PUBLIC / "report.pdf").read_bytes()))
    text = " ".join(" ".join(p.extract_text() for p in pdf.pages).split())
    assert "14 of 60 articles" in text and "45 of its 49 flagged" in text
    notes = cases(study)
    assert [c["decision"]["id"] for c in notes] == manifest["case_ids"]
    for c in notes:
        assert c["heading"] in html and c["heading"] in text
        ref = c["ref"]
        assert c["context"][ref["start"] : ref["end"]] == ref["quote"]
    with ZipFile(PUBLIC / "evidence.xlsx") as archive:
        assert archive.testzip() is None
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

        def rows(sheet):
            return (
                len(
                    ElementTree.fromstring(archive.read(f"xl/worksheets/sheet{sheet}.xml")).findall(
                        ".//x:sheetData/x:row", ns
                    )
                )
                - 1
            )

        assert rows(2) == 60 and rows(3) == 822 and rows(4) == counts["references"] == 16855
    assert study["selection"]["uses_model_outcomes"] is False
    assert study["fresh_inference_requests"] == 0 and study["independent_human_labels"] is False
