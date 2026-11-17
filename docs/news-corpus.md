# The 5,000-article news corpus

This collection is separate from the twenty institutional case studies in `corpus/documents/`. Its purpose is to provide genuine English news articles, real publication dates and attributed writers for contextual analysis and publication-date dashboards.

The source is **Global Voices**, a multilingual newsroom and community of writers/translators. This is one publisher's editorial collection, not a representative sample of all news media. Selection never uses antisemitism, Islamophobia, predicted findings or expected alert outcomes.

The bundled snapshot completed on **16 November 2026**: **5,000 articles**, **5,251,119 words measured by whitespace splitting**, **778 distinct credited author names**, **585 translated articles**, and **72 publication months**. This word count is not an inference provider's billable token count. All 6,754 candidates were processed; 5,978 passed eligibility and duplicate checks and 776 were excluded. The 2019 fallback was not needed. Exactly 1,000 article groups are held out and 4,000 are development material.

Both the file/provenance audit and the independent source-character audit passed **5,000/5,000**. The latter verifies that all selected publisher text is retained in order after the declared media/embed exclusions and formatting normalization. These are source-integrity results, not model-interpretation accuracy. No paid inference was used to construct this collection.

## Collection status and source frame

The default frame is **1 January 2020 through 31 December 2025 UTC**, using published posts from the English main site, `globalvoices.org`. Public API metadata verified 6,754 candidates: 1,198 in 2020; 1,165 in 2021; 1,111 in 2022; 1,204 in 2023; 1,070 in 2024; and 1,006 in 2025. These counts precede substantive-body, rights, byline and duplicate checks.

The target is **5,000 distinct, complete source articles**, not fragments of the twenty institutional documents. An unavailable or ineligible article is not replaced with generated text. Articles with fewer than eighty extracted whitespace tokens are excluded as insubstantial. Publisher-internal announcements and unresolved third-party republications are excluded. Full long articles remain full; no maximum-length truncation is used.

If the default frame has fewer than 5,000 eligible unique text groups, the collector activates the predeclared **2019–2025 frame** and records that change in its collection manifest. The additional year has 1,108 metadata candidates. If both frames are insufficient, the collector stops with an explicit shortfall. It never changes the time frame to force a topic spike.

While downloading, `corpus/news/progress.json` records actual processed, eligible, excluded and failed counts. A completed collection requires `collection.json` and a passing `audit.json`; progress or candidate counts alone are not proof that 5,000 articles were delivered.

## Download, resume and audit

```bash
# Explicit network work; default target 5,000, seed 20261116.
python scripts/fetch_news_corpus.py --collect --workers 6 --rate 3

# Repeat the same command to resume frozen metadata and per-article caches.
python scripts/fetch_news_corpus.py --collect --workers 6 --rate 3

# Offline integrity check of an already published collection.
python scripts/fetch_news_corpus.py --audit

# Independently check all selected bodies against the frozen API source cache.
python scripts/fetch_news_corpus.py --audit-sources
```

`--rate 3` means a global maximum of three GET requests per second, shared by all workers. The default is four workers, the examples use six, and the supported maximum is eight. The maximum permitted rate is five requests per second. HTTP 429 responses honor a bounded Retry-After delay; failed requests retry with backoff. HTTPS verification and inherited proxy settings remain enabled.

Frozen API pages, source HTML, rights policy and per-article extraction receipts are cached under the ignored directory `data/news-cache/`. A resume reuses completed work and reattempts failed public fetches. Extraction-version changes can regenerate article text from cached API/HTML data without repeating successful downloads. Source data and retrieval dates remain recorded.

All requests are public GET operations; no login, API key, comments, notifications or form submissions are required. The collector does not execute source scripts or fetch embedded media.

## Licensing and attribution

