"""Frozen-profile monthly corpus trajectories with explicit context and bounded graphs.

Rates describe imported source documents. They are not estimates of societal
prevalence, and binomial intervals do not incorporate classifier errors.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
import re
from urllib.parse import urlparse

from .learning import _group

COMPLETE = {"accepted"}
POSITIVE = "meets_criterion"


def wilson_interval(k, n, z=1.959963984540054):
    if n <= 0:
        return None, None
    p = k / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - radius), min(1.0, center + radius)


def fisher_greater(k, n, baseline_k, baseline_n):
    """One-sided conditional exact test for a 2x2 count table."""
    if not (0 <= k <= n and 0 <= baseline_k <= baseline_n):
        raise ValueError("Counts must be non-negative and no larger than their denominators")
    total, successes = n + baseline_n, k + baseline_k
    if total == 0:
        return 1.0
    def log_choose(a, b):
        if b < 0 or b > a:
            return -math.inf
        return math.lgamma(a + 1) - math.lgamma(b + 1) - math.lgamma(a - b + 1)
    denominator = log_choose(total, n)
    probabilities = [log_choose(successes, x) + log_choose(total - successes, n - x) - denominator
                     for x in range(max(k, n - (total - successes)), min(n, successes) + 1)]
    if not probabilities:
        return 0.0
    maximum = max(probabilities)
    return min(1.0, math.exp(maximum) * sum(math.exp(value - maximum) for value in probabilities))


def holm_adjust(values):
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    adjusted = [1.0] * len(values)
    previous = 0.0
    for rank, (index, value) in enumerate(ordered):
        previous = max(previous, min(1.0, (len(values) - rank) * value))
        adjusted[index] = previous
    return adjusted


def _month(value):
    match = re.match(r"^(\d{4})-(\d{2})(?:-|$)", str(value or ""))
    if not match or not 1 <= int(match[2]) <= 12:
        return None
    return match[1] + "-" + match[2]


def _months(first, last):
    year, month = map(int, first.split("-"))
    final_year, final_month = map(int, last.split("-"))
    count = (final_year - year) * 12 + final_month - month + 1
    if count > 600:
        return None
    return [f"{year + (month - 1 + i) // 12:04d}-{(month - 1 + i) % 12 + 1:02d}" for i in range(count)]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _rows(store, sql, params):
    offset = 0
    while True:
        page = store.query(sql + " LIMIT 500 OFFSET ?", [*params, offset])
        yield from page
        if len(page) < 500:
            break
        offset += len(page)


def _scoped_rows(store, sql, params, document_ids, order):
    identities = sorted(set(document_ids))
    # Keep D1 parameter counts below its bound while restricting each scan.
    for start in range(0, len(identities), 80):
        part = identities[start:start + 80]
        scoped = sql + " AND document_id IN (" + ",".join("?" for _ in part) + ") ORDER BY " + order
        yield from _rows(store, scoped, [*params, *part])


def _resolved(row, scope):
    original = json.loads(row["payload_json"]) if row["payload_json"] != "{}" else {}
    for key in ("result", "criterion_kind", "attribution", "speech_role", "speaker_stance", "document_stance"):
        original.setdefault(key, row.get(key))
    original.setdefault("hypothetical", bool(row.get("hypothetical")))
    if scope == "provisional":
        return original
    review = json.loads(row.get("review_json") or "{}")
    if review.get("actor_kind") != "human":
        return None
    if row.get("review_status") == "unreviewed":
        return None
    if row.get("review_status") == "rejected" and "result" not in review.get("corrections", {}):
        return None
    if scope == "adjudicated" and not review.get("adjudicated"):
        return None
    return review.get("resolved")


def _composition(document):
    words = document.get("word_count") or 0
    length = "short" if words <= 1000 else "medium" if words <= 5000 else "long"
    source = document.get("institution") or urlparse(document.get("source_url") or "").hostname or "Unknown source"
    return source + "|" + str(document.get("document_type") or "document") + "|" + length


def _tvd(left, right):
    total_left, total_right = sum(left.values()), sum(right.values())
    if not total_left or not total_right:
        return 1.0
    return 0.5 * sum(abs(left[key] / total_left - right[key] / total_right) for key in set(left) | set(right))


def detect_spikes(monthly_items, *, alpha=0.01, baseline_months=12, min_current_n=30, min_baseline_n=200,
                  min_positives=5, min_absolute_change=0.01, min_rate_ratio=2, min_coverage=95,
                  min_label_coverage=90, max_unknown_fraction=0.10, max_composition_tvd=0.10,
                  positive_key=None, denominator_key=None, as_of_month=None):
    """Evaluate already aggregated document counts without reading assessments.

    Input counts must be distinct, completely assessed document clusters. The
    caller controls the frozen lens/query/source scope and supplies composition
    counts. Missing denominators or composition never become a claimed spike.
    """
    if baseline_months < 1 or not 0 < alpha < 1:
        raise ValueError("A positive baseline window and alpha in (0,1) are required")
    if as_of_month is None:
        from datetime import datetime, timezone
        as_of_month = datetime.now(timezone.utc).strftime("%Y-%m")
    by_month, suppressed = {}, []
    def number(item, explicit, names):
        for key in ([explicit] if explicit else names):
            if key and item.get(key) is not None:
                value = item[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or int(value) != value:
                    raise ValueError("Exact spike tests require non-negative integer document counts")
                return int(value)
        return None
    for item in monthly_items:
        date = _month(item.get("date") or item.get("month"))
        if not date:
            suppressed.append({"date": item.get("date") or item.get("month"), "reason": "unknown_or_nonmonthly_period"})
            continue
        if date in by_month:
            raise ValueError("Supply one pre-aggregated row per monthly cohort")
        n = number(item, denominator_key, ["eligible_documents", "complete_doc_n", "analysed_documents", "analyzed_documents", "known_documents", "known_n"])
        k = number(item, positive_key, ["positive_documents", "endorsed_indicators", "indicator_positive_documents", "indicator_pos"])
        if n is None or k is None:
            suppressed.append({"date": date, "reason": "explicit_known_document_denominator_required"})
            continue
        if k > n:
            raise ValueError("Positive document count exceeds its known denominator")
        unknown = number(item, None, ["uncertain_documents", "unknown_documents", "unknown"]) or 0
        analyzed = number(item, None, ["analyzed_documents", "analysed_documents"]) or n + unknown
        unknown_fraction = unknown / analyzed if analyzed else 0.0
        label_coverage = float(item.get("label_coverage", 100 * n / analyzed if analyzed else 0))
        by_month[date] = {"date": date, "k": k, "n": n, "coverage": float(item.get("coverage", 0)),
                          "label_coverage": label_coverage, "unknown_fraction": unknown_fraction,
                          "composition": item.get("composition") or {}}
    dates = sorted(by_month)
    expanded = _months(dates[0], dates[-1]) if dates else []
    dates = expanded if expanded is not None else dates
    tests = []
    for index in range(baseline_months, len(dates)):
        date = dates[index]
        current = by_month.get(date)
        history = [by_month.get(period) for period in dates[index - baseline_months:index]]
        if not current or date >= as_of_month:
            if current:
                suppressed.append({"date": date, "reason": "open_or_future_publication_month"})
            continue
        if any(not row or row["n"] == 0 for row in history):
            suppressed.append({"date": date, "reason": "insufficient_nonempty_calendar_history"})
            continue
        baseline_n = sum(row["n"] for row in history)
        baseline_k = sum(row["k"] for row in history)
        if current["n"] < min_current_n or baseline_n < min_baseline_n:
            suppressed.append({"date": date, "reason": "insufficient_document_support"})
            continue
        historical_mix = Counter()
        composition_known = bool(current["composition"]) and all(row["composition"] for row in history)
        for row in history:
            historical_mix.update(row["composition"])
        mix_change = _tvd(Counter(current["composition"]), historical_mix) if composition_known else None
        gates = []
        if current["coverage"] < min_coverage or any(row["coverage"] < min_coverage for row in history):
            gates.append("incomplete_processing")
        if current["label_coverage"] < min_label_coverage or any(row["label_coverage"] < min_label_coverage for row in history):
            gates.append("insufficient_known_label_coverage")
        if current["unknown_fraction"] > max_unknown_fraction or any(row["unknown_fraction"] > max_unknown_fraction for row in history):
            gates.append("too_many_unknown_outcomes")
        if mix_change is None:
            gates.append("unknown_source_composition")
        elif mix_change > max_composition_tvd:
            gates.append("composition_change")
        tests.append({"date": date, "k": current["k"], "n": current["n"], "baseline_k": baseline_k,
                      "baseline_n": baseline_n, "composition_tvd": mix_change, "quality_gates": gates,
                      "p_value": fisher_greater(current["k"], current["n"], baseline_k, baseline_n)})
    adjusted = holm_adjust([item["p_value"] for item in tests])
    candidates = []
    for item, adjusted_p in zip(tests, adjusted):
        rate = item["k"] / item["n"]
        baseline_rate = item["baseline_k"] / item["baseline_n"]
        ratio = rate / baseline_rate if baseline_rate else None
        item.update({"adjusted_p": adjusted_p, "absolute_change": rate - baseline_rate,
                     "rate_ratio": ratio, "status": "not_flagged"})
        if item["quality_gates"]:
            item["status"] = "suppressed_" + item["quality_gates"][0]
        elif item["k"] >= min_positives and rate - baseline_rate >= min_absolute_change and (ratio is None or ratio >= min_rate_ratio) and adjusted_p <= alpha:
            item["status"] = "review_candidate"
            candidates.append(item)
    return {"tests": tests, "candidates": candidates, "suppressed": suppressed,
            "policy": {"baseline_months": baseline_months, "alpha": alpha, "multiplicity": "Holm within this frozen comparison family",
                       "min_current_n": min_current_n, "min_baseline_n": min_baseline_n, "min_positives": min_positives,
                       "min_absolute_change": min_absolute_change, "min_rate_ratio": min_rate_ratio,
                       "min_coverage": min_coverage, "min_label_coverage": min_label_coverage,
                       "max_unknown_fraction": max_unknown_fraction, "max_composition_tvd": max_composition_tvd,
                       "as_of_month": as_of_month, "comparison_count": len(tests)},
            "notice": "Provisional review candidates under conditional count-model assumptions, not population prevalence or interpretation accuracy."}


def _graph(documents, assessments, months_by_document, max_nodes, max_edges):
    nodes, edges = {}, []
    truncated = False
    def node(identity, kind, label, reference):
        if identity in nodes:
            return True
        if len(nodes) >= max_nodes:
            return False
        nodes[identity] = {"id": identity, "type": kind, "label": label, "reference": reference}
        return True
    def edge(source, target, kind, reference):
        if len(edges) >= max_edges:
            return False
        edges.append({"source": source, "target": target, "type": kind, "reference": reference})
        return True
    by_document = defaultdict(list)
    for assessment in assessments:
        if assessment.get("result") == POSITIVE:
            by_document[assessment["document_id"]].append(assessment)
    for doc in sorted(documents, key=lambda item: item["id"]):
        identity = "document:" + doc["id"]
        if not node(identity, "document", doc["title"], {"document_id": doc["id"]}):
            truncated = True
            break
        author = doc.get("author") or "Unknown"
        if author.lower() not in {"unknown", "anonymous", "not provided"}:
            author_id = "author:" + _digest(author)[:20]
            if node(author_id, "attributed_author", author, {"field": "documents.author"}):
                if not edge(identity, author_id, "authored_by", {"document_id": doc["id"]}):
                    truncated = True
        month = months_by_document.get(doc["id"])
        if month:
            period_id = "month:" + month
            if node(period_id, "publication_month", month, {"date": month}):
                if not edge(identity, period_id, "published_in", {"document_id": doc["id"]}):
                    truncated = True
        grouped = defaultdict(list)
        for assessment in by_document.get(doc["id"], []):
            grouped[assessment["lens_id"] + "/" + assessment["criterion_id"]].append(assessment["id"])
        for key, refs in sorted(grouped.items()):
            criterion_id = "criterion:" + key
            if node(criterion_id, "criterion", key, {"criterion_key": key}):
                if not edge(identity, criterion_id, "has_contextual_assessment", {"assessment_ids": refs[:10], "count": len(refs)}):
                    truncated = True
        if len(nodes) >= max_nodes or len(edges) >= max_edges:
            truncated = True
            break
    return {"nodes": list(nodes.values()), "edges": edges, "truncated": truncated,
            "limits": {"nodes": max_nodes, "edges": max_edges},
            "notice": "Typed references to source authors, publications and assessment criteria. No inferred social affiliation or private belief."}


def monthly_trajectories(store, project_id, run_id, lens_id=None, criterion_id=None, review_scope="provisional",
                         mode="publication", max_graph_nodes=120, max_graph_edges=240,
                         document_ids=None, speech_roles=None):
    if review_scope not in {"provisional", "reviewed", "adjudicated"}:
        raise ValueError("Choose provisional, reviewed or adjudicated scope")
    if mode not in {"publication", "assessment"}:
        raise ValueError("Choose publication or assessment time")
    if not 1 <= max_graph_nodes <= 500 or not 1 <= max_graph_edges <= 1000:
        raise ValueError("Graph limits exceed the bounded response contract")
    if speech_roles and set(speech_roles) - {"author_assertion", "attributed_quotation", "reported_speech", "unknown"}:
        raise ValueError("Unknown speech-role filter")
    if document_ids is not None and (not isinstance(document_ids, (list, tuple, set)) or len(document_ids) > 5000):
        raise ValueError("Document scope must contain at most 5000 IDs")
    run = store.one("SELECT * FROM runs WHERE id=? AND project_id=?", [run_id, project_id])
    if not run:
        raise ValueError("Run not found in this project")
    config, model_profile = json.loads(run["config_json"]), json.loads(run.get("model_profile_json") or "{}")
    profile_hash = config.get("profile_hash") or model_profile.get("profile_hash") or _digest({"config": config, "model_profile": model_profile})
    requested = {item.get("id") for item in config.get("source_snapshot", []) if isinstance(item, dict)}
    docs = list(_rows(store, "SELECT * FROM documents WHERE project_id=? ORDER BY id", [project_id]))
    if requested:
        docs = [doc for doc in docs if doc["id"] in requested]
    if document_ids is not None:
        allowed = set(document_ids)
        docs = [doc for doc in docs if doc["id"] in allowed]
    documents = {doc["id"]: doc for doc in docs}
    all_units = defaultdict(dict)
    for row in _scoped_rows(store, "SELECT document_id,unit_id,status,characters FROM v3_unit_statuses WHERE run_id=?", [run_id], documents, "document_id,unit_id"):
        all_units[row["document_id"]][row["unit_id"]] = row
    if not requested:
        docs = [doc for doc in docs if doc["id"] in all_units]
        documents = {doc["id"]: doc for doc in docs}
    where, parameters = ["project_id=?", "run_id=?"], [project_id, run_id]
    if lens_id:
        where.append("lens_id=?")
        parameters.append(lens_id)
    if criterion_id:
        where.append("criterion_id=?")
        parameters.append(criterion_id)
    assessments, matched_units, assessment_dates = [], defaultdict(set), {}
    has_indicator_criterion = False
    expected_decisions, selected_decisions = defaultdict(set), defaultdict(set)
    columns = "id,document_id,unit_id,lens_id,criterion_id,criterion_kind,result,attribution,speech_role,speaker_stance,document_stance,hypothetical,review_status,review_json,created_at"
    original = "CASE WHEN review_json!='{}' THEN payload_json ELSE '{}' END AS payload_json" if review_scope == "provisional" else "'{}' AS payload_json"
    for row in _scoped_rows(store, "SELECT " + columns + "," + original + " FROM v3_assessments WHERE " + " AND ".join(where), parameters, documents, "document_id,unit_id,id"):
        if row["document_id"] not in documents:
            continue
        decision_key = (row["document_id"], row["unit_id"])
        has_indicator_criterion = has_indicator_criterion or row["criterion_kind"] == "indicator"
        expected_decisions[decision_key].add((row["lens_id"], row["criterion_id"]))
        previous = assessment_dates.get(row["document_id"])
        if previous is None or row["created_at"] < previous:
            assessment_dates[row["document_id"]] = row["created_at"]
        payload = _resolved(row, review_scope)
        if payload is None:
            continue
        # Aggregate only the necessary fields; do not retain every quote,
        # context paragraph and explanation while scanning a large corpus.
        payload = {**{key: payload.get(key) for key in ("result", "attribution", "speech_role", "speaker_stance", "document_stance", "hypothetical")},
                   "id": row["id"], "document_id": row["document_id"], "unit_id": row["unit_id"],
                   "lens_id": row["lens_id"], "criterion_id": row["criterion_id"], "criterion_kind": row["criterion_kind"]}
        matched_units[row["document_id"]].add(row["unit_id"])
        selected_decisions[decision_key].add((row["lens_id"], row["criterion_id"]))
        if not speech_roles or payload.get("speech_role") in speech_roles:
            assessments.append(payload)
    grouped_docs = defaultdict(list)
    publication_months = {doc["id"]: _month(doc.get("published_at")) for doc in docs}
    for doc in docs:
        grouped_docs[_group(doc)].append(doc)
    canonical = [min(group, key=lambda d: (publication_months[d["id"]] or "9999-99", d["id"])) for group in grouped_docs.values()]
    selected_months = {doc["id"]: _month(assessment_dates.get(doc["id"])) if mode == "assessment" else publication_months[doc["id"]] for doc in canonical}
    observed = sorted({value for value in selected_months.values() if value})
    series_months = _months(observed[0], observed[-1]) if observed else []
    sparse = series_months is None
    series_months = observed if sparse else series_months
    buckets = {month: {"date": month, "documents": 0, "findings": 0, "indicators": 0, "reported": 0,
                       "endorsed": 0, "document_supports": 0, "unknown": 0, "counterspeech": 0, "hypothetical": 0,
                       "eligible_documents": 0, "analyzed_documents": 0, "uncertain_documents": 0,
                       "positive_documents": 0, "total_words": 0,
                       "unit_total": 0, "unit_complete": 0, "character_total": 0, "character_complete": 0,
                       "composition": Counter()} for month in series_months}
    by_doc = defaultdict(list)
    for assessment in assessments:
        by_doc[assessment["document_id"]].append(assessment)
    matrix = defaultdict(lambda: {"documents": 0, "eligible_documents": 0, "positive_documents": 0,
                                 "findings": 0, "indicators": 0, "reported": 0, "endorsed": 0})
    unknown_period = 0
    for doc in canonical:
        month = selected_months[doc["id"]]
        if not month:
            unknown_period += 1
            bucket = defaultdict(int)
            bucket["composition"] = Counter()
        else:
            bucket = buckets[month]
        bucket["documents"] += 1
        bucket["total_words"] += doc.get("word_count") or 0
        units = all_units.get(doc["id"], {})
        complete = {uid for uid, value in units.items() if value["status"] in COMPLETE}
        bucket["unit_total"] += len(units)
        bucket["unit_complete"] += len(complete)
        bucket["character_total"] += sum(max(0, row["characters"]) for row in units.values())
        bucket["character_complete"] += sum(max(0, units[uid]["characters"]) for uid in complete)
        analyzed = (bool(units) and len(complete) == len(units) and complete <= matched_units[doc["id"]]
                    and all(expected_decisions[(doc["id"], uid)] == selected_decisions[(doc["id"], uid)] for uid in complete))
        contextual = [item for item in by_doc.get(doc["id"], []) if item["criterion_kind"] == "indicator"]
        known_positive = any(item.get("result") == POSITIVE and not item.get("hypothetical") and item.get("attribution") == "author" and item.get("speaker_stance") == "supports" for item in contextual)
        uncertain = any(item.get("result") == "uncertain" or
                        (item.get("result") == POSITIVE and not item.get("hypothetical") and
                         (item.get("attribution") == "unknown" or (item.get("attribution") == "author" and
                          item.get("speaker_stance", "unclear") in {"unclear", "mixed", "not_applicable"}))) for item in contextual)
        eligible = analyzed and (known_positive or not uncertain)
        bucket["analyzed_documents"] += int(analyzed)
        bucket["uncertain_documents"] += int(analyzed and uncertain and not known_positive)
        if eligible:
            bucket["eligible_documents"] += 1
            bucket["composition"][_composition(doc)] += 1
        author_key = (doc.get("author") or "Unknown", doc.get("institution") or "", doc.get("document_type") or "document")
        author = matrix[author_key]
        author["documents"] += 1
        author["eligible_documents"] += int(eligible)
        endorsed_document = False
        for assessment in by_doc.get(doc["id"], []):
            if assessment["criterion_kind"] == "indicator" and assessment.get("result") == "uncertain":
                bucket["unknown"] += 1
            if assessment.get("result") != POSITIVE:
                continue
            bucket["findings"] += 1
            author["findings"] += 1
            if assessment["criterion_kind"] != "indicator":
                continue
            bucket["indicators"] += 1
            author["indicators"] += 1
            role, stance = assessment.get("speech_role", "unknown"), assessment.get("document_stance", "unclear")
            if role in {"attributed_quotation", "reported_speech"}:
                bucket["reported"] += 1
                author["reported"] += 1
            if assessment.get("hypothetical"):
                bucket["hypothetical"] += 1
            elif assessment.get("attribution") == "author" and assessment.get("speaker_stance") == "supports":
                bucket["endorsed"] += 1
                author["endorsed"] += 1
                endorsed_document = True
            elif stance == "opposes":
                bucket["counterspeech"] += 1
            elif stance in {"unclear", "mixed", "not_applicable"} or role == "unknown":
                bucket["unknown"] += 1
            if not assessment.get("hypothetical") and stance == "supports":
                bucket["document_supports"] += 1
        if eligible and endorsed_document:
            bucket["positive_documents"] += 1
            author["positive_documents"] += 1
    months = []
    for month in series_months:
        bucket = buckets[month]
        k, n = bucket["positive_documents"], bucket["eligible_documents"]
        lower, upper = wilson_interval(k, n)
        processing = bucket["character_complete"] / bucket["character_total"] if bucket["character_total"] else (
            bucket["unit_complete"] / bucket["unit_total"] if bucket["unit_total"] else 0)
        source_complete = bucket["analyzed_documents"] / bucket["documents"] if bucket["documents"] else 0
        label_coverage = n / bucket["analyzed_documents"] if bucket["analyzed_documents"] else 0
        rates_enabled = bool(lens_id and has_indicator_criterion)
        bucket.update({"rate": k / n if n and rates_enabled else None, "lower": lower if rates_enabled else None,
                       "upper": upper if rates_enabled else None, "coverage": 100 * min(processing, source_complete),
                       "label_coverage": 100 * label_coverage,
                       "unknown_result_lower_bound": k / bucket["analyzed_documents"] if bucket["analyzed_documents"] and rates_enabled else None,
                       "unknown_result_upper_bound": (k + bucket["uncertain_documents"]) / bucket["analyzed_documents"] if bucket["analyzed_documents"] and rates_enabled else None,
                       "composition": dict(bucket["composition"])})
        months.append(bucket)
    detection = detect_spikes(months) if mode == "publication" and lens_id and has_indicator_criterion and requested else {"tests": [], "candidates": [], "policy": {}, "suppressed": []}
    author_rows = [{"author": key[0], "institution": key[1], "document_type": key[2], **value,
                    "rate": value["positive_documents"] / value["eligible_documents"] if value["eligible_documents"] and lens_id and has_indicator_criterion else None}
                   for key, value in sorted(matrix.items())]
    return {"project_id": project_id, "run_id": run_id, "profile_hash": profile_hash, "mode": mode,
            "source_snapshot_available": bool(requested),
            "review_scope": review_scope, "lens_id": lens_id, "criterion_id": criterion_id,
            "document_scope_count": len(docs), "speech_roles": list(speech_roles or []),
            "monthly": months, "items": months, "matrix": author_rows,
            "unknown_period_documents": unknown_period, "duplicate_documents": len(docs) - len(canonical),
            "sparse_empty_months": sparse, "statistical_tests": detection["tests"], "spike_candidates": detection["candidates"],
            "spike_policy": detection["policy"], "spike_suppressed": detection["suppressed"],
            "graph": _graph(canonical, assessments, publication_months, max_graph_nodes, max_graph_edges),
            "metric": "Unique fully assessed document clusters with an author-endorsed, non-hypothetical indicator under the selected lens",
            "interval_notice": "Wilson 95% intervals assume independent document clusters and exclude classifier, sampling and extraction error.",
            "alert_notice": "Frozen-snapshot review candidates only. Holm controls this displayed comparison family under valid test assumptions, not unlimited repeated looks or societal prevalence.",
            "scope_notice": "Choose one lens for comparable rates and candidate flags. Counts of quoted, opposed and hypothetical expression remain separate.",
            "count_definitions": {"findings": "All explicit meets_criterion assessment rows, including boundary/descriptive criteria",
                                  "indicators": "Meets_criterion rows whose frozen criterion kind is indicator",
                                  "reported": "Indicator rows in attributed quotation or reported speech, potentially overlapping other contexts",
                                  "endorsed": "Non-hypothetical indicator rows attributed to the author with speaker stance supports",
                                  "document_supports": "Separate document-stance support metric, including supported attributed quotations",
                                  "rate": "Determinate eligible document-cluster proportion, not raw finding density",
                                  "unknown_bounds": "Bounds caused by explicit uncertain outcomes only, not classifier-error or population confidence bounds"},
            "interpretation_accuracy": None, "independent_reference_labels": False}


build_trajectories = monthly_trajectories
