"""Bounded offline EML/MBOX extraction with explicitly heuristic speech roles.

Mail headers identify a claimed sender, not an authenticated identity. Attachment
contents are never unpacked, executed or fetched. Canonical decoded text is the
source for evidence offsets; raw bytes retain their separate source digest.
"""
from __future__ import annotations

import hashlib
import json
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
import re

MAIL_EXTRACTOR_VERSION = "mail-context-1.0"
MAX_MAIL_FILE_BYTES = 64 * 1024 * 1024
MAX_MAIL_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_MAIL_MESSAGES = 2000
MAX_MAIL_PARTS = 256
MAX_MAIL_DEPTH = 16
MAX_MAIL_BODY_CHARS = 2_000_000
MAX_MAIL_TEXT_CHARS = 10_000_000
MAX_MAIL_HEADER_CHARS = 32_768
MAX_MAIL_BLOCK_CHARS = 8000
_HEADERS = ("From", "To", "Cc", "Bcc", "Reply-To", "Subject", "Date", "Message-ID", "In-Reply-To", "References")
_MESSAGE_IDS = re.compile(r"<[^<>\s]{1,996}>")
_FORWARD = re.compile(r"^\s*(?:-{2,}\s*(?:Original Message|Forwarded message)\s*-*|Begin forwarded message:)\s*$", re.I)
_WROTE = re.compile(r"^\s*On .{1,1000}wrote:\s*$", re.I)
MAIL_AUTHOR_NOTICE = (
    "Mail author groups use supplied, unverified document metadata. Only explicitly marked newly authored mail text "
    "enters these group counts; headers, quoted/forwarded history, signatures and unknown eligibility are excluded. "
    "All source records and assessments remain in the evidence ledger. Mail author rates are suppressed because "
    "source word counts include context and do not establish message-level authorship or endorsement."
)


def is_mail_document(document: dict) -> bool:
    metadata = document.get("metadata") or document.get("metadata_json") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (ValueError, TypeError):
            metadata = {}
    version = metadata.get("mail_context_version") if isinstance(metadata, dict) else None
    return bool((version is not None and version != "")
                or str(document.get("filename") or "").lower().endswith((".eml", ".mbox")))


def mail_document_sql(alias="d") -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
        raise ValueError("Invalid internal SQL alias")
    safe = f"CASE WHEN json_valid({alias}.metadata_json) THEN {alias}.metadata_json ELSE '{{}}' END"
    return f"(lower(COALESCE({alias}.filename,'')) LIKE '%.eml' OR lower(COALESCE({alias}.filename,'')) LIKE '%.mbox' OR COALESCE(json_extract({safe},'$.mail_context_version'),'')<>'')"


def author_eligible_sql(document_alias="d", assessment_alias="a") -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", assessment_alias):
        raise ValueError("Invalid internal SQL alias")
    safe = f"CASE WHEN json_valid({assessment_alias}.payload_json) THEN {assessment_alias}.payload_json ELSE '{{}}' END"
    return f"(NOT {mail_document_sql(document_alias)} OR COALESCE(json_type({safe},'$.eligible_for_author_counts')='true',0))"


def finding_author_eligible(finding: dict) -> bool:
    return finding.get("eligible_for_author_counts") is True