The [Global Voices republishing policy](https://globalvoices.org/about/global-voices-attribution-policy/) permits sharing and adaptation, including commercial reuse, unless otherwise stated. It requires the author and original article link at the top, appropriate credit, the licence link and an indication of changes. Current article footer links specify [Creative Commons Attribution 3.0](https://creativecommons.org/licenses/by/3.0/).

For each eligible article the collector records:

- The observed article licence links, publisher-policy checksum and source HTML checksum.
- A matching WordPress post ID in the source HTML, rather than crediting a redirected/unrelated article.
- Current-article author and translator bylines, their public profile URLs, and distinct roles.
- Rights-related exclusions and omitted non-text/external material.

A site-wide footer does not override an explicit article-specific restriction or establish rights to partner material. Notices of an external original publication or permission-based republication are conservatively excluded. External images, captions, video/audio, scripts, iframes and social-media embeds are omitted. Ordinary attributed journalistic quotations remain in their source context and retain their original rights. This is a publisher-licence and source-evidence check, not a claim of exhaustive legal clearance.

The public WordPress author endpoint is restricted, so the collector reads the source article's `.contributor-name` credits. It excludes unrelated story-card bylines, which use a different structure, and deduplicates repeated header/footer credits. Missing current-article author credits cause exclusion rather than invention.

## Files and body offsets

A completed collection contains:

| Path | Content |
| --- | --- |
| `articles/gv-<post-id>.txt` | One real article per UTF-8 file, preceded by attribution/licence metadata |
| `metadata/gv-<post-id>.json` | Source metadata, rights receipt, body offsets, exact extracted blocks and checksums |
| `manifest.jsonl` | Five thousand article records without duplicating full block bodies |
| `holdout.jsonl` | One thousand frozen heldout article/story-group references |
| `metadata-part-*.jsonl.gz` | Metadata/body-block shards of at most 500 articles for convenient distribution |
| `candidate-receipts-<frame>.jsonl.gz` | Candidate eligibility/exclusion receipts, including unselected candidates |
| `collection.json` | Frame, seed, monthly allocation, policy and duplicate rules |
| `audit.json` | Actual integrity results; interpretation accuracy remains null |

The TXT header gives title, byline, translator when present, original URL, publication date and CC BY 3.0 attribution. The delimiter `=== ARTICLE BODY ===` precedes the source article body. Sidecar `body_start` and `body_end` are Unicode-code-point offsets into the complete TXT file, excluding the header and trailing file newline. Article block `body_start` / `body_end` are relative to that body, not to the attribution header.

Publisher HTML blockquotes are rendered with a `> ` prefix and retain `source_kind: blockquote` / `source_is_quotation: true`. This preserves quotation context during the HTML-to-TXT transformation. It does not decide whether the journalist endorses a quoted speaker. Links attached to source blocks are recorded separately.

`iter_news_documents()` in the collector yields the shared extracted-document shape used by DOXA: stable ID, title, author, institution, document type, publication date, source/rights metadata and blocks with paragraph locators. HTML page numbers remain unavailable (`null`), rather than invented.

Checksums distinguish the raw article HTML, API-rendered content, persisted article body, full TXT file and canonical block JSON. Source HTML may contain changing surrounding website content; the body checksum identifies the exact text analyzed.

## Sampling, duplicates and the holdout

Sampling begins after eligibility and duplicate processing. Monthly quotas are proportional to eligible unique article groups and use largest remainders to total exactly 5,000. Within each month, a seeded SHA256 order selects source IDs. Each selected record carries its month count, selected quota and conditional inclusion probability.

The collector groups exact text editions and high-overlap near copies, retaining the earliest publication representative. Near-copy candidates use the bottom eight five-word-shingle hashes; candidate pairs need two shared hashes, at least 85% shingle Jaccard similarity and a length ratio of at least 0.80. This is transparent text/republication grouping, not exhaustive semantic detection of every story about the same event.

One thousand selected story groups enter the heldout manifest using separate seeded, month-proportional allocation. The remaining four thousand are development material. Duplicate groups do not cross the split. No human interpretation labels are fabricated, and the heldout split does not itself establish model accuracy.

## Historical dates and dashboard limits

Public source APIs expose current article text and modification timestamps, not anonymous first-edition revision history. This collection freezes the text available at retrieval in 2026 and groups it by the original English publication date. It must not be presented as a reconstruction of exactly what readers saw in the first edition.

Keep original publication date, English translation publication date where applicable, modification time and collection time distinct. Historical backfills belong to their original publication periods; a new import or modified article must not create a new-publication spike.

The twenty institutional case studies are excluded from the default news denominator. Topic navigation and contextual classifications operate after cohort selection. A rise in reporting about prejudice, a rise in quoted hostility and author-endorsed hostility are different series. A reliable increase may be absent; no articles are enriched or invented to create one.

## Verification

The collector's offline audit checks exact article/holdout counts, TXT/body hashes, locators, author/source/licence evidence, source post identity, unique bodies and story groups. It writes actual measured corpus word totals and records that independent interpretation labels are unavailable.

The separate source-visibility audit compares every selected body's character order against all visible text in the frozen API HTML, after the declared media/embed exclusions. Whitespace/NFC normalization and generated blockquote prefixes are accounted for. This catches lost unwrapped editorial notes as well as missing article paragraphs. Its receipts are stored in `source-audit.json` and compressed per-article `source-audit-receipts.jsonl.gz`; the source API cache is required, and no network requests are made by this check.

`pytest tests/test_news_corpus.py` covers source-structure parsing, quotation/media separation, rights exceptions, quota determinism, duplicate grouping and attribution/body boundaries. The full bundled-data check runs once a completed collection exists; before that, it explicitly reports that collection validation has not yet occurred.
