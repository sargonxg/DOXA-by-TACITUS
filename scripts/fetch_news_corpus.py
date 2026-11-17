#!/usr/bin/env python3
"""Collect 5,000 genuine licensed Global Voices English articles; explicit, resumable network work."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import heapq
import html
import json
from pathlib import Path
import re
import sys
import time
import unicodedata
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx  # noqa: E402
from bs4 import BeautifulSoup, Comment, NavigableString  # noqa: E402

VERSION = "globalvoices-news-v1.4"
POLICY = "https://globalvoices.org/about/global-voices-attribution-policy/"
LICENSE = "https://creativecommons.org/licenses/by/3.0/"
API = "https://globalvoices.org/wp-json/wp/v2"
SEED = "20261116"
DELIMITER = "\n=== ARTICLE BODY ===\n"
EXCLUDED_TYPES = {"announcements", "global voices summit", "global voices community", "about global voices"}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def stable(seed, *items):
    return sha("|".join([str(seed), *map(str, items)]))


def clean(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_gzip_json(path):
    with gzip.open(path, "rt", encoding="utf-8") as source:
        return json.load(source)


def write_gzip_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, separators=(",", ":"))


class RateLimiter:
    def __init__(self, requests_per_second):
        self.interval = 1 / requests_per_second
        self.next = 0.0
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            delay = self.next - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next = time.monotonic() + self.interval


async def request(client, limiter, url, params=None):
    error = None
    for attempt in range(4):
        await limiter.wait()
        try:
            response = await client.get(url, params=params)
            if response.status_code == 429:
                retry = min(60, max(1, int(response.headers.get("retry-after", "5"))))
                await asyncio.sleep(retry)
                continue
            response.raise_for_status()
            return response
        except (httpx.HTTPError, ValueError) as exc:
            error = exc
            await asyncio.sleep(min(8, 2 ** attempt))
    raise RuntimeError(f"Public-source fetch failed: {type(error).__name__ if error else 'rate_limit'}")


async def metadata(client, limiter, cache, start_year):
    folder = cache / f"api-{start_year}-2025"
    index = folder / "index.json"
    if index.exists():
        state = json.loads(index.read_text())
    else:
        state = {"start_year": start_year, "end_year": 2025, "frozen_at": now(), "pages": None, "source_total": None}
    all_rows = []
    page = 1
    while state["pages"] is None or page <= state["pages"]:
        path = folder / f"page-{page:04d}.json.gz"
        if path.exists():
            saved = read_gzip_json(path)
        else:
            params = {
                "after": f"{start_year}-01-01T00:00:00", "before": "2026-01-01T00:00:00",
                "per_page": 100, "page": page, "orderby": "date", "order": "asc", "status": "publish",
                "_fields": "id,date,date_gmt,modified,modified_gmt,link,title,author,categories,tags,content,status",
            }
            response = await request(client, limiter, API + "/posts", params)
            saved = {"url": str(response.url), "retrieved_at": now(), "response_sha256": sha(response.content),
                     "total": int(response.headers["x-wp-total"]), "pages": int(response.headers["x-wp-totalpages"]),
                     "records": response.json()}
            write_gzip_json(path, saved)
        if state["pages"] is None:
            state.update({"pages": saved["pages"], "source_total": saved["total"]})
            write_json(index, state)
        for row in saved["records"]:
            row = dict(row)
            row["_api_response_sha256"] = saved["response_sha256"]
            row["_api_retrieved_at"] = saved["retrieved_at"]
            all_rows.append(row)
        if page % 10 == 0 or page == state["pages"]:
            print(f"Metadata {start_year}-2025: page {page}/{state['pages']}; {len(all_rows)} records", flush=True)
        page += 1
    unique = {row["id"]: row for row in all_rows}
    if len(unique) != state["source_total"]:
        raise RuntimeError(f"Frozen metadata pagination mismatch: {len(unique)} unique / {state['source_total']} announced.")
    return sorted(unique.values(), key=lambda r: (r["date_gmt"], r["id"])), state


async def taxonomy(client, limiter, cache):
    path = cache / "categories.json"
    if path.exists():
        return json.loads(path.read_text())
    result = {}
    page = 1
    while True:
        response = await request(client, limiter, API + "/categories",
                                 {"per_page": 100, "page": page, "_fields": "id,name,slug,parent,count"})
        for item in response.json():
            result[str(item["id"])] = item
        if page >= int(response.headers.get("x-wp-totalpages", page)):
            break
        page += 1
    write_json(path, result)
    return result


def public_source_link(url):
    """Keep citation URLs without copying AWS signing credentials into the corpus."""
    base, separator, tail = url.partition("?")
    if not separator:
        return url
    query, fragment_separator, fragment = tail.partition("#")
    keys = {unquote(item.split("=", 1)[0]).casefold() for item in query.split("&")}
    if not any(key.startswith("x-amz-") or key == "awsaccesskeyid" for key in keys):
        return url
    query = "&".join(
        item for item in query.split("&")
        if item and not unquote(item.split("=", 1)[0]).casefold().startswith("x-amz-")
        and unquote(item.split("=", 1)[0]).casefold() not in {"awsaccesskeyid", "signature", "expires"}
    )
    return base + ("?" + query if query else "") + ("#" + fragment if fragment_separator else "")


def extract_body(rendered, doc_id):
    soup = BeautifulSoup(rendered, "html.parser")
    exclusions = []
    for selector, reason in [
        (".wp-caption,figure,figcaption", "External media and its caption excluded from the licensed text edition"),
        ("script,style,iframe,object,embed,video,audio,img,picture,svg", "Executable or external/non-text media excluded"),
        ("blockquote.twitter-tweet,blockquote.instagram-media,twitter-widget,.instagram-media,.fb-post", "External social embed excluded; original rights not assumed"),
    ]:
        nodes = soup.select(selector)
        if nodes:
            exclusions.append({"selector": selector, "count": len(nodes), "reason": reason})
        for node in nodes:
            node.decompose()
    names = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "dt", "dd", "tr", "div", "section", "article"}
    paragraphs, pieces, links = [], [], set()
    active_kind, quoted = "unstructured_text", False

    def flush():
        text = clean("".join(pieces))
        if text:
            paragraphs.append((("> " if quoted else "") + text, sorted(links), "blockquote" if quoted else active_kind))
        pieces.clear()
        links.clear()

    def walk(node, kind="unstructured_text", in_quote=False):
        nonlocal active_kind, quoted
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            if not pieces:
                active_kind, quoted = kind, in_quote
            pieces.append(str(node))
            return
        name = getattr(node, "name", "")
        boundary = name in names or name in {"br", "hr"}
        if boundary:
            flush()
        next_kind = name if name in names else kind
        next_quote = in_quote or name == "blockquote"
        if name == "a" and str(node.get("href", "")).startswith(("http://", "https://")):
            links.add(public_source_link(node["href"]))
        for child in getattr(node, "children", []):
            walk(child, next_kind, next_quote)
        if boundary:
            flush()

    walk(soup)
    flush()
    blocks, texts, position = [], [], 0
    for i, (text, links, source_kind) in enumerate(paragraphs, 1):
        blocks.append({"id": f"{doc_id}-b{i:05d}", "text": text, "page": None, "paragraph": i,
                       "body_start": position, "body_end": position + len(text), "source_links": links,
                       "source_kind": source_kind, "source_is_quotation": source_kind == "blockquote"})
        texts.append(text)
        position += len(text) + 2
    return "\n\n".join(texts), blocks, exclusions


def rights_rejection(text, category_names):
    if {name.casefold() for name in category_names}.intersection(EXCLUDED_TYPES):
        return "publisher_internal_announcement"
    # Only inspect licensing/republication notices near article boundaries,
    # not every mention of copyright inside reported news.
    boundary = (text[:2500] + "\n" + text[-1800:]).casefold()
    origin = re.search(r"(?:originally (?:published|appeared)|republished from)\s+([^\n.]{1,160})", boundary)
    if origin and not re.search(r"global\s*voices|advox", origin.group(1)):
        return "external_original_publication_requires_separate_rights"
    if re.search(r"\brepublished\b.{0,120}\bpermission\b", boundary):
        return "third_party_permission_is_not_a_cc_license"
    if re.search(r"(?:this (?:article|story|post)|originally published|originally appeared).{0,180}(?:republished|reproduced|with permission|partnership agreement)", boundary):
        if not re.search(r"(?:originally (?:published|appeared)|republished).{0,100}global voices", boundary):
            return "third_party_republication_requires_separate_rights"
    if re.search(r"republished (?:here )?(?:with permission|under (?:a |the )?partnership)", boundary):
        return "third_party_republication_requires_separate_rights"
    if re.search(r"creative commons.{0,60}(?:noncommercial|non-commercial|no.?derivatives|share.?alike)|cc[- ]by[- ](?:nc|nd|sa)|all rights reserved", boundary):
        return "article_specific_restrictive_rights_notice"
    return None


def credits_and_license(raw):
    soup = BeautifulSoup(raw, "html.parser")
    credits = {"authors": [], "translators": []}
    # This class is used by the current article's header/footer credits;
    # related story cards use text-credits-section instead.
    for node in soup.select(".contributor-name"):
        label = node.select_one(".credit-label")
        person = node.select_one("a.user-link")
        if label is None or person is None:
            continue
        role = "translators" if "translat" in label.get_text(" ", strip=True).casefold() else "authors"
        value = {"name": clean(person.get_text(" ", strip=True)), "url": person.get("href", "")}
        if value["name"] and value not in credits[role]:
            credits[role].append(value)
    licenses = sorted({a.get("href", "").rstrip("/") + "/" for a in soup.select("a[rel~='license'][href]")})
    canonical = soup.select_one("link[rel='canonical'][href]")
    return credits, licenses, canonical.get("href") if canonical else None


async def article(client, limiter, row, categories, cache, policy):
    doc_id = "gv-" + str(row["id"])
    path = cache / "records" / f"{doc_id}.json"
    api_body = row.get("content", {}).get("rendered", "")
    api_hash = sha(api_body)
    previous = None
    if path.exists():
        previous = json.loads(path.read_text())
        if previous.get("api_content_sha256") == api_hash and previous.get("extractor_version") == VERSION and previous.get("status") != "fetch_failed":
            updated_reason = rights_rejection(previous.get("body", ""), [c.get("name", "") for c in previous.get("categories", [])])
            previous["rights_rule_version"] = "gv-rights-2"
            if updated_reason:
                previous.update({"status": "excluded", "exclusion_reason": updated_reason})
            write_json(path, previous)
            return previous
    record = {
        "id": doc_id, "publisher_id": row["id"], "source_id": f"globalvoices:{row['id']}",
        "source_url": row["link"], "title": clean(html.unescape(row["title"]["rendered"])),
        "published_at": row["date_gmt"] + "Z", "modified_at": row.get("modified_gmt", "") + "Z",
        "api_author_id": row.get("author"), "api_content_sha256": api_hash,
        "api_response_sha256": row["_api_response_sha256"], "api_retrieved_at": row["_api_retrieved_at"],
        "extractor_version": VERSION, "rights_rule_version": "gv-rights-2", "language": "en", "synthetic": False,
        "language_basis": "English edition at globalvoices.org; quoted passages may be in other languages.",
        "institution": "Global Voices", "document_type": "news_article",
        "categories": [categories.get(str(i), {"id": i}) for i in row.get("categories", [])],
        "tag_ids": row.get("tags", []), "policy_sha256": policy["sha256"],
    }
    body, blocks, exclusions = extract_body(api_body, doc_id)
    record.update({"body": body, "blocks": blocks, "body_sha256": sha(body),
                   "word_count": len(body.split()), "page_count": None,
                   "excluded_material": exclusions, "body_characters": len(body)})
    reason = rights_rejection(body, [c.get("name", "") for c in record["categories"]])
    if len(body.split()) < 80:
        reason = "less_than_80_words_not_substantive_article"
    if not row["link"].startswith("https://globalvoices.org/"):
        reason = "not_english_main_site"
    if reason:
        record.update({"status": "excluded", "exclusion_reason": reason})
        write_json(path, record)
        return record
    try:
        raw_path = cache / "html" / f"{doc_id}.html.gz"
        if raw_path.exists() and previous and previous.get("api_content_sha256") == api_hash:
            with gzip.open(raw_path, "rb") as source:
                raw = source.read()
            resolved_url = previous.get("resolved_source_url", row["link"])
            retrieved_at = previous.get("retrieved_at", now())
        else:
            response = await request(client, limiter, row["link"])
            raw = response.content
            resolved_url = str(response.url)
            retrieved_at = now()
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(raw_path, "wb") as output:
                output.write(raw)
        credits, licenses, canonical = credits_and_license(raw)
        identity = re.search(rb"postid-(\d+)", raw)
        observed_post_id = int(identity.group(1)) if identity else None
        record.update({"retrieved_at": retrieved_at, "raw_html_sha256": sha(raw),
                       "resolved_source_url": resolved_url, "canonical_url": canonical,
                       "source_bytes": len(raw), **credits, "observed_license_urls": licenses})
        record["observed_html_post_id"] = observed_post_id
        if observed_post_id != row["id"]:
            reason = "source_article_identity_not_verified"
        elif LICENSE not in licenses:
            reason = "expected_article_license_not_observed"
        elif not credits["authors"]:
            reason = "current_article_byline_not_resolved"
        record["status"] = "excluded" if reason else "eligible"
        record["exclusion_reason"] = reason
        record["license"] = "CC-BY-3.0"
        record["license_url"] = LICENSE
        record["rights_source_url"] = POLICY
        record["rights_review"] = {
            "status": "publisher_license_and_article_footer_checked",
            "license_urls": licenses, "raw_html_sha256": sha(raw), "policy_sha256": policy["sha256"],
            "scope": "Global Voices-created text under publisher CC-BY terms; external media/embeds excluded. Journalistic quotations retain attribution and original rights. No exhaustive legal-clearance claim.",
        }
    except Exception as error:
        record.update({"status": "fetch_failed", "exclusion_reason": type(error).__name__ + ": " + str(error)[:200]})
    write_json(path, record)
    return record


def quotas(counts, target):
    total = sum(counts.values())
    if total < target:
        raise ValueError(f"Only {total} eligible source documents for requested {target}.")
    result = {month: target * count // total for month, count in counts.items()}
    left = target - sum(result.values())
    order = sorted(counts, key=lambda month: (-(target * counts[month] % total), month))
    for month in order[:left]:
        result[month] += 1
    return result


def shingles(text):
    words = re.findall(r"\w+", text.casefold())
    return {hashlib.sha256(" ".join(words[i:i + 5]).encode()).digest()[:8] for i in range(max(0, len(words) - 4))}


def deduplicate(records):
    """Group exact and high-overlap text editions; never infer common events."""
    parents = {r["id"]: r["id"] for r in records}
    by_id = {r["id"]: r for r in records}

    def find(value):
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    exact, buckets = {}, defaultdict(list)
    for record in records:
        canonical_hash = sha(clean(record["body"]).casefold())
        if canonical_hash in exact:
            parents[find(record["id"])] = find(exact[canonical_hash])
            continue
        exact[canonical_hash] = record["id"]
        hashes = shingles(record["body"])
        shared = Counter(other for token in heapq.nsmallest(8, hashes) for other in buckets[token])
        for other, overlaps in shared.items():
            if overlaps < 2:
                continue
            prior = by_id[other]
            if min(record["word_count"], prior["word_count"]) / max(record["word_count"], prior["word_count"]) < 0.8:
                continue
            other_hashes = shingles(prior["body"])
            if len(hashes & other_hashes) / max(1, len(hashes | other_hashes)) >= 0.85:
                parents[find(record["id"])] = find(other)
        for token in heapq.nsmallest(8, hashes):
            buckets[token].append(record["id"])
    groups = defaultdict(list)
    for record in records:
        groups[find(record["id"])].append(record)
    representatives = []
    for members in groups.values():
        members.sort(key=lambda r: (r["published_at"], r["id"]))
        group_id = "story-" + stable(SEED, *sorted(r["id"] for r in members))[:20]
        representative = members[0]
        representative["story_group_id"] = group_id
        representative["duplicate_source_ids"] = [r["source_id"] for r in members[1:]]
        representatives.append(representative)
    return representatives


def select_sample(records, target, seed):
    counts = Counter(r["published_at"][:7] for r in records)
    allocation = quotas(counts, target)
    grouped = defaultdict(list)
    for record in records:
        grouped[record["published_at"][:7]].append(record)
    selected = []
    for month in sorted(grouped):
        ordered = sorted(grouped[month], key=lambda r: (stable(seed, "cohort", r["source_id"]), r["id"]))
        for record in ordered[:allocation[month]]:
            record["sampling"] = {"month": month, "eligible_month_documents": counts[month],
                                  "selected_month_documents": allocation[month],
                                  "inclusion_probability": allocation[month] / counts[month],
                                  "seed": str(seed), "method": "monthly-proportional-largest-remainder-seeded-hash"}
            selected.append(record)
    heldout_counts = quotas(Counter(r["published_at"][:7] for r in selected), min(1000, target // 5))
    for month in sorted(grouped):
        members = sorted([r for r in selected if r["published_at"].startswith(month)],
                         key=lambda r: stable(seed, "heldout", r["story_group_id"]))
        heldout = {r["id"] for r in members[:heldout_counts.get(month, 0)]}
        for record in members:
            record["split"] = "holdout" if record["id"] in heldout else "development"
    return sorted(selected, key=lambda r: (r["published_at"], r["id"])), allocation, counts


def publish(records, base, frame, policy, seed):
    selected, allocation, eligible_counts = select_sample(records, frame["target"], seed)
    base.mkdir(parents=True, exist_ok=True)
    expected = {r["id"] for r in selected}
    for folder, suffix in [(base / "articles", ".txt"), (base / "metadata", ".json")]:
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*" + suffix):
            if old.stem not in expected:
                old.unlink()
    manifest, heldout = [], []
    for record in selected:
        for block in record["blocks"]:
            block["source_links"] = [public_source_link(url) for url in block.get("source_links", [])]
        authors = "; ".join(a["name"] for a in record["authors"])
        translator = "; ".join(a["name"] for a in record.get("translators", []))
        header = (f"{record['title']}\nWritten by: {authors}\n"
                  + (f"Translated by: {translator}\n" if translator else "")
                  + f"Originally published by Global Voices: {record['source_url']}\nPublished: {record['published_at']}\n"
                  + f"Licence: CC BY 3.0 ({LICENSE})\n"
                  + "Text edition: HTML converted to plain text; external media/social embeds omitted; > marks publisher blockquotes.\n")
        full = header + DELIMITER + record["body"] + "\n"
        text_path = base / "articles" / f"{record['id']}.txt"
        text_path.write_text(full, encoding="utf-8")
        sidecar = {k: v for k, v in record.items() if k != "body"}
        sidecar.update({"author": authors, "body_start": len(header + DELIMITER), "body_end": len(full) - 1,
                        "offset_unit": "unicode_code_points", "txt_sha256": sha(full),
                        "text_path": f"articles/{record['id']}.txt",
                        "metadata_path": f"metadata/{record['id']}.json",
                        "attribution": f"This story by {authors} originally appeared on Global Voices. {record['source_url']}",
                        "content_sha256": sha(json.dumps(record["blocks"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
                        "source_sha256": record["raw_html_sha256"],
                        "historical_revision_notice": "Current retrieved text grouped by original publication date; not a reconstructed first edition."})
        write_json(base / sidecar["metadata_path"], sidecar)
        row = {k: v for k, v in sidecar.items() if k not in {"blocks"}}
        manifest.append(row)
        if record["split"] == "holdout":
            heldout.append({"id": record["id"], "source_id": record["source_id"], "story_group_id": record["story_group_id"],
                            "body_sha256": record["body_sha256"], "published_at": record["published_at"]})
    write_jsonl(base / "manifest.jsonl", manifest)
    write_jsonl(base / "holdout.jsonl", heldout)
    write_json(base / "collection.json", {
        "schema_version": 1, "version": VERSION, "status": "complete", "frozen_at": now(), **frame,
        "publisher": "Global Voices", "language": "en", "seed": str(seed),
        "article_count": len(selected), "holdout_count": len(heldout),
        "institutional_case_studies_included": False, "policy": policy,
        "monthly_allocation": allocation, "eligible_month_counts": dict(eligible_counts),
        "selection_notice": "No keyword, predicted issue, or expected label influences selection. This is one publisher's selected news corpus, not a representative sample of all media.",
        "duplicate_policy": "One earliest-publication representative per exact/high-overlap text group; bottom-8 five-word-shingle candidate search, two shared hashes, Jaccard >=0.85 and length ratio >=0.80. Not exhaustive event-level grouping.",
    })
    # Gzip metadata shards stay well below hosting/file-size limits.
    for old in base.glob("metadata-part-*.jsonl.gz"):
        old.unlink()
    for start in range(0, len(selected), 500):
        with gzip.open(base / f"metadata-part-{start // 500 + 1:03d}.jsonl.gz", "wt", encoding="utf-8") as output:
            for record in selected[start:start + 500]:
                sidecar = json.loads((base / "metadata" / f"{record['id']}.json").read_text())
                output.write(json.dumps(sidecar, ensure_ascii=False, separators=(",", ":")) + "\n")
    return audit(base)


def iter_news_documents(base=None):
    """Yield shared DOXA extracted-document contracts; attribution headers are excluded."""
    base = Path(base or ROOT / "corpus" / "news")
    with (base / "manifest.jsonl").open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            document = json.loads((base / row["metadata_path"]).read_text(encoding="utf-8"))
            yield document


def audit(base):
    collection = json.loads((base / "collection.json").read_text())
    rows = [json.loads(line) for line in (base / "manifest.jsonl").read_text().splitlines()]
    errors, words, body_hashes, groups, heldout = [], 0, set(), set(), set()
    for row in rows:
        doc = json.loads((base / row["metadata_path"]).read_text())
        raw = (base / row["text_path"]).read_text(encoding="utf-8")
        body = raw[doc["body_start"]:doc["body_end"]]
        checks = {
            "txt_checksum": sha(raw) == doc["txt_sha256"],
            "body_checksum": sha(body) == doc["body_sha256"],
            "attribution_not_analyzed": raw[:doc["body_start"]].endswith(DELIMITER),
            "substantive_full_text": doc["word_count"] >= 80 and len(body.split()) == doc["word_count"],
            "blocks_resolve": all(body[b["body_start"]:b["body_end"]] == b["text"] for b in doc["blocks"]),
            "article_license_observed": LICENSE in doc.get("observed_license_urls", []),
            "byline_present": bool(doc.get("authors")),
            "public_real_news": doc["synthetic"] is False and doc["source_url"].startswith("https://globalvoices.org/"),
            "raw_source_checksum": bool(re.fullmatch("[a-f0-9]{64}", doc.get("raw_html_sha256", ""))),
            "source_identity": doc.get("observed_html_post_id") == doc.get("publisher_id"),
            "unique_body": doc["body_sha256"] not in body_hashes,
            "unique_story_group": doc["story_group_id"] not in groups,
        }
        if not all(checks.values()):
            errors.append({"id": doc["id"], "failed_checks": [k for k, v in checks.items() if not v]})
        words += doc["word_count"]
        body_hashes.add(doc["body_sha256"])
        groups.add(doc["story_group_id"])
        if doc["split"] == "holdout":
            heldout.add(doc["id"])
    if len(rows) != collection["target"]:
        errors.append({"count": len(rows), "expected": collection["target"]})
    if len(heldout) != min(1000, collection["target"] // 5):
        errors.append({"holdout": len(heldout), "expected": min(1000, collection["target"] // 5)})
    heldout_rows = [json.loads(line) for line in (base / "holdout.jsonl").read_text().splitlines()]
    heldout_ids = {row["id"] for row in heldout_rows}
    if heldout_ids != heldout or len(heldout_rows) != len(heldout):
        errors.append({"holdout_manifest": "Frozen holdout membership differs from article sidecars."})
    actual_months = dict(Counter(row["published_at"][:7] for row in rows))
    if actual_months != {month: count for month, count in collection["monthly_allocation"].items() if count}:
        errors.append({"monthly_allocation": "Article dates do not match frozen monthly quotas."})
    artifact_sizes = [(path.stat().st_size, str(path.relative_to(base))) for path in base.rglob("*") if path.is_file()]
    oversized = [name for size, name in artifact_sizes if size >= 50 * 1024 * 1024]
    if oversized:
        errors.append({"artifacts_at_least_50_mib": oversized})
    report = {"schema_version": 1, "audited_at": now(), "status": "pass" if not errors else "fail",
              "article_count": len(rows), "word_count": words, "holdout_count": len(heldout),
              "rights_status": "Publisher licence and article-footer evidence checked; exceptions/exclusions recorded. Not exhaustive legal clearance.",
              "interpretation_accuracy": None, "independent_human_labels": False, "errors": errors,
              "manifest_sha256": sha((base / "manifest.jsonl").read_bytes()),
              "holdout_manifest_sha256": sha((base / "holdout.jsonl").read_bytes()),
              "largest_artifact_bytes": max((size for size, _ in artifact_sizes), default=0),
              "largest_artifact": max(artifact_sizes, default=(0, ""))[1]}
    write_json(base / "audit.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


def audit_sources(base, cache):
    """Independently compare every selected body with frozen visible API text."""
    collection = json.loads((base / "collection.json").read_text())
    manifest = {row["publisher_id"]: row for row in
                (json.loads(line) for line in (base / "manifest.jsonl").read_text().splitlines())}
    receipts, errors = [], []
    folder = cache / f"api-{collection['frame_start'][:4]}-2025"
    if not folder.exists():
        raise ValueError("Frozen source API cache is required for --audit-sources; no network is requested.")

    def inventory(text):
        return unicodedata.normalize("NFC", re.sub(r"\s+", "", text))

    for path in sorted(folder.glob("page-*.json.gz")):
        for row in read_gzip_json(path)["records"]:
            if row["id"] not in manifest:
                continue
            record = manifest[row["id"]]
            doc = json.loads((base / record["metadata_path"]).read_text())
            rendered = row.get("content", {}).get("rendered", "")
            soup = BeautifulSoup(rendered, "html.parser")
            for node in soup.select(".wp-caption,figure,figcaption,script,style,iframe,object,embed,video,audio,img,picture,svg,blockquote.twitter-tweet,blockquote.instagram-media,twitter-widget,.instagram-media,.fb-post"):
                node.decompose()
            expected = inventory(soup.get_text("", strip=False))
            observed = inventory("".join(block["text"][2:] if block.get("source_is_quotation") and block["text"].startswith("> ") else block["text"] for block in doc["blocks"]))
            matched = expected == observed and sha(rendered) == doc["api_content_sha256"]
            receipt = {"id": doc["id"], "api_content_sha256": sha(rendered),
                       "expected_visible_text_sha256": sha(expected), "observed_visible_text_sha256": sha(observed),
                       "visible_nonwhitespace_characters": len(expected), "matched": matched}
            receipts.append(receipt)
            if not matched:
                errors.append({"id": doc["id"], "expected_characters": len(expected), "observed_characters": len(observed)})
    if len(receipts) != len(manifest):
        errors.append({"checked": len(receipts), "expected": len(manifest)})
    with gzip.open(base / "source-audit-receipts.jsonl.gz", "wt", encoding="utf-8") as output:
        for receipt in sorted(receipts, key=lambda r: r["id"]):
            output.write(json.dumps(receipt, separators=(",", ":")) + "\n")
    report = {"schema_version": 1, "audited_at": now(), "status": "pass" if not errors else "fail",
              "checked": len(receipts), "matched": sum(r["matched"] for r in receipts), "errors": errors,
              "method": "Independent visible-DOM character-order comparison against frozen API content; whitespace/NFC normalization and generated blockquote markers excluded.",
              "scope": "All selected publisher-visible text after declared media/embed exclusions; not semantic accuracy or first-edition reconstruction."}
    write_json(base / "source-audit.json", report)
    print(json.dumps(report, indent=2), flush=True)
    return report


async def collect(args):
    base, cache = args.output, args.cache
    cache.mkdir(parents=True, exist_ok=True)
    base.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(args.rate)
    progress = {"status": "collecting", "started_at": now(), "target": args.count, "requests_per_second": args.rate,
                "workers": args.workers, "fetched": 0, "eligible": 0, "excluded": 0, "failed": 0}
    write_json(base / "progress.json", progress)
    headers = {"User-Agent": "DOXA-by-TACITUS/0.2 (licensed academic news corpus; rate-bounded public GET requests)", "Accept": "application/json,text/html"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=15), follow_redirects=True, headers=headers,
                                 limits=httpx.Limits(max_connections=args.workers, max_keepalive_connections=args.workers)) as client:
        policy_path = cache / "policy.json"
        if policy_path.exists():
            policy = json.loads(policy_path.read_text())
        else:
            response = await request(client, limiter, POLICY)
            policy = {"url": POLICY, "retrieved_at": now(), "sha256": sha(response.content),
                      "license_url": LICENSE, "text": clean(BeautifulSoup(response.content, "html.parser").get_text(" ", strip=True))}
            write_json(policy_path, policy)
        categories = await taxonomy(client, limiter, cache)
        for start_year in [2020, 2019]:
            rows, frame_state = await metadata(client, limiter, cache, start_year)
            progress.update({"frame_start": f"{start_year}-01-01", "frame_end": "2025-12-31",
                             "candidate_count": len(rows), "metadata_frozen_at": frame_state["frozen_at"],
                             "fetched": 0, "eligible": 0, "excluded": 0, "failed": 0})
            write_json(base / "progress.json", progress)
            queue = asyncio.Queue()
            for row in rows:
                queue.put_nowait(row)
            results = []
            lock = asyncio.Lock()

            async def worker():
                while True:
                    try:
                        row = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    value = await article(client, limiter, row, categories, cache, policy)
                    async with lock:
                        results.append(value)
                        progress["fetched"] += 1
                        progress[{"eligible": "eligible", "excluded": "excluded", "fetch_failed": "failed"}[value["status"]]] += 1
                        if progress["fetched"] % 25 == 0 or progress["fetched"] == len(rows):
                            progress["updated_at"] = now()
                            write_json(base / "progress.json", progress)
                            print(f"Articles {progress['fetched']}/{len(rows)}; eligible {progress['eligible']}; excluded {progress['excluded']}; failed {progress['failed']}", flush=True)
                    queue.task_done()

            await asyncio.gather(*(worker() for _ in range(args.workers)))
            receipts = [{k: v for k, v in record.items() if k not in {"body", "blocks"}} for record in sorted(results, key=lambda r: r["id"])]
            with gzip.open(base / f"candidate-receipts-{start_year}-2025.jsonl.gz", "wt", encoding="utf-8") as output:
                for record in receipts:
                    output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            if progress["failed"]:
                progress["status"] = "incomplete_fetches_resume_required"
                write_json(base / "progress.json", progress)
                raise RuntimeError("Public-source fetches remain incomplete; resume collection. No fabricated replacements or published complete corpus.")
            eligible = deduplicate([record for record in results if record["status"] == "eligible"])
            progress["eligible_unique_story_groups"] = len(eligible)
            if len(eligible) >= args.count:
                frame = {"frame_start": f"{start_year}-01-01", "frame_end": "2025-12-31", "target": args.count,
                         "metadata_candidate_count": len(rows), "eligible_unique_story_groups": len(eligible),
                         "metadata_frozen_at": frame_state["frozen_at"],
                         "fallback_activated": start_year == 2019}
                report = publish(eligible, base, frame, policy, args.seed)
                progress["status"] = "complete" if report["status"] == "pass" else "audit_failed"
                progress["published_article_count"] = report["article_count"]
                write_json(base / "progress.json", progress)
                return 0 if report["status"] == "pass" else 1
            if start_year == 2020:
                print(f"Only {len(eligible)} eligible unique stories; activating predeclared 2019-2025 frame version.", flush=True)
        progress["status"] = "eligible_source_shortfall"
        write_json(base / "progress.json", progress)
        raise RuntimeError("Both predeclared frames have fewer eligible stories than requested. No chunked or fabricated padding.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--audit-sources", action="store_true", help="Compare selected bodies with frozen source API cache, without network.")
    parser.add_argument("--output", type=Path, default=ROOT / "corpus" / "news")
    parser.add_argument("--cache", type=Path, default=ROOT / "data" / "news-cache")
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--seed", default=SEED)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--rate", type=float, default=3.0, help="Global maximum GET requests per second.")
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or not 0 < args.rate <= 5 or args.count < 1:
        parser.error("Use 1-8 workers, 0<rate<=5 requests/second and a positive document count.")
    if args.collect:
        return asyncio.run(collect(args))
    if args.audit:
        return 0 if audit(args.output)["status"] == "pass" else 1
    if args.audit_sources:
        return 0 if audit_sources(args.output, args.cache)["status"] == "pass" else 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
