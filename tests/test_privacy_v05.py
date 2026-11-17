import base64
import json
import os
import re
import stat

import pytest

from doxa.privacy import (
    Detection,
    PrivateMapVault,
    PrivacyConfigurationError,
    PrivacyError,
    PrivacyLimits,
    flatten_fields,
    map_span,
    pseudonymize,
    restore_fields,
    vault_key_from_env,
)


KEY = bytes(range(32))


def test_preview_redacts_headers_metadata_and_body_but_retains_explicit_limits():
    fields = flatten_fields(
        "Call Alice at (212)555-0123 or alice@example.com. SSN 123-45-6789.",
        metadata={"filename": "Alice_private.txt", "alice@example.com": "https://example.org/Alice"},
        headers={"From": "Alice <alice@example.com>"},
    )
    result = pseudonymize(fields, custom_entities=[{"value": "Alice", "kind": "PERSON"}], context_id="project-1")
    public = result.public_record()
    preview = "\n".join(public["fields"].values())
    assert "alice@example.com" not in preview
    assert "(212)555-0123" not in preview
    assert "123-45-6789" not in preview
    assert "https://example.org/Alice" not in preview
    assert "Alice_private.txt" not in preview
    assert "_private.txt" in preview
    assert not public["guaranteed_anonymous"]
    assert public["profile"]["additional_detector"] is None
    assert public["profile"]["rules_version"] == "doxa-pii-rules-1"
    assert public["counts"]["by_kind"]["EMAIL"] >= 2
    assert any("omitted headers" in notice for notice in public["limitations"])
    assert "original" not in public["substitutions"][0]
    assert "custom_entities" not in public["profile"]
    assert restore_fields(public, result.private_record()) == fields


def test_custom_terms_can_include_full_filename_and_explicit_aliases():
    result = pseudonymize(
        {"body": "Alice asked A. Example to read Alice_private.txt. Alice replied."},
        custom_entities=[
            {"value": "Alice", "kind": "PERSON", "identity": "person-1"},
            {"value": "A. Example", "kind": "PERSON", "identity": "person-1"},
            {"value": "Alice_private.txt", "kind": "IDENTIFIER"},
        ],
    )
    public = result.public_record()
    assert public["fields"]["body"].count("⟦DOXA_PERSON_0001⟧") == 3
    assert "Alice" not in public["fields"]["body"]
    assert public["counts"] == {"entities": 2, "replacements": 4, "by_kind": {"IDENTIFIER": 1, "PERSON": 3}}


def test_result_is_deterministic_detached_and_context_scoped():
    fields = {"subject": "Alice sent mail", "body": "alice@example.com and Alice"}
    terms = [{"value": "Alice", "kind": "PERSON"}]
    a = pseudonymize(fields, custom_entities=terms, context_id="a")
    b = pseudonymize(dict(reversed(list(fields.items()))), custom_entities=list(reversed(terms)), context_id="a")
    assert a.public_record() == b.public_record()
    assert a.private_record() == b.private_record()
    assert a.transform_id != pseudonymize(fields, custom_entities=terms, context_id="b").transform_id
    detached = a.public_record()
    detached["fields"]["body"] = "changed"
    assert a.public_record()["fields"]["body"] != "changed"
    assert "Alice" not in repr(a)
    assert "alice@example.com" not in repr(a)


def test_longer_overlaps_win_without_double_counting_or_broken_offsets():
    fields = {"body": "Ann Lee mailed ann@example.com at https://example.org/ann@example.com."}
    result = pseudonymize(fields, custom_entities=[{"value": "Ann", "kind": "PERSON"},
                                                 {"value": "Ann Lee", "kind": "PERSON"}])
    public = result.public_record()
    assert public["counts"]["by_kind"] == {"EMAIL": 1, "PERSON": 1, "URL": 1}
    spans = public["substitutions"]
    assert all(left["original_end"] <= right["original_start"] for left, right in zip(spans, spans[1:]))
    assert restore_fields(public, result.private_record()) == fields


