"""Minimal connector boundary: collection and credentials do not enter analysis.

Future Drive, RSS, repository or archive adapters yield these manifests. The
ingestion service remains responsible for authorization, hashing, extraction,
deduplication and source versions. Connectors never emit analysis findings.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SourceRecord:
    external_id: str
    filename: str
    content_type: str
    source_url: str | None = None
    version: str | None = None
    metadata: dict = field(default_factory=dict)


class Connector(Protocol):
    name: str

    async def enumerate(self, cursor: str | None = None) -> AsyncIterator[SourceRecord]: ...

    async def read(self, source: SourceRecord) -> AsyncIterator[bytes]: ...

    async def checkpoint(self) -> str | None: ...
