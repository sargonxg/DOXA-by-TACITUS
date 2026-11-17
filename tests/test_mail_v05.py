"""Mail fidelity, bounded MIME handling and source-aware inference transport."""
import asyncio
import copy
from email.message import EmailMessage
import hashlib
import json
from unittest.mock import patch

import pytest

from doxa.contextual import PROMPT_VERSION, UNIT_BUILDER_VERSION, analyze_units, build_units
from doxa.extraction import extract_document
from doxa.mail import extract_mail


def message(body="Please send the report.\n", *, sender="Alice <alice@example.test>", identifier="<first@example.test>", reply=None):
    mail = EmailMessage()
    mail["From"] = sender
    mail["To"] = "Bob <bob@example.test>"
    mail["Subject"] = "A selected thread"
    mail["Date"] = "Tue, 17 Nov 2026 10:00:00 -0500"
    mail["Message-ID"] = identifier
    if reply:
        mail["In-Reply-To"] = reply
        mail["References"] = reply
    mail.set_content(body)
    return mail


def save(tmp_path, value, name="source.eml"):
    path = tmp_path / name
    path.write_bytes(value.as_bytes() if isinstance(value, EmailMessage) else value)
    return path


def extracted_document(path):
    result = extract_document(path)
    return {"id": "mail-source", "sha256": result["metadata"]["sha256"], "blocks": result["blocks"],
            "author": result["metadata"]["author"], "metadata": result["metadata"]}


def body_blocks(result):
    return [block for block in result["blocks"] if block["mail_source_field"] == "canonical_body"]