def _hash(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def extraction_settings() -> dict:
    return {"mail_context_version": MAIL_EXTRACTOR_VERSION,
            "max_mail_file_bytes": MAX_MAIL_FILE_BYTES, "max_mail_message_bytes": MAX_MAIL_MESSAGE_BYTES,
            "max_mail_messages": MAX_MAIL_MESSAGES, "max_mail_parts": MAX_MAIL_PARTS,
            "max_mail_depth": MAX_MAIL_DEPTH, "max_mail_body_characters": MAX_MAIL_BODY_CHARS,
            "max_mail_text_characters": MAX_MAIL_TEXT_CHARS, "max_mail_header_characters": MAX_MAIL_HEADER_CHARS,
            "max_mail_block_characters": MAX_MAIL_BLOCK_CHARS,
            "attachment_policy": "metadata_only", "role_policy": "explicit_markers_heuristic"}


def _messages(path: Path, extension: str):
    if path.stat().st_size > MAX_MAIL_FILE_BYTES:
        raise ValueError("Mail export exceeds the 64 MiB parsing limit; split the mailbox into smaller exports.")
    if extension == ".eml":
        if path.stat().st_size > MAX_MAIL_MESSAGE_BYTES:
            raise ValueError("Email exceeds the 8 MiB message limit; export a smaller message without large attachments.")
        yield path.read_bytes(), None
        return
    # Read-only mboxo/mboxrd separator scan. Escaped body lines beginning >From
    # remain exact canonical body text; no mailbox library can create/lock files.
    buffer = bytearray()
    envelope = None
    count = 0
    with path.open("rb") as stream:
        for line in stream:
            if line.startswith(b"From "):
                if envelope is not None:
                    count += 1
                    if count > MAX_MAIL_MESSAGES:
                        raise ValueError("Mailbox exceeds the 2,000-message limit; split the export.")
                    yield bytes(buffer), envelope
                elif buffer.strip():
                    raise ValueError("Mailbox has content before its first From separator; export a valid MBOX file.")
                envelope = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if len(envelope) > MAX_MAIL_HEADER_CHARS:
                    raise ValueError("Mailbox envelope exceeds the header limit.")
                buffer.clear()
            else:
                buffer.extend(line)
                if len(buffer) > MAX_MAIL_MESSAGE_BYTES:
                    raise ValueError("A mailbox message exceeds the 8 MiB limit; split or remove large attachments.")
        if envelope is None:
            raise ValueError("Mailbox has no From separators; use EML for a single RFC 5322 message.")
        count += 1
        if count > MAX_MAIL_MESSAGES:
            raise ValueError("Mailbox exceeds the 2,000-message limit; split the export.")
        yield bytes(buffer), envelope


def _header(message: Message, name: str) -> str:
    try:
        value = ", ".join(str(item) for item in message.get_all(name, []))
    except (LookupError, UnicodeError, ValueError) as exc:
        raise ValueError("An email header could not be decoded safely.") from exc
    if len(value) > MAX_MAIL_HEADER_CHARS:
        raise ValueError("An email header exceeds the parsing limit; export a smaller message.")
    return value


def _validate_tree(message: Message) -> None:
    stack = [(message, 0)]
    count = 0
    while stack:
        part, depth = stack.pop()
        count += 1
        if count > MAX_MAIL_PARTS or depth > MAX_MAIL_DEPTH:
            raise ValueError("Email MIME nesting or part count exceeds the parsing limit.")
        payload = part.get_payload()
        if isinstance(payload, list):
            stack.extend((child, depth + 1) for child in payload)


def _decode_part(part: Message, warnings: list[str]) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        payload = str(part.get_payload() or "").encode("utf-8")
    charset = part.get_content_charset() or "ascii"
    try:
        text = payload.decode(charset)
    except (LookupError, UnicodeError):
        text = payload.decode("utf-8", errors="replace")
        warnings.append("A body charset was missing, invalid or inconsistent; UTF-8 replacement decoding requires source review.")
    if len(text) > MAX_MAIL_BODY_CHARS:
        raise ValueError("An email body exceeds the character limit; split the export.")
    if any(ord(char) < 32 and char not in "\t\r\n\f" for char in text):
        raise ValueError("Email body contains unsupported binary/control data.")
    if part.defects:
        warnings.append("Email MIME defects were detected; inspect the decoded text against the original message.")
    return text


def _html(text: str) -> str:
    # Reuse the existing visible-text policy: scripts, remote resources, hidden
    # text and embedded objects are excluded. Explicit blockquotes gain visible
    # markers before role segmentation; no arbitrary CSS quote inference.
    from .extraction import _VisibleHTML

    class MailHTML(_VisibleHTML):
        def __init__(self):
            super().__init__()
            self.quote_depth = 0

        def flush(self):
            value = "".join(self.parts)
            self.parts.clear()
            if value.strip():
                prefix = "> " * self.quote_depth
                self.blocks.append("\n".join(prefix + line for line in value.split("\n")))

        def handle_starttag(self, tag, attrs):
            if tag == "blockquote" and not self.skip and not self.hidden:
                self.flush()
                self.quote_depth += 1
            super().handle_starttag(tag, attrs)

        def handle_endtag(self, tag):
            if tag == "blockquote" and not self.skip and not self.hidden:
                self.flush()
                self.quote_depth = max(0, self.quote_depth - 1)
            super().handle_endtag(tag)

    parser = MailHTML()
    parser.feed(text)
    parser.close()
    parser.flush()
    return "\n\n".join(parser.blocks)


def _body(message: Message, warnings: list[str]) -> tuple[str, list[dict], list[str]]:
    attachments: list[dict] = []
    representations: list[str] = []

    def visit(part: Message, part_id: str) -> list[tuple[str, str]]:
        content_type = part.get_content_type()
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        if filename is not None and len(filename) > MAX_MAIL_HEADER_CHARS:
            raise ValueError("An attachment filename exceeds the header limit.")
        if filename is not None or disposition == "attachment" or content_type == "message/rfc822":
            attachments.append({"part_id": part_id, "filename": filename, "content_type": content_type,
                                "disposition": disposition, "content_id": _header(part, "Content-ID"),
                                "content_extracted": False})
            return []
        if content_type == "multipart/encrypted":
            warnings.append("Encrypted email content is excluded; provide an explicitly decrypted export for analysis.")
            return []
        if part.is_multipart():
            children = [visit(child, f"{part_id}.{index}") for index, child in enumerate(part.get_payload(), 1)]
            if content_type == "multipart/alternative":
                candidates = [child for child in children if child]
                selected = next((child for child in candidates if any(kind == "text/plain" for _, kind in child)), candidates[0] if candidates else [])
                if len(candidates) > 1:
                    warnings.append("Alternative MIME body representations are not counted repeatedly; plain text is preferred.")
                return selected
            return [item for child in children for item in child]
        if content_type not in {"text/plain", "text/html"}:
            attachments.append({"part_id": part_id, "filename": filename, "content_type": content_type,
                                "disposition": disposition, "content_id": _header(part, "Content-ID"),
                                "content_extracted": False})
            return []
        text = _decode_part(part, warnings)
        if content_type == "text/html":
            text = _html(text)
            warnings.append("HTML mail excludes scripts, styles, hidden/embedded material and remote resources; explicit blockquotes receive quote markers.")
        return [(text, content_type)]

    selected = visit(message, "1")
    representations.extend(kind for _, kind in selected)
    body = "\n\n".join(text for text, _ in selected)
    if len(body) > MAX_MAIL_BODY_CHARS:
        raise ValueError("An email body exceeds the character limit; split the export.")
    return body, attachments, representations


def _roles(body: str):
    start = 0
    group_start = 0
    previous = None
    persistent = None
    for line in body.splitlines(keepends=True):
        value = line.rstrip("\r\n")
        if persistent:
            role = persistent
        elif _FORWARD.fullmatch(value):
            role = persistent = "forwarded"
        elif value == "-- " or re.match(r"^Sent from my .+", value, re.I):
            role = persistent = "signature"
        elif re.match(r"^[ \t]*>", value) or _WROTE.fullmatch(value):
            role = "quoted"
        else:
            role = "authored"
        if previous is not None and role != previous:
            yield group_start, start, previous
            group_start = start
        previous = role
        start += len(line)
    if previous is not None:
        yield group_start, len(body), previous


def selected_writing_blocks(text: str, message_key: str) -> list[dict]:
    """Preserve pasted text with heuristic quotation roles, not email headers."""
    if not isinstance(text, str) or not text.strip() or len(text) > 100000:
        raise ValueError("Save between 1 and 100,000 characters of selected writing")
    blocks = []
    for start, end, role in _roles(text):
        for left, right in _ranges(text, start, end):
            blocks.append({"id": f"selected-{len(blocks)}", "text": text[left:right],
                           "message_key": message_key, "mail_role": role,
                           "mail_sender": "Selected writer (user-declared)",
                           "mail_source_field": "selected_text", "source_char_start": left,
                           "source_char_end": right, "eligible_for_author_counts": role == "authored",
                           "source_chain": {"message_sender_claim": "Selected writer (user-declared)",
                                            "body_role": role, "represented_speaker": None,
                                            "verification": "not_independently_verified"}})
    return blocks


def _ranges(text: str, start: int, end: int):
    base = start
    for match in re.finditer(r"\r?\n[ \t]*\r?\n", text[base:end]):
        cut = base + match.end()
        yield from _bounded(text, start, cut)
        start = cut
    if start < end:
        yield from _bounded(text, start, end)


def _bounded(text: str, start: int, end: int):
    while end - start > MAX_MAIL_BLOCK_CHARS:
        cut = text.rfind(" ", start + MAX_MAIL_BLOCK_CHARS // 2, start + MAX_MAIL_BLOCK_CHARS)
        cut = cut + 1 if cut >= 0 else start + MAX_MAIL_BLOCK_CHARS
        yield start, cut
        start = cut
    if end > start:
        yield start, end


def extract_mail(path: Path, extension: str) -> tuple[list[dict], dict, list[str]]:
    """Return source blocks, metadata and warnings without any network access."""
    extension = extension.lower()
    if extension not in {".eml", ".mbox"}:
        raise ValueError("Mail extraction supports EML and MBOX only.")
    warnings = ["Email sender/recipient headers are unverified source claims; quotation, forwarding and signature roles use explicit-marker heuristics and require review."]
    blocks: list[dict] = []
    messages: list[dict] = []
    total = 0
    for index, (raw, envelope) in enumerate(_messages(Path(path), extension), 1):
        try:
            message = BytesParser(policy=policy.default).parsebytes(raw)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("Email could not be parsed safely.") from exc
        _validate_tree(message)
        if not list(message.keys()):
            raise ValueError("Email has no RFC 5322 headers; use TXT for plain text.")
        if len(list(message.keys())) > 2000:
            raise ValueError("Email header count exceeds the parsing limit.")
        headers = {name: value for name in _HEADERS if (value := _header(message, name))}
        if sum(len(value) for value in headers.values()) > MAX_MAIL_HEADER_CHARS:
            raise ValueError("Email headers exceed the total character limit.")
        message_ids = _MESSAGE_IDS.findall(headers.get("Message-ID", ""))
        references = _MESSAGE_IDS.findall(headers.get("References", ""))
        reply_ids = _MESSAGE_IDS.findall(headers.get("In-Reply-To", ""))
        message_key = f"m{index:06d}-{_hash(raw)[:16]}"
        body, attachments, representations = _body(message, warnings)
        if message.defects:
            warnings.append("Email MIME defects were detected; inspect the decoded text against the original message.")
        header_text = "\n".join(f"{name}: {value}" for name, value in headers.items())
        total += len(body) + len(header_text)
        if total > MAX_MAIL_TEXT_CHARS:
            raise ValueError("Mailbox decoded text exceeds the 10-million-character limit; split the export.")
        shared = {"message_key": message_key, "message_id": message_ids[0] if message_ids else None,
                  "mail_sender": headers.get("From", "Unknown"), "offset_unit": "unicode_code_points"}
        if header_text.strip():
            blocks.append({"text": header_text, **shared, "mail_role": "header", "mail_source_field": "canonical_headers",
                           "source_char_start": 0, "source_char_end": len(header_text),
                           "eligible_for_author_counts": False})
        for start, end, role in _roles(body):
            for left, right in _ranges(body, start, end):
                if body[left:right].strip():
                    blocks.append({"text": body[left:right], **shared, "mail_role": role,
                                   "mail_source_field": "canonical_body", "source_char_start": left,
                                   "source_char_end": right, "eligible_for_author_counts": role == "authored",
                                   "source_chain": {"message_sender_claim": shared["mail_sender"],
                                                    "body_role": role, "represented_speaker": None,
                                                    "verification": "not_independently_verified"}})
        published_at = None
        if headers.get("Date"):
            try:
                date = parsedate_to_datetime(headers["Date"])
                if date.tzinfo is not None:
                    published_at = date.isoformat()
                else:
                    warnings.append("A message date has no time zone; its absolute publication time is unknown.")
            except (ValueError, TypeError, OverflowError):
                warnings.append("A message date could not be parsed; the original Date header is preserved.")
        messages.append({**shared, "raw_sha256": _hash(raw), "headers": headers, "mbox_envelope": envelope,
                         "references": references, "in_reply_to": reply_ids, "published_at": published_at,
                         "canonical_headers_sha256": _hash(header_text), "canonical_body_sha256": _hash(body),
                         "body_characters": len(body), "body_representations": representations,
                         "attachments": attachments, "has_extractable_body": bool(body.strip())})
        if attachments:
            warnings.append("Attachment contents, including attached messages and archives, are excluded; only attachment metadata is retained.")
    if not any(message["has_extractable_body"] for message in messages):
        raise ValueError("Mail export contains no extractable message body; attachments and encrypted material are not analyzed.")
    identity_counts: dict[str, int] = {}
    for message in messages:
        if message["message_id"]:
            identity_counts[message["message_id"]] = identity_counts.get(message["message_id"], 0) + 1
    by_id = {message["message_id"]: message for message in messages
             if message["message_id"] and identity_counts[message["message_id"]] == 1}
    if any(count > 1 for count in identity_counts.values()):
        warnings.append("Repeated Message-ID headers are not unique identities; conflicting messages require review.")
    for message in messages:
        chain = message["references"] or message["in_reply_to"]
        root = chain[0] if chain else message["message_id"] or message["message_key"]
        seen = set()
        while root in by_id and root not in seen:
            seen.add(root)
            parent = by_id[root]
            parents = parent["references"] or parent["in_reply_to"]
            if not parents:
                break
            root = parents[0]
        if root in seen and by_id.get(root, {}).get("in_reply_to"):
            warnings.append("Circular reply references require review; thread grouping is heuristic.")
        message["thread_key"] = "t_" + _hash(root)[:24]
        message["context_missing"] = bool(chain and any(item not in by_id for item in chain))
    info = {message["message_key"]: message for message in messages}
    known: dict[tuple[str, str], str] = {}
    for index, block in enumerate(blocks, 1):
        block.update({"thread_key": info[block["message_key"]]["thread_key"], "paragraph": index})
        if block["mail_role"] == "header":
            continue
        normalized = " ".join(re.sub(r"(?m)^[ \t]*>+[ \t]?", "", block["text"]).split())
        key = (block["thread_key"], normalized)
        if block["mail_role"] in {"quoted", "forwarded"} and normalized and key in known:
            block["repeated_quote_of"] = known[key]
        elif normalized:
            known.setdefault(key, f"b{index:06d}")
    if any(message["context_missing"] for message in messages):
        warnings.append("One or more referenced messages are absent; thread context is incomplete.")
    if extension == ".mbox":
        warnings.append("MBOX From separators define message boundaries; escaped >From body lines are retained. Verify exports with ambiguous unescaped separators.")
    metadata = {"mail_context_version": MAIL_EXTRACTOR_VERSION, "messages": messages,
                "message_count": len(messages), "thread_count": len({item["thread_key"] for item in messages}),
                "author": messages[0]["mail_sender"] if len(messages) == 1 else "Multiple correspondents (unverified headers)",
                "title": messages[0]["headers"].get("Subject", "Email message") if len(messages) == 1 else f"Mailbox: {len(messages)} messages",
                "page_count_basis": "Unavailable: mail is not paginated", "needs_review": True,
                "exclusions": ["Attachment contents, images and encrypted material", "Unused alternative MIME body representations",
                               "HTML scripts, styles, hidden/embedded material and remote resources", "Whitespace-only body role segments"],
                "mail_role_policy": "Explicit markers only; not authenticated authorship or inferred endorsement",
                "mail_word_count_basis": "All extracted context headers and body roles; author-only rates must filter eligible_for_author_counts"}
    return blocks, metadata, list(dict.fromkeys(warnings))
