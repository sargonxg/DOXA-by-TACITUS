"""Frozen-split DOXA model and behavioral evaluation."""
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from time import perf_counter
import json
from shutil import copyfile

from sklearn.metrics import f1_score, confusion_matrix

from doxa.models import CLASS_ORDER, CRITERIA, PortableModel
from doxa.models.baseline import data_matrices, hash_file, load_config, read_table, write_json, write_sums


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def measure(rows):
    if not rows:
        return {"support": dict.fromkeys(CLASS_ORDER, 0), "coverage": None,
                "macro_f1": None, "baseline_macro_f1": None, "confusion_matrix": None}
    actual = [r["expected"] for r in rows]
    predicted = [r["predicted"] or "abstained" for r in rows]
    prior = [r["baseline"] or "abstained" for r in rows]
    labels = list(CLASS_ORDER)
    return {
        "support": {label: actual.count(label) for label in labels},
        "coverage": ratio(sum(r["status"] == "ok" for r in rows), len(rows)),
        "macro_f1": float(f1_score(actual, predicted, labels=labels, average="macro", zero_division=0)),
        "baseline_macro_f1": float(f1_score(actual, prior, labels=labels, average="macro", zero_division=0)),
        "confusion_columns": [*labels, "abstained"],
        "confusion_matrix": confusion_matrix(actual, predicted, labels=[*labels, "abstained"])[:3].tolist(),
    }


def behavioral(dataset, predictions):
    lookup = {(p["case_id"], p["criterion_id"]): p for p in predictions}
    pairs = read_table(dataset, "behavioral_pairs", "challenge") or []
    records = []
    for pair in pairs:
        if pair.get("validity_status") not in {"valid", "provisionally_valid"}:
            continue
        criteria = ([pair["criterion_id"]] if pair.get("criterion_id") else
                    [c for case, c in lookup if case == pair["case_a_id"]])
        for criterion in criteria:
            a = lookup.get((pair["case_a_id"], criterion))
            b = lookup.get((pair["case_b_id"], criterion))
            if not a or not b:
                continue
            covered = a["status"] == "ok" and b["status"] == "ok"
            records.append({
                "pair_id": pair["pair_id"], "criterion_id": criterion,
                "operator": pair["operator"], "expected_relation": pair["expected_relation"],
                "covered": covered, "expected_a": a["expected"], "expected_b": b["expected"],
                "predicted_a": a["predicted"], "predicted_b": b["predicted"],
                "both_correct": covered and a["predicted"] == a["expected"]
                and b["predicted"] == b["expected"],
                "consistent": covered and a["predicted"] == b["predicted"],
            })
    invariant = [r for r in records if r["expected_a"] == r["expected_b"]]
    directional = [r for r in records if r["expected_a"] != r["expected_b"]]
    return {
        "eligible_pairs": len(records), "coverage": ratio(sum(r["covered"] for r in records), len(records)),
        "both_correct_rate": ratio(sum(r["both_correct"] for r in records), len(records)),
        "consistency_rate": ratio(sum(r["consistent"] for r in invariant), len(invariant)),
        "directional_success_rate": ratio(sum(r["both_correct"] for r in directional), len(directional)),
        "invariant_denominator": len(invariant), "directional_denominator": len(directional),
        "results": records,
    }


def assess_rows(rows, model, support, split):
    for row in rows:
        judgment, case = row["judgment"], row["case"]
        criterion = judgment["criterion_id"]
        result, = model.predict(*row["input"], [criterion], case["case_id"])["criteria"]
        yield {
            "case_id": case["case_id"], "judgment_id": judgment["judgment_id"],
            "family_id": case["family_id"], "criterion_id": criterion, "split": split,
            "expected": judgment["reference_decision"], "predicted": result["suggested_decision"],
            "baseline": support[criterion]["most_frequent"], "status": result["prediction_status"],
            "reason": result["reason"], "scores": result["scores"],
            "origin": case["source_origin"], "text": case["text"],
            "speaker_stance": judgment.get("speaker_stance"),
            "topic_tags": case.get("topic_tags", []),
            "annotation_status": judgment.get("annotation_status"),
            "human_review_count": judgment.get("human_review_count", 0),
            "model_revision": model.manifest["model_revision"],
            "training_data_revision": model.manifest["training_data_revision"],
        }


