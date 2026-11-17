# DOXA Quest: a practical walkthrough

![DOXA Quest: import documents, choose your lens, run local or cloud AI, check evidence, compare findings, export reports.](images/doxa-quest.png)

The illustrated map follows six research steps. The application puts the linked
source collection, charts, observations and evidence inspector in one workspace;
setup and export actions open drawers. Your aim is an interpretation another
reader can inspect, rather than a score assigned to a person.

## Level 1 — Bring your sources together

Start DOXA at **http://localhost:8000** and open **Import**. Choose the institutional
case studies to load twenty real public documents and a historical Gemma result
snapshot without new inference requests. Its observations are labeled legacy
under methodology v3; contextual source rates require a new contextual run.

Add PDF, DOCX, TXT, Markdown, saved HTML or a ZIP archive for your own inquiry.
Small files travel in bounded groups under one logical import. Large files use
resumable parts: after interruption, refresh and select the same file to continue.
Use **Import metadata CSV** to correct authors, institutions, dates and document
types by document ID, filename or checksum. The **Sources** pane opens extracted
text in the evidence inspector. Successful manual imports select **Corpus · all
imported sources**, where new files remain visible without model measurements.
Choose a saved analysis to return to its frozen cohort; analyze the corpus scope
to include newly imported material.

## Level 2 — Declare the inquiry

Open **Analyze**. State your research question, choose explicit analytical
frameworks and declare the reviewer role and priorities. Add a custom rubric when
needed, marking its criteria as indicators, boundaries, or descriptive frames.
Report audiences—research/civil society, news/policy, or teaching—are selected
separately in the report drawer.

For example: “How do these institutions balance protecting religious minorities
with protecting political expression? Distinguish author endorsement, reported
speech, criticism, rebuttal and cases requiring wider context.”

IHRA, Jerusalem and Nexus remain distinct frameworks. A language-norms inquiry
examines contested assumptions; it does not produce an objective correctness
score. Reviewer roles do not simulate demographic identities.

## Level 3 — Choose the engine and processing location

Open **Cloud / local** for model readiness and the monthly processing cap.

- **Cloudflare:** the default remote profile. Explicitly enable source-text
  transfer for each run and set its budget before starting.
- **Ollama:** the local `qwen3.5:4b` fallback. Its 16 GB laptop profile is a test
  target, not a guarantee of throughput; use measured progress and pilot runs.
- **Lexical navigation:** exact term matches without a model. Matches do not
  establish contextual indicators, endorsement, or prejudice prevalence.
- **Configured OpenAI-compatible / RunPod:** server-managed alternatives for
  larger-model experiments.

CPU retrieval and rubric experiments are explicit optional actions using already
installed artifacts. Opening the model drawer never downloads weights. Follow
**Processing record** for progress, costs and issues; cancel or resume eligible
live jobs there. Historical saved snapshots cannot be resumed.

**Independent model verification**, in Models and Processing, compares a
completed primary contextual run with a separate GPT-OSS 120B job. It requires
explicit cloud consent and has a $2 budget cap. Read selected-unit coverage and
literal output differences in its saved comparison; primary counts stay separate.
Model agreement does not establish interpretation accuracy.

## Level 4 — Read and review the evidence

Select an entry in **Retained observations**. The inspector highlights its exact
source span and nearby paragraphs. **Interpretation** separates the criterion,
proposition, target, speaker attribution, speaker stance, document stance,
hypothetical status, alternative reading and uncertainty.

In **Review & correct**, save a confirmed, rejected or revised decision with your
reasoning. V3 observations support structured corrections while preserving the
original proposal. Legacy observations support review notes and state; start a
v3 run to obtain contextual fields. Citation checks establish where words occur,
not whether their interpretation is correct.

**Reviewed learning** can approve a human-reviewed example, freeze an immutable
snapshot, create a candidate and evaluate paired runs. Activation requires an
eligible evaluation report and an explicit decision; reviews do not train model
weights automatically. A draft snapshot can be chosen in Analyze for a candidate
evaluation run, and activated memory can be rolled back.

## Level 5 — Compare within a controlled scope

Search the complete corpus or set a period, framework and speech role. **Filters**
adds author/institution, source type, import batch, outcome, stance and review
state. Sources, observations, headline metrics and exported reports share the
same server query snapshot. Select timeline periods or author × criterion matrix
cells to narrow the scope. Counts of sources and assessment rows remain separate;
reported and endorsed source counts can overlap.

**Trajectory** uses publication time, a frozen method and eligible source clusters.
Choose one framework for comparable rates. Coverage, determinate outcomes, source
composition, baseline size and multiple-comparison gates qualify statistical
review candidates. **History** uses assessment time and does not imply a change
in the author's original position. **Evidence map** traces the selected source,
attribution and criterion through typed links.

Compare import scopes through the batch filter. A source absent from an
incremental import is **not included in that import**; this does not imply
its deletion. Repeated sources can belong to several import histories.

## Level 6 — Export the research scope

Open the download button in the command bar. **Reports** uses the current frozen
query snapshot; choose research/civil society, news/policy, or teaching emphasis.

- **PDF / HTML:** a readable research record with methods, charts, source
  references and an explicitly bounded evidence appendix.
- **XLSX:** filterable evidence, source registers, lenses, processing records and
  charts.
- **CSV / JSONL:** the complete selected data, including provenance and source
  references.

Audience changes presentation rather than calculations. After a review or source
metadata change, refresh the workspace before generating a new report. A stale
snapshot is rejected instead of silently exporting changed totals.

## Read recorded results with their limits

The current twenty-source contextual run retains **11,701/11,704 criterion assessments**, covering **613/616 source units**, with **18/20 documents** fully assessed. It remains partial. The [commented institutional](../examples/institutional-context-commentary.pdf) and [political-framing](../examples/political-framing-commentary.pdf) PDFs explain actual quotations and model readings with separate AI-authored notes. The [79-unit independent comparison](v3-independent-review.md) preserves both models' disagreements without treating either as a human verdict.

The historical twenty-document cloud showcase contains 1,071 retained
observations; 5,788 of 8,999 source blocks passed its checks. These are recorded
v1 facts, not v3 contextual measurements or interpretation-accuracy estimates.
[Benchmark summaries](../benchmarks/README.md) identify model versions, samples,
incomplete coverage, errors and hardware. Independent human reference annotations
are required before claiming interpretation accuracy.

The current browser suite uses synthetic fixtures to prove 5,000-source paging,
linked scope, source review, report downloads, cloud-consent controls, interrupted
upload recovery and grouped-import membership. Functional test fixtures establish
application behavior; they are not model benchmarks.

Continue with [how DOXA works](../frontend/public/how-it-works.html),
[methodology](methodology.md), [architecture](architecture.md),
[deployment](deployment.md), or the [research dossier](research.md).
