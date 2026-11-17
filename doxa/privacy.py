"""Bounded, versioned pseudonymization and an encrypted native identity vault.

This is a rules-and-explicit-dictionary preview, not an anonymity guarantee or
a trained PII detector. Only supplied fields are inspected. Defaults perform no
network calls or model downloads. Offsets count Unicode code points, not bytes
or JavaScript UTF-16 code units.
"""
from __future__ import annotations

import base64
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Iterable, Mapping, Protocol
import unicodedata
import uuid


SCHEMA = "doxa-privacy-transform-1"
RULES_VERSION = "doxa-pii-rules-1"
VAULT_SCHEMA = "doxa-private-map-aesgcm-1"
MAX_VAULT_BYTES = 16 * 1024 * 1024
_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{0,159}\Z")
_CONTEXT = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
_KIND = re.compile(r"[A-Z][A-Z0-9_]{0,31}\Z")
_ID = re.compile(r"[a-f0-9]{32}\Z")
_RESERVED = "⟦DOXA_"

LIMITATIONS = (
    "Pseudonymization is not guaranteed anonymization; context can still identify people.",
    "Rules can miss identifiers and mask non-identifiers. Review the complete outbound preview.",
    "Names and aliases are detected only when explicitly supplied or found by a separately declared detector.",
    "Only supplied fields are inspected; omitted headers, metadata, images and attachments are not checked.",
    "Masking identity terms can change attribution and framework applicability.",
)


class PrivacyError(ValueError):
    """A privacy operation could not be completed safely."""


class PrivacyConfigurationError(PrivacyError):
    """Operator-provided privacy configuration is missing or invalid."""


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _context(value: str) -> str:
    if not isinstance(value, str) or not _CONTEXT.fullmatch(value):
        raise PrivacyError("Context must be a bounded opaque identifier")
    return value


def _kind(value: str) -> str:
    if not isinstance(value, str) or not _KIND.fullmatch(value):
        raise PrivacyError("Entity kind must be an uppercase identifier")
    return value


