# Methodology and interpretation limits

DOXA is a transparent research preview for analyzing expressions in a document collection. It is not a validated test of an author's character, private beliefs, political affiliation or religious identity. The current release has no independent human reference labels and makes no established interpretation-accuracy claim.

## The unit of analysis

A finding concerns a passage, an explicitly identified target or proposition, and a named analytical lens. The document's author and a quoted speaker are different roles. A publication describing, criticizing, quoting or teaching about prejudice does not thereby endorse it. Hypothetical characters in official guidance remain hypothetical.

Document authors and institutional metadata come from source attribution or user input. Unknown speakers remain unknown. Name similarity alone does not justify merging authors. Comparisons characterize the imported texts and selected periods; they do not establish an author's unexpressed motives or population-wide public opinion.

"Doxa" describes assumptions treated as self-evident within discourse. There is no universal scientific doxa score. Presuppositions, framing, rhetorical choices and repeated assumptions can support reviewable hypotheses; they do not reveal an author's mind. Claims about missing perspectives require a declared comparator.

## Separate evidence, interpretation and review

The intended finding record includes an exact source span, document and block identifiers, page or paragraph locator, target, document-author/quoted-speaker distinction, lens criterion, concise interpretation, alternative interpretation where material, and uncertainty/review state. Model and prompt versions belong to the run.

Exact quotation validation is mechanical: the supplied quote must resolve to the persisted extracted block. Source validity establishes provenance, not correctness of the interpretation. Findings without valid supporting spans should not enter confirmed evidence totals. Source text is untrusted data; document instructions must not change system behavior or execute tools.

The processing coverage denominator is the set of extracted source blocks or pages scheduled for analysis. Show extraction failures, skipped content, unsupported pages and unfinished analysis separately. A successful extraction is not proof that all visually present information was captured.

## Versioned lenses

Different definitions serve different purposes and may disagree. Keep lens results separate, including their source date, jurisdiction, criteria and limitations. Do not take a majority vote among definitions or combine them into an unexplained prejudice score.

- **IHRA (2016):** a non-legally binding working definition with contextual examples. It explicitly excludes criticism of Israel similar to that leveled against other countries. [Primary source](https://holocaustremembrance.com/resources/working-definition-antisemitism).
- **Jerusalem Declaration (2021):** a definition and fifteen contextual guidelines. It distinguishes anti-Jewish hostility from political criticism and identifies actions that are not antisemitic in themselves. Its FAQ rejects use as a legal or quasi-legal speech code. [Primary source](https://jerusalemdeclaration.org/).
- **Nexus (updated 2024):** includes anti-Jewish beliefs, conduct and systemic conditions, and explains why criticism or disproportionate attention to Israel is not prima facie proof of antisemitism. [Primary source](https://nexusproject.us/nexus-resources/the-nexus-document/).
- **England anti-Muslim-hostility guidance (2026):** a non-statutory, contextual definition emphasizing intentional hostile conduct, stereotyping intended to encourage hatred, and targeted discrimination. Its accompanying text protects criticism and ridicule of religion. Text alone may not establish intent or unlawfulness. [Primary source](https://www.gov.uk/guidance/a-definition-of-anti-muslim-hostility).
- **Runnymede:** an anti-Muslim-racism perspective that includes structural and institutional patterns. Its wider scope should not be presented as interchangeable with the England conduct-and-intent definition. [2025 briefing](https://www.runnymedetrust.org/publications/islamophobia-towards-a-working-definition).
- **Custom inquiry:** the user supplies the research question and rubric. "Political correctness" is a contested concept; there is no default objective PC threshold.

Criterion descriptions in software are operational summaries for research. They are not official endorsements by the organizations that published these frameworks, nor an exhaustive legal interpretation of their texts.

## Report personas

Personas change the questions emphasized and the presentation; they do not change source evidence or assert what a demographic group thinks.

| Persona | Emphasis |
| --- | --- |
| Researchers and civil society | Corpus design, observable patterns, competing interpretations, source provenance and uncertainty |
| Newsrooms and policy analysts | Clear findings, institutional/time comparisons, contextual quotations and limitations |
| Universities and teaching | Explainable examples, definition comparisons, discussion questions and quotation-versus-endorsement distinctions |

Every report should state the corpus, active filters, lens/run version, coverage and provisional status. Dashboard and report totals should be computed from the same persisted findings. Institution/author comparisons require visible document and passage denominators.

## Benchmarks for this release

The bundled twenty-document collection comprises real public institutional publications. It has no fabricated authors and no generated substitute documents. It is intentionally selected for comparison and product demonstration, not a representative sample or a hidden ground-truth dataset. Some source authors created hypothetical examples; those examples retain their original status.

Required mechanical checks cover source/content checksums, valid locators, nonempty extractions, unique block identifiers and exact citation resolution. Real provider runs must record exact model identifiers, available revisions, hardware or provider, duration, usage, cost and completed coverage. Mocked tests do not establish model quality or GPU speed.

The qualitative error audit should inspect all invalid citations and unresolved attribution cases, then the first five findings per document plus twenty deterministically sampled additional findings. Record whether a human, implementing agent or another model performed the audit. Model self-review is not independent human validation. Publish observed failure examples, including the source context and whether the finding was rejected or corrected.

No interpretation accuracy, precision, recall or F1 should be reported without independent reference labels. Model-generated confidence is not a calibrated probability. Use uncertainty and review states until calibration has been evaluated. When independent labels become available, preserve annotator disagreements, split by document/source rather than random passages, and report per-lens/per-category precision and recall with sample sizes.

Synthetic unit and functional cases may test negation, quotation, counterspeech, parsing and prompt-injection behavior. They are separate from the real twenty-document showcase.

## Research foundations and known gaps

[HateCheck (ACL 2021)](https://aclanthology.org/2021.acl-long.4/) demonstrates why overall F1 can conceal functional failures. Its [CC-BY-4.0 dataset](https://github.com/paul-rottger/hatecheck-data) has 3,728 cases across 29 functionalities, but its seven original target groups do not include Jewish people. It cannot validate an antisemitism detector by itself.

[Measuring Hate Speech](https://huggingface.co/datasets/ucberkeley-dlab/measuring-hate-speech/blob/main/README.md) includes Jewish and Muslim targets and preserves annotator-level variation: 39,565 unique social-media comments and 135,556 annotation rows. Its domain differs from long policy documents.

[Plank (EMNLP 2022)](https://aclanthology.org/2022.emnlp-main.731/) explains why genuine human label variation is not merely annotation noise. [Atwell et al. (ACL Findings 2025)](https://aclanthology.org/2025.findings-acl.107/) identifies discrepancies between LLM and human presupposition judgments. [Dela Cruz et al. (GEM 2025)](https://aclanthology.org/2025.gem-1.49/) finds extractive rationales can improve confidence estimates in another classification domain; that result is not calibration evidence for DOXA.

The [Media Frames Corpus](https://github.com/dallascard/media_frames_corpus) provides a useful framing precedent, but its full news texts require separate Lexis-Nexis access rights. Public metadata or repository code licensing does not make those articles freely redistributable.
