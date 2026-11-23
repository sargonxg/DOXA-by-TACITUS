# Research scorecards

DOXA freezes a research question, selected source versions, model identity, and the selected method cards before analysis. The **Research scorecard** derives each result from the saved analysis and a workspace query. Its data and evidence are available in the dashboard, PDF, HTML, XLSX, CSV, JSONL, and JSON-LD formats.

## Run the Civic Square example

1. Start DOXA and select **Open worked example** to inspect the authored scorecard, two matched pairs, and the ambiguous “restore order” passage. The scorecard labels describe a fictional hearing. [Fictional inputs](../examples/scorecard-inputs/) · [PDF](../frontend/public/studies/scorecard/doxa-scorecard.pdf)
2. For a fresh local run, explicitly pull `qwen3.5:2b-q4_K_M` into Ollama. Native DOXA uses `http://127.0.0.1:11434`. The laptop preset checks that Ollama reports a Q4 quantization and a resolved model digest. Its context, CPU thread count, and source limits are fixed. [Install](../README.md#start-locally)
3. From the repository root, run the script below against a running native workspace. It creates a new project, imports four inputs and their metadata, applies the declared rubric and weights, pauses after a task settles, resumes, and saves the real scorecard, PDF, workbook and graph. Review the new outputs against the authored examples. [Inquiry and weights](../examples/scorecard-inputs/inquiry.json)

```sh
python scripts/run_scorecard_example.py --pause-once --output ./doxa-civic-square
```

The same workflow works interactively. Import the four text and caption files, add the metadata CSV separately, and choose the bounded laptop preset. Enter the question and custom descriptive criteria in the inquiry file. Select those criteria for the weighted index. In **Jobs**, pause and resume if needed. In **Matched comparisons**, form two cohorts by issuer and declare the hearing, topic, and dates. Save a scorecard for the run and optionally select that comparison. The workbench treats the caption text as the source and retains the cue time as a locator.

## Read the measurements

- Each selected and deduplicated source contributes one outcome per criterion. Planned decisions come from the frozen source units. A source is incomplete when extraction or a planned decision is unresolved. A fully assessed source is positive when at least one exact passage supports the criterion. It is uncertain when a reading remains uncertain and there is no supported match. A determinate negative needs an explicit applicable non-match. A source with every unit inapplicable is inapplicable.
- The positive rate is positive sources divided by determinate sources. Determinate coverage divides positive and negative sources by all eligible sources. Eligibility includes unresolved and incomplete sources. Processing coverage separately reports retained explicit decisions divided by planned decisions.
- Freeze an index name and positive whole weights for at least two single-source descriptive criteria before analysis. Each contribution is the criterion's observed positive rate multiplied by its weight. Require 80% determinate coverage for every weighted criterion. The sensitivity range assigns unresolved eligible sources to the negative and positive extremes in turn. The equal-weight result tests the weighting decision. In the authored example, **40 × 0.75 + 25 × 0.5 + 35 × 0.5 = 60**. [Composite indicator design](https://doi.org/10.1787/9789264043466-en)
- A matched pair uses declared event, topic, genre, date window, and different source families. Each criterion's result is **100 × the mean left-minus-right difference** across pairs with determinate outcomes and exact text on both sides. A separate omission question needs a bounded inventory of relevant information reasonably available at the time. Two matched pairs describe a worked comparison within the sample.
- The model scorecard uses original model outcomes. The review overlay can apply a human-adjudicated correction or make a rejected result uncertain. Generate a new snapshot after review or changed query scope. The original snapshot keeps its own values and evidence.

**Caption extraction.** SRT and WebVTT use one block per cue with integer millisecond start and end times. Caption headers, cue timing, and malformed cues are recorded as exclusions. A file with no usable cues fails extraction. PDF pages requiring OCR and incomplete caption files get an extraction review state in the scorecard. Check the source preview before making a coverage claim.

## Reuse the snapshot

| Operation | Interface |
| --- | --- |
| Check scope | `POST /api/v3/projects/{project_id}/preflight` with the query, selected lenses, and `execution_profile: "laptop-8gb-v1"`. |
| Freeze method and weights | Add `execution_profile` and optional `index_spec` with `name` and per-criterion `weights` to the v3 run request. |
| Save scores | `POST /api/v3/projects/{project_id}/scorecards` with `query_id`, optional `comparison_id`, and basis `model` or `review_overlay`. |
| Read scores | `GET /api/v3/projects/{project_id}/scorecards/{scorecard_id}`. |
| Export | `POST /api/v3/projects/{project_id}/report-jobs` with the saved `scorecard_id` and requested format. |

The JSONL header carries the scope, method cards, index and summary. Following rows carry one outcome per source and criterion. The JSON-LD export keeps the same IDs and links each source-level outcome to its representative decision and exact passage. The graph can reproduce a criterion rate offline. [Example JSON-LD](../frontend/public/studies/scorecard/scorecard-evidence.jsonld) · [Graph vocabulary](evidence-graph.md)

## Evaluation and hardware verification

For a new corpus, preregister the target, data selection, comparison scope, codebook, and disagreements to retain. Keep independent story or source groups together when splitting development, calibration, and held-out cases. Monitor criterion-level errors, abstentions and processing gaps. Challenge cases should include different descriptions of the same event, plausible alternative readings, unavailable comparator evidence, and incomplete extraction. Apply the existing independent-human gates before publishing interpretation accuracy. [Evaluation protocol](methodology-v06.md)

The managed Linux VM smoke run used the actual Q4 model on four source units. It paused and resumed, retained **15 of 16 decisions**, and exported the PDF, workbook and evidence graph in **10 minutes 39 seconds**. One unweighted premise decision remained incomplete. The model's index was **52.1**. Its documented-risk criterion found one match in three determinate sources and marked the fourth source inapplicable. The authored walkthrough records three matches in four sources. This gives reviewers a concrete applicability decision to check alongside the evidence. [Measured run receipt](../benchmarks/scorecard-laptop-smoke-v09.json)

For the 8 GB target, run the actual starter pack with a browser open, pause and resume, then export a report. Record hardware, OS, model digest, elapsed time, retained decisions, and app plus Ollama peak memory. The target for this pack is 20 minutes or less, 5.5 GiB or less for the combined app and inference processes, and a coverage-qualified index. Publish the tested configuration with any hardware support claim.
