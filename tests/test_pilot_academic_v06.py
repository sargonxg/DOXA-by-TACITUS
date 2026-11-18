"""Readable frozen academic annexes and unchanged legacy publication bytes."""
from copy import deepcopy
from io import BytesIO
import hashlib
import json
from pathlib import Path
import zipfile
from xml.etree import ElementTree

from doxa.lineage import content_sha256
from doxa.pdf_tools import open_pdf
from doxa.pilot_reports import build_pilot_report, freeze_snapshot, METHOD_CARD_APPENDIX_LIMIT
from doxa.service_profiles import validate_brief
from test_pilot_reports_v05 import sample

ROOT = Path(__file__).resolve().parents[1]


def test_actual_pdf_contains_primary_scholarly_citation_purpose_limits_and_link():
    source = sample("public")
    report = build_pilot_report(source, "pdf")
    with open_pdf(stream=report["content"]) as pdf:
        text = " ".join(" ".join(page.get_text() for page in pdf).split())
        links = [link["uri"] for page in pdf for link in page.get_links()]
    assert "Methodology and academic foundations" in text
    assert "Measurement and Fairness" in text and "Jacobs and Wallach" in text
    assert "Supports this design choice" in text and "Does not establish" in text
    assert "do not establish DOXA interpretation accuracy" in text
    assert "https://arxiv.org/abs/1912.05511" in links
    frozen_cards = source["brief"]["method_cards_snapshot"]
    assert frozen_cards["snapshot_sha256"] in text
    assert report["manifest"]["academic_annex"]["snapshot_sha256"] == frozen_cards["snapshot_sha256"]
    assert report["manifest"]["interpretation_status"] == "reference_pending"


def test_human_annex_is_bounded_but_json_and_xlsx_retain_every_card_and_reference():
    source = sample("public")
    source["brief"] = validate_brief({"profile_id": "public", "lens_ids": ["discourse", "ihra", "jda", "nexus", "political-framing"]})
    cards = source["brief"]["method_cards_snapshot"]["cards"]
    assert len(cards) > METHOD_CARD_APPENDIX_LIMIT
    html = build_pilot_report(source, "html")
    summary = html["manifest"]["academic_annex"]
    assert summary["method_cards_shown"] == METHOD_CARD_APPENDIX_LIMIT
    assert summary["method_cards_total"] == len(cards)
    text = html["content"].decode()
    assert f"Showing {METHOD_CARD_APPENDIX_LIMIT} of {len(cards)} frozen method cards" in text
    assert "complete contracts and references remain in JSON/XLSX" in text
    data = json.loads(build_pilot_report(source, "json")["content"])
    assert data["report_brief"]["brief"]["method_cards_snapshot"] == source["brief"]["method_cards_snapshot"]
    xlsx = build_pilot_report(source, "xlsx")
    with zipfile.ZipFile(BytesIO(xlsx["content"])) as workbook:
        assert workbook.testzip() is None
        sheets = workbook.read("xl/workbook.xml").decode()
        assert "Academic methods" in sheets and "Academic references" in sheets
        shared = workbook.read("xl/sharedStrings.xml").decode()
        assert "Supports this design choice" in shared and "Does not establish" in shared
        assert all(card["card_sha256"] in shared for card in cards)
        assert "https://arxiv.org/abs/1912.05511" in shared


def test_long_frozen_reference_url_wraps_in_human_reports_and_survives_xlsx_parts():
    source = sample("personal")
    brief = source["brief"]
    frozen = brief["method_cards_snapshot"]
    url = "https://example.org/research?evidence=" + "x" * 70000
    frozen["cards"][0]["references"][0]["url"] = url
    card = frozen["cards"][0]
    card["card_sha256"] = content_sha256({key: value for key, value in card.items() if key != "card_sha256"})
    frozen["snapshot_sha256"] = content_sha256({key: value for key, value in frozen.items() if key != "snapshot_sha256"})
    brief["fingerprint"] = content_sha256({key: value for key, value in brief.items() if key != "fingerprint"})
    report = build_pilot_report(source, "pdf")
    with open_pdf(stream=report["content"]) as pdf:
        visible = " ".join(page.get_text() for page in pdf)
        links = [row["uri"] for page in pdf for row in page.get_links()]
    assert "example.org/research" in visible and "x" * 200 not in visible
    assert url in links
    workbook = build_pilot_report(source, "xlsx")
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(BytesIO(workbook["content"])) as archive:
        strings = ["".join(item.itertext()) for item in ElementTree.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", ns)]
    # Explicitly numbered reference rows retain each cell-sized URL fragment.
    assert url[:32000] in strings and url[32000:64000] in strings and url[64000:] in strings
    assert len(freeze_snapshot(source)["report_brief"]["brief"]["method_cards_snapshot"]["cards"]) == len(frozen["cards"])


def test_legacy_examples_keep_exact_json_html_bytes_and_all_committed_hashes():
    manifests = [ROOT / "examples" / f"v05-pilot-demo-{name}.manifest.json" for name in ("company", "public", "personal")]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in manifests}
    for profile in ("company", "public", "personal"):
        prefix = ROOT / "examples" / ("v05-pilot-demo-" + profile)
        source = json.loads(prefix.with_suffix(".input.json").read_text())
        copied = deepcopy(source)
        for format in ("json", "html"):
            rendered = build_pilot_report(source, format)
            assert rendered["content"] == prefix.with_suffix("." + format).read_bytes()
            assert "academic_annex" not in rendered["manifest"]
        assert source == copied
    assert before == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in manifests}
    for manifest in manifests:
        for file in json.loads(manifest.read_text())["files"]:
            path = manifest.parent / file["file"]
            assert path.stat().st_size == file["bytes"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == file["sha256"]
