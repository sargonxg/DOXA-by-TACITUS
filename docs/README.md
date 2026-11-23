# DOXA documentation

DOXA is an open-source workbench for political framing and bias analysis. Run it locally or deploy a private workspace. [TACITUS](https://tacitus.me/#doxa)

## Read and inspect

| Question | Guide and artifacts |
| --- | --- |
| Can I analyze a document or speech on a laptop? | [Research scorecards](research-scorecards.md) · [Civic Square example](../README.md#worked-scorecard) |
| Can I inspect a worked antisemitism case? | [Plzeň case, red flags, and methods](antisemitism-case.md) · [PDF](../frontend/public/studies/antisemitism/antisemitism-case.pdf) · [Decision ledger](../frontend/public/studies/antisemitism/decisions.csv) |
| What did DOXA find in 60 real news articles? | [Illustrated field study and sampling method](reading-muslim-coverage.md) · [PDF](../frontend/public/studies/muslim-coverage/report.pdf) · [data workbook](../frontend/public/studies/muslim-coverage/evidence.xlsx) |
| How do I trace a recorded decision? | [Evidence graph and offline queries](evidence-graph.md) · [JSON-LD](../frontend/public/studies/muslim-coverage/evidence.jsonld) |
| What does the tool do? | [Reader explainer](v04-reader-explainer.md) · [illustrated PDF](../examples/v04-reader-explainer.pdf) · [8-bit Quest](guide.md) |
| How are claims anchored and qualified? | [Technical annex](v04-technical-annex.md) · [PDF](../examples/v04-technical-annex.pdf) · [interpretation contract](v3-interpretation.md) |
| Open a small offline recorded example | [Twenty-source PDF](../examples/v06-recorded-example.pdf) · [complete XLSX](../examples/v06-recorded-example.xlsx) · [HTML](../examples/v06-recorded-example.html) · [manifest](../examples/v06-recorded-example.manifest.json) |
| Can I inspect real cases? | [15-case atlas](v04-case-atlas.md) · [PDF](../examples/v04-case-atlas.pdf) · [original annotated examples](v3-annotated-examples.md) |
| What is political bias here? | [Political-framing methodology](political-bias-methodology.md) · [framework methodology](methodology.md) |
| Which literature and definitions informed it? | [Research map](v04-research-map.json) · [BibTeX](v04-bibliography.bib) · [CSL JSON](v04-bibliography.csl.json) · [criterion/source audit](v04-criterion-source-audit.json) |

## Run and extend

| Task | Documentation |
| --- | --- |
| Start with the current release | [Quickstart](../README.md#start-locally) · [0.7 delivery plan](release-plan-v07.md) · [Workbench reference](workbench-reference.md) |
| Use the lightweight local profile | [v0.6 refinements](refinement-v06.md) · [8 GB local profile and doctor](local-v06.md) · [v0.6 verification](verification-v06.md) |
| Inspect extraction and context | [Permissive PDF defaults, immutable versions and structural context](extraction-v06.md) |
| Recover long work | [Persistent import/indexing/guided report activities](activity-jobs-v06.md) |
| Enable retention and deletion-safe restore | [External deletion ledger and recovery guard](privacy-lifecycle-v06.md) |
| Prepare a dedicated online pilot | [GCP recipe, fixed scope, cost records and operating limits](hosted-pilots-v06.md) |
| Operate Company, Public and Personal guided pilots | [Native access and privacy guide](managed-pilots-v05.md) · [report guide and three PDFs](pilot-reports-v05.md) · [v0.5 verification](verification-v05.md) |
| Install, persist, back up and deploy | [Deployment](deployment.md) · [architecture](architecture.md) |
| Choose local or cloud models | [Model profiles](models-v3.md) · [actual bounded CPU study](local-study-v04.md) · [model/system card](model-card.md) |
| Understand available data and rights | [Dataset card](dataset-card.md) · [news collection](news-corpus.md) · [institutional collection](corpus.md) · [licences](licenses.md) |
| Export reproducible evidence | [Casebooks and persistent jobs](report-jobs.md) · [recorded export guide](v3-recorded-exports.md) · [v0.4 verification](verification-v04.md) |
| Review, compare and learn | [Service toolkit](v3-service-toolkit.md) · [independent model review](v3-independent-review.md) · [staged human evaluation](evaluation-v04.md) |
| Inspect the new 10,000-source software exercise | [Measurement scope and 22 checks](scale-v06.md) · [receipt](../benchmarks/v06-scale-10000.json) · [XLSX](../examples/v06-scale-10000.xlsx) |
| Reproduce recorded benchmarks | [Benchmark index](../benchmarks/README.md) · [benchmark design](benchmarking.md) · [verification baseline](verification-v3.md) |

[Prioritized refinement and evaluation roadmap](refinement-roadmap.md) records the next experiments and their acceptance evidence.

The [commercial service roadmap](commercial-service-roadmap.md) audits what is missing for paid delivery and plans all three launch packages—company language audits, political/media analysis and personal email review—with guided US pilots, independent methodology validation, privacy controls, connectors, pricing experiments and scale gates. The local/native pilot controls and deployment tooling are implemented in v0.6; independent studies, actual customer operations, OAuth connectors, subscriptions and shared SaaS remain future qualification work.

## Publication and development record

[Contributing](../CONTRIBUTING.md) · [Changelog](../CHANGELOG.md) · [citation metadata](../CITATION.cff) · [evidence-pack manifest](../examples/v04-evidence-pack.manifest.json) · [processed lineage index](../examples/v04-lineage-index.json) · [source-reference audit](../examples/v04-corpus-verification.json).

The 5,000-source recorded run is **partial**, with 79 unresolved decisions. Human reference labels are pending. Documentation distinguishes software behavior, source fidelity, processing coverage, model interpretation and independent evaluation.
