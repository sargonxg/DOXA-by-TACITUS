#!/usr/bin/env python3
"""Build three explicitly synthetic, offline guided-pilot report demonstrations."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from doxa.pilot_reports import build_pilot_report
from doxa.service_profiles import validate_brief

ROOT = Path(__file__).resolve().parents[1]


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def document(document_id, title, blocks, *, kind="document", author="Fictional demonstration author"):
    source_text = "\n\n".join(block["text"] for block in blocks)
    return {"id": document_id, "title": "SYNTHETIC · " + title, "author": author,
            "document_type": kind, "filename": document_id + ".txt", "source_version_id": document_id + "-v1",
            "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
            "license": "Original synthetic demonstration text supplied with the DOXA repository; not a real organization or correspondence.",
            "blocks": blocks}, source_text


def inputs():
    company_policy, company_policy_text = document("company-policy", "Fictional company communications policy", [
        {"id": "p1", "text": "This fictional company asks staff to state the requested action, name the responsible team where appropriate, and give a clear deadline. A direct request can be appropriate; qualification can be appropriate when information is incomplete."},
        {"id": "p2", "text": "Quoted complaints and reports of discrimination must retain attribution. The policy does not authorize employee rankings, emotional diagnoses or inferences about religious or political beliefs."}], kind="policy")
    company_message, company_message_text = document("company-message", "Fictional project update", [
        {"id": "m1", "text": "Please review the draft before Friday. We will share the corrected customer notice tomorrow. Perhaps the support team can confirm which wording is still ambiguous.", "mail_role": "authored", "eligible_for_author_counts": True},
        {"id": "m2", "text": "> A customer wrote: 'Your notice seems to suggest that every complaint is invalid.' This is quoted background, not the team's endorsement.", "mail_role": "quoted", "eligible_for_author_counts": False},
        {"id": "m3", "text": "Review the legal team's clarification before sending the notice. No customer notice is actually sent by this demonstration.", "mail_role": "authored", "eligible_for_author_counts": True}], kind="email", author="Fictional project team")
    public_a, public_a_text = document("public-a", "Fictional hearing report A", [
        {"id": "a1", "text": "At a fictional city hearing, the housing committee described the proposed permit reform as a way to reduce delays. Its chair said, 'We will publish the underlying data before Monday.' A tenant group asked whether the reform would reduce available protections."},
        {"id": "a2", "text": "A witness reported the slogan 'Muslims cannot be trusted.' The committee explicitly rejected that stereotype and affirmed that residents must be treated equally. Reporting and condemning this language is different from endorsing it."}], kind="news", author="Fictional reporter A")
    public_b, public_b_text = document("public-b", "Fictional hearing report B", [
        {"id": "b1", "text": "A fictional tenant group framed the same permit proposal as a question of accountability. Its spokesperson said, 'Please provide the evidence before Monday.' The report also recorded the committee's argument that processing delays should be reduced."},
        {"id": "b2", "text": "The report described criticism of the city's policy and condemnation of anti-Muslim stereotyping. These are different subjects. Possibly the selected hearing record is incomplete; this example supplies no independent evidence of deliberately omitted voices."}], kind="news", author="Fictional reporter B")
    personal, personal_text = document("personal-thread", "Fictional selected email thread", [
        {"id": "e1", "text": "Please send the revised figures before Friday. I think the dates in the summary may need clarification. We will review the final version together.", "mail_role": "authored", "eligible_for_author_counts": True},
        {"id": "e2", "text": "> Previous message: Please confirm the old deadline.\n> The quoted request belongs to the previous message, not automatically to the current writer.", "mail_role": "quoted", "eligible_for_author_counts": False},
        {"id": "e3", "text": "--\nFictional signature · no real email account is connected", "mail_role": "signature", "eligible_for_author_counts": False}], kind="email", author="Fictional individual author")
    privacy = {"source_material": "All documents, names and correspondence are fictional software demonstrations.",
               "mode": "synthetic originals", "processing": "Local deterministic markers and report rendering only; no model inference.",
               "identity_map": "none", "human_review": "No real expert review or independent reference labels were supplied."}
    company = {"brief": validate_brief({"profile_id": "company", "audience": "Fictional communications team",
                "company_policy": {"title": "Fictional communications policy", "version": "demo-1", "text": company_policy_text}}),
               "documents": [company_policy, company_message]}
    public = {"brief": validate_brief({"profile_id": "public", "audience": "Reader learning how a comparison is scoped",
               "comparator": {"question": "Which positions and attributed voices appear in two fictional reports of the same hearing?",
                              "inclusion_rules": "Exactly the two supplied fictional hearing reports; no claim of representative media coverage.",
                              "topic": "Fictional permit reform hearing", "genres": ["news"],
                              "source_groups": [{"label": "Fictional report A", "document_ids": ["public-a"]},
                                                {"label": "Fictional report B", "document_ids": ["public-b"]}]}}),
              "documents": [public_a, public_b]}
    personal_input = {"brief": validate_brief({"profile_id": "personal", "question": "Identify observable requests and time wording without guessing how the recipient feels.", "audience": "The fictional sender reviewing their own selected thread"}),
                      "documents": [personal]}
    result = {"company": company, "public": public, "personal": personal_input}
    source_texts = {"company-policy": company_policy_text, "company-message": company_message_text,
                    "public-a": public_a_text, "public-b": public_b_text, "personal-thread": personal_text}
    for value in result.values():
        value.update({"data_origin": "synthetic_demonstration", "privacy_disclosure": privacy,
                      "findings": [], "expert_commentary": []})
    return result, source_texts


def build(destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    source_dir = destination / "pilot-v05-inputs"
    source_dir.mkdir(exist_ok=True)
    cases, source_texts = inputs()
    for name, value in source_texts.items():
        (source_dir / (name + ".txt")).write_bytes(value.encode())
    profiles = []
    for profile, snapshot in cases.items():
        name = f"v05-pilot-demo-{profile}"
        input_path = destination / (name + ".input.json")
        input_path.write_bytes(encoded(snapshot))
        files = [input_path]
        report_manifests = {}
        for format in ("pdf", "html", "xlsx", "json"):
            artifact = build_pilot_report(snapshot, format)
            path = destination / (name + "." + format)
            path.write_bytes(artifact["content"])
            files.append(path)
            report_manifests[format] = artifact["manifest"]
        files.extend(source_dir / (document["id"] + ".txt") for document in snapshot["documents"])
        manifest = {"schema_version": "doxa-synthetic-pilot-demonstration-1.0", "profile": profile,
                    "data_origin": "synthetic_demonstration", "model_requests": 0, "network_requests": 0,
                    "human_reference_labels": 0, "operator_signed_reviews": 0,
                    "description": "Authored fictional sources demonstrate report software; these are not model-quality or prejudice-detection results.",
                    "artifact_manifests": report_manifests,
                    "files": [{"file": path.relative_to(destination).as_posix(), "bytes": path.stat().st_size,
                               "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in files]}
        manifest_path = destination / (name + ".manifest.json")
        manifest_path.write_bytes(encoded(manifest))
        profiles.append({"profile": profile, "counts": report_manifests["json"]["counts"],
                         "manifest": manifest_path.name, "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()})
    invalid = deepcopy(cases["personal"])
    invalid["expert_commentary"] = [{"document_id": "personal-thread", "block_id": "e1", "start": 0,
                                     "end": 8, "quote": "Invented", "note": "Invalid evidence must not be published."}]
    try:
        build_pilot_report(invalid, "json")
    except ValueError as error:
        rejected = "quotation" in str(error).lower()
    else:
        rejected = False
    if not rejected:
        raise RuntimeError("The invalid-quotation publication check did not reject fabricated evidence")
    receipt = {"schema_version": "doxa-pilot-demo-build-receipt-1.0", "profiles": profiles,
               "data_origin": "synthetic_demonstration", "model_requests": 0, "network_requests": 0,
               "human_reference_labels": 0, "operator_signed_reviews": 0, "fabricated_quote_rejected": rejected,
               "reproduction": "Identical inputs/methods preserve snapshot hashes; PDF/XLSX bytes may vary with fonts, libraries and generated metadata."}
    (destination / "v05-pilot-demo.receipt.json").write_bytes(encoded(receipt))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "examples")
    print(json.dumps(build(parser.parse_args().output), indent=2))
