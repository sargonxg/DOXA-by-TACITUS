"""Reference-conditioned metrics with explicit sampling and release boundaries.

There are no model-generated labels, self-reported confidence scores or SciPy
dependencies. Enriched diagnostic samples never qualify population claims.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import random

RESULTS = ("meets_criterion", "does_not_meet", "not_applicable", "uncertain")
DECISIONS = set(RESULTS) - {"uncertain"}
GATE_POLICY = {
    "version": "doxa-reference-gates-0.6", "confidence": 0.95,
    "supported_finding_precision_lower": 0.95, "minimum_predicted_positive_clusters": 100,
    "detection_recall_lower": 0.80, "detection_non_abstained_coverage": 0.80,
    "notice": "Product release thresholds, not academic consensus or achieved DOXA performance.",
}


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def exact_binomial_lower(successes: int, total: int, alpha=0.05):
    """One-sided Clopper-Pearson lower bound, using a stable binomial tail."""
    if type(successes) is not int or type(total) is not int or not 0 <= successes <= total <= 10000:
        raise ValueError("Binomial counts must be integers with 0 <= successes <= total <= 10000")
    if not 0 < alpha < 1:
        raise ValueError("Alpha must lie strictly between zero and one")
    if not total:
        return None
    if not successes:
        return 0.0
    if successes == total:
        return alpha ** (1 / total)
    coefficients = [math.lgamma(total + 1) - math.lgamma(i + 1) - math.lgamma(total - i + 1)
                    for i in range(successes, total + 1)]

    def tail(probability):
        logs = [coefficient + i * math.log(probability) + (total - i) * math.log1p(-probability)
                for i, coefficient in enumerate(coefficients, successes)]
        maximum = max(logs)
        return math.exp(maximum) * sum(math.exp(value - maximum) for value in logs)

    low, high = 0.0, successes / total
    for _ in range(64):
        middle = (low + high) / 2
        if tail(middle) < alpha:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def _cells(record):
    prediction = (record.get("prediction") or {}).get("result")
    reference = (record.get("reference") or {}).get("result")
    # Ambiguous reference labels remain in the confusion table, but cannot be
    # converted to binary negatives. Abstentions count as missed positives.
    return (int(prediction == "meets_criterion" and reference == "meets_criterion"),
            int(prediction == "meets_criterion" and reference in {"does_not_meet", "not_applicable"}),
            int(prediction != "meets_criterion" and reference == "meets_criterion"))


def _cluster_bounds(records, *, replicates, seed):
    clusters = defaultdict(lambda: [0, 0, 0])
    for record in records:
        cluster = record.get("cluster")
        if not cluster:
            return {"precision": None, "recall": None, "reason": "missing_cluster_identity"}
        counts = _cells(record)
        clusters[cluster] = [a + b for a, b in zip(clusters[cluster], counts)]
    if len(clusters) < 2:
        return {"precision": None, "recall": None, "reason": "fewer_than_two_clusters"}
    rng, values = random.Random(seed), [clusters[key] for key in sorted(clusters)]
    precision, recall = [], []
    for _ in range(replicates):
        tp = fp = fn = 0
        for _ in range(len(values)):
            a, b, c = values[rng.randrange(len(values))]
            tp, fp, fn = tp + a, fp + b, fn + c
        # Undefined replicates are conservative zero for a release lower bound,
        # rather than silently discarded to improve the result.
        precision.append(_ratio(tp, tp + fp) or 0.0)
        recall.append(_ratio(tp, tp + fn) or 0.0)
    index = max(0, math.floor(0.05 * replicates) - 1)
    return {"precision": sorted(precision)[index], "recall": sorted(recall)[index],
            "replicates": replicates, "clusters": len(values), "reason": None}


def _intervals(spans):
    grouped = defaultdict(list)
    for span in spans or []:
        if not isinstance(span, dict) or type(span.get("start")) is not int or type(span.get("end")) is not int:
            continue
        if not 0 <= span["start"] < span["end"]:
            continue
        grouped[(span.get("document_id"), span.get("block_id"))].append((span["start"], span["end"]))
    result = {}
    for key, intervals in grouped.items():
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        result[key] = merged
    return result


def span_concordance(records):
    """Union-aware character overlap; never scores software fallback anchors."""
    compared = predicted = reference = overlap = exact = 0
    for record in records:
        gold = record.get("reference") or {}
        model = record.get("prediction") or {}
        if gold.get("evidence_spans") is None or model.get("detail_supplied") is not True:
            continue
        left, right = _intervals(model.get("evidence_refs")), _intervals(gold["evidence_spans"])
        compared += 1
        exact += int(left == right)
        predicted += sum(end - start for intervals in left.values() for start, end in intervals)
        reference += sum(end - start for intervals in right.values() for start, end in intervals)
        for key in set(left) & set(right):
            overlap += sum(max(0, min(b, d) - max(a, c)) for a, b in left[key] for c, d in right[key])
    return {"compared_cases": compared, "exact_span_set_matches": exact,
            "unicode_character_precision": _ratio(overlap, predicted),
            "unicode_character_recall": _ratio(overlap, reference),
            "notice": "Span concordance is not interpretation validity; software fallback anchors and unannotated spans are excluded."}


def reviewer_agreement(records):
    groups = defaultdict(list)
    for record in records:
        reviewers = sorted(record.get("reviews") or [], key=lambda item: item["reviewer_id"])
        for i, first in enumerate(reviewers):
            for second in reviewers[i + 1:]:
                groups[(first["reviewer_id"], second["reviewer_id"])].append(
                    (first["labels"]["result"], second["labels"]["result"]))
    result = []
    for pair, labels in sorted(groups.items()):
        left, right = Counter(a for a, _ in labels), Counter(b for _, b in labels)
        observed = sum(a == b for a, b in labels) / len(labels)
        expected = sum(left[label] * right[label] for label in RESULTS) / len(labels) ** 2
        result.append({"reviewers": list(pair), "compared_cases": len(labels), "observed_agreement": observed,
                       "cohen_kappa": (observed - expected) / (1 - expected) if expected < 1 else None})
    return {"pairs": result, "notice": "Agreement does not prove accuracy, qualifications or real-world independence; initial disagreements are retained."}


def criterion_metrics(records, *, protocol=None, heldout_complete=False):
    """Compute conditional diagnostics and preregistered quantitative gates.

    The caller supplies server-read frozen cases and adjudications, not model
    self-reviews. This pure function also supports explicit synthetic unit tests.
    """
    if len(records) > 10000:
        raise ValueError("A bounded evaluation contains at most 10000 primary cases")
    protocol = protocol or {}
    groups = defaultdict(list)
    for record in records:
        groups[(record.get("lens_id", ""), record.get("criterion_id", ""))].append(record)
    results = []
    for (lens, criterion), cases in sorted(groups.items()):
        reviewed = [record for record in cases if record.get("reference")]
        matrix = Counter(((record.get("reference") or {}).get("result"),
                          (record.get("prediction") or {}).get("result") or "unprocessed") for record in reviewed)
        tp = fp = fn = 0
        for record in reviewed:
            a, b, c = _cells(record)
            tp, fp, fn = tp + a, fp + b, fn + c
        predicted_positive = [record for record in reviewed if (record.get("prediction") or {}).get("result") == "meets_criterion"]
        positive_clusters = {record.get("cluster") for record in predicted_positive if record.get("cluster")}
        decisions = sum((record.get("prediction") or {}).get("result") in DECISIONS for record in cases)
        coverage = _ratio(decisions, len(cases))
        blockers = []
        if not protocol:
            blockers.append("no_preregistered_protocol")
        if protocol.get("sampling_design") != "probability_sample":
            blockers.append("diagnostic_or_unverified_sampling_design")
        if not heldout_complete:
            blockers.append("locked_test_not_fully_adjudicated")
        if len(reviewed) != len(cases):
            blockers.append("reference_labels_incomplete")
        if any(not record.get("cluster") for record in cases):
            blockers.append("missing_cluster_identity")
        if len(positive_clusters) < GATE_POLICY["minimum_predicted_positive_clusters"]:
            blockers.append("fewer_than_100_reviewed_predicted_positive_clusters")
        method = protocol.get("confidence_method")
        lower_precision = lower_recall = None
        if method == "exact_binomial":
            cluster_counts = Counter(record.get("cluster") for record in cases)
            if any(count > 1 for count in cluster_counts.values()):
                blockers.append("exact_binomial_requires_one_primary_case_per_independent_cluster")
            else:
                lower_precision = exact_binomial_lower(tp, tp + fp)
                lower_recall = exact_binomial_lower(tp, tp + fn)
        elif method == "cluster_bootstrap":
            repetitions = protocol.get("bootstrap_replicates", 1999)
            seed = protocol.get("seed", 20261118)
            if type(repetitions) is not int or not 999 <= repetitions <= 4999 or type(seed) is not int:
                raise ValueError("Preregister 999–4999 cluster bootstrap replicates and an integer seed")
            bounds = _cluster_bounds(reviewed, replicates=repetitions, seed=seed)
            lower_precision, lower_recall = bounds["precision"], bounds["recall"]
            if bounds["reason"]:
                blockers.append(bounds["reason"])
            if not tp or not fp or not fn:
                blockers.append("degenerate_bootstrap_requires_prespecified_rare_error_procedure")
        else:
            blockers.append("confidence_method_not_preregistered")
        if any(len(record.get("reviews") or []) < 2 or not record.get("separate_adjudicator") for record in reviewed):
            blockers.append("two_reviews_and_separate_adjudication_not_documented")
        ambiguous = sum(record["reference"]["result"] == "uncertain" for record in reviewed)
        if ambiguous:
            blockers.append("reference_ambiguity_requires_prespecified_sensitivity_analysis")
        support_pass = not blockers and lower_precision is not None and lower_precision >= 0.95
        detection_pass = support_pass and lower_recall is not None and lower_recall >= 0.80 and coverage is not None and coverage >= 0.80
        results.append({"lens_id": lens, "criterion_id": criterion, "eligible_cases": len(cases),
                        "adjudicated_cases": len(reviewed), "confusion_matrix": [
                            {"reference": a, "model": b, "count": n} for (a, b), n in sorted(matrix.items())],
                        "binary": {"true_positive": tp, "false_positive": fp, "missed_reference_positive": fn,
                                   "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
                                   "ambiguous_reference_cases_excluded_from_binary": ambiguous},
                        "non_abstained_decision_coverage": coverage,
                        "model_abstentions": sum((record.get("prediction") or {}).get("result") == "uncertain" for record in cases),
                        "unprocessed_cases": sum((record.get("prediction") or {}).get("result") not in RESULTS for record in cases),
                        "independent_predicted_positive_clusters": len(positive_clusters),
                        "one_sided_95_lower": {"precision": lower_precision, "recall": lower_recall, "method": method,
                                               "coverage_kind": "exact_if_independent_case_design_holds" if method == "exact_binomial" else "nominal_percentile_bootstrap_not_finite_sample_guarantee"},
                        "span_concordance": span_concordance(reviewed),
                        "attribution_exact_agreement": _ratio(sum(record["prediction"].get("attribution") == record["reference"].get("attribution") for record in reviewed), len(reviewed)),
                        "release_gate": {"supported_finding_thresholds_met": support_pass,
                                         "automated_detection_thresholds_met": detection_pass,
                                         "blocking_reasons": sorted(set(blockers)),
                                         "publication_authorized": False,
                                         "external_checks": ["reviewer qualifications and independence", "source rights and sampling-frame audit", "expert sign-off for consequential claims"]}})
    return {"version": "doxa-reference-metrics-0.6", "metrics_scope": "preregistered_locked_sample" if protocol.get("sampling_design") == "probability_sample" else "selected_case_diagnostics_only",
            "criterion_metrics": results, "reviewer_agreement": reviewer_agreement([record for record in records if record.get("reference")]),
            "gate_policy": dict(GATE_POLICY), "interpretation_accuracy": None,
            "notice": "Metrics apply only to their source/domain/method and sampling design. No universal accuracy or automatic publication approval is inferred."}
