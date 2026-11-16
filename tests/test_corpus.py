"""Offline invariants for the bundled real-source corpus, not semantic gold labels."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "corpus" / "manifest.json").read_text())
ENTRIES = MANIFEST["documents"]


def load_document(entry):
    return json.loads((ROOT / "corpus" / "documents" / f"{entry['id']}.json").read_text())


def test_exactly_twenty_real_source_snapshots_with_manifest_parity():
    expected = {f"{country}{i:02d}" for country in ("uk", "us") for i in range(1, 11)}
    assert {entry["id"] for entry in ENTRIES} == expected
    assert {path.stem for path in (ROOT / "corpus" / "documents").glob("*.json")} == expected
    assert MANIFEST["gold_labels_available"] is False
    assert all(entry["synthetic"] is False and entry["language"] == "en" for entry in ENTRIES)


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda entry: entry["id"])
def test_public_source_provenance_rights_and_content_integrity(entry):
    doc = load_document(entry)
    assert doc["source_url"] == entry["source_url"]
    parsed = urlparse(doc["source_url"])
    assert parsed.scheme == "https"
    assert parsed.hostname.endswith((".gov", ".gov.uk"))
    assert doc["title"] == entry["title"] and doc["author"] == entry["author"]
    assert doc["institution"] and doc["published_at"] and doc["document_type"]
    assert doc["license"] in {"OGL-UK-3.0", "US-PUBLIC-DOMAIN-GOVERNMENT-WORK"}
    assert doc["rights_source_url"].startswith("https://")
    assert doc["license_url"].startswith("https://")
    assert doc["attribution"] and doc["rights_notes"]
    assert "exhaustive" in doc["rights_review"]["scope"].lower()
    assert re.fullmatch(r"[0-9a-f]{64}", doc["source_sha256"])
    assert doc["source_bytes"] > 1000
    canonical = json.dumps(doc["blocks"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(canonical).hexdigest() == doc["content_sha256"]
    assert doc["word_count"] >= 300
    assert doc["word_count"] == sum(len(block["text"].split()) for block in doc["blocks"])


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda entry: entry["id"])
def test_evidence_blocks_keep_real_page_or_paragraph_locators(entry):
    doc = load_document(entry)
    blocks = doc["blocks"]
    assert blocks and doc["block_count"] == len(blocks)
    assert len({block["id"] for block in blocks}) == len(blocks)
    for block in blocks:
        assert block["id"].startswith(entry["id"] + "-")
        assert isinstance(block["text"], str) and block["text"].strip()
        assert block["paragraph"] >= 1
        if entry["source_format"] == "pdf":
            assert 1 <= block["page"] <= doc["page_count"]
        else:
            assert block["page"] is None
            assert doc["page_count"] is None
    assert doc["coverage"]["ocr_performed"] is False
    assert doc["coverage"]["images_analyzed"] is False
    assert doc["coverage"]["interpretation_gold_labels"] is False


def test_corpus_is_substantial_varied_institutional_discourse_not_twenty_snippets():
    docs = [load_document(entry) for entry in ENTRIES]
    assert sum(doc["word_count"] for doc in docs) > 300_000
    assert sum(doc.get("page_count") or 0 for doc in docs) >= 500
    assert len({doc["document_type"] for doc in docs}) >= 8
    assert len({doc["institution"] for doc in docs}) >= 6
    assert len({doc["source_sha256"] for doc in docs}) == 20
    assert len({doc["published_at"][:4] for doc in docs}) >= 7
    assert sum(doc["institution"] == "Equal Employment Opportunity Commission" for doc in docs) == 4
    assert sum(doc["institution"] == "White House" for doc in docs) == 4


@pytest.mark.parametrize("source_id", ["us05", "us06", "us07", "us08"])
def test_real_eeoc_guidance_preserves_groff_supersession_and_hypothetical_status(source_id):
    doc = load_document(next(entry for entry in ENTRIES if entry["id"] == source_id))
    text = "\n".join(block["text"] for block in doc["blocks"])
    assert "Groff" in text and "2023" in text
    assert any("supersedes" in notice.lower() for notice in doc["notices"])
    assert any("hypothetical" in notice.lower() for notice in doc["notices"])


def test_month_precision_and_retrieval_date_are_explicit():
    doc = load_document(next(entry for entry in ENTRIES if entry["id"] == "us10"))
    assert doc["published_at"] == "2024-03-01"
    assert any("only month" in notice for notice in doc["notices"])
    assert doc["retrieved_at"].startswith("2026-")