def test_unicode_code_point_mapping_does_not_normalize_source_or_claim_substitutions_exact():
    text = "🙂 Cafe\u0301: Zoë writes résumé to zoe@example.org. End."
    result = pseudonymize({"body": text}, custom_entities=[{"value": "Zoë", "kind": "PERSON"}])
    public = result.public_record()
    first = public["substitutions"][0]
    assert text[first["original_start"]:first["original_end"]] == "Zoë"
    assert first["original_start"] == text.index("Zoë")
    masked = public["fields"]["body"]
    assert masked.startswith("🙂 Cafe\u0301: ")
    start = masked.index("résumé")
    exact = map_span(public, "body", start, start + len("résumé"))
    assert exact["exact_text"]
    assert text[exact["start"]:exact["end"]] == "résumé"
    substituted = map_span(public, "body", first["transformed_start"] + 1, first["transformed_end"] - 1)
    assert not substituted["exact_text"]
    assert text[substituted["start"]:substituted["end"]] == "Zoë"
    back = map_span(public, "body", text.index("résumé"), text.index("résumé") + len("résumé"),
                    direction="original_to_transformed")
    assert masked[back["start"]:back["end"]] == "résumé"
    full = map_span(public, "body", 0, len(masked))
    assert (full["start"], full["end"], full["exact_text"]) == (0, len(text), False)
    assert restore_fields(public, result.private_record())["body"] == text


def test_no_identifiers_and_empty_field_preserve_copy_mapping():
    result = pseudonymize({"body": "Ordinary policy text.", "title": ""})
    public = result.public_record()
    assert public["counts"]["replacements"] == 0
    assert public["fields"] == {"body": "Ordinary policy text.", "title": ""}
    assert public["segments"]["title"] == []
    assert map_span(public, "body", 0, 8)["exact_text"]
    assert restore_fields(public, result.private_record()) == public["fields"]


def test_card_checksum_and_ip_validation_reduce_false_redaction():
    source = "Card 4111 1111 1111 1111; invalid 4111 1111 1111 1112; IPv4 192.0.2.4 and invalid 999.1.2.3; IPv6 2001:db8::1"
    public = pseudonymize({"body": source}).public_record()
    text = public["fields"]["body"]
    assert "4111 1111 1111 1111" not in text
    assert "4111 1111 1111 1112" in text
    assert "192.0.2.4" not in text
    assert "999.1.2.3" in text
    assert "2001:db8::1" not in text
    assert public["counts"]["by_kind"]["IP_ADDRESS"] == 2


def test_international_phones_and_secret_tokens():
    source = "+44 20 7946 0958; +447700900123; +1 212-555-0123; sk-live_1234567890123456789"
    result = pseudonymize({"body": source})
    public = result.public_record()
    assert public["counts"]["by_kind"]["PHONE"] == 3
    assert public["counts"]["by_kind"]["SECRET"] == 1
    assert restore_fields(public, result.private_record())["body"] == source


def test_case_sensitive_secret_and_url_paths_do_not_share_identity():
    public = pseudonymize({"body": "https://example.org/Alice https://example.org/alice sk-ABCDEFGHIJKLM sk-abcdefghijklm"}).public_record()
    assert public["counts"]["entities"] == 4


def test_names_are_not_silently_claimed_detected_by_rules():
    public = pseudonymize({"body": "Zoë and Arjun discussed policy."}).public_record()
    assert public["fields"]["body"] == "Zoë and Arjun discussed policy."
    assert public["counts"]["replacements"] == 0
    assert any("Names and aliases" in notice for notice in public["limitations"])


def test_dictionary_word_boundaries_preserve_unrelated_words_but_cover_filenames():
    public = pseudonymize({"body": "Ann wrote Ann_private.txt for an Anniversary."},
                          custom_entities=[{"value": "Ann"}]).public_record()
    assert "Anniversary" in public["fields"]["body"]
    assert "Ann_private.txt" not in public["fields"]["body"]
    assert public["counts"]["replacements"] == 2


@pytest.mark.parametrize("fields", [{}, {"body": 4}, {"alice@example.com": "body"}, {"body": "⟦DOXA_PERSON_0001⟧"}])
def test_invalid_inputs_fail_without_partial_preview(fields):
    with pytest.raises(PrivacyError):
        pseudonymize(fields)


@pytest.mark.parametrize("entities", [[{"value": ""}], [{"value": "x", "kind": "name with spaces"}],
                                      [{"value": "x", "regex": ".*"}], [{"value": "x", "identity": ""}]])
def test_dictionary_is_literal_bounded_and_validated(entities):
    with pytest.raises(PrivacyError):
        pseudonymize({"body": "x"}, custom_entities=entities)


def test_literal_dictionary_does_not_execute_regular_expressions():
    result = pseudonymize({"body": "A.*B is a literal name; AXXXXB is unchanged."},
                          custom_entities=[{"value": "A.*B", "kind": "PERSON"}])
    assert "AXXXXB" in result.public_record()["fields"]["body"]
    assert result.public_record()["counts"]["replacements"] == 1


