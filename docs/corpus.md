# The real twenty-document showcase

**Public institutions: prejudice, protection and freedom of expression** is a purposive collection of twenty real English-language public publications, retrieved on 16 November 2026. The bundled text editions contain **356,964 extracted words**, cover **533 PDF pages** plus eleven HTML documents, and occupy about 3.35 MB as JSON. No document or author was invented for the showcase.

The collection supports comparison of institutional framing, protection from discrimination, security policy, religious freedom and speech boundaries. It is not a representative sample of news coverage, society, religious communities or public opinion. A government institution's publication of a discriminatory example does not imply endorsement.

## Use offline or refresh explicitly

The application imports the bundled files under `corpus/documents/`; opening the showcase does not need to retrieve source websites. `corpus/manifest.json` is the source-selection manifest. Each JSON snapshot records title, author, institution, document type, publication date, original and resolved source URLs, language, rights, source-byte SHA256, extracted-content SHA256, retrieval time, extractor version, block count and word count.

PDF blocks have their original 1-based page number and a paragraph index within that page. HTML blocks have a paragraph index and `page: null`; HTML does not have an invented print page number. Block IDs and quoted text can be used directly as evidence locators.

```bash
# No network: validate all bundled snapshots and write the provenance audit.
python scripts/fetch_corpus.py --audit

# Explicit network operation: fetch the same real sources and replace snapshots.
python scripts/fetch_corpus.py --refresh

# Refresh selected source documents only, then audit the full collection.
python scripts/fetch_corpus.py --refresh --ids us03 us04
```

Refresh uses HTTPS with TLS verification, respects the environment's proxy settings, and needs `httpx`, `beautifulsoup4` and `PyMuPDF`. Changed remote content produces a new checksum and retrieval record. Same extracted input produces the same content checksum; live websites may update. The script stops with a failing exit code when a fetch, extraction or integrity check fails. It never silently replaces an unavailable publication with generated text.

## Source manifest

The word counts below are measured from the bundled extracted text, including source notes and bibliography. They are not publisher-supplied polished word counts. A dash in the pages column means HTML.

