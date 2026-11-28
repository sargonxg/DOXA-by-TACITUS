"""Loader for DOXA's non-executable parameter format."""
from pathlib import Path
from math import exp
import json

import numpy as np

from doxa.features import LIMIT, preprocess, vector

CLASS_ORDER = ("present", "absent", "insufficient_context")
CRITERIA = (
    "evaluative_framing", "collective_blame", "identity_stereotyping",
    "identity_derogation_exclusion", "group_conspiracy", "evidential_overclaim",
    "material_omission", "comparative_double_standard",
)
RESEARCH_ONLY = {"material_omission", "comparative_double_standard"}
GROUP_DIRECTED = set(CRITERIA[1:5])
ACTIONS = {
    "evaluative_framing": ["offer_optional_rewording", "add_attribution"],
    "evidential_overclaim": ["request_source", "qualify_causal_claim"],
    "collective_blame": ["specialist_review", "specify_responsible_actor"],
    "identity_stereotyping": ["specialist_review", "remove_collective_generalization"],
    "identity_derogation_exclusion": ["specialist_review"],
    "group_conspiracy": ["specialist_review", "request_source"],
}


def input_error(text, context):
    if not isinstance(text, str) or not isinstance(context, str):
        return "expected_text"
    if len(text) > LIMIT or len(context) > LIMIT:
        return "input_limit_exceeded"
    if any(0xD800 <= ord(char) <= 0xDFFF for char in text + context):
        return "invalid_unicode"
    if not preprocess(text):
        return "empty_passage"
    return None


def softmax(logits):
    offset = max(logits)
    values = [exp(value - offset) for value in logits]
    total = sum(values)
    return [value / total for value in values]


class PortableModel:
    def __init__(self, folder):
        folder = Path(folder)
        self.manifest = json.loads((folder / "model_manifest.json").read_text(encoding="utf-8"))
        if self.manifest["class_order"] != list(CLASS_ORDER):
            raise ValueError("Unsupported class order")
        self.vocabulary = json.loads((folder / "vocabulary.json").read_text(encoding="utf-8"))
        self.idf = np.fromfile(folder / "idf.f32", dtype="<f4")
        self.heads = {name: i for i, name in enumerate(self.manifest["heads"])}
        self.weights = np.fromfile(folder / "coefficients.f32", dtype="<f4").reshape(
            len(self.heads), len(CLASS_ORDER), len(self.vocabulary)
        )
        self.intercepts = np.fromfile(folder / "intercepts.f32", dtype="<f4").reshape(
            len(self.heads), len(CLASS_ORDER)
        )
        if len(self.idf) != len(self.vocabulary):
            raise ValueError("IDF size does not match vocabulary")

    def score(self, head, weights):
        rows = self.weights[head]
        logits = [
            float(self.intercepts[head, k]) +
            sum(value * float(rows[k, idx]) for idx, value in weights.items())
            for k in range(len(CLASS_ORDER))
        ]
        return dict(zip(CLASS_ORDER, softmax(logits), strict=True))

    def predict(self, text, context="", criterion_ids=None, record_id="local-input"):
        requested = CRITERIA if criterion_ids is None else criterion_ids
        error = input_error(text, context)
        counts, weights, has_text = ({}, {}, False) if error else vector(
            text, context, self.vocabulary, self.idf
        )
        results = []
        for criterion in requested:
            status, reason, decision, scores = "ok", None, None, None
            priority, recommendations = "unassigned", []
            if error:
                status, reason = "invalid_input", error
            elif criterion not in self.heads:
                status, reason = "unsupported_criterion", "no_deployed_head"
                recommendations = ["request_context"]
            elif not has_text:
                status = "abstained"
                reason = "context_only_features" if counts else "no_known_features"
                recommendations = ["request_context"]
            else:
                scores = self.score(self.heads[criterion], weights)
                decision = max(scores, key=scores.get)
                if decision == "insufficient_context":
                    priority, recommendations = "P2", ["request_context"]
                elif decision == "present":
                    priority = "P3" if criterion in GROUP_DIRECTED else (
                        "P2" if criterion == "evidential_overclaim" else "P1"
                    )
                    recommendations = ACTIONS[criterion]
                else:
                    recommendations = ["no_change"]
            results.append({
                "criterion_id": criterion, "prediction_status": status, "reason": reason,
                "suggested_decision": decision, "scores": scores,
                "score_calibration": "not_validated", "evidence": [],
                "evidence_status": "not_generated_by_this_model", "model_cues": [],
                "severity_assessment": {"status": "not_assessed", "descriptor": None, "source": None},
                "suggested_review_priority": priority, "recommendations": recommendations,
                "human_review_status": "unreviewed",
            })
        if results and all(result["suggested_decision"] == "absent" for result in results):
            for result in results:
                result["suggested_review_priority"] = "P0"
        return {
            "schema_version": "1.0.0", "record_id": record_id,
            "assessment_stage": "screening", "criteria": results,
            "model_revision": self.manifest["model_revision"],
            "training_data_revision": self.manifest["training_data_revision"],
            "review_policy_version": "doxa-review-v0.1",
            "limitations": [
                "experimental_provisional_pilot", "english_only",
                "scores_are_uncalibrated", "user_context_is_not_independently_verified",
            ],
        }