def test_input_entity_span_and_deadline_limits(monkeypatch):
    with pytest.raises(PrivacyError, match="input size"):
        pseudonymize({"body": "🙂" * 3}, limits=PrivacyLimits(max_total_bytes=10))
    with pytest.raises(PrivacyError, match="Too many"):
        pseudonymize({"body": "A B"}, custom_entities=[{"value": "A"}, {"value": "B"}],
                     limits=PrivacyLimits(max_custom_entities=1))
    with pytest.raises(PrivacyError, match="span count"):
        pseudonymize({"body": "a@b.org c@d.org"}, limits=PrivacyLimits(max_spans=1))
    ticks = iter((0, 10))
    monkeypatch.setattr("doxa.privacy.time.monotonic", lambda: next(ticks))
    with pytest.raises(PrivacyError, match="deadline"):
        pseudonymize({"body": "a@b.org"}, limits=PrivacyLimits(deadline_seconds=1))


@pytest.mark.parametrize("kwargs", [{"deadline_seconds": 0}, {"deadline_seconds": float("nan")},
                                   {"max_total_bytes": -1}, {"max_fields": True}, {"max_spans": 100001}])
def test_unsafe_limit_configuration_rejected(kwargs):
    with pytest.raises(PrivacyConfigurationError):
        PrivacyLimits(**kwargs)


def test_optional_detector_is_explicit_versioned_and_checked():
    class NameDetector:
        profile = {"name": "test-only", "version": "1", "revision": "fixture-v1"}

        def detect(self, text):
            return [Detection(0, 3, "PERSON")]

    result = pseudonymize({"body": "Zoë says hello."}, detector=NameDetector())
    assert "Zoë" not in result.public_record()["fields"]["body"]
    assert result.public_record()["profile"]["additional_detector"]["revision"] == "fixture-v1"

    class BadDetector(NameDetector):
        def detect(self, text):
            return [Detection(0, 999, "PERSON")]

    with pytest.raises(PrivacyError, match="invalid source span"):
        pseudonymize({"body": "Zoë"}, detector=BadDetector())
    NameDetector.profile = {"name": "unversioned"}
    with pytest.raises(PrivacyConfigurationError):
        pseudonymize({"body": "Zoë"}, detector=NameDetector())


def test_flatten_nested_data_also_inspects_keys_and_rejects_unbounded_or_non_json_inputs():
    original = {"alice@example.com": {"people": ["Arjun", "Zoë"]}, "number": 12}
    fields = flatten_fields("Body", original, {"To": "alice@example.com"})
    assert "alice@example.com" in fields.values()
    assert all("alice@example.com" not in name for name in fields)
    assert "Zoë" in fields.values()
    assert not any("alice@example.com" in value for value in pseudonymize(fields).public_record()["fields"].values())
    with pytest.raises(PrivacyError):
        flatten_fields("Body", {"bad": float("nan")})
    with pytest.raises(PrivacyError):
        flatten_fields("Body", {"bad": object()})
    recursive = {}
    recursive["self"] = recursive
    with pytest.raises(PrivacyError, match="structure limits"):
        flatten_fields("Body", recursive)


@pytest.mark.parametrize("start,end", [(0, 0), (-1, 2), (0, 999), (True, 3)])
def test_invalid_mapping_intervals_are_rejected(start, end):
    public = pseudonymize({"body": "A source"}).public_record()
    with pytest.raises(PrivacyError):
        map_span(public, "body", start, end)


def test_restore_detects_public_manifest_and_private_substitution_tampering():
    result = pseudonymize({"body": "alice@example.com"})
    public, private = result.public_record(), result.private_record()
    public["fields"]["body"] = "tampered"
    with pytest.raises(PrivacyError, match="does not match"):
        restore_fields(public, private)
    private["substitutions"][0]["original"] = "other@example.com"
    with pytest.raises(PrivacyError, match="integrity"):
        restore_fields(result.public_record(), private)


def test_vault_requires_operator_key_and_never_falls_back_to_service_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_PRIVACY_VAULT_KEY", raising=False)
    monkeypatch.setenv("DOXA_SERVICE_TOKEN", "not-a-privacy-key")
    with pytest.raises(PrivacyConfigurationError):
        vault_key_from_env()
    with pytest.raises(PrivacyConfigurationError):
        PrivateMapVault(tmp_path / "vault")
    assert not (tmp_path / "vault").exists()
    monkeypatch.setenv("DOXA_PRIVACY_VAULT_KEY", base64.b64encode(KEY).decode())
    assert vault_key_from_env() == KEY
    assert PrivateMapVault.from_env(tmp_path / "vault")
    for value in ("invalid!", base64.b64encode(b"short").decode()):
        monkeypatch.setenv("DOXA_PRIVACY_VAULT_KEY", value)
        with pytest.raises(PrivacyConfigurationError):
            vault_key_from_env()