| ID | Publication; attributed author/institution | Publication date | Pages | Words |
| --- | --- | --- | ---: | ---: |
| uk01 | [Independent Review of Prevent](https://www.gov.uk/government/publications/independent-review-of-prevents-report-and-government-response/independent-review-of-prevent-accessible); William Shawcross / Home Office | 2023-02-08 | — | 69,124 |
| uk02 | [Response to the Independent Review of Prevent](https://www.gov.uk/government/publications/independent-review-of-prevents-report-and-government-response/the-response-to-the-independent-review-of-prevent-accessible); Home Office | 2023-02-08 | — | 10,857 |
| uk03 | [Prevent duty guidance](https://assets.publishing.service.gov.uk/media/65e5a5bd3f69457ff1035fe2/14.258_HO_Prevent+Duty+Guidance_v5d_Final_Web_1_.pdf); Home Office | 2023-09-07 | 67 | 20,993 |
| uk04 | [Hate crime, England and Wales, year ending March 2024](https://www.gov.uk/government/statistics/hate-crime-england-and-wales-year-ending-march-2024/hate-crime-england-and-wales-year-ending-march-2024); Home Office | 2024-10-10 | — | 8,373 |
| uk05 | [The Casey Review: opportunity and integration](https://assets.publishing.service.gov.uk/media/5a80c4fded915d74e6230579/The_Casey_Review_Report.pdf); Dame Louise Casey / Department for Communities and Local Government | 2016-12-05 | 199 | 67,905 |
| uk06 | [The future of the Higher Education (Freedom of Speech) Act 2023](https://assets.publishing.service.gov.uk/media/685ac9c0454906840a44d63f/the-future-of-the-higher-education-freedom-of-speech-act-2023-web-version.pdf); Department for Education | 2025-06-26 | 34 | 10,225 |
| uk07 | [Government response: employment opportunities for Muslims](https://assets.publishing.service.gov.uk/media/5a800a2b40f0b62305b88cee/58077_Cm9371_WEB.pdf); Government Equalities Office | 2016-12-20 | 22 | 7,176 |
| uk08 | [Action Against Hate](https://assets.publishing.service.gov.uk/media/5f8db94a8fa8f56ad07d1152/2016_Hate_Crime_Action_Plan.pdf); Home Office, DCLG and Ministry of Justice | 2016-07-26 | 39 | 12,651 |
| uk09 | [Response to the Lord Mann NHS review](https://www.gov.uk/government/publications/government-response-to-the-lord-mann-review-on-antisemitism-and-other-forms-of-racism-in-the-nhs/government-response-to-the-lord-mann-review-into-antisemitism-and-other-forms-of-racism-in-the-nhs); Department of Health and Social Care | 2026-06-04 | — | 1,331 |
| uk10 | [A Definition of Anti-Muslim Hostility](https://www.gov.uk/guidance/a-definition-of-anti-muslim-hostility); Ministry of Housing, Communities and Local Government | 2026-03-09 | — | 2,826 |
| us01 | [National Strategy to Counter Antisemitism](https://bidenwhitehouse.archives.gov/wp-content/uploads/2023/05/U.S.-National-Strategy-to-Counter-Antisemitism.pdf); Biden White House | 2023-05-25 | 60 | 25,356 |
| us02 | [National Strategy to Counter Islamophobia and Anti-Arab Hate](https://bidenwhitehouse.archives.gov/wp-content/uploads/2024/12/National-Strategy-Doc.pdf); Biden White House | 2024-12-12 | 67 | 34,005 |
| us03 | [Executive Order 14188: Additional Measures To Combat Anti-Semitism](https://www.govinfo.gov/content/pkg/DCPD-202500196/html/DCPD-202500196.htm); President Donald J. Trump / White House | 2025-01-29 | — | 861 |
| us04 | [Executive Order 14149: Restoring Freedom of Speech and Ending Federal Censorship](https://www.govinfo.gov/content/pkg/DCPD-202500116/html/DCPD-202500116.htm); President Donald J. Trump / White House | 2025-01-20 | — | 582 |
| us05 | [Section 12: Religious Discrimination](https://www.eeoc.gov/laws/guidance/section-12-religious-discrimination); EEOC | 2021-01-15 | — | 59,614 |
| us06 | [Employer responsibilities concerning Muslim or Middle Eastern individuals](https://www.eeoc.gov/laws/guidance/questions-and-answers-employers-responsibilities-concerning-employment-individuals); EEOC | 2015-12-23 | — | 1,795 |
| us07 | [Religious Garb and Grooming in the Workplace](https://www.eeoc.gov/laws/guidance/religious-garb-and-grooming-workplace-rights-and-responsibilities); EEOC | 2014-03-06 | — | 7,075 |
| us08 | [Best Practices for Eradicating Religious Discrimination](https://www.eeoc.gov/laws/guidance/best-practices-eradicating-religious-discrimination-workplace); EEOC | 2008-07-22 | — | 1,921 |
| us09 | [Combating Religious Discrimination Today: Final Report](https://www.justice.gov/d9/combating_religious_discrimination_today_final_report_2016.pdf); DOJ Civil Rights Division | 2016-07-21 | 40 | 12,761 |
| us10 | [Combating Religious Discrimination](https://www.justice.gov/d9/2024-03/combating_religious_discrimination.pdf); DOJ Civil Rights Division | 2024-03 | 5 | 1,533 |

The selected Prevent guidance PDF was updated on 6 March 2024. March 2024 is the only publication-date precision given for us10; its manifest uses March 1 as the sortable month representation and explicitly records that convention. Publication dates and retrieval dates are separate.

## Rights and exclusions

UK government-authored information is supplied under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/). Relevant PDFs and report bodies include their Crown copyright/OGL statements; the [GOV.UK terms](https://www.gov.uk/help/terms-conditions) govern standard page content. Attribution: **Contains public sector information licensed under the Open Government Licence v3.0.**

US federal employee works prepared as official duties are public domain under 17 USC 105. See [GovInfo policies](https://www.govinfo.gov/about/policies), [DOJ legal policies](https://www.justice.gov/legalpolicies), and the [Biden White House archive copyright policy](https://bidenwhitehouse.archives.gov/copyright/).

These statements do not transfer third-party copyright merely because a source is a government publication. The bundled edition contains extracted text and excludes images, logos, seals, scripts, forms and website navigation. It does not download linked third-party publications. Source quotations, bibliography and legal citations retain attribution and their original rights; this corpus does not claim a new licence over them or exhaustive legal clearance. Do not reuse an extracted third-party quotation as a separately licensed full work. Source copyright and licence notices remain in the extracted bodies where present.

The metadata records exclusion categories and source limitations. These are text editions of the real publications, not scanned facsimiles. No visual rhetoric, scanned-image text or figure content is inferred. Original source links remain available for contextual verification.

## Important interpretation cases

- The four EEOC documents contain a later notice explaining that *Groff v. DeJoy* (2023) supersedes contrary material concerning undue hardship. That notice was verified in all four bundled text editions. The documents should not be treated as uniformly current legal guidance.
- Hypothetical discriminatory scenarios in the official guidance remain hypothetical examples. Their example characters are not evidence of real people or incidents.
- Two administrations' White House texts and the UK security, equality and free-expression texts support comparisons of expressed policy framing. A difference in framing is not proof of prejudice.
- Repeated institutions support institutional and period comparisons; they do not justify inferring that all staff or readers share the publication's claims.
- Official statistics describe recorded incidents using their own denominators and recording limitations. Their mention of an increase should not be transformed into a general prevalence claim.

## What the audit establishes

`benchmarks/corpus-audit.json` records the provenance/integrity audit. On the initial snapshot, all twenty documents passed: real manifest URL, substantial nonempty content, unique block IDs, canonical content checksum, recorded source checksum, locators, real-document status and rights metadata.

This audit does **not** establish OCR completeness, semantic fidelity of every layout transformation, model interpretation accuracy, calibrated confidence or fairness. The collection has no independent human gold labels. Semantic provider benchmark results must be reported separately, with observed coverage and errors; do not infer them from the provenance audit.
