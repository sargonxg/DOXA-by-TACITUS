#!/usr/bin/env python3
"""Fetch and extract the twenty real public-source showcase publications.

Bundled JSON files work offline. Refresh is explicit and network-enabled:
    python scripts/fetch_corpus.py --refresh
Audit the bundled snapshots without network:
    python scripts/fetch_corpus.py --audit
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from io import BytesIO

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTImage, LTTextContainer
from pypdf import PdfReader
import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus" / "manifest.json"
DOCUMENTS = ROOT / "corpus" / "documents"
AUDIT = ROOT / "benchmarks" / "corpus-audit.json"
EXTRACTOR_VERSION = "doxa-public-corpus-v2"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def content_digest(blocks: list[dict]) -> str:
    canonical = json.dumps(blocks, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return digest(canonical.encode("utf-8"))


def extract_pdf(raw: bytes, doc_id: str) -> tuple[list[dict], dict]:
    blocks: list[dict] = []
    images = 0
    page_total = 0
    pdf = PdfReader(BytesIO(raw))
    if pdf.is_encrypted:
        raise ValueError("Encrypted source PDF")
    page_total = len(pdf.pages)
    def count_images(node):
        if isinstance(node, LTImage):
            return 1
        return sum(count_images(child) for child in node) if hasattr(node, "__iter__") and not isinstance(node, LTTextContainer) else 0
    for page_index, page in enumerate(extract_pages(BytesIO(raw))):
        images += count_images(page)
        paragraph = 0
        for block in page:
            if not isinstance(block, LTTextContainer):
                continue
            text = clean(block.get_text())
            if not text:
                continue
            paragraph += 1
            blocks.append({"id": f"{doc_id}-p{page_index + 1:04d}-b{paragraph:04d}",
                           "text": text, "page": page_index + 1, "paragraph": paragraph})
    pdf.close()
    return blocks, {
        "page_count": page_total,
        "extraction_method": "pdfminer-six-text-boxes-v1",
        "excluded_material": [
            {"category": "non-text-images", "count": images,
             "reason": "Images, seals and photos are not included in the text snapshot."}
        ],
        "source_text_characters": sum(len(b["text"]) for b in blocks),
    }


def extract_html(raw: bytes, doc_id: str) -> tuple[list[dict], dict]:
    soup = BeautifulSoup(raw, "html.parser")
    excluded: list[dict] = []
    for selector, reason in [
        ("script,style,noscript,iframe", "Executable and embedded remote content"),
        ("nav,header,footer,aside,form,button", "Site navigation and controls"),
        ("img,svg,picture", "Images, seals and logos are not included in the text snapshot"),
    ]:
        found = soup.select(selector)
        if found:
            excluded.append({"category": selector, "count": len(found), "reason": reason})
        for node in found:
            node.decompose()
    selectors = (
        [".gem-c-govspeak", ".govspeak", "main", "article", "body"]
        if doc_id.startswith("uk")
        else ["main article", "main", "article", "body"]
    )
    root = None
    used_selector = None
    for selector in selectors:
        candidates = soup.select(selector)
        candidates = [n for n in candidates if len(n.get_text(" ", strip=True)) > 200]
        if candidates:
            root = max(candidates, key=lambda n: len(n.get_text(" ", strip=True)))
            used_selector = selector
            break
    if root is None:
        raise ValueError("No substantive source article found")
    names = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "tr", "pre", "blockquote"}
    texts: list[str] = []
    for node in root.find_all(list(names)):
        parent = node.parent
        nested = False
        while parent is not None and parent is not root:
            if parent.name in names:
                nested = True
                break
            parent = parent.parent
        if nested:
            continue
        if node.name == "pre":
            texts.extend(clean(part) for part in re.split(r"\n\s*\n", node.get_text()) if clean(part))
        else:
            text = clean(node.get_text(" ", strip=True))
            if text:
                texts.append(text)
    if not texts:
        texts = [clean(p) for p in re.split(r"\n\s*\n", root.get_text("\n", strip=True)) if clean(p)]
    blocks = [
        {"id": f"{doc_id}-b{i:05d}", "text": text, "page": None, "paragraph": i}
        for i, text in enumerate(texts, 1)
    ]
    return blocks, {
        "page_count": None,
        "extraction_method": "beautifulsoup-content-blocks",
        "content_selector": used_selector,
        "excluded_material": excluded,
        "source_text_characters": len(clean(root.get_text(" ", strip=True))),
    }


def fetch_document(client: httpx.Client, entry: dict) -> dict:
    response = None
    for attempt in range(3):
        try:
            response = client.get(entry["source_url"])
            response.raise_for_status()
            break
        except httpx.HTTPError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    assert response is not None
    raw = response.content
    if entry["source_format"] == "pdf":
        if not raw.startswith(b"%PDF"):
            raise ValueError("Source response is not a PDF")
        blocks, extraction = extract_pdf(raw, entry["id"])
    else:
        blocks, extraction = extract_html(raw, entry["id"])
    words = sum(len(re.findall(r"\S+", b["text"])) for b in blocks)
    if words < 300:
        raise ValueError(f"Source extraction too short: {words} words")
    joined = "\n".join(b["text"] for b in blocks)
    return {
        **entry,
        "schema_version": 1,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "resolved_source_url": str(response.url),
        "source_content_type": response.headers.get("content-type"),
        "source_sha256": digest(raw),
        "source_bytes": len(raw),
        "content_sha256": content_digest(blocks),
        "extractor_version": EXTRACTOR_VERSION,
        "word_count": words,
        "block_count": len(blocks),
        **extraction,
        "rights_review": {
            "status": "source-license-reviewed",
            "scope": "Government-authored prose under the stated source rights; no exhaustive legal clearance claim.",
            "third_party_policy": "Citations and short contextual quotations retain attribution and original rights. No linked third-party publication, image, logo or seal is copied into this text edition.",
            "source_license_statement_present": bool(re.search(r"Open Government\s+Licence|Crown copyright", joined, re.I)),
            "notices_preserved": [notice for notice in entry.get("notices", [])],
        },
        "coverage": {
            "status": "text-extracted",
            "ocr_performed": False,
            "images_analyzed": False,
            "interpretation_gold_labels": False,
            "limitations": "Text-only snapshot. Layout, figures, scanned text and visual rhetoric are not analyzed. Extraction is not proof of semantic completeness.",
        },
        "blocks": blocks,
    }


def audit(entries: list[dict]) -> dict:
    results = []
    for entry in entries:
        path = DOCUMENTS / f"{entry['id']}.json"
        if not path.exists():
            results.append({"id": entry["id"], "status": "missing"})
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        blocks = document.get("blocks", [])
        ids = [b["id"] for b in blocks]
        checks = {
            "real_source_url": document["source_url"] == entry["source_url"],
            "nonempty": len(blocks) > 0 and document["word_count"] >= 300,
            "unique_block_ids": len(set(ids)) == len(ids),
            "content_checksum": content_digest(blocks) == document.get("content_sha256"),
            "source_checksum_recorded": bool(re.fullmatch(r"[0-9a-f]{64}", document.get("source_sha256", ""))),
            "locators_present": all(b.get("paragraph") and (b.get("page") is not None or entry["source_format"] == "html") for b in blocks),
            "not_synthetic": document.get("synthetic") is False,
            "rights_recorded": bool(document.get("license") and document.get("rights_source_url") and document.get("rights_review")),
        }
        results.append({
            "id": entry["id"],
            "title": document["title"],
            "status": "pass" if all(checks.values()) else "fail",
            "checks": checks,
            "word_count": document["word_count"],
            "block_count": len(blocks),
            "page_count": document.get("page_count"),
            "source_sha256": document["source_sha256"],
            "content_sha256": document["content_sha256"],
        })
    report = {
        "schema_version": 1,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "document_count": len(entries),
        "passed": sum(r["status"] == "pass" for r in results),
        "failed": sum(r["status"] != "pass" for r in results),
        "word_count": sum(r.get("word_count", 0) for r in results),
        "interpretation_accuracy": None,
        "interpretation_accuracy_note": "No independent human reference labels. This audit checks bundled source extraction and provenance only; it does not measure analysis accuracy.",
        "results": results,
    }
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Explicitly download and replace bundled text snapshots.")
    parser.add_argument("--audit", action="store_true", help="Validate bundled snapshots without network.")
    parser.add_argument("--ids", nargs="*", help="Only refresh these manifest document IDs.")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = manifest["documents"]
    if args.refresh:
        DOCUMENTS.mkdir(parents=True, exist_ok=True)
        selected = set(args.ids or [entry["id"] for entry in entries])
        unknown = selected - {entry["id"] for entry in entries}
        if unknown:
            parser.error("Unknown IDs: " + ", ".join(sorted(unknown)))
        with httpx.Client(timeout=90, follow_redirects=True, headers={"User-Agent": "DOXA-research-corpus/1.0 (public document text extraction)"}) as client:
            for entry in entries:
                if entry["id"] not in selected:
                    continue
                try:
                    doc = fetch_document(client, entry)
                    path = DOCUMENTS / f"{entry['id']}.json"
                    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                    print(f"{entry['id']}: {doc['word_count']:,} words; {doc['block_count']:,} blocks", flush=True)
                except Exception as error:
                    print(f"{entry['id']}: FAILED ({type(error).__name__}: {error})", file=sys.stderr, flush=True)
                    return 1
    elif not args.audit:
        parser.print_help()
        return 0
    report = audit(entries)
    print(f"Corpus audit: {report['passed']}/{report['document_count']} passed; {report['word_count']:,} words", flush=True)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