@dataclass(frozen=True)
class PrivacyLimits:
    max_total_bytes: int = 2 * 1024 * 1024
    max_fields: int = 256
    max_custom_entities: int = 100
    max_spans: int = 20000
    deadline_seconds: float = 5.0

    def __post_init__(self):
        for name, cap in (("max_total_bytes", 16 * 1024 * 1024), ("max_fields", 1024),
                          ("max_custom_entities", 500), ("max_spans", 100000)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= cap:
                raise PrivacyConfigurationError(f"Invalid privacy limit: {name}")
        if isinstance(self.deadline_seconds, bool) or not isinstance(self.deadline_seconds, (int, float)) or not 0 < self.deadline_seconds <= 30:
            raise PrivacyConfigurationError("Invalid privacy deadline")


@dataclass(frozen=True)
class Detection:
    start: int
    end: int
    kind: str
    identity: str | None = None
    priority: int = 20


class Detector(Protocol):
    """Optional explicit extension. Implementations must declare a pinned profile.

    No detector is installed, downloaded or invoked unless explicitly supplied
    by an operator. This interface makes no inference or performance claim.
    """

    profile: Mapping[str, str]

    def detect(self, text: str) -> Iterable[Detection]: ...


@dataclass(frozen=True, repr=False)
class TransformResult:
    _public_json: str
    _private_json: str

    @property
    def transform_id(self) -> str:
        return self.public_record()["transform_id"]

    def public_record(self) -> dict:
        """Detached preview and provenance; contains no original substitutions."""
        return json.loads(self._public_json)

    def private_record(self) -> dict:
        """Sensitive mapping; pass directly to PrivateMapVault, never to an API."""
        return json.loads(self._private_json)

    def __repr__(self) -> str:
        return f"TransformResult(transform_id={self.transform_id!r})"


_RULES = (
    ("EMAIL", re.compile(r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@(?:[\w](?:[\w-]{0,61}[\w])?\.)+[\w](?:[\w-]{0,61}[\w])?(?![\w.-])")),
    ("PHONE", re.compile(r"(?<!\w)(?:\+?1[ .-]?)?(?:\(\d{3}\)[ .-]?|\d{3}[ .-])\d{3}[ .-]\d{4}(?!\w)")),
    ("PHONE", re.compile(r"(?<!\w)\+\d{1,3}(?:[ .-]\d{1,4}){1,3}[ .-]\d{3,4}(?!\w)")),
    ("PHONE", re.compile(r"(?<!\w)\+\d{9,15}(?!\w)")),
    ("SSN", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    ("IP_ADDRESS", re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")),
    ("IP_ADDRESS", re.compile(r"(?<![\w:])(?:[a-fA-F0-9]{0,4}:){2,7}[a-fA-F0-9]{0,4}(?![\w:])")),
    ("PAYMENT_CARD", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")),
    ("URL", re.compile(r"\bhttps?://[^\s<>\"']+", re.IGNORECASE)),
    ("SECRET", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{16})\b")),
)


def _luhn(value: str) -> bool:
    digits = [int(char) for char in value if char.isascii() and char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _canonical(kind: str, value: str) -> str:
    if kind in {"PHONE", "SSN", "PAYMENT_CARD"}:
        return "".join(char for char in value if char.isdigit())
    normalized = unicodedata.normalize("NFKC", value)
    if kind in {"URL", "SECRET", "IDENTIFIER"}:
        return normalized
    return normalized.casefold()


def _custom_entities(values: Iterable[dict], limits: PrivacyLimits) -> list[dict]:
    result = []
    for value in values:
        if len(result) >= limits.max_custom_entities:
            raise PrivacyError("Too many custom entities")
        if not isinstance(value, dict) or set(value) - {"value", "kind", "identity"}:
            raise PrivacyError("Custom entities accept only value, kind and optional identity")
        text = value.get("value")
        if not isinstance(text, str) or not text.strip() or len(text) > 512:
            raise PrivacyError("Custom entity values must contain 1–512 characters")
        kind = _kind(value.get("kind", "PERSON"))
        identity = value.get("identity")
        if identity is not None and (not isinstance(identity, str) or not identity.strip() or len(identity) > 160):
            raise PrivacyError("Custom identity must contain 1–160 characters")
        result.append({"value": text, "kind": kind, "identity": identity})
    return sorted(result, key=lambda item: (item["kind"], item["identity"] or "", item["value"]))


def _detector_profile(detector: Detector | None) -> dict | None:
    if detector is None:
        return None
    profile = dict(detector.profile)
    if set(profile) != {"name", "version", "revision"} or any(
        not isinstance(value, str) or not value or len(value) > 200 for value in profile.values()
    ):
        raise PrivacyConfigurationError("Additional detector must declare name, version and revision")
    return profile


def _select(candidates: list[Detection]) -> list[Detection]:
    """Prefer wider protective spans, with deterministic tie-breaking."""
    selected: list[Detection] = []
    starts: list[int] = []
    for item in sorted(candidates, key=lambda row: (-(row.end - row.start), row.priority,
                                                  row.start, row.kind, row.identity or "")):
        index = bisect_left(starts, item.start)
        if index and selected[index - 1].end > item.start:
            continue
        if index < len(selected) and selected[index].start < item.end:
            continue
        starts.insert(index, item.start)
        selected.insert(index, item)
    return selected


def pseudonymize(fields: Mapping[str, str], *, custom_entities: Iterable[dict] = (),
                 context_id: str = "local", detector: Detector | None = None,
                 limits: PrivacyLimits | None = None) -> TransformResult:
    """Return a deterministic outbound preview plus an in-memory private map.

    The API adapter chooses its boundary: in a server preview originals have
    already reached DOXA. This function alone does not establish pre-upload
    masking. Source fields and custom terms must never be operationally logged.
    """
    limits = limits or PrivacyLimits()
    context_id = _context(context_id)
    if not isinstance(fields, Mapping) or not 1 <= len(fields) <= limits.max_fields:
        raise PrivacyError("Privacy preview requires a bounded non-empty field mapping")
    if any(not isinstance(name, str) or not _FIELD.fullmatch(name) for name in fields):
        raise PrivacyError("Field names must be bounded non-identifying ASCII identifiers")
    if any(not isinstance(value, str) for value in fields.values()):
        raise PrivacyError("Privacy fields must contain text")
    if sum(len(value.encode("utf-8")) for value in fields.values()) > limits.max_total_bytes:
        raise PrivacyError("Privacy preview exceeds the configured input size")
    if any(_RESERVED in value for value in fields.values()):
        raise PrivacyError("Input contains reserved pseudonym markers")
    custom = _custom_entities(custom_entities, limits)
    additional = _detector_profile(detector)
    started = time.monotonic()
    candidate_count = 0

    def checkpoint():
        if time.monotonic() - started > limits.deadline_seconds:
            raise PrivacyError("Privacy preview exceeded its processing deadline; nothing was saved")

    matches: dict[str, list[Detection]] = {}
    for name, source in sorted(fields.items()):
        candidates: list[Detection] = []

        def add(item: Detection):
            nonlocal candidate_count
            checkpoint()
            if not isinstance(item, Detection) or isinstance(item.start, bool) or isinstance(item.end, bool) or not isinstance(item.start, int) or not isinstance(item.end, int) or not 0 <= item.start < item.end <= len(source):
                raise PrivacyError("Detector returned an invalid source span")
            _kind(item.kind)
            if item.identity is not None and (not isinstance(item.identity, str) or len(item.identity) > 512):
                raise PrivacyError("Detector returned an invalid identity")
            if isinstance(item.priority, bool) or not isinstance(item.priority, int) or not 0 <= item.priority <= 100:
                raise PrivacyError("Detector returned an invalid priority")
            candidate_count += 1
            if candidate_count > limits.max_spans:
                raise PrivacyError("Privacy preview exceeds the configured span count")
            candidates.append(item)

        for kind, pattern in _RULES:
            checkpoint()
            for match in pattern.finditer(source):
                end = match.end()
                if kind == "URL":
                    end = end - len(source[match.start():end]) + len(source[match.start():end].rstrip(".,);]"))
                text = source[match.start():end]
                if kind == "IP_ADDRESS":
                    try:
                        ipaddress.ip_address(text)
                    except ValueError:
                        continue
                if kind == "PAYMENT_CARD" and not _luhn(text):
                    continue
                add(Detection(match.start(), end, kind))
        for entity in custom:
            checkpoint()
            value = entity["value"]
            # Underscores are filename/header separators here, not a reason to
            # retain an explicitly supplied name in Alice_private.txt.
            expression = (r"(?<![^\W_])" if value[0].isalnum() or value[0] == "_" else "") + re.escape(value)
            if value[-1].isalnum() or value[-1] == "_":
                expression += r"(?![^\W_])"
            for match in re.finditer(expression, source, re.IGNORECASE):
                add(Detection(match.start(), match.end(), entity["kind"], entity["identity"], 0))
        if detector is not None:
            for item in detector.detect(source):
                add(item)
        matches[name] = _select(candidates)
        checkpoint()

    aliases: dict[tuple[str, str], str] = {}
    counters: Counter = Counter()
    transformed: dict[str, str] = {}
    public_spans, private_spans = [], []
    segments: dict[str, list[dict]] = {}
    for name, source in sorted(fields.items()):
        chunks: list[str] = []
        field_segments: list[dict] = []
        old_cursor, new_cursor = 0, 0
        for item in matches[name]:
            checkpoint()
            original = source[item.start:item.end]
            identity = (item.kind, "explicit:" + item.identity if item.identity is not None else "literal:" + _canonical(item.kind, original))
            if identity not in aliases:
                counters[item.kind] += 1
                aliases[identity] = f"{_RESERVED}{item.kind}_{counters[item.kind]:04d}⟧"
            replacement = aliases[identity]
            if item.start > old_cursor:
                copied = source[old_cursor:item.start]
                chunks.append(copied)
                field_segments.append({"kind": "copy", "original_start": old_cursor, "original_end": item.start,
                                       "transformed_start": new_cursor, "transformed_end": new_cursor + len(copied)})
                new_cursor += len(copied)
            span = {"field": name, "kind": item.kind, "original_start": item.start, "original_end": item.end,
                    "transformed_start": new_cursor, "transformed_end": new_cursor + len(replacement),
                    "replacement": replacement}
            public_spans.append(span)
            private_spans.append({**span, "original": original})
            field_segments.append({"kind": "substitution", **{key: span[key] for key in (
                "original_start", "original_end", "transformed_start", "transformed_end")}})
            chunks.append(replacement)
            new_cursor += len(replacement)
            old_cursor = item.end
        if old_cursor < len(source):
            copied = source[old_cursor:]
            chunks.append(copied)
            field_segments.append({"kind": "copy", "original_start": old_cursor, "original_end": len(source),
                                   "transformed_start": new_cursor, "transformed_end": new_cursor + len(copied)})
        transformed[name] = "".join(chunks)
        segments[name] = field_segments
    public = {"schema_version": SCHEMA, "context_id": context_id, "offset_unit": "unicode_codepoints",
              "profile": {"rules_version": RULES_VERSION, "custom_entities_sha256": _digest(_json(custom)),
                          "additional_detector": additional},
              "fields": transformed, "source_hashes": {name: _digest(value) for name, value in sorted(fields.items())},
              "source_lengths": {name: len(value) for name, value in sorted(fields.items())},
              "transformed_hashes": {name: _digest(value) for name, value in transformed.items()},
              "substitutions": public_spans, "segments": segments,
              "counts": {"entities": len(aliases), "replacements": len(public_spans),
                         "by_kind": dict(sorted(Counter(span["kind"] for span in public_spans).items()))},
              "limitations": list(LIMITATIONS), "guaranteed_anonymous": False}
    public["transform_id"] = _digest(_json(public))[:32]
    private = {"schema_version": SCHEMA, "transform_id": public["transform_id"], "context_id": context_id,
               "manifest_sha256": _digest(_json(public)), "substitutions": private_spans}
    return TransformResult(_json(public), _json(private))


def flatten_fields(body: str, metadata: Mapping | None = None, headers: Mapping | None = None) -> dict[str, str]:
    """Inspect arbitrary metadata/header keys as values, not identifying paths.

    Indexed names reveal structure only. This is an inspection representation,
    not an automatic reconstruction of the input metadata object.
    """
    if not isinstance(body, str):
        raise PrivacyError("Body must contain text")
    fields = {"body": body}

    def visit(value, prefix: str, depth: int = 0):
        if depth > 10 or len(fields) >= 256:
            raise PrivacyError("Metadata exceeds privacy preview structure limits")
        if isinstance(value, Mapping):
            if any(not isinstance(key, str) for key in value):
                raise PrivacyError("Metadata keys must contain text")
            for index, (key, child) in enumerate(sorted(value.items())):
                fields[f"{prefix}.key.{index:04d}"] = key
                visit(child, f"{prefix}.value.{index:04d}", depth + 1)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                visit(child, f"{prefix}.{index:04d}", depth + 1)
        elif isinstance(value, str):
            fields[prefix] = value
        elif value is None or isinstance(value, (bool, int, float)):
            try:
                fields[prefix] = _json(value)
            except ValueError as error:
                raise PrivacyError("Metadata must contain finite JSON values") from error
        else:
            raise PrivacyError("Metadata must contain JSON-compatible values")

    if metadata is not None:
        visit(metadata, "metadata")
    if headers is not None:
        visit(headers, "headers")
    if len(fields) > 256:
        raise PrivacyError("Metadata exceeds privacy preview structure limits")
    return fields


def map_span(record: dict, field: str, start: int, end: int, *,
             direction: str = "transformed_to_original") -> dict:
    """Map a span conservatively; substitutions never claim text equality."""
    if direction not in {"transformed_to_original", "original_to_transformed"}:
        raise PrivacyError("Unknown privacy span-map direction")
    if field not in record.get("fields", {}):
        raise PrivacyError("Unknown privacy field")
    source, target = ("transformed", "original") if direction == "transformed_to_original" else ("original", "transformed")
    length = len(record["fields"][field]) if source == "transformed" else record["source_lengths"][field]
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (start, end)) or not 0 <= start < end <= length:
        raise PrivacyError("Span must be a non-empty in-range Unicode interval")
    selected = [segment for segment in record["segments"][field]
                if segment[f"{source}_start"] < end and segment[f"{source}_end"] > start]
    if not selected:
        raise PrivacyError("Privacy span mapping is incomplete")
    mapped, exact = [], True
    for segment in selected:
        if segment["kind"] == "copy":
            shift = segment[f"{target}_start"] - segment[f"{source}_start"]
            mapped.append((max(start, segment[f"{source}_start"]) + shift,
                           min(end, segment[f"{source}_end"]) + shift))
        else:
            exact = False
            mapped.append((segment[f"{target}_start"], segment[f"{target}_end"]))
    return {"field": field, "start": min(item[0] for item in mapped), "end": max(item[1] for item in mapped),
            "exact_text": exact, "offset_unit": "unicode_codepoints", "direction": direction}


def restore_fields(public: dict, private: dict) -> dict[str, str]:
    """Reidentify only after the calling service has enforced reveal authority."""
    if (public.get("schema_version") != SCHEMA or private.get("schema_version") != SCHEMA or
            public.get("transform_id") != private.get("transform_id") or
            public.get("context_id") != private.get("context_id") or
            _digest(_json(public)) != private.get("manifest_sha256")):
        raise PrivacyError("Privacy mapping does not match this transformation")
    restored = dict(public["fields"])
    try:
        by_field: dict[str, list[dict]] = {}
        for span in private["substitutions"]:
            by_field.setdefault(span["field"], []).append(span)
        for name, spans in by_field.items():
            value = restored[name]
            for span in sorted(spans, key=lambda item: item["transformed_start"], reverse=True):
                start, end = span["transformed_start"], span["transformed_end"]
                if value[start:end] != span["replacement"]:
                    raise PrivacyError("Privacy mapping contains an invalid replacement")
                value = value[:start] + span["original"] + value[end:]
            restored[name] = value
        if {name: _digest(value) for name, value in restored.items()} != public["source_hashes"]:
            raise PrivacyError("Restored fields fail their source integrity checks")
    except (KeyError, TypeError, IndexError) as error:
        raise PrivacyError("Privacy mapping is malformed") from error
    return restored


def vault_key_from_env(name: str = "DOXA_PRIVACY_VAULT_KEY") -> bytes:
    """Read an operator-provisioned base64 AES-256 key; never derive a fallback."""
    value = os.environ.get(name, "")
    try:
        key = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise PrivacyConfigurationError(f"{name} must be a base64-encoded 32-byte key") from error
    if len(key) != 32:
        raise PrivacyConfigurationError(f"{name} must be a base64-encoded 32-byte key")
    return key


class PrivateMapVault:
    """Local encrypted maps, separate from Store objects and HTTP downloads.

    The key belongs in the operator's secret manager, outside the data directory
    and repository. Backups need the relevant key; changing it is not a rotation
    procedure. This class does not implement authorization or backup erasure.
    """

    def __init__(self, directory: str | Path, key: bytes | None = None):
        if not isinstance(key, bytes) or len(key) != 32:
            raise PrivacyConfigurationError("An operator-provisioned 32-byte privacy vault key is required")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as error:
            raise PrivacyConfigurationError("The cryptography runtime dependency is required for the privacy vault") from error
        self._cipher = AESGCM(key)
        self.directory = Path(directory).absolute()
        if self.directory.is_symlink():
            raise PrivacyConfigurationError("Privacy vault directory cannot be a symlink")
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)

    @classmethod
    def from_env(cls, directory: str | Path) -> PrivateMapVault:
        return cls(directory, vault_key_from_env())

    def _path(self, entry_id: str) -> Path:
        if not isinstance(entry_id, str) or not _ID.fullmatch(entry_id):
            raise PrivacyError("Invalid private mapping identity")
        return self.directory / f"{entry_id}.map"

    @staticmethod
    def _aad(envelope: dict) -> bytes:
        return _json({name: envelope[name] for name in ("schema_version", "entry_id", "context_id", "transform_id")}).encode("utf-8")

    def save(self, value: TransformResult | dict) -> dict:
        mapping = value.private_record() if isinstance(value, TransformResult) else value
        if (not isinstance(mapping, dict) or mapping.get("schema_version") != SCHEMA or
                not isinstance(mapping.get("transform_id"), str) or not _ID.fullmatch(mapping["transform_id"])):
            raise PrivacyError("Invalid private transformation mapping")
        context_id = _context(mapping.get("context_id"))
        raw = _json(mapping).encode("utf-8")
        if len(raw) > MAX_VAULT_BYTES // 2:
            raise PrivacyError("Private mapping exceeds vault entry size")
        entry_id = uuid.uuid4().hex
        nonce = os.urandom(12)
        envelope = {"schema_version": VAULT_SCHEMA, "entry_id": entry_id, "context_id": context_id,
                    "transform_id": mapping["transform_id"]}
        ciphertext = self._cipher.encrypt(nonce, raw, self._aad(envelope))
        envelope.update({"nonce": base64.b64encode(nonce).decode("ascii"),
                         "ciphertext": base64.b64encode(ciphertext).decode("ascii")})
        encoded = _json(envelope).encode("utf-8")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".map-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                os.chmod(temporary, 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            destination = self._path(entry_id)
            if destination.exists() or destination.is_symlink():
                raise PrivacyError("Private mapping identity collision")
            temporary.replace(destination)
            temporary = None
            if hasattr(os, "O_DIRECTORY"):
                descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {"id": entry_id, "context_id": context_id, "transform_id": mapping["transform_id"],
                "ciphertext_sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded),
                "algorithm": "AES-256-GCM", "visibility": "private_native_vault"}

    def load(self, entry_id: str, *, expected_context_id: str, expected_transform_id: str | None = None) -> dict:
        path = self._path(entry_id)
        _context(expected_context_id)
        descriptor = None
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_VAULT_BYTES or info.st_mode & 0o077:
                raise PrivacyError("Private mapping file is unsafe")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                raw = stream.read(MAX_VAULT_BYTES + 1)
            if len(raw) > MAX_VAULT_BYTES:
                raise PrivacyError("Private mapping exceeds vault entry size")
            envelope = json.loads(raw)
            if (envelope.get("schema_version") != VAULT_SCHEMA or envelope.get("entry_id") != entry_id or
                    envelope.get("context_id") != expected_context_id or
                    (expected_transform_id is not None and envelope.get("transform_id") != expected_transform_id)):
                raise PrivacyError("Private mapping does not match the authorized context")
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            plaintext = self._cipher.decrypt(nonce, ciphertext, self._aad(envelope))
            mapping = json.loads(plaintext)
            if (mapping.get("schema_version") != SCHEMA or mapping.get("context_id") != expected_context_id or
                    mapping.get("transform_id") != envelope["transform_id"]):
                raise PrivacyError("Private mapping content is inconsistent")
            return mapping
        except FileNotFoundError:
            raise PrivacyError("Private mapping was not found") from None
        except PrivacyError:
            raise
        except Exception as error:
            # Neither source values nor low-level crypto details belong in errors.
            raise PrivacyError("Private mapping failed authenticated decryption or validation") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def delete(self, entry_id: str) -> bool:
        path = self._path(entry_id)
        if path.is_symlink():
            raise PrivacyError("Private mapping file is unsafe")
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True
