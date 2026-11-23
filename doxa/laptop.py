"""Resource envelope for the explicit native laptop analysis preset."""
from __future__ import annotations

PROFILE = "laptop-8gb-v1"
MODEL = "qwen3.5:2b-q4_K_M"
ENDPOINT = "http://127.0.0.1:11434"
CONTEXT = 4096
UNIT_SETTINGS = {"unit_max_chars": 800, "context_sentences": 1}
LIMITS = {"documents": 8, "units": 24, "criteria": 6}


def configure(config, *, remote):
    if remote or config.get("verification_of"):
        raise ValueError("The laptop preset requires native storage and a new contextual analysis.")
    return {**config, "provider": "ollama", "model": MODEL, "methodology_version": "3.0",
            "budget_usd": 0.0, "allow_remote_text": False, "context_policy": None,
            "learning_snapshot_id": "", **UNIT_SETTINGS}


def workload(source_ids, document, lenses, custom_lens):
    from .contextual import _criteria, build_units
    _, criteria = _criteria(lenses, {"custom_lens": custom_lens})
    count = len(source_ids)
    total = 0
    within_source_limits = 0 < count <= LIMITS["documents"]
    if within_source_limits:
        for source_id in source_ids:
            units = build_units(document(source_id, True), max_unit_chars=UNIT_SETTINGS["unit_max_chars"],
                                context_sentences=UNIT_SETTINGS["context_sentences"])
            total += len(units)
            if total > LIMITS["units"]:
                break
    checks = {"documents": count, "units": total if within_source_limits else None, "criteria": len(criteria)}
    valid = within_source_limits and 0 < total <= LIMITS["units"] and len(criteria) <= LIMITS["criteria"]
    return {**checks, "limits": LIMITS, "fits": valid, "decisions": total * len(criteria) if valid else None,
            "unit_count_is_lower_bound": total > LIMITS["units"],
            "source_text_destination": ENDPOINT, "model": MODEL, "budget_usd": 0}