def evaluate(dataset=Path("release_staging/dataset"), folder=Path("release_staging/model"), config=None):
    load_config(config, {"split": "test", "challenge_split": "challenge",
                        "aggregate": "macro_mean_over_supported_criteria",
                        "abstentions": "count_as_missed_reference_labels"})
    dataset, folder = Path(dataset), Path(folder)
    model = PortableModel(folder)
    for name, expected in model.manifest["training_data_files"].items():
        if hash_file(dataset / name) != expected:
            raise ValueError(f"Frozen dataset hash mismatch for {name}")
    if not model.heads:
        print("No deployable head meets the training support floor")
    support = json.loads((folder / "training_support.json").read_text(encoding="utf-8"))
    all_rows = data_matrices(dataset)
    by_split = {
        split: list(assess_rows(all_rows[split], model, support, split))
        for split in ("test", "challenge")
    }
    test = by_split["test"]
    heads = {
        name: [r for r in test if r["criterion_id"] == name] for name in CRITERIA
    }
    scored = [r for r in test if r["criterion_id"] in model.heads]
    criteria = {
        name: {"status": support[name]["status"],
               **(measure(rows) if name in model.heads else {"test_rows": len(rows)})}
        for name, rows in heads.items()
    }
    scores = [criteria[name] for name in model.heads if criteria[name]["macro_f1"] is not None]
    overall = {
        "test_rows": len(test), "scored_rows": len(scored),
        "coverage": ratio(sum(r["status"] == "ok" for r in scored), len(scored)),
        "full_test_coverage": ratio(sum(r["status"] == "ok" for r in test), len(test)),
        "macro_f1": mean(r["macro_f1"] for r in scores) if scores else None,
        "baseline_macro_f1": mean(r["baseline_macro_f1"] for r in scores) if scores else None,
    }
    slices = defaultdict(list)
    for row in scored:
        slices[f"source_origin:{row['origin']}"].append(row)
        if row["speaker_stance"] in {"reports", "rebuts", "endorses", "unclear"}:
            slices[f"speaker_stance:{row['speaker_stance']}"].append(row)
        for tag in row["topic_tags"]:
            slices[f"topic:{tag}"].append(row)
    diagnostics = []
    counts = Counter()
    for row in scored:
        expected, predicted = row["expected"], row["predicted"]
        kind = (
            "missing_context" if expected == "insufficient_context" else
            "false_positive" if expected == "absent" and predicted == "present" else
            "false_negative" if expected == "present" and predicted != "present" else
            "correct" if expected == predicted else "other_error"
        )
        counts[kind] += 1
        if counts[kind] <= 4:
            diagnostics.append(row | {"slice": kind})
    samples = []
    for row in all_rows["test"][:30]:
        start = perf_counter()
        model.predict(*row["input"])
        samples.append((perf_counter() - start) * 1000)
    behavioral_results = behavioral(dataset, by_split["challenge"])
    metrics = {
        "version": "1.0.0", "annotation_status": "provisional",
        "model_revision": model.manifest["model_revision"],
        "dataset_revision": model.manifest["training_data_revision"],
        "overall": overall, "criteria": criteria, "behavioral": behavioral_results,
        "diagnostic_counts": dict(counts), "diagnostics": diagnostics,
        "slices": {label: measure(rows) for label, rows in slices.items()},
        "human_reviewed_test_rows": sum(row["human_review_count"] > 0 for row in test),
        "runtime": {"python_cpu_prediction_median_ms": median(samples) if samples else None,
                    "python_cpu_prediction_samples": len(samples),
                    "portable_weights_and_vocab_bytes": sum(
                        (folder / name).stat().st_size for name in (
                            "idf.f32", "coefficients.f32", "intercepts.f32", "vocabulary.json"
                        )
                    )},
    }
    write_json(folder / "metrics.json", metrics)
    with (folder / "predictions.jsonl").open("w", encoding="utf-8") as out:
        for rows in by_split.values():
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    report = [
        "# Frozen evaluation", "",
        f"Scored {len(scored)} of {len(test)} test rows across {len(model.heads)} trained heads.",
        f"Mean criterion macro-F1: {overall['macro_f1']}. Most-frequent baseline: {overall['baseline_macro_f1']}.",
        f"Coverage on supported criteria: {overall['coverage']}; across all test rows: {overall['full_test_coverage']}.",
        f"Eligible behavioral pairs: {behavioral_results['eligible_pairs']}.",
        "", "## Example errors and successes", "",
    ]
    report.extend(f"- {r['case_id']} ({r['criterion_id']}, {r['slice']}): expected {r['expected']}, predicted {r['predicted']}."
                  for r in diagnostics)
    report.extend(["", "The study measures a small, provisional pilot. Scores are uncalibrated and the model does not extract evidence spans.", ""])
    (folder / "EVALUATION.md").write_text("\n".join(report), encoding="utf-8")
    write_card(folder, model, metrics)
    write_sums(folder)
    print(json.dumps(overall))
    return metrics


