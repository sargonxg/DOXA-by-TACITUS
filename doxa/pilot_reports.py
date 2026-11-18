"""Bounded synchronous reports for guided pilots, with no external resources.

The renderer verifies source anchors and recomputes deterministic wording
markers. It never turns them into bias judgments. ``trusted_reviews`` is an
assertion by the authenticated server adapter, not a browser input field.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from html import escape
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

from .lenses import get_lens
from .service_profiles import OBSERVATION_VERSION, get_profile, observe_communication, report_brief

REPORT_VERSION = "doxa-guided-pilot-report-1.0"
MAX_DOCUMENTS = 100
MAX_BLOCKS = 2000
MAX_SOURCE_CHARACTERS = 500000
MAX_OBSERVATIONS = 10000
MAX_COMMENTARY = 200
MAX_FINDINGS = 2000
MAX_EVIDENCE_QUOTE_CHARACTERS = 4000
MAX_TOTAL_EVIDENCE_CHARACTERS = 1000000
MAX_EVIDENCE_REFERENCES = 20000
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
EVIDENCE_APPENDIX_LIMIT = 30
COMMENTARY_APPENDIX_LIMIT = 30
METHOD_CARD_APPENDIX_LIMIT = 12
REFERENCE_APPENDIX_LIMIT = 12
_RESULTS = {"meets_criterion", "does_not_meet", "not_applicable", "uncertain"}
_FONT_LOCK = threading.Lock()
_BLOCK_CONTEXT_FIELDS = ("page", "paragraph", "line_start", "line_end", "source_char_start", "source_char_end",
                         "message_key", "message_id", "thread_key", "mail_sender", "mail_source_field", "source_chain", "repeated_quote_of")


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text(value, field: str, maximum: int = 4000, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"{field} must be {'nonempty ' if required else ''}text of at most {maximum} characters")
    return value


def _anchor(value: dict, sources: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Evidence must be an object")
    document_id = _text(value.get("document_id"), "evidence document_id", 200, required=True)
    block_id = _text(value.get("block_id"), "evidence block_id", 200, required=True)
    source = sources.get((document_id, block_id))
    if source is None:
        raise ValueError("Evidence refers to a source block outside the frozen selection")
    start, end = value.get("start"), value.get("end")
    quote = _text(value.get("quote"), "evidence quote", MAX_EVIDENCE_QUOTE_CHARACTERS, required=True)
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source["text"]):
        raise ValueError("Evidence requires valid Unicode source offsets")
    if source["text"][start:end] != quote:
        raise ValueError("Evidence quotation no longer matches its frozen source span")
    return {"document_id": document_id, "block_id": block_id, "start": start, "end": end,
            "quote": quote, "offset_unit": "unicode_code_points"}


def _provided_observations(value) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, dict):
        output = []
        for document_id, result in value.items():
            rows = result.get("observations", []) if isinstance(result, dict) else result
            if not isinstance(rows, list):
                raise ValueError("Supplied observations must be lists or per-document observation results")
            output.extend({**item, "document_id": document_id} for item in rows if isinstance(item, dict))
            if any(not isinstance(item, dict) for item in rows):
                raise ValueError("Each supplied observation must be an object")
        return output
    if not isinstance(value, list):
        raise ValueError("Supplied observations must be a list or a per-document mapping")
    output = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Each supplied observation must be an object")
        if "observations" in item:
            rows = item["observations"]
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("Each per-document observation result must contain a list")
            output.extend({**row, "document_id": item.get("document_id", row.get("document_id"))} for row in rows)
        else:
            output.append(item)
    return output


def _coverage(value, document_ids: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) - {"original_run", "selected_sources"}:
        raise ValueError("Run coverage accepts only original_run and selected_sources scopes")
    result = {}

    def counters(row, allowed):
        if not isinstance(row, dict) or set(row) - allowed:
            raise ValueError("Coverage contains an unknown counter field")
        if any(type(number) is not int or not 0 <= number <= 9223372036854775807 for number in row.values()):
            raise ValueError("Coverage counters must be nonnegative integers")
        return dict(row)

    if "original_run" in value:
        original = counters(value["original_run"], {"documents_total", "documents_complete", "blocks_total", "blocks_done", "tasks_failed"})
        for total, done in (("documents_total", "documents_complete"), ("blocks_total", "blocks_done")):
            if total in original and done in original and original[done] > original[total]:
                raise ValueError("Completed coverage cannot exceed its recorded total")
        result["original_run"] = original
    if "selected_sources" in value:
        sources = value["selected_sources"]
        if not isinstance(sources, list) or len(sources) > MAX_DOCUMENTS:
            raise ValueError("Selected-source coverage must be a bounded list")
        seen = set()
        result["selected_sources"] = []
        for row in sources:
            if not isinstance(row, dict) or set(row) - {"document_id", "units_total", "units_accepted", "decisions_retained"}:
                raise ValueError("Selected-source coverage contains unknown fields")
            document_id = _text(row.get("document_id"), "coverage document_id", 200, required=True)
            if document_id not in document_ids or document_id in seen:
                raise ValueError("Selected-source coverage IDs must belong uniquely to the frozen selection")
            seen.add(document_id)
            counts = counters({key: number for key, number in row.items() if key != "document_id"}, {"units_total", "units_accepted", "decisions_retained"})
            if "units_total" in counts and "units_accepted" in counts and counts["units_accepted"] > counts["units_total"]:
                raise ValueError("Accepted units cannot exceed the recorded unit total")
            result["selected_sources"].append({"document_id": document_id, **counts})
        if seen != document_ids:
            raise ValueError("Selected-source coverage must name every frozen selected source; unknown counters can be omitted")
    if len(_json(result)) > 32000:
        raise ValueError("Run coverage exceeds the synchronous report limit")
    return result


def _run_provenance(value, document_ids: set[str], brief_fingerprint: str) -> dict | None:
    if value is None:
        return None
    limits = {"run_id": 200, "run_status": 80, "provider": 200, "model": 1000, "model_revision": 1000,
              "method_fingerprint": 200, "question": 4000, "perspective": 4000,
              "service_brief_fingerprint": 200}
    allowed = set(limits) | {"selected_document_ids", "selection", "coverage"}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value) or set(value) - allowed:
        raise ValueError("Run provenance accepts only the declared safe identity, method and selection fields")
    result = {}
    for field, maximum in limits.items():
        if field in value:
            result[field] = None if value[field] is None else _text(value[field], "run provenance " + field, maximum, required=field == "run_id")
    if not result.get("run_id"):
        raise ValueError("Run provenance requires an actual run ID")
    ids = value.get("selected_document_ids")
    if (not isinstance(ids, list) or len(ids) > MAX_DOCUMENTS
            or any(not isinstance(item, str) or not item or len(item) > 200 for item in ids)
            or len(set(ids)) != len(ids) or set(ids) != document_ids):
        raise ValueError("Run provenance must name exactly the frozen selected document IDs")
    if value.get("selection") != "selected_sources_and_declared_lenses":
        raise ValueError("Run provenance must disclose selected sources and declared lenses")
    fingerprint = result.get("service_brief_fingerprint")
    if fingerprint and fingerprint != brief_fingerprint:
        raise ValueError("Run provenance service brief fingerprint differs from the frozen report brief")
    result["selected_document_ids"] = list(ids)
    result["selection"] = value["selection"]
    if "coverage" in value:
        result["coverage"] = _coverage(value["coverage"], document_ids)
    return result


def freeze_snapshot(snapshot: dict, *, trusted_reviews: bool = False) -> dict:
    """Validate and detach all source/report inputs without modifying callers."""
    if not isinstance(snapshot, dict):
        raise ValueError("A pilot report requires a snapshot object")
    if type(trusted_reviews) is not bool:
        raise ValueError("trusted_reviews must be an explicit server boolean")
    data_origin = _text(snapshot.get("data_origin", "not_declared"), "data_origin", 80)
    if data_origin not in {"not_declared", "synthetic_demonstration", "customer_supplied", "uploaded_selection", "public_source"}:
        raise ValueError("Unknown pilot report data origin")
    brief = report_brief(snapshot.get("brief"))
    documents = snapshot.get("documents")
    if not isinstance(documents, list) or not 1 <= len(documents) <= MAX_DOCUMENTS:
        raise ValueError(f"Choose between 1 and {MAX_DOCUMENTS} source documents for a synchronous pilot report")
    normalized_documents, all_blocks, sources, source_records = [], [], {}, []
    doc_ids = set()
    characters = block_count = 0
    for raw in documents:
        if not isinstance(raw, dict):
            raise ValueError("Each source document must be an object")
        document_id = _text(raw.get("id"), "document id", 200, required=True)
        if document_id in doc_ids:
            raise ValueError("Source document IDs must be unique")
        doc_ids.add(document_id)
        blocks = raw.get("blocks")
        if not isinstance(blocks, list):
            raise ValueError("Each selected document must supply its frozen extracted blocks")
        document = {"id": document_id, "blocks": []}
        for key in ("title", "author", "institution", "filename", "document_type", "published_at", "source_url", "license", "source_version_id"):
            document[key] = _text(raw.get(key), f"document {key}", 2000)
        source_hash = _text(raw.get("sha256"), "document sha256", 64)
        if source_hash and not re.fullmatch(r"[a-fA-F0-9]{64}", source_hash):
            raise ValueError("Recorded source sha256 must be a hexadecimal SHA-256 digest")
        document["sha256"] = source_hash
        for raw_block in blocks:
            if not isinstance(raw_block, dict):
                raise ValueError("Each source block must be an object")
            block_id = _text(raw_block.get("id"), "block id", 200, required=True)
            key = (document_id, block_id)
            if key in sources:
                raise ValueError("Source block IDs must be unique within each document")
            text = _text(raw_block.get("text"), "source block text", MAX_SOURCE_CHARACTERS)
            block_count += 1
            characters += len(text)
            if block_count > MAX_BLOCKS or characters > MAX_SOURCE_CHARACTERS:
                raise ValueError("Synchronous pilot report exceeds its source block/character limit; narrow the selection")
            block = {"id": block_id, "document_id": document_id, "text": text}
            context = {field: deepcopy(raw_block[field]) for field in _BLOCK_CONTEXT_FIELDS if field in raw_block}
            try:
                context_bytes = _json(context)
            except (TypeError, ValueError):
                raise ValueError("Source locator and mail context must be finite JSON data") from None
            if len(context_bytes) > 8000:
                raise ValueError("Source locator and mail context exceed the synchronous report limit")
            block.update(context)
            if "mail_role" in raw_block:
                block["mail_role"] = _text(raw_block["mail_role"], "mail_role", 80)
            for field in ("ineligible_for_author_counts", "eligible_for_author_counts"):
                if field in raw_block:
                    if type(raw_block[field]) is not bool:
                        raise ValueError(f"{field} must be a boolean")
                    block[field] = raw_block[field]
            document["blocks"].append(block)
            all_blocks.append(block)
            sources[key] = block
        document["word_count"] = sum(len(block["text"].split()) for block in document["blocks"])
        document["extracted_blocks_sha256"] = _sha(_json(document["blocks"]))
        source_records.append({"document_id": document_id, "source_sha256": source_hash or None,
                               "source_hash_origin": "recorded source metadata; original bytes not read by renderer" if source_hash else "not_supplied",
                               "extracted_blocks_sha256": document["extracted_blocks_sha256"],
                               "source_version_id": document["source_version_id"] or None})
        normalized_documents.append(document)
    run_provenance = _run_provenance(snapshot.get("run_provenance"), doc_ids, brief["brief"]["fingerprint"])
    canonical = observe_communication(all_blocks, brief["brief"]["profile_id"], max_observations=MAX_OBSERVATIONS)
    if not canonical["coverage"]["observations_complete"]:
        raise ValueError("Synchronous pilot report exceeds its observation limit; narrow the selection")
    canonical_keys = {(row["document_id"], row["block_id"], row["start"], row["end"], row["kind"]) for row in canonical["observations"]}
    provided = _provided_observations(snapshot.get("observations"))
    if len(provided) > MAX_OBSERVATIONS:
        raise ValueError("Too many supplied observations")
    for row in provided:
        anchor = _anchor(row, sources)
        if (anchor["document_id"], anchor["block_id"], anchor["start"], anchor["end"], row.get("kind")) not in canonical_keys:
            raise ValueError("Supplied observation does not match the current deterministic wording method")
    notes = snapshot.get("expert_commentary", [])
    if not isinstance(notes, list) or len(notes) > MAX_COMMENTARY:
        raise ValueError(f"Provide at most {MAX_COMMENTARY} operator commentary entries")
    commentary = []
    evidence_characters = 0
    for row in notes:
        if not isinstance(row, dict):
            raise ValueError("Operator commentary must be an object")
        document_id = _text(row.get("document_id"), "commentary document_id", 200, required=True)
        if document_id not in doc_ids:
            raise ValueError("Commentary refers to a document outside the selection")
        has_anchor = any(row.get(field) is not None for field in ("block_id", "start", "end", "quote"))
        anchor = _anchor(row, sources) if has_anchor else {"document_id": document_id}
        evidence_characters += len(anchor.get("quote", ""))
        actor = _text(row.get("actor_id", row.get("actor")), "commentary actor_id", 200)
        reviewed = trusted_reviews and bool(actor.strip()) and row.get("review_status") == "operator_reviewed"
        commentary.append({**anchor, "note": _text(row.get("note"), "commentary note", 4000, required=True),
                           "actor_id": actor if trusted_reviews else "", "review_status": "operator_reviewed" if reviewed else "provisional",
                           "review_origin": "server-attributed operator self-declaration" if reviewed else "unverified commentary",
                           "independent_validation": False})
    findings = snapshot.get("findings", [])
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        raise ValueError(f"Provide at most {MAX_FINDINGS} saved contextual decisions")
    decisions = []
    reference_count = 0
    by_id = {item["id"]: item for item in normalized_documents}
    for row in findings:
        if not isinstance(row, dict) or row.get("result") not in _RESULTS:
            raise ValueError("Saved contextual decisions require an explicit supported result type")
        lens = get_lens(_text(row.get("lens_id"), "finding lens_id", 200, required=True))
        criterion_id = row.get("criterion_id", row.get("criterion"))
        criterion = next((item for item in lens["criteria"] if item["id"] == criterion_id), None)
        if criterion is None or lens["id"] not in brief["brief"]["lens_ids"]:
            raise ValueError("Saved decision criterion is outside the declared brief")
        if row.get("criterion_version", lens["version"]) != lens["version"]:
            raise ValueError("Historical criteria need their frozen rubric; use the existing casebook report workflow")
        if row.get("criterion_kind", criterion["kind"]) != criterion["kind"]:
            raise ValueError("Saved decision kind disagrees with the declared criterion")
        anchor = _anchor(row, sources)
        document = by_id[anchor["document_id"]]
        for supplied, recorded in (("source_hash", "sha256"), ("source_version_id", "source_version_id")):
            if row.get(supplied) and row[supplied] != document[recorded]:
                raise ValueError("Saved decision source identity differs from the frozen source")
        refs = row.get("evidence_refs", [])
        if not isinstance(refs, list) or len(refs) > 100 or any(not isinstance(ref, dict) for ref in refs):
            raise ValueError("Saved decision evidence references must be a bounded list")
        reference_count += len(refs)
        if reference_count > MAX_EVIDENCE_REFERENCES:
            raise ValueError("Too many saved decision evidence references")
        references = [_anchor({**ref, "document_id": ref.get("document_id", anchor["document_id"])}, sources) for ref in refs]
        evidence_characters += len(anchor["quote"]) + sum(len(ref["quote"]) for ref in references)
        if evidence_characters > MAX_TOTAL_EVIDENCE_CHARACTERS:
            raise ValueError("Saved decision evidence exceeds the synchronous report limit")
        decisions.append({**anchor, "id": _text(row.get("id"), "finding id", 200),
                          "lens_id": lens["id"], "criterion_id": criterion["id"], "criterion_kind": criterion["kind"],
                          "criterion_version": lens["version"], "result": row["result"], "evidence_refs": references,
                          "criterion_version_origin": "provided_version_checked" if "criterion_version" in row else "current_registry_lookup; historical_version_not_supplied",
                          **{field: _text(row.get(field), f"finding {field}", 4000) for field in ("speaker", "attribution", "proposition", "speaker_stance", "document_stance", "explanation", "alternative", "source_version_id", "source_hash")},
                          "review_status": "provisional_fixture" if data_origin == "synthetic_demonstration" else "provisional_model_output",
                          "origin": "authored demonstration fixture; not model output" if data_origin == "synthetic_demonstration" else "supplied saved contextual decision"})
    privacy = snapshot.get("privacy_disclosure", "No privacy-processing statement was supplied to the renderer.")
    if isinstance(privacy, dict):
        if len(_json(privacy)) > 32000:
            raise ValueError("Privacy disclosure exceeds the report limit")
        privacy = deepcopy(privacy)
    else:
        privacy = _text(privacy, "privacy_disclosure", 32000)
    result = {"schema_version": REPORT_VERSION, "data_origin": data_origin, "report_brief": brief, "documents": normalized_documents,
              "run_provenance": run_provenance,
              "observations": canonical["observations"], "observation_counts": canonical["counts"],
              "observation_coverage": canonical["coverage"], "observation_limitations": canonical["limitations"],
              "findings": decisions, "expert_commentary": commentary, "privacy_disclosure": privacy,
              "source_records": source_records,
              "counts": {"documents": len(normalized_documents), "blocks": block_count, "characters": characters,
                         "words": sum(item["word_count"] for item in normalized_documents), "wording_observations": len(canonical["observations"]),
                         "authored_eligible_wording_observations": sum(item["count_eligible"] for item in canonical["observations"]),
                         "context_wording_observations": sum(not item["count_eligible"] for item in canonical["observations"]),
                         "saved_contextual_decisions": len(decisions), "operator_commentary": len(commentary),
                         "operator_signed_notes": sum(item["review_status"] == "operator_reviewed" for item in commentary)},
              "interpretation_status": "reference_pending", "expert_qualification": "not_verified_by_application",
              "source_selection_complete": True,
              "appendix": {"wording_observations_shown": min(len(canonical["observations"]), EVIDENCE_APPENDIX_LIMIT),
                           "wording_observations_total": len(canonical["observations"]),
                           "contextual_decisions_shown": min(len(decisions), EVIDENCE_APPENDIX_LIMIT),
                           "contextual_decisions_total": len(decisions),
                           "operator_notes_shown": min(len(commentary), COMMENTARY_APPENDIX_LIMIT),
                           "operator_notes_total": len(commentary),
                           "full_selected_data_formats": ["json", "xlsx"]}}
    academic = _academic_summary(result)
    if academic is not None:
        result["academic_annex"] = academic
    if len(_json(result)) > MAX_ARTIFACT_BYTES // 2:
        raise ValueError("Frozen pilot report input is too large; narrow the selection")
    return result


def _plain(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value or "")


def _academic_records(snapshot: dict) -> tuple[list[dict], list[dict]]:
    """Resolve citations from the frozen brief, never a newer registry."""
    frozen = snapshot["report_brief"]["brief"].get("method_cards_snapshot")
    if not frozen:
        return [], []
    cards = frozen["cards"]
    references, known = [], {}
    for card in cards:
        for raw in card.get("references", []):
            key = _sha(_json(raw))
            if key not in known:
                reference = {**deepcopy(raw), "reference_id": "R" + str(len(references) + 1), "cited_by_criteria": []}
                references.append(reference)
                known[key] = reference
            if card["id"] not in known[key]["cited_by_criteria"]:
                known[key]["cited_by_criteria"].append(card["id"])
    return cards, references


def _academic_summary(snapshot):
    cards, references = _academic_records(snapshot)
    frozen = snapshot["report_brief"]["brief"].get("method_cards_snapshot")
    if not frozen:
        return None
    return {"snapshot_sha256": frozen["snapshot_sha256"], "registry_version": frozen["registry_version"],
            "method_cards_shown": min(len(cards), METHOD_CARD_APPENDIX_LIMIT), "method_cards_total": len(cards),
            "references_shown": min(len(references), REFERENCE_APPENDIX_LIMIT), "references_total": len(references),
            "full_contract_formats": ["json", "xlsx"],
            "notice": "Academic references motivate operational choices; they do not establish DOXA interpretation accuracy or certify this report."}


def _display_method_text(value, maximum=1400):
    text = "\n".join(str(item) for item in value) if isinstance(value, list) else _plain(value)
    return text if len(text) <= maximum else text[:maximum] + " … [complete field retained in JSON/XLSX]"


def _reference_url(reference):
    url = str(reference.get("url") or "")
    parsed = urlsplit(url)
    return url if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password else None


def _reference_link_label(url):
    # Retain the full URL as the link target and in structured data. A short
    # visible label keeps long query strings out of a human report's layout.
    parsed = urlsplit(url)
    label = (parsed.hostname or "Reference") + parsed.path
    return label if len(label) <= 110 else label[:107] + "…"


def _card_lines(card):
    return [("Construct", card.get("construct", "")),
            ("Operational rule", card.get("operational_rule", "")),
            ("Required context", card.get("required_context", [])),
            ("Exclusions", card.get("exclusion_rules", [])),
            ("Permitted report wording", card.get("permitted_report_wording", ""))]


def _readable_privacy_lines(value):
    if not isinstance(value, dict):
        return [_plain(value)]
    labels = {"mode": "Source handling", "destination": "Processing destination", "identity_mapping_included": "Identity mapping included in the report"}
    lines = []
    for key, item in list(value.items())[:20]:
        text = "yes" if item is True else "no" if item is False else _display_method_text(item, 1000)
        lines.append(labels.get(key, str(key).replace("_", " ").capitalize()) + ": " + text)
    if len(value) > 20:
        lines.append(f"Showing 20 of {len(value)} supplied privacy declarations; complete values remain in JSON/XLSX.")
    return lines


def _wording_coverage_lines(snapshot):
    coverage = snapshot["observation_coverage"]
    return [f"Selected extracted-text blocks scanned: {coverage.get('blocks_scanned', 'not recorded')}; source characters scanned: {coverage.get('characters_scanned', 'not recorded')}.",
            f"Blocks eligible for authored-wording counts: {coverage.get('authored_eligible_blocks', 'not recorded')}; context blocks excluded from those counts: {coverage.get('excluded_context_blocks', 'not recorded')}.",
            f"Wording markers retained: {coverage.get('observations_returned', 'not recorded')}; omitted after the declared limit: {coverage.get('observations_omitted', 'not recorded')}.",
            "Selected extracted-text scan complete: " + ("yes" if coverage.get("source_complete") else "no") + ". This describes the wording scan, not interpretation accuracy or the completeness of original-document extraction.",
            "Complete coverage counters and frozen selection remain in JSON/XLSX."]


def _chart_rows(snapshot: dict) -> list[tuple[str, int]]:
    labels = {"request_marker": "Request wording", "future_commitment_marker": "Future commitment wording",
              "time_constraint_marker": "Time or deadline wording", "hedge_marker": "Qualification wording",
              "imperative_form": "Imperative form"}
    return [(labels.get(label, label.replace("_", " ")), count) for label, count in snapshot["observation_counts"].items()]


def _task_sections(snapshot: dict) -> list[tuple[str, list[str]]]:
    brief = snapshot["report_brief"]["brief"]
    if brief["profile_id"] == "company":
        policy = brief["company_policy"]
        channels = Counter(document["document_type"] or "unspecified" for document in snapshot["documents"])
        paragraphs = ["Source mix by declared document type: " + "; ".join(f"{name}: {count}" for name, count in sorted(channels.items())),
                      "These are selected organizational documents. No employee ranking, inferred beliefs or population-wide company score is produced."]
        if policy:
            paragraphs += [f"Declared customer policy: {policy['title']} · version {policy['version']}.",
                           "The supplied policy is frozen in the data annex. Supplying it does not establish that alignment was evaluated; any policy judgment needs a declared criterion, contextual evidence and review."]
        else:
            paragraphs.append("No versioned company policy was supplied. Policy alignment is not assessed.")
        return [("Organizational scope and declared policy", paragraphs)]
    if brief["profile_id"] == "public":
        comparator = brief["comparator"]
        paragraphs = ["Frames, target-specific stance and comparative asymmetry remain different constructs. A descriptive criterion being met is not a prejudice verdict."]
        if comparator:
            paragraphs += ["Declared comparison question: " + comparator["question"], "Inclusion rules: " + comparator["inclusion_rules"],
                           "Comparison status: declared and unverified. The renderer does not certify matching, omitted perspectives or deliberate selection."]
            selected = {document["id"] for document in snapshot["documents"]}
            for group in comparator["source_groups"]:
                ids = set(group["document_ids"])
                paragraphs.append(f"{group['label']}: {len(ids & selected)} declared source IDs present in this report; {len(ids - selected)} absent from this selection.")
        else:
            paragraphs.append("No comparator was declared. Omission, double-standard and matched-source claims are not assessed by this report.")
        paragraphs.append("Trajectories and statistically significant spike claims are not calculated by this bounded renderer; use a separately qualified monitoring protocol.")
        return [("Political comparison design and limits", paragraphs)]
    roles = Counter(block.get("mail_role", "unclassified document text") for document in snapshot["documents"] for block in document["blocks"])
    return [("Selected writing and conversational context", [
        "Available source blocks: " + "; ".join(f"{role}: {count}" for role, count in sorted(roles.items())),
        "Quoted, forwarded, signature and explicitly ineligible blocks are excluded from authored-unit marker counts. Parser roles and sender headers are not proof of authorship.",
        "The user's question and audience above govern the inquiry. Wording observations do not predict recipient reactions, diagnose feelings or confirm that a commitment is intended.",
        "No message is sent or rewritten. Possible tone readings need context and qualified review; recipient-perception claims need independent reader ratings."]) ]


def _provenance_lines(snapshot: dict) -> list[str]:
    provenance = snapshot["run_provenance"]
    if provenance is None:
        return ["No contextual run provenance was supplied. The renderer does not invent a model identity or claim new model inference.",
                "Contextual processing coverage is not recorded here; retained decisions cannot establish complete processing."]
    def recorded(field):
        return provenance.get(field) or "not recorded"
    lines = [f"Run: {recorded('run_id')} · Status: {recorded('run_status')} · Provider: {recorded('provider')} · Model: {recorded('model')}",
            f"Model revision: {recorded('model_revision')} · Method fingerprint: {recorded('method_fingerprint')}",
            "Actual run question: " + recorded("question"), "Actual run perspective: " + recorded("perspective"),
            "Run service-brief fingerprint: " + recorded("service_brief_fingerprint"),
            "Scope: selected sources and declared lenses; " + str(len(provenance["selected_document_ids"])) + " selected source IDs retained in the data annex.",
            "This is supplied frozen-run provenance, not independent interpretation validation or proof that managed weights were pinned."]
    coverage = provenance.get("coverage", {})
    original = coverage.get("original_run")
    if original is not None:
        lines.append("Original run coverage (recorded progress scope): " + ("; ".join(f"{key.replace('_', ' ')}: {value}" for key, value in original.items()) or "counters not recorded"))
    else:
        lines.append("Original run coverage: not supplied.")
    selected = coverage.get("selected_sources")
    if selected is not None:
        lines.append("Selected-source coverage below is separate from the original run's wider processing scope.")
        for row in selected:
            counts = "; ".join(f"{key.replace('_', ' ')}: {value}" for key, value in row.items() if key != "document_id")
            lines.append(row["document_id"] + ": " + (counts or "counters not recorded"))
    else:
        lines.append("Selected-source unit/decision coverage: not supplied.")
    lines.append("Missing counters and expected decision counts are not inferred. Partial or unrecorded processing must not be read as a negative result or complete absence of relevant language.")
    return lines


def _html(snapshot: dict) -> bytes:
    brief = snapshot["report_brief"]
    profile = get_profile(brief["brief"]["profile_id"])
    parts = ["<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; form-action 'none'\">",
             f"<title>{escape(profile['name'])} | Guided pilot report</title>",
             "<style>body{font:16px/1.6 system-ui,sans-serif;color:#173c39;max-width:1060px;margin:auto;padding:36px}h1{font-size:40px;line-height:1.15}h2{margin-top:40px}small,.muted{color:#53635e}.status{background:#fff2d4;padding:16px;border-left:5px solid #b58945}.stats{display:flex;gap:16px;flex-wrap:wrap}.stat{padding:16px;background:#edf2e9;min-width:130px}.stat strong{display:block;font-size:28px}table{border-collapse:collapse;width:100%;margin:16px 0}td,th{border-bottom:1px solid #d6dfd6;text-align:left;padding:10px;overflow-wrap:anywhere}blockquote{border-left:4px solid #1f7a70;padding:12px 18px;margin:18px 0;background:#f3f6f1;white-space:pre-wrap;overflow-wrap:anywhere}.case{break-inside:avoid}pre{white-space:pre-wrap;overflow-wrap:anywhere}.bar{display:inline-block;background:#1f7a70;height:14px;margin-right:10px}@media print{body{padding:0}h2{break-after:avoid}}</style>",
             "<body><header><small>DOXA BY TACITUS · BIAS DETECTION AS A SERVICE</small>", f"<h1>{escape(profile['name'])}</h1>",
             f"<p>{escape(profile['description'])} · Guided pilot evidence report</p></header>",
             "<p class='status'><strong>Reference labels pending.</strong> Wording markers are deterministic observations, not bias verdicts or recipient-tone predictions. Operator signatures are self-declared review records; the application does not establish expert qualifications or independent validity.</p>"]
    counts = snapshot["counts"]
    parts.append("<section class='stats' aria-label='Selected source counts'>")
    for label, key in [("Selected documents", "documents"), ("Source words", "words"), ("Wording observations", "wording_observations"), ("Contextual decision records", "saved_contextual_decisions")]:
        parts.append(f"<div class='stat'><strong>{counts[key]:,}</strong>{escape(label)}</div>")
    parts.append("<p>Data origin: " + escape(snapshot["data_origin"].replace("_", " ")) + ". " + ("Sources and illustrative decisions are authored software fixtures, not model outputs or independent reference labels." if snapshot["data_origin"] == "synthetic_demonstration" else "Source rights and permissions are the responsibility of the authorized service workflow.") + "</p>")
    parts.extend(["</section><h2>Question, audience and declared method</h2>",
                  f"<p><strong>Question:</strong> {escape(brief['brief']['question'])}</p>",
                  f"<p><strong>Audience:</strong> {escape(brief['brief']['audience'])}</p>",
                  f"<p><strong>Perspective:</strong> {escape(brief['brief']['perspective'])}</p>",
                  "<p>Report sections: " + escape(" · ".join(brief["sections"])) + "</p>",
                  "<p>Declared frameworks: " + escape(", ".join(brief["brief"]["lens_ids"])) + "</p>"])
    for title, paragraphs in _task_sections(snapshot):
        parts.append("<h2>" + escape(title) + "</h2>")
        parts.extend("<p>" + escape(paragraph) + "</p>" for paragraph in paragraphs)
    parts.append("<h2>Observable wording in eligible authored units</h2><p>Counts below are matched wording markers. Repeated markers, document length, quotation and missing context affect interpretation. These are counts, not quality ratings.</p>")
    parts.append(f"<p>{counts['wording_observations']} total retained markers: {counts['authored_eligible_wording_observations']} eligible under the authorship heuristic and {counts['context_wording_observations']} contextual markers excluded from the chart.</p><table><tr><th>Marker</th><th>Count</th></tr>")
    maximum = max([value for _, value in _chart_rows(snapshot)] + [1])
    for label, count in _chart_rows(snapshot):
        parts.append(f"<tr><td>{escape(label)}</td><td><span class='bar' style='width:{round(180 * count / maximum)}px' aria-hidden='true'></span>{count}</td></tr>")
    parts.append("</table><h2>Source selection</h2><table><tr><th>Source</th><th>Type</th><th>Words</th></tr>")
    for document in snapshot["documents"]:
        parts.append(f"<tr><td>{escape(document['title'] or document['id'])}<br><small>{escape(document['author'])}</small></td><td>{escape(document['document_type'])}</td><td>{document['word_count']:,}</td></tr>")
    parts.append("</table><h2>Commented source cases</h2>")
    appendix = snapshot["appendix"]
    parts.append(f"<p>Showing {appendix['wording_observations_shown']} of {appendix['wording_observations_total']} wording observations. JSON/XLSX retain the complete bounded selection.</p>")
    for row in snapshot["observations"][:EVIDENCE_APPENDIX_LIMIT]:
        parts.extend(["<article class='case'>", f"<h3>{escape(row['label'])}</h3>", f"<blockquote>{escape(row['quote'])}</blockquote>",
                      f"<p>{escape(row['document_id'])} / {escape(row['block_id'])} · Unicode offsets {row['start']}–{row['end']} · {'eligible authored unit' if row['count_eligible'] else 'context excluded from authored counts'}</p>",
                      "<p class='muted'>Observed wording; speaker attribution and contextual meaning need review.</p></article>"])
    parts.append("<h2>" + ("Illustrative contextual decision fixtures" if snapshot["data_origin"] == "synthetic_demonstration" else "Saved contextual model decisions") + "</h2>")
    parts.append(f"<p>Showing {appendix['contextual_decisions_shown']} of {appendix['contextual_decisions_total']} supplied decisions. Each remains a provisional interpretation; descriptive or boundary outcomes are not prejudice indicators.</p>")
    for row in snapshot["findings"][:EVIDENCE_APPENDIX_LIMIT]:
        parts.extend([f"<article class='case'><h3>{escape(row['lens_id'])} / {escape(row['criterion_id'])}</h3>",
                      f"<p>{escape(row['criterion_kind'])} · {escape(row['result'])} · {escape(row['review_status'])}</p><blockquote>{escape(row['quote'])}</blockquote>",
                      f"<p>Speaker: {escape(row['speaker'])}; attribution: {escape(row['attribution'])}; proposition: {escape(row['proposition'])}</p>",
                      f"<p>{escape(row['explanation'])}</p><p>Alternative: {escape(row['alternative'])}</p></article>"])
    parts.append("<h2>Operator commentary and review declarations</h2>")
    parts.append(f"<p>Showing {appendix['operator_notes_shown']} of {appendix['operator_notes_total']} notes; {counts['operator_signed_notes']} server-attributed operator signatures. These are not independent validation labels.</p>")
    for row in snapshot["expert_commentary"][:COMMENTARY_APPENDIX_LIMIT]:
        if row.get("quote"):
            parts.append(f"<blockquote>{escape(row['quote'])}</blockquote>")
        parts.append(f"<p>{escape(row['note'])}</p><p class='muted'>{escape(row['review_status'])} · {escape(row['actor_id'] or 'Identity not verified')} · {escape(row['review_origin'])}</p>")
    parts.append("<h2>Contextual run provenance</h2>")
    parts.extend("<p>" + escape(line) + "</p>" for line in _provenance_lines(snapshot))
    academic = snapshot.get("academic_annex")
    if academic:
        cards, references = _academic_records(snapshot)
        parts.append("<section aria-label='Methodology and academic foundations'><h2>Methodology and academic foundations</h2>")
        parts.append("<p>" + escape(academic["notice"]) + "</p>")
        parts.append(f"<p>Showing {academic['method_cards_shown']} of {academic['method_cards_total']} frozen method cards and {academic['references_shown']} of {academic['references_total']} unique references. Fields are bounded for readability; complete contracts and references remain in JSON/XLSX.</p>")
        parts.append("<p>Frozen registry: " + escape(academic["registry_version"]) + " · Snapshot SHA256: " + escape(academic["snapshot_sha256"]) + "</p>")
        for card in cards[:METHOD_CARD_APPENDIX_LIMIT]:
            parts.append("<h3>" + escape(card["id"] + " · " + card["label"]) + "</h3>")
            for name, value in _card_lines(card):
                parts.append("<p><strong>" + escape(name) + ":</strong> " + escape(_display_method_text(value)) + "</p>")
            identifiers = [reference["reference_id"] for reference in references if card["id"] in reference["cited_by_criteria"]]
            parts.append("<p class='muted'>References: " + escape(", ".join(identifiers)) + " · Frozen card SHA256: " + escape(card["card_sha256"]) + "</p>")
        parts.append("<h3>Primary references and their limited role</h3>")
        for reference in references[:REFERENCE_APPENDIX_LIMIT]:
            title = f"{reference['reference_id']}. {reference.get('authors', '')} ({reference.get('year', '')}). {reference.get('title', '')}"
            parts.append("<p><strong>" + escape(_display_method_text(title, 600)) + "</strong></p>")
            url = _reference_url(reference)
            if url:
                parts.append("<p><a rel='noopener noreferrer' href='" + escape(url, quote=True) + "'>" + escape(_reference_link_label(url)) + "</a></p>")
            parts.append("<p><strong>Supports this design choice:</strong> " + escape(_display_method_text(reference.get("supported_claim", ""), 700)) + "</p>")
            parts.append("<p><strong>Does not establish:</strong> " + escape(_display_method_text(reference.get("does_not_establish", ""), 700)) + "</p>")
        parts.append("</section>")
    if academic:
        parts.append("<h2>Privacy, coverage and limitations</h2>")
        parts.extend("<p>" + escape(line) + "</p>" for line in _readable_privacy_lines(snapshot["privacy_disclosure"]) + _wording_coverage_lines(snapshot))
        parts.append("<ul>")
    else:
        parts.append("<h2>Privacy, coverage and limitations</h2><pre>" + escape(_plain(snapshot["privacy_disclosure"])) + "</pre>")
        parts.append("<pre>" + escape(_plain(snapshot["observation_coverage"])) + "</pre><ul>")
    parts.extend("<li>" + escape(item) + "</li>" for item in brief["limitations"] + snapshot["observation_limitations"])
    parts.append("</ul><p>Source hashes and the frozen brief are included in the companion manifest; original bytes were not re-read by this renderer. Complete selected blocks, observations and notes are available in JSON/XLSX.</p></body></html>")
    return "".join(parts).encode("utf-8")


def _font() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import reportlab
    candidates = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                  Path("/Library/Fonts/Arial Unicode.ttf"), Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"]
    path = next((item for item in candidates if item.is_file()), None)
    if path is None:
        raise ValueError("Pilot PDF reports require an installed local TrueType font; Docker includes DejaVu Sans")
    name = "DoxaPilot" + _sha(str(path).encode())[:12]
    with _FONT_LOCK:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path)))
    return name, path.name


def _pdf(snapshot: dict) -> tuple[bytes, dict]:
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    font, filename = _font()
    styles = getSampleStyleSheet()
    for item in styles.byName.values():
        item.fontName = font
        item.wordWrap = "CJK"
    styles["Title"].textColor = colors.HexColor("#173c39")
    styles["BodyText"].fontSize = 9
    styles["BodyText"].leading = 13
    buffer = BytesIO()
    story = []

    def paragraph(value, style="BodyText"):
        text = _plain(value)
        for index in range(0, max(1, len(text)), 2000):
            story.append(Paragraph(escape(text[index:index + 2000]).replace("\n", "<br/>"), styles[style]))
        story.append(Spacer(1, 5))

    brief = snapshot["report_brief"]
    paragraph("DOXA BY TACITUS · BIAS DETECTION AS A SERVICE", "Heading2")
    paragraph(brief["service"], "Title")
    paragraph("Guided pilot evidence report")
    paragraph("Data origin: " + snapshot["data_origin"].replace("_", " ") + ". " + ("Sources and illustrative decisions are authored software fixtures, not model outputs or independent reference labels." if snapshot["data_origin"] == "synthetic_demonstration" else "Rights and permissions are recorded by the authorized service workflow."))
    paragraph("REFERENCE LABELS PENDING. Wording markers are not bias verdicts or recipient-tone predictions. Operator signatures are self-declarations; expert qualification and independent validity are not established by this application.")
    for key in ("question", "audience"):
        paragraph(f"{key.capitalize()}: {brief['brief'][key]}")
    stats = Drawing(470, 67)
    for index, (label, key) in enumerate((("SELECTED DOCUMENTS", "documents"), ("SOURCE WORDS", "words"),
                                        ("WORDING OBSERVATIONS", "wording_observations"), ("CONTEXTUAL DECISIONS", "saved_contextual_decisions"))):
        x = index * 118
        stats.add(Rect(x, 7, 110, 54, rx=3, ry=3, fillColor=colors.HexColor("#edf2e9"), strokeColor=None))
        stats.add(String(x + 8, 44, label, fontName=font, fontSize=6.5, fillColor=colors.HexColor("#53635e")))
        stats.add(String(x + 8, 19, f"{snapshot['counts'][key]:,}", fontName=font, fontSize=20, fillColor=colors.HexColor("#173c39")))
    story.append(stats)
    paragraph("Observable wording in eligible authored units", "Heading2")
    paragraph("Matched wording counts only. These are not quality ratings, bias measurements or calibrated probabilities.")
    paragraph(f"{snapshot['counts']['wording_observations']} total retained markers: {snapshot['counts']['authored_eligible_wording_observations']} eligible under the authorship heuristic; {snapshot['counts']['context_wording_observations']} contextual markers excluded from this chart.")
    chart = Drawing(470, 145)
    maximum = max([value for _, value in _chart_rows(snapshot)] + [1])
    for index, (label, count) in enumerate(_chart_rows(snapshot)):
        y = 125 - index * 25
        chart.add(String(0, y, label, fontName=font, fontSize=9))
        chart.add(Rect(165, y - 2, 240 * count / maximum, 12, fillColor=colors.HexColor("#1f7a70"), strokeColor=None))
        chart.add(String(415, y, str(count), fontName=font, fontSize=9))
    story.append(chart)
    paragraph("Declared method and analytical scope", "Heading2")
    paragraph("Perspective: " + brief["brief"]["perspective"])
    paragraph("Report sections: " + " · ".join(brief["sections"]))
    paragraph("Declared frameworks: " + ", ".join(brief["brief"]["lens_ids"]))
    for title, paragraphs in _task_sections(snapshot):
        paragraph(title, "Heading2")
        for text in paragraphs:
            paragraph(text)
    paragraph("Source selection", "Heading2")
    for document in snapshot["documents"]:
        paragraph(f"{document['title'] or document['id']} · {document['author']} · {document['document_type']} · {document['word_count']:,} words")
    appendix = snapshot["appendix"]
    paragraph("Commented source cases", "Heading2")
    paragraph(f"Showing {appendix['wording_observations_shown']} of {appendix['wording_observations_total']} wording observations. JSON/XLSX retain the complete bounded selection.")
    for row in snapshot["observations"][:EVIDENCE_APPENDIX_LIMIT]:
        paragraph(row["label"], "Heading3")
        paragraph(row["quote"])
        paragraph(f"{row['document_id']} / {row['block_id']} · Unicode offsets {row['start']}–{row['end']} · {'eligible authored unit' if row['count_eligible'] else 'context excluded from authored counts'}")
        paragraph("Observed wording; attribution and contextual meaning require review.")
    paragraph("Illustrative contextual decision fixtures" if snapshot["data_origin"] == "synthetic_demonstration" else "Saved contextual model decisions", "Heading2")
    paragraph(f"Showing {appendix['contextual_decisions_shown']} of {appendix['contextual_decisions_total']} supplied decisions; descriptive and boundary outcomes are not prejudice indicators.")
    for row in snapshot["findings"][:EVIDENCE_APPENDIX_LIMIT]:
        paragraph(f"{row['lens_id']} / {row['criterion_id']} · {row['criterion_kind']} · {row['result']}", "Heading3")
        paragraph(row["quote"])
        paragraph(f"Speaker: {row['speaker']} · Attribution: {row['attribution']} · Proposition: {row['proposition']}")
        paragraph(row["explanation"])
        paragraph("Alternative: " + row["alternative"])
        paragraph(row["review_status"])
    paragraph("Operator commentary and review declarations", "Heading2")
    paragraph(f"Showing {appendix['operator_notes_shown']} of {appendix['operator_notes_total']} notes; {snapshot['counts']['operator_signed_notes']} operator signatures. These are not independent reference labels.")
    for row in snapshot["expert_commentary"][:COMMENTARY_APPENDIX_LIMIT]:
        if row.get("quote"):
            paragraph(row["quote"])
        paragraph(row["note"])
        paragraph(f"{row['review_status']} · {row['actor_id'] or 'Identity not verified'} · {row['review_origin']}")
    paragraph("Contextual run provenance", "Heading2")
    for line in _provenance_lines(snapshot):
        paragraph(line)
    academic = snapshot.get("academic_annex")
    if academic:
        cards, references = _academic_records(snapshot)
        paragraph("Methodology and academic foundations", "Heading2")
        paragraph(academic["notice"])
        paragraph(f"Showing {academic['method_cards_shown']} of {academic['method_cards_total']} frozen method cards and {academic['references_shown']} of {academic['references_total']} unique references. Fields are bounded for readability; complete contracts and references remain in JSON/XLSX.")
        paragraph("Frozen registry: " + academic["registry_version"])
        paragraph("Snapshot SHA256: " + academic["snapshot_sha256"])
        for card in cards[:METHOD_CARD_APPENDIX_LIMIT]:
            paragraph(card["id"] + " · " + card["label"], "Heading3")
            for name, value in _card_lines(card):
                paragraph(name + ": " + _display_method_text(value))
            identifiers = [reference["reference_id"] for reference in references if card["id"] in reference["cited_by_criteria"]]
            paragraph("References: " + ", ".join(identifiers) + " · Frozen card SHA256: " + card["card_sha256"])
        paragraph("Primary references and their limited role", "Heading3")
        for reference in references[:REFERENCE_APPENDIX_LIMIT]:
            paragraph(_display_method_text(f"{reference['reference_id']}. {reference.get('authors', '')} ({reference.get('year', '')}). {reference.get('title', '')}", 600))
            url = _reference_url(reference)
            if url:
                story.append(Paragraph('<link color="#1f7a70" href="' + escape(url, quote=True) + '">' + escape(_reference_link_label(url)) + '</link>', styles["BodyText"]))
            paragraph("Supports this design choice: " + _display_method_text(reference.get("supported_claim", ""), 700))
            paragraph("Does not establish: " + _display_method_text(reference.get("does_not_establish", ""), 700))
    paragraph("Privacy, coverage and limitations", "Heading2")
    if academic:
        for line in _readable_privacy_lines(snapshot["privacy_disclosure"]) + _wording_coverage_lines(snapshot):
            paragraph(line)
    else:
        paragraph(snapshot["privacy_disclosure"])
        paragraph(snapshot["observation_coverage"])
    for limit in brief["limitations"] + snapshot["observation_limitations"]:
        paragraph(limit)
    paragraph("Original file bytes were not re-read by the renderer. Source hashes are recorded metadata; extracted block hashes, source selection and the frozen brief are retained in the companion manifest and complete selected JSON/XLSX.")
    glyphs = pdfmetrics.getFont(font).face.charToGlyph
    displayed_text = _plain(brief) + _plain(snapshot["documents"]) + _plain(snapshot["expert_commentary"])
    missing = sorted({ord(char) for char in displayed_text if ord(char) >= 32 and ord(char) not in glyphs})
    if missing:
        paragraph("Font coverage: some source characters may not render in this local PDF font. Original Unicode text remains in JSON/XLSX/HTML. Missing code points: " + ", ".join(f"U+{value:04X}" for value in missing[:30]))
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=42, leftMargin=42, topMargin=40, bottomMargin=42,
                            title=brief["service"] + " guided pilot report", author="DOXA by TACITUS")

    def footer(canvas, document):
        canvas.setFont(font, 8)
        canvas.drawString(42, 25, "DOXA / TACITUS · Reference labels pending")
        canvas.drawRightString(A4[0] - 42, 25, str(document.page))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue(), {"font_file": filename, "missing_code_points": [f"U+{value:04X}" for value in missing]}


def _xlsx(snapshot: dict) -> bytes:
    import xlsxwriter
    buffer = BytesIO()
    book = xlsxwriter.Workbook(buffer, {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False, "strings_to_numbers": False})
    header = book.add_format({"bold": True, "bg_color": "#173C39", "font_color": "#FFFFFF", "text_wrap": True})
    wrap = book.add_format({"text_wrap": True, "valign": "top"})

    def sheet(name, columns, rows):
        output = book.add_worksheet(name)
        output.freeze_panes(1, 0)
        output.set_column(0, max(0, len(columns) - 1), 24)
        for column, label in enumerate(columns):
            output.write_string(0, column, label, header)
        row_count = 0
        for row_count, values in enumerate(rows, 1):
            for column, value in enumerate(values):
                if isinstance(value, bool):
                    output.write_boolean(row_count, column, value)
                elif isinstance(value, (int, float)):
                    output.write_number(row_count, column, value)
                else:
                    text = _plain(value)
                    if len(text) > 32767:
                        raise ValueError("Workbook field exceeds Excel's cell limit; use the complete JSON artifact")
                    output.write_string(row_count, column, text, wrap)
        output.autofilter(0, 0, row_count, len(columns) - 1)
        return output
    sheet("Read me", ["Field", "Value"], [("Service", snapshot["report_brief"]["service"]),
          ("Data origin", snapshot["data_origin"]),
          ("Interpretation status", "reference_pending"), ("Review", "Operator signatures are self-declarations; not independent validation or verified expert qualifications."),
          ("Marker meaning", "Observable wording, not bias, tone, politeness or emotion scores."),
          ("Privacy disclosure", snapshot["privacy_disclosure"]),
          *[("Limitation", item) for item in snapshot["report_brief"]["limitations"]]])
    document_fields = ["id", "title", "author", "institution", "filename", "document_type", "published_at", "source_url", "license", "source_version_id", "word_count", "sha256", "extracted_blocks_sha256"]
    sheet("Sources", document_fields, [[row[field] for field in document_fields] for row in snapshot["documents"]])
    source_rows = []
    for document in snapshot["documents"]:
        for block in document["blocks"]:
            for start in range(0, max(1, len(block["text"])), 32000):
                source_rows.append((document["id"], block["id"], start, min(start + 32000, len(block["text"])), block["text"][start:start + 32000], block.get("mail_role", ""), block.get("eligible_for_author_counts", ""), block.get("ineligible_for_author_counts", ""), {field: block[field] for field in _BLOCK_CONTEXT_FIELDS if field in block}))
    sheet("Source blocks", ["Document ID", "Block ID", "Unicode start", "Unicode end", "Source text fragment", "Mail role", "Eligible for author counts", "Ineligible for author counts", "Source locator and mail context"], source_rows)
    observation_fields = ["id", "document_id", "block_id", "start", "end", "quote", "kind", "label", "communicative_act_candidate", "count_eligible", "mail_role", "attribution", "status", "review_status", "offset_unit", "provenance"]
    sheet("Wording observations", observation_fields,
          [[row.get(field, "") for field in observation_fields] for row in snapshot["observations"]])
    # Evidence references can exceed a cell; each reference gets a complete row.
    decision_fields = ["id", "document_id", "block_id", "start", "end", "lens_id", "criterion_id", "criterion_kind", "criterion_version", "criterion_version_origin", "result", "speaker", "attribution", "proposition", "speaker_stance", "document_stance", "explanation", "alternative", "source_hash", "source_version_id", "review_status", "origin"]
    sheet("Contextual decisions", decision_fields, [[row[field] for field in decision_fields] for row in snapshot["findings"]])
    evidence_rows = []
    for index, row in enumerate(snapshot["findings"]):
        for reference in [{key: row[key] for key in ("document_id", "block_id", "start", "end", "quote")}, *row["evidence_refs"]]:
            for offset in range(0, len(reference["quote"]), 32000):
                evidence_rows.append((row["id"] or str(index), reference["document_id"], reference["block_id"], reference["start"] + offset,
                                      min(reference["start"] + offset + 32000, reference["end"]), reference["quote"][offset:offset + 32000]))
    sheet("Decision evidence", ["Decision ID", "Document ID", "Block ID", "Start", "End", "Exact quote fragment"], evidence_rows)
    sheet("Operator notes", ["Document ID", "Block ID", "Start", "End", "Exact quote", "Note", "Actor ID", "Review status", "Review origin", "Independent validation"],
          [(row["document_id"], row.get("block_id", ""), row.get("start", ""), row.get("end", ""), row.get("quote", ""), row["note"], row["actor_id"], row["review_status"], row["review_origin"], False) for row in snapshot["expert_commentary"]])
    method_text = _plain(snapshot["report_brief"])
    sheet("Method and coverage", ["Field", "Part", "Value"], [("Frozen method", index // 32000 + 1, method_text[index:index + 32000]) for index in range(0, len(method_text), 32000)] +
          [("Counts", 1, snapshot["counts"]), ("Coverage", 1, snapshot["observation_coverage"]), ("Input snapshot SHA256", 1, _sha(_json(snapshot)))])
    if snapshot.get("academic_annex"):
        cards, references = _academic_records(snapshot)
        def complete_rows(rows):
            # Every long field survives Excel's cell limit with an explicit
            # part number; no reference or operational rule is silently cut.
            for row in rows:
                fragments = [[value] if isinstance(value, (int, float, bool)) else [_plain(value)[offset:offset + 32000] for offset in range(0, max(1, len(_plain(value))), 32000)] for value in row[1:]]
                for part in range(max([len(values) for values in fragments] + [1])):
                    yield [row[0], part + 1, *[values[part] if part < len(values) else "" for values in fragments]]
        sheet("Academic methods", ["Criterion", "Part", "Label", "Construct", "Kind", "Operational rule", "Required context", "Exclusions", "Permitted report wording", "Reference IDs", "Card SHA256"], complete_rows([
            [card["id"], card["label"], card.get("construct", ""), card.get("criterion_kind", ""), card.get("operational_rule", ""), "\n".join(card.get("required_context", [])), "\n".join(card.get("exclusion_rules", [])), card.get("permitted_report_wording", ""), ", ".join(reference["reference_id"] for reference in references if card["id"] in reference["cited_by_criteria"]), card["card_sha256"]]
            for card in cards]))
        sheet("Academic references", ["Reference ID", "Part", "Authors", "Year", "Title", "Source URL", "Supports this design choice", "Does not establish", "Cited by criteria"], complete_rows([
            [reference["reference_id"], reference.get("authors", ""), reference.get("year", ""), reference.get("title", ""), reference.get("url", ""), reference.get("supported_claim", ""), reference.get("does_not_establish", ""), "\n".join(reference["cited_by_criteria"])]
            for reference in references]))
    source_fields = ["document_id", "source_sha256", "source_hash_origin", "extracted_blocks_sha256", "source_version_id"]
    sheet("Source provenance", source_fields, [[row[field] for field in source_fields] for row in snapshot["source_records"]])
    provenance_values = list(snapshot["run_provenance"].items()) if snapshot["run_provenance"] is not None else [("Status", "No contextual run provenance supplied; no model identity invented.")]
    provenance_rows = []
    for field, value in provenance_values:
        text = _plain(value)
        provenance_rows.extend((field, offset // 32000 + 1, text[offset:offset + 32000]) for offset in range(0, max(1, len(text)), 32000))
    sheet("Run provenance", ["Field", "Part", "Value"], provenance_rows)
    book.close()
    return buffer.getvalue()


def build_pilot_report(snapshot: dict, format: str, *, trusted_reviews: bool = False) -> dict:
    """Return ``content/media_type/extension/manifest`` for a private artifact.

    The adapter must authorize all selected source IDs and downloads, stamp
    operator identities, and persist the manifest separately. This function
    does not create a durable job, store or publish customer data.
    """
    formats = {"pdf": "application/pdf", "html": "text/html; charset=utf-8",
               "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "json": "application/json"}
    if format not in formats:
        raise ValueError("Pilot report format must be pdf, html, xlsx or json")
    frozen = freeze_snapshot(snapshot, trusted_reviews=trusted_reviews)
    snapshot_bytes = _json(frozen)
    extras = {}
    if format == "pdf":
        content, extras = _pdf(frozen)
    elif format == "html":
        content = _html(frozen)
    elif format == "xlsx":
        content = _xlsx(frozen)
    else:
        content = snapshot_bytes
    if len(content) > MAX_ARTIFACT_BYTES:
        raise ValueError("Pilot artifact exceeds its synchronous size limit; narrow the selection")
    manifest = {"schema_version": REPORT_VERSION, "format": format, "media_type": formats[format],
                "bytes": len(content), "sha256": _sha(content), "snapshot_sha256": _sha(snapshot_bytes),
                "brief": frozen["report_brief"]["brief"], "source_records": frozen["source_records"],
                "run_provenance": frozen["run_provenance"],
                "counts": frozen["counts"], "privacy_disclosure": frozen["privacy_disclosure"],
                "interpretation_status": "reference_pending", "data_origin": frozen["data_origin"], "expert_qualification": "not_verified_by_application",
                "observation_method": OBSERVATION_VERSION, "model_inference_performed_by_renderer": False,
                "appendix": frozen["appendix"], "pdf_font": extras or None,
                "data_scope": "full bounded selected input; not necessarily the entire project",
                "delivery": "bounded synchronous artifact; not a durable report job"}
    if frozen.get("academic_annex"):
        manifest["academic_annex"] = frozen["academic_annex"]
    return {"content": content, "media_type": formats[format], "extension": format, "manifest": manifest}
