"""Explicit, bounded source-derived context; no inferred endorsement or speaker.

Only material inside the supplied source document is eligible. Thread references
are unverified provenance. Omitted or absent context is recorded, never filled in.
"""
from __future__ import annotations

from copy import deepcopy
import re

CONTEXT_POLICY = "structure-aware-1.0"
MAX_CONTEXT_CHARACTERS = 1600
MAX_ANCESTORS = 2


def _heading(block):
    level = block.get("heading_level")
    if isinstance(level, int) and not isinstance(level, bool) and 1 <= level <= 6:
        return level
    match = re.match(r"^ {0,3}(#{1,6})[ \t]+\S", block["text"])
    return len(match.group(1)) if match else None


def enrich_context(document, units, sentences):
    """Attach bounded heading/explicit mail-ancestor anchors in place."""
    blocks = document["blocks"]
    by_block = {}
    by_message = {}
    for sentence in sentences:
        by_block.setdefault(sentence["block_id"], []).append(sentence)
        if sentence.get("message_key"):
            by_message.setdefault(sentence["message_key"], []).append(sentence)
    heading_before, stack = {}, []
    for block in blocks:
        level = _heading(block)
        if level is not None:
            stack = [(old_level, old_id) for old_level, old_id in stack if old_level < level]
            stack.append((level, block["id"]))
        heading_before[block["id"]] = [identity for _, identity in stack]
    metadata = document.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    messages = [item for item in metadata.get("messages", []) if isinstance(item, dict)]
    by_key = {item.get("message_key"): item for item in messages if item.get("message_key")}
    by_id = {item.get("message_id"): item for item in messages if item.get("message_id")}
    for unit in units:
        candidates, omitted, missing = [], [], []
        current_ids = {item["id"] for item in [*unit.get("context_before", []), *unit["sentences"], *unit.get("context_after", [])]}
        for block_id in heading_before.get(unit["block_id"], []):
            # Headings are exact first source sentences, not generated summaries.
            for sentence in by_block.get(block_id, [])[:1]:
                candidates.append((sentence, "section_heading"))
        if unit.get("message_key"):
            current = by_key.get(unit["message_key"], {})
            references = current.get("references") or current.get("in_reply_to") or []
            if not isinstance(references, list):
                references = []
            seen = {unit["message_key"]}
            ancestors = 0
            for reference in reversed(references):
                parent = by_id.get(reference)
                if not parent:
                    missing.append(str(reference))
                    continue
                key = parent.get("message_key")
                if key in seen:
                    continue
                seen.add(key)
                if parent.get("thread_key") != unit.get("thread_key"):
                    omitted.append({"reason": "thread_mismatch", "message_key": key})
                    continue
                if ancestors >= MAX_ANCESTORS:
                    omitted.append({"reason": "ancestor_limit", "message_key": key})
                    continue
                ancestors += 1
                # Keep actual sender headers plus bounded newly-authored text;
                # quoted ancestors do not turn into the current sender's speech.
                parent_sentences = by_message.get(key, [])
                candidates.extend((item, "ancestor_message_header") for item in parent_sentences if item.get("mail_role") == "header")
                authored = next((item for item in parent_sentences if item.get("mail_role") == "authored"), None)
                if authored:
                    candidates.append((authored, "ancestor_message_authored"))
        selected, characters = [], 0
        for sentence, relationship in candidates:
            if sentence["id"] in current_ids:
                continue
            current_ids.add(sentence["id"])
            if characters + len(sentence["text"]) > MAX_CONTEXT_CHARACTERS:
                omitted.append({"reason": "character_budget", "sentence_id": sentence["id"], "characters": len(sentence["text"])})
                continue
            selected.append({**deepcopy(sentence), "context_relationship": relationship})
            characters += len(sentence["text"])
        unit["structural_context"] = selected
        # Depth is a visible marker observation, never a claim about the actual
        # number or identity of speakers in prose quotation.
        for sentence in unit["sentences"]:
            sentence["quotation_marker_depth"] = max((len(match.group(1)) for match in re.finditer(r"(?m)^\s*(>+)", sentence["text"])), default=0)
        unit["context_manifest"] = {"policy": CONTEXT_POLICY, "max_structural_characters": MAX_CONTEXT_CHARACTERS,
                                    "max_ancestors": MAX_ANCESTORS, "selected_characters": characters,
                                    "selected_sentences": len(selected), "omitted_context": omitted,
                                    "missing_parent_messages": list(dict.fromkeys(missing)),
                                    "context_sufficiency": "review_required",
                                    "heading_policy": "explicit_block_heading_metadata_or_Markdown_markers",
                                    "heading_structure_available": bool(heading_before.get(unit["block_id"])),
                                    "scope": "Exact anchors in this supplied source only; no external thread fetch or inferred speaker."}
    return units