def write_card(folder, model, metrics):
    overall = metrics["overall"]
    behavioral_metrics = metrics["behavioral"]
    names = ", ".join(model.heads)
    directional = [
        row for row in behavioral_metrics["results"] if row["expected_a"] != row["expected_b"]
    ]
    directional_correct = sum(row["both_correct"] for row in directional)
    directional_covered = sum(row["covered"] for row in directional)
    training = json.loads((folder / "training_config.json").read_text(encoding="utf-8"))
    support = json.loads((folder / "training_support.json").read_text(encoding="utf-8"))
    train_count = sum(sum(criterion["class_counts"].values()) for criterion in support.values())
    fitted_count = sum(sum(support[criterion]["class_counts"].values()) for criterion in model.heads)
    def shown(value):
        return "unavailable" if value is None else f"{value:.3f}"
    card = f"""---
language:
- en
license: apache-2.0
tags:
- political-discourse
- text-classification
- evaluation
- responsible-ai
---

# DOXA Linear v0.1.0

DOXA Linear is an experimental English-language screening baseline for
political discourse in context. It uses separate text and context character
3–5-gram TF-IDF features and one multinomial logistic regression head per
supported criterion. It returns separate criterion predictions with
uncalibrated scores. Review belongs to a person.

## Supported screening

The model trains {len(model.heads)} heads: {names or 'none'}.
Material omission and comparative double standards are reference-packet and
paired-case research tasks without model heads in this release.
Unknown passages abstain. Empty or over-limit passages are invalid.
The model cannot verify user-supplied facts, assess severity, or extract
evidence spans. Reference annotations are separate from live predictions.

Use the model to inspect patterns and test research hypotheses. Keep context,
quotation, rebuttal, and ordinary political criticism visible in review.
The pilot is unsuitable for automated discipline, author profiling, legal
findings, or organizational safety claims.

## Data and results

The frozen DOXA Pilot training partition has {train_count} synthetic,
AI-provisional judgments. {fitted_count} contribute to trained heads.
There is zero independent human review. Model evaluation uses whole held-out
scenario families, with a separate synthetic behavioral challenge.

| Test measure | Result |
| --- | ---: |
| Rows on supported criteria | {overall['scored_rows']} / {overall['test_rows']} |
| Mean criterion macro-F1 | {shown(overall['macro_f1'])} |
| Most-frequent baseline macro-F1 | {shown(overall['baseline_macro_f1'])} |
| Supported test coverage | {shown(overall['coverage'])} |
| Behavioral pairs correct on both sides | {shown(behavioral_metrics['both_correct_rate'])} |
| Directional behavioral success | {shown(behavioral_metrics['directional_success_rate'])} |
| Behavioral coverage | {shown(behavioral_metrics['coverage'])} |

**Directional challenge: {directional_correct}/{len(directional)} correct,
with {directional_covered}/{len(directional)} covered.** The model missed the
explicit-rebuttal and added-attribution changes. This is a limitation on contextual screening
despite the higher frozen-test macro-F1. The small synthetic sample does not
establish accuracy or fairness in real enterprise communications.
See [EVALUATION.md](EVALUATION.md), [metrics.json](metrics.json) and
[predictions.jsonl](predictions.jsonl) for support and observed errors.

## Load and reproduce

In the accompanying DOXA source checkout, run `uv sync --locked`.
Download the model files at the release revision into a local folder,
or reproduce them with the commands below.

```python
from doxa.models import PortableModel

model = PortableModel("release_staging/model")
result = model.predict("I oppose the cabinet's decision and call for its reversal.")
print(result["criteria"])
```

```bash
uv run doxa train --config configs/baseline.yaml
uv run doxa evaluate --config configs/evaluation.yaml
```

The loader reads JSON metadata and little-endian float32 arrays.
`model_manifest.json` specifies the shapes and revisions.
`SHA256SUMS` verifies the payload. There is no Transformers pipeline or
hosted inference widget for this format.

The training-data content SHA-256 is
`{model.manifest['training_data_sha256']}`. The model content SHA-256 is
`{model.manifest['model_sha256']}`. Training used seed {training['seed']}
and at most one numerical-library thread.

## Cite and contribute

Cite *DOXA Linear v0.1.0*, Giulio Catanzariti / TACITUS, with the immutable
revision. To propose a correction, share the record ID, rubric criterion,
and evidence for the proposed change through the project repository.
Do not share confidential passages.
"""
    (folder / "README.md").write_text(card, encoding="utf-8")
    copyfile("LICENSE", folder / "LICENSE")