def test_vault_encrypts_private_map_permissions_nonce_context_and_round_trip(tmp_path):
    vault = PrivateMapVault(tmp_path / "private-maps", KEY)
    fields = {"body": "Alice writes alice@example.org."}
    result = pseudonymize(fields, context_id="project-1", custom_entities=[{"value": "Alice"}])
    one, two = vault.save(result), vault.save(result)
    assert one["id"] != two["id"]
    first = vault.directory / (one["id"] + ".map")
    second = vault.directory / (two["id"] + ".map")
    assert stat.S_IMODE(vault.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert b"Alice" not in first.read_bytes()
    assert b"alice@example.org" not in first.read_bytes()
    a, b = json.loads(first.read_bytes()), json.loads(second.read_bytes())
    assert a["nonce"] != b["nonce"]
    assert a["ciphertext"] != b["ciphertext"]
    private = vault.load(one["id"], expected_context_id="project-1", expected_transform_id=result.transform_id)
    assert restore_fields(result.public_record(), private) == fields
    with pytest.raises(PrivacyError, match="authorized context"):
        vault.load(one["id"], expected_context_id="project-2")
    with pytest.raises(PrivacyError, match="authorized context"):
        vault.load(one["id"], expected_context_id="project-1", expected_transform_id="a" * 32)


def test_vault_detects_ciphertext_header_and_key_tampering(tmp_path):
    vault = PrivateMapVault(tmp_path / "private", KEY)
    result = pseudonymize({"body": "alice@example.org"}, context_id="project-1")
    receipt = vault.save(result)
    path = vault.directory / (receipt["id"] + ".map")
    saved = path.read_bytes()
    envelope = json.loads(saved)
    cipher = bytearray(base64.b64decode(envelope["ciphertext"]))
    cipher[-1] ^= 1
    envelope["ciphertext"] = base64.b64encode(cipher).decode()
    path.write_text(json.dumps(envelope))
    with pytest.raises(PrivacyError, match="authenticated decryption"):
        vault.load(receipt["id"], expected_context_id="project-1")
    envelope = json.loads(saved)
    envelope["context_id"] = "project-2"
    path.write_text(json.dumps(envelope))
    with pytest.raises(PrivacyError, match="authenticated decryption"):
        vault.load(receipt["id"], expected_context_id="project-2")
    path.write_bytes(saved)
    with pytest.raises(PrivacyError, match="authenticated decryption"):
        PrivateMapVault(vault.directory, bytes(reversed(KEY))).load(receipt["id"], expected_context_id="project-1")
    assert "alice@example.org" not in path.read_text()


def test_vault_rejects_traversal_symlinks_and_loose_file_permissions(tmp_path):
    vault = PrivateMapVault(tmp_path / "private", KEY)
    result = pseudonymize({"body": "a@b.org"})
    receipt = vault.save(result)
    with pytest.raises(PrivacyError):
        vault.load("../outside", expected_context_id="local")
    path = vault.directory / (receipt["id"] + ".map")
    os.chmod(path, 0o644)
    with pytest.raises(PrivacyError, match="unsafe"):
        vault.load(receipt["id"], expected_context_id="local")
    os.chmod(path, 0o600)
    link = vault.directory / ("a" * 32 + ".map")
    link.symlink_to(path)
    with pytest.raises(PrivacyError):
        vault.load("a" * 32, expected_context_id="local")
    with pytest.raises(PrivacyError):
        vault.delete("a" * 32)
    outside = tmp_path / "symlink-vault"
    outside.symlink_to(vault.directory)
    with pytest.raises(PrivacyConfigurationError):
        PrivateMapVault(outside, KEY)
    assert vault.delete(receipt["id"])
    assert not vault.delete(receipt["id"])
    with pytest.raises(PrivacyError, match="not found"):
        vault.load(receipt["id"], expected_context_id="local")


def test_separate_vault_contexts_have_no_shared_plaintext_files(tmp_path):
    vault = PrivateMapVault(tmp_path / "private", KEY)
    result = pseudonymize({"body": "private@example.org"}, context_id="tenant-a")
    receipt = vault.save(result)
    assert re.fullmatch(r"[0-9a-f]{32}", receipt["id"])
    assert {path.suffix for path in vault.directory.iterdir()} == {".map"}
    assert not list(vault.directory.glob("*.tmp"))
    assert receipt["algorithm"] == "AES-256-GCM"
    assert "original" not in json.dumps(receipt)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named-pipe behavior")
def test_vault_rejects_named_pipe_without_blocking(tmp_path):
    vault = PrivateMapVault(tmp_path / "private", KEY)
    identity = "b" * 32
    os.mkfifo(vault.directory / (identity + ".map"), mode=0o600)
    with pytest.raises(PrivacyError, match="unsafe"):
        vault.load(identity, expected_context_id="local")
