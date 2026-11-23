"""Timed transcript extraction with line and cue provenance."""
from __future__ import annotations

import re


def _milliseconds(stamp: str, extension: str) -> int:
    pattern = r"(\d{2,9}):([0-5]\d):([0-5]\d),(\d{3})"
    if extension == ".vtt":
        pattern = r"(?:(\d{2,9}):)?([0-5]\d):([0-5]\d)\.(\d{3})"
    match = re.fullmatch(pattern, stamp)
    if not match:
        raise ValueError("invalid timestamp")
    hours, minutes, seconds, fraction = (int(value or 0) for value in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + fraction


def extract_captions(text: str, extension: str) -> tuple[list[dict], list[int], int]:
    """Keep each cue's original visible text and original start line."""
    lines = text.splitlines()
    if extension == ".vtt":
        if not lines or not re.fullmatch(r"WEBVTT(?:[ \t]+[^<>]*)?", lines[0]):
            raise ValueError("WebVTT requires a WEBVTT header on line 1.")
        index = next((i for i, line in enumerate(lines) if not line.strip()), len(lines))
    else:
        index = 0
    blocks, examples, malformed = [], [], 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        first = index
        while index < len(lines) and lines[index].strip():
            index += 1
        cue = lines[first:index]
        if extension == ".vtt" and re.match(r"^(?:NOTE(?:[ \t]|$)|STYLE$|REGION$)", cue[0]):
            continue
        timing = 0 if "-->" in cue[0] else 1
        try:
            if timing + 1 >= len(cue):
                raise ValueError("missing cue timing or text")
            match = re.fullmatch(r"\s*(\S+)[ \t]+-->[ \t]+(\S+)(?:[ \t]+.*)?", cue[timing])
            if not match or any("-->" in line for line in cue[timing + 1:]):
                raise ValueError("malformed timing or missing cue separator")
            start, end = (_milliseconds(stamp, extension) for stamp in match.groups())
            if end <= start:
                raise ValueError("cue must end after its start")
        except ValueError:
            malformed += 1
            if len(examples) < 20:
                examples.append(first + 1)
            continue
        blocks.append({"text": "\n".join(cue[timing + 1:]), "paragraph": len(blocks) + 1,
                       "line_start": first + timing + 2, "cue_start_ms": start, "cue_end_ms": end})
    if not blocks:
        sample = f" Check cue timing near line {examples[0]}." if examples else ""
        raise ValueError("Caption file contains no usable timed cues." + sample)
    return blocks, examples, malformed
