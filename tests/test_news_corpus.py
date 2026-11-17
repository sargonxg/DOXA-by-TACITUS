"""News collector contracts use synthetic HTML fixtures; the bundled news must be real."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("news_collector", ROOT / "scripts" / "fetch_news_corpus.py")
news = importlib.util.module_from_spec(spec)
spec.loader.exec_module(news)


def test_current_article_credits_exclude_related_story_cards():
    raw = b"""<html><body>
    <div class="contributor-name"><span class="credit-label">Written by</span><a class="user-link" href="/author/alice/">Alice</a></div>
    <div class="contributor-name"><span class="credit-label">Translated by</span><a class="user-link" href="/author/bob/">Bob</a></div>
    <div class="contributor-name"><span class="credit-label">Written by</span><a class="user-link" href="/author/alice/">Alice</a></div>
    <article class="gv-promo-card"><div class="text-credits-section"><span class="credit-label">Written by</span><a class="user-link" href="/author/unrelated/">Unrelated</a></div></article>
    <a rel="license" href="https://creativecommons.org/licenses/by/3.0/">Licence</a>
    </body></html>"""
    credits, licenses, _ = news.credits_and_license(raw)
    assert [person["name"] for person in credits["authors"]] == ["Alice"]
    assert [person["name"] for person in credits["translators"]] == ["Bob"]
    assert news.LICENSE in licenses


def test_body_keeps_journalistic_quotation_but_excludes_external_embeds():
    rendered = """<p>The reporter described a public statement.</p>
    <blockquote><p>Equal rights should apply to everybody.</p></blockquote>
    <figure><img src="remote.jpg"><figcaption>Third-party photo caption</figcaption></figure>
    <blockquote class="twitter-tweet">Embedded external tweet</blockquote>
    <script>run_untrusted()</script><p>A café discussion followed.</p>"""
    body, blocks, exclusions = news.extract_body(rendered, "gv-1")
    assert "Equal rights should apply" in body and "café" in body
    assert "> Equal rights should apply" in body
    assert "Third-party photo caption" not in body
    assert "Embedded external tweet" not in body
    assert "run_untrusted" not in body
    assert exclusions
    assert all(body[block["body_start"]:block["body_end"]] == block["text"] for block in blocks)
    assert all(block["page"] is None and block["paragraph"] >= 1 for block in blocks)
    assert any(block["source_is_quotation"] for block in blocks)


def test_unwrapped_editorial_and_republication_notices_are_not_lost():
    rendered = '<em>This story was originally published by Another Newsroom.</em><p>Main article content follows.</p><div>Unwrapped editorial note.</div>'
    body, blocks, _ = news.extract_body(rendered, "gv-editorial")
    assert "originally published by Another Newsroom" in body
    assert "Unwrapped editorial note." in body
    assert news.rights_rejection(body, []) is not None
    assert all(body[b["body_start"]:b["body_end"]] == b["text"] for b in blocks)


def test_rights_exceptions_are_not_assumed_covered_by_publisher_footer():
    assert news.rights_rejection("This article was republished with permission from an external publication.", []) is not None
    assert news.rights_rejection("This story originally appeared on Another Newsroom.", []) is not None
    assert news.rights_rejection("This story originally appeared on Another Newsroom. Global Voices republished it.", []) is not None
    assert news.rights_rejection("This story originally appeared on Global Voices in Portuguese.", []) is None
    assert news.rights_rejection("Creative Commons Attribution-NonCommercial licence applies.", []) is not None
    assert news.rights_rejection("Ordinary news reporting about antisemitism and Islamophobia.", []) is None
    assert news.rights_rejection("A community announcement.", ["Global Voices Community"]) is not None


def test_largest_remainder_allocation_has_exact_count_and_no_topic_input():
    assert news.quotas({"2020-01": 3, "2020-02": 7}, 5) == {"2020-01": 2, "2020-02": 3}
    with pytest.raises(ValueError):
        news.quotas({"2020-01": 2}, 5)


def records(count=10):
    result = []
    for i in range(count):
        doc_id = f"gv-{i}"
        body, blocks, exclusions = news.extract_body(
            "<p>" + ("A journalist reported a café discussion with attributed sources. " * 12)
            + f"Unique fixture number {i}.</p>", doc_id)
        result.append({
            "id": doc_id, "source_id": f"globalvoices:{i}", "publisher_id": i,
            "observed_html_post_id": i,
            "source_url": f"https://globalvoices.org/2020/01/01/unit-fixture-{i}/",
            "title": f"Unit fixture {i}", "published_at": f"2020-0{1 + i % 2}-01T12:00:00Z",
            "modified_at": "2026-01-01T12:00:00Z", "retrieved_at": "2026-11-16T12:00:00Z",
            "authors": [{"name": "Fixture Author", "url": "https://globalvoices.org/author/fixture/"}],
            "translators": [], "institution": "Global Voices", "document_type": "news_article",
            "language": "en", "synthetic": False, "body": body, "blocks": blocks,
            "word_count": len(body.split()), "body_sha256": news.sha(body),
            "story_group_id": f"unit-story-{i}", "raw_html_sha256": "a" * 64,
            "observed_license_urls": [news.LICENSE], "license": "CC-BY-3.0",
            "license_url": news.LICENSE, "rights_source_url": news.POLICY,
            "rights_review": {"status": "unit_fixture_only"},
            "excluded_material": exclusions, "api_content_sha256": "b" * 64,
        })
    return result


def test_seeded_monthly_selection_and_holdout_are_reproducible():
    first, allocation, counts = news.select_sample(records(20), 10, "fixed-seed")
    second, _, _ = news.select_sample(list(reversed(records(20))), 10, "fixed-seed")
    assert [record["id"] for record in first] == [record["id"] for record in second]
    assert len(first) == 10 and sum(allocation.values()) == 10
    assert sum(record["split"] == "holdout" for record in first) == 2
    assert counts == {"2020-01": 10, "2020-02": 10}
    assert all(0 < record["sampling"]["inclusion_probability"] <= 1 for record in first)


def test_exact_and_near_duplicate_groups_cannot_inflate_article_count():
    originals = records(2)
    duplicate = {**originals[0], "id": "gv-copy", "source_id": "globalvoices:copy", "published_at": "2020-03-01T12:00:00Z"}
    groups = news.deduplicate([originals[0], duplicate])
    assert len(groups) == 1
    assert groups[0]["id"] == originals[0]["id"]
    assert duplicate["source_id"] in groups[0]["duplicate_source_ids"]


def test_txt_attribution_is_outside_body_offsets_and_import_contract(tmp_path):
    policy = {"url": news.POLICY, "sha256": "c" * 64}
    frame = {"frame_start": "2020-01-01", "frame_end": "2025-12-31", "target": 10}
    report = news.publish(records(), tmp_path, frame, policy, "unit-seed")
    assert report["status"] == "pass" and report["article_count"] == 10
    imported = list(news.iter_news_documents(tmp_path))
    assert len(imported) == 10
    for doc in imported:
        raw = (tmp_path / doc["text_path"]).read_text()
        body = raw[doc["body_start"]:doc["body_end"]]
        assert "Written by: Fixture Author" in raw[:doc["body_start"]]
        assert "Written by: Fixture Author" not in body
        assert news.sha(body) == doc["body_sha256"]
        assert doc["source_sha256"] == doc["raw_html_sha256"]
        assert all(block["text"] in body for block in doc["blocks"])
    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert all("blocks" not in row for row in manifest)
    heldout_path = tmp_path / "holdout.jsonl"
    heldout = [json.loads(line) for line in heldout_path.read_text().splitlines()]
    heldout[0]["id"] = "tampered-member"
    heldout_path.write_text("\n".join(json.dumps(row) for row in heldout) + "\n")
    assert news.audit(tmp_path)["status"] == "fail"


def test_bundled_real_news_collection_integrity_when_complete():
    base = ROOT / "corpus" / "news"
    if not (base / "collection.json").exists():
        pytest.skip("The explicit real-news collector has not completed; no 5,000-document result is assumed.")
    collection = json.loads((base / "collection.json").read_text())
    rows = [json.loads(line) for line in (base / "manifest.jsonl").read_text().splitlines()]
    assert collection["status"] == "complete" and collection["target"] == 5000
    assert len(rows) == 5000
    assert len(list((base / "articles").glob("*.txt"))) == 5000
    hashes, groups, holdout_groups, development_groups = set(), set(), set(), set()
    for row in rows:
        doc = json.loads((base / row["metadata_path"]).read_text())
        text = (base / row["text_path"]).read_text(encoding="utf-8")
        body = text[doc["body_start"]:doc["body_end"]]
        assert news.sha(text) == doc["txt_sha256"] and news.sha(body) == doc["body_sha256"]
        assert doc["synthetic"] is False and doc["license"] == "CC-BY-3.0"
        assert doc["source_url"].startswith("https://globalvoices.org/")
        assert doc["observed_html_post_id"] == doc["publisher_id"]
        assert doc["authors"] and news.LICENSE in doc["observed_license_urls"]
        assert all(body[b["body_start"]:b["body_end"]] == b["text"] for b in doc["blocks"])
        assert doc["body_sha256"] not in hashes and doc["story_group_id"] not in groups
        hashes.add(doc["body_sha256"])
        groups.add(doc["story_group_id"])
        (holdout_groups if doc["split"] == "holdout" else development_groups).add(doc["story_group_id"])
    assert len(holdout_groups) == 1000 and len(development_groups) == 4000
    assert holdout_groups.isdisjoint(development_groups)


def test_source_visibility_audit_detects_source_mismatch(tmp_path):
    source_rows = records()
    api_rows = []
    for row in source_rows:
        rendered = "<p>" + row["body"] + "</p>"
        row["api_content_sha256"] = news.sha(rendered)
        api_rows.append({"id": row["publisher_id"], "content": {"rendered": rendered}})
    base, cache = tmp_path / "published", tmp_path / "cache"
    news.publish(source_rows, base, {"frame_start": "2020-01-01", "frame_end": "2025-12-31", "target": 10},
                 {"url": news.POLICY, "sha256": "c" * 64}, "source-audit-seed")
    path = cache / "api-2020-2025" / "page-0001.json.gz"
    news.write_gzip_json(path, {"records": api_rows})
    assert news.audit_sources(base, cache)["status"] == "pass"
    api_rows[0]["content"]["rendered"] = "<p>Changed source text must not silently pass.</p>"
    news.write_gzip_json(path, {"records": api_rows})
    assert news.audit_sources(base, cache)["status"] == "fail"


def test_source_links_strip_signing_credentials_and_keep_citation():
    signed = ('https://example.org/paper.pdf?download=1&X-Amz-Credential=fixture'
              '&X-Amz-Security-Token=fixture&X-Amz-Signature=fixture'
              '&X-Amz-Date=20240710T061252Z&X-Amz-Expires=300#page=2')
    public = 'https://example.org/paper.pdf?download=1#page=2'
    assert news.public_source_link(signed) == public
    assert news.public_source_link(public) == public
    assert news.public_source_link('https://example.org/?expires=300') == 'https://example.org/?expires=300'
    _, blocks, _ = news.extract_body(f'<p>A source <a href="{signed}">paper</a>.</p>', 'gv-test')
    assert blocks[0]['source_links'] == [public]
    assert news.public_source_link('https://example.org/?%58-Amz-Credential=fixture') == 'https://example.org/'


def test_publish_sanitizes_cached_source_links(tmp_path):
    rows = records()
    rows[0]['blocks'][0]['source_links'] = ['https://example.org/paper?X-Amz-Credential=fixture']
    news.publish(rows, tmp_path, {'target': 10}, {'url': news.POLICY, 'sha256': 'c' * 64}, 'unit-seed')
    doc = json.loads((tmp_path / 'metadata' / 'gv-0.json').read_text())
    assert doc['blocks'][0]['source_links'] == ['https://example.org/paper']
    assert doc['content_sha256'] == news.sha(json.dumps(doc['blocks'], ensure_ascii=False, sort_keys=True, separators=(',', ':')))
