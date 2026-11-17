# DOXA dataset and recorded-result card

**Release:** 0.4.0. The v0.3 source/result baseline is preserved; the new case atlas, lineage files and evaluation scaffold are derived publications, not new gold labels.

| Collection | Scope and status |
| --- | --- |
| Global Voices | 5,000 frozen articles; 5,251,119 whitespace-counted words; 778 distinct credited byline names; 585 translated articles; 72 publication months, 2020–2025. |
| Institutional | 20 purposively selected UK/US public publications, approximately 357,000 words, with source-specific rights. |
| Political case packet | Four recorded institutional sources used for detailed framing examples; illustrative, not representative. |
| Independent-model verification | A targeted recorded comparison over 79 units, with 1,500 paired assessments; not independent human labels. |
| v0.4 case atlas | Fifteen curated illustrative cases, frozen original passages/model readings and separately labeled AI commentary. |
| Human evaluation scaffold | 200 sampled cases: 100 development diagnostics and 100 held out, separate story groups; no reference labels yet. |
| Functional fixtures | 24 authored base cases × five transformations = 120 fixtures; protocol expectations, not executed model outcomes. |

## Construction and provenance

The canonical news manifest is [corpus/news/manifest.jsonl](../corpus/news/manifest.jsonl), SHA256 `6ea29f063abcdda5425d91277c9137cbb66f461fa2fff26f1f3543c51fb02ff3`. Individual records preserve source URL, title, author/translator, publication date, rights, exclusions and content hashes. Normalized TXT is provided for reproducible inference; saved extraction changes distinguish body text from publisher furniture.

The frozen month-stratified article grouping separates 4,000 development and 1,000 held-out articles. That corpus partition differs from the new 100/100 human-review sample. Keep story groups disjoint and record all exposure before using any held-out labels.

## Recorded analysis and missingness

The six-framework news run is **partial**: all 5,000 articles attempted; 4,927 complete documents; 10,686 of 10,759 accepted units; 204,342 of 204,421 decisions. Missing decisions remain missing, rather than negative. The portable [showcase manifest](../benchmarks/v3-news/showcase.json) and split compressed parts retain original outputs, unit states and usage. The retained 3,860,336 source references were checked against frozen text.

The v0.4 publication audit checks 4,207,998 exact references across five recorded archives. That mechanical audit does not adjudicate semantic correctness. Full archive hashes and limits are in the [evidence pack](../examples/v04-evidence-pack.manifest.json).

## Rights and appropriate use

Global Voices notices record observed CC BY 3.0/publisher terms and exclusions; institutional material retains its own source notices. Follow [licence guidance](licenses.md), per-source records and attribution requirements. Model weights and dependencies carry separate terms. This is a documented audit, not exhaustive legal clearance.

Global Voices is one publisher, not a representative sample of all media or political perspectives. Credited name strings are not resolved personal identities. Current frozen editions do not reconstruct all historical versions. Dates describe publication time; assessment/review history has a separate clock. Read the [news collection audit](news-corpus.md), [institutional card](corpus.md) and [evaluation protocol](evaluation-v04.md) before generalizing results.