def test_eml_decodes_headers_charsets_and_retains_exact_unicode_slices(tmp_path):
    mail = message("Café 🧭 e\u0301.\n\nA second paragraph.\n\nA third paragraph.\n", sender="Renée <renee@example.test>")
    mail.replace_header("Subject", "Café résumé")
    path = save(tmp_path, mail)
    first = extract_document(path)
    assert first == extract_document(path)
    assert first["metadata"]["title"] == "Café résumé"
    assert first["metadata"]["author"] == "Renée <renee@example.test>"
    assert first["metadata"]["messages"][0]["published_at"] == "2026-11-17T10:00:00-05:00"
    text = "".join(block["text"] for block in body_blocks(first))
    assert text == mail.get_content()
    for block in body_blocks(first):
        assert text[block["source_char_start"]:block["source_char_end"]] == block["text"]
    manifest = first["metadata"]["messages"][0]
    assert manifest["canonical_body_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert first["extraction_manifest"]["needs_review"]
    assert not first["extraction_manifest"]["source_coverage_complete"]
    assert first["extraction_manifest"]["engine_versions"]["doxa-mail"] == "mail-context-1.0"


def test_markers_separate_new_text_quotes_forwarding_and_signatures(tmp_path):
    source = "I disagree with this claim.\n\nOn Tuesday someone wrote:\n> All of them are bad.\n\nHere is my own conclusion.\n-- \nAlice\n"
    result = extract_document(save(tmp_path, message(source)))
    blocks = body_blocks(result)
    assert {block["mail_role"] for block in blocks} == {"authored", "quoted", "signature"}
    assert all(block["eligible_for_author_counts"] == (block["mail_role"] == "authored") for block in blocks)
    assert all(block["source_chain"]["represented_speaker"] is None for block in blocks)
    assert "".join(block["text"] for block in blocks) == source
    forward = extract_document(save(tmp_path, message("My note.\n-----Original Message-----\nFrom: Other person\nTheir position.\n")))
    assert any(block["mail_role"] == "forwarded" and "Their position" in block["text"] for block in body_blocks(forward))


def test_mbox_groups_explicit_reply_links_and_marks_repeated_quote_without_losing_text(tmp_path):
    first = message("The report is ready.\n")
    second = message("Thank you.\n\n> The report is ready.\n", sender="Bob <bob@example.test>", identifier="<reply@example.test>", reply="<first@example.test>")
    raw = b"From alice Tue Nov 17 10:00:00 2026\n" + first.as_bytes() + b"\nFrom bob Tue Nov 17 10:05:00 2026\n" + second.as_bytes()
    path = save(tmp_path, raw, "thread.mbox")
    result = extract_document(path)
    assert result["metadata"]["message_count"] == 2
    assert result["metadata"]["thread_count"] == 1
    assert len(result["metadata"]["messages"]) == 2
    assert not any(item["context_missing"] for item in result["metadata"]["messages"])
    repeat = next(block for block in body_blocks(result) if block.get("repeated_quote_of"))
    original = next(block for block in result["blocks"] if block["id"] == repeat["repeated_quote_of"])
    assert original["mail_role"] == "authored"
    assert repeat["mail_role"] == "quoted" and not repeat["eligible_for_author_counts"]
    assert "> The report is ready." in repeat["text"]


def test_missing_context_and_duplicate_message_ids_are_not_hidden(tmp_path):
    mail = message("A reply without its parent.", reply="<absent@example.test>")
    result = extract_document(save(tmp_path, mail))
    assert result["metadata"]["messages"][0]["context_missing"]
    assert "context is incomplete" in " ".join(result["extraction_warnings"])
    raw = b"From one now\n" + mail.as_bytes() + b"\nFrom two now\n" + mail.as_bytes()
    duplicate = extract_document(save(tmp_path, raw, "duplicates.mbox"))
    assert "Repeated Message-ID" in " ".join(duplicate["extraction_warnings"])


def test_plain_alternative_is_not_double_counted_and_attachments_are_metadata_only(tmp_path):
    mail = message("Only this plain representation.\n")
    mail.add_alternative("<p>Only this HTML representation.</p>", subtype="html")
    mail.add_attachment(b"malicious compressed bytes", maintype="application", subtype="zip", filename="../../escape.zip")
    mail.add_attachment(message("Nested private text must not be extracted."))
    result = extract_document(save(tmp_path, mail))
    text = "".join(block["text"] for block in body_blocks(result))
    assert "Only this plain" in text
    assert "HTML representation" not in text
    assert "Nested private" not in text and "compressed bytes" not in text
    attachments = result["metadata"]["messages"][0]["attachments"]
    assert len(attachments) == 2
    assert all(not part["content_extracted"] for part in attachments)
    assert not (tmp_path.parent / "escape.zip").exists()


def test_html_quoted_blocks_and_hidden_scripts_are_processed_offline(tmp_path):
    mail = message()
    mail.set_content("<p>My visible note.</p><blockquote><p>The quoted claim.</p></blockquote>"
                     "<script>fetch('https://secret.invalid')</script><p hidden>private hidden</p>"
                     "<img src='https://secret.invalid/pixel'><p>Final visible words.</p>", subtype="html")
    with patch("socket.create_connection", side_effect=AssertionError("No network permitted")):
        result = extract_document(save(tmp_path, mail))
    text = "".join(block["text"] for block in body_blocks(result))
    assert "My visible note" in text and "Final visible words" in text
    assert "fetch" not in text and "private hidden" not in text and "secret.invalid" not in text
    assert any(block["mail_role"] == "quoted" and "The quoted claim" in block["text"] for block in body_blocks(result))


def test_bad_encoding_warns_and_resource_limits_reject_without_partial_success(tmp_path):
    raw = b"From: a@example.test\nSubject: bad encoding\nContent-Type: text/plain; charset=unknown-charset\n\n\xffbody"
    result = extract_document(save(tmp_path, raw))
    assert "decoding requires source review" in " ".join(result["extraction_warnings"])
    with patch("doxa.mail.MAX_MAIL_MESSAGE_BYTES", 30), pytest.raises(ValueError, match="message limit"):
        extract_document(save(tmp_path, message()))
    mail = message()
    mail.make_mixed()
    for _ in range(3):
        mail.add_attachment(b"attachment", maintype="application", subtype="octet-stream")
    with patch("doxa.mail.MAX_MAIL_PARTS", 2), pytest.raises(ValueError, match="MIME nesting or part count"):
        extract_document(save(tmp_path, mail))
    with patch("doxa.mail.MAX_MAIL_BODY_CHARS", 10), pytest.raises(ValueError, match="character limit"):
        extract_document(save(tmp_path, message()))
    with pytest.raises(ValueError, match="no From separators"):
        extract_mail(save(tmp_path, b"No mailbox separator\n", "invalid.mbox"), ".mbox")


def test_long_body_offsets_remain_exact_and_mailbox_count_limits_fail_explicitly(tmp_path):
    body = ("Unicode 🧭 exact paragraph. " * 900) + "\n\nFinal paragraph.\n"
    result = extract_document(save(tmp_path, message(body)))
    assert "".join(block["text"] for block in body_blocks(result)) == body
    assert all(len(block["text"]) <= 8000 for block in body_blocks(result))
    for block in body_blocks(result):
        assert body[block["source_char_start"]:block["source_char_end"]] == block["text"]
    raw = (b"From one now\n" + message().as_bytes() + b"\n") * 3
    with patch("doxa.mail.MAX_MAIL_MESSAGES", 2), pytest.raises(ValueError, match="message limit"):
        extract_document(save(tmp_path, raw, "too-many.mbox"))


def test_mime_depth_and_attachment_only_messages_fail_actionably(tmp_path):
    nested = message()
    nested.make_mixed()
    child = message("Nested body.")
    child.make_mixed()
    nested.attach(child)
    with patch("doxa.mail.MAX_MAIL_DEPTH", 1), pytest.raises(ValueError, match="MIME nesting"):
        extract_document(save(tmp_path, nested))
    attachment_only = EmailMessage()
    attachment_only["Subject"] = "Only an attachment"
    attachment_only.add_attachment(b"private attachment contents", maintype="application", subtype="pdf", filename="source.pdf")
    with pytest.raises(ValueError, match="no extractable message body"):
        extract_document(save(tmp_path, attachment_only))


class RecordingProvider:
    profile = {"provider": "test", "model": "mail-fixture", "mode": "local", "context_limit": 32768}

    def __init__(self):
        self.calls = []

    def estimate_cost(self, *_):
        return 0

    async def generate(self, messages, schema, **kwargs):
        payload = json.loads(messages[-1]["content"])
        self.calls.append((messages, payload))
        return {"content": {"decisions": [{"unit_id": unit["unit_id"], "results": [3] * len(payload["criterion_order"])} for unit in payload["units"]]},
                "finish_reason": "stop", "usage": {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0}}


def test_mail_units_wire_and_assessments_preserve_roles_and_never_borrow_other_messages(tmp_path):
    first = message("First sender's own wording.\n\n> A quoted claim.\n")
    second = message("Second sender's unrelated wording.\n", sender="Other <other@example.test>", identifier="<other@example.test>")
    path = save(tmp_path, b"From one now\n" + first.as_bytes() + b"\nFrom two now\n" + second.as_bytes(), "two.mbox")
    doc = extracted_document(path)
    units = build_units(doc)
    for unit in units:
        assert len({(sentence["message_key"], sentence["mail_role"]) for sentence in unit["sentences"]}) == 1
        assert all(sentence["message_key"] == unit["message_key"] for sentence in [*unit["context_before"], *unit["context_after"]])
        assert unit["builder_version"] == UNIT_BUILDER_VERSION + "-mail-context-1.0"
    provider = RecordingProvider()
    result = asyncio.run(analyze_units(doc, ["discourse"], provider))
    assert provider.calls and result["coverage"]["complete"]
    assert result["model_profile"]["prompt_version"] == PROMPT_VERSION + "-mail-1.0"
    wire = [unit for _, payload in provider.calls for unit in payload["units"]]
    assert all(unit["mail_context"]["message_key"] for unit in wire)
    assert any(unit["mail_context"]["mail_role"] == "quoted" for unit in wire)
    assert all(assessment["eligible_for_author_counts"] == (assessment["mail_context"]["mail_role"] == "authored") for assessment in result["assessments"])
    assert "not authenticated identities" in provider.calls[0][0][0]["content"]


def test_forged_mail_provenance_fails_before_any_inference(tmp_path):
    doc = extracted_document(save(tmp_path, message("Own text.\n> Quoted text.\n")))
    for mutate in ("unit", "sentence", "unrelated_context"):
        units = copy.deepcopy(build_units(doc))
        if mutate == "unit":
            units[-1]["mail_role"] = "authored"
        elif mutate == "sentence":
            units[-1]["sentences"][0]["eligible_for_author_counts"] = True
        else:
            units[-1]["context_before"][0]["message_key"] = "forged-message"
        provider = RecordingProvider()
        result = asyncio.run(analyze_units(doc, ["discourse"], provider, units=units))
        assert not provider.calls
        assert result["errors"][0]["code"] == "invalid_input"
