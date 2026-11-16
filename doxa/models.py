"""Versioned, transport-independent contracts for evidence-based analysis.

Citation offsets are Unicode code points, inclusive start/exclusive end, against
the unmodified extracted block. They are not byte offsets or PDF coordinates.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0"


class CandidateFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    start: int | None
    end: int | None
    quote: str = Field(min_length=1, max_length=8000)
    category: str = Field(min_length=1, max_length=100)
    target: str = Field(max_length=300)
    lens_id: str = Field(min_length=1, max_length=100)
    criterion: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    explanation: str = Field(min_length=1, max_length=2000)
    alternative: str = Field(min_length=1, max_length=1500)
    stance: Literal["supports", "opposes", "reports", "mixed", "unclear", "not_applicable"]
    speaker: str = Field(max_length=300)
    attribution: Literal["author", "quoted_speaker", "reported_speaker", "unknown"]
    certainty: Literal["supported", "ambiguous", "insufficient_context"]


class FindingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[CandidateFinding]


class Finding(CandidateFinding):
    id: str
    document_id: str
    context: str
    review_status: Literal["unreviewed", "confirmed", "rejected", "revised"] = "unreviewed"
    methodology_version: str = CONTRACT_VERSION
    evidence_kind: Literal["model_interpretation", "lexical_match"] = "model_interpretation"
    locator: dict = Field(default_factory=dict)


def finding_schema(*, lenses: list[dict] | None = None, block_ids: list[str] | None = None) -> dict:
    """Only the provider response contract, without application-owned fields."""
    schema = FindingsResponse.model_json_schema()
    properties = schema["$defs"]["CandidateFinding"]["properties"]
    if lenses:
        properties["lens_id"]["enum"] = list(dict.fromkeys(lens["id"] for lens in lenses))
        properties["criterion"]["enum"] = list(dict.fromkeys(criterion["id"] for lens in lenses for criterion in lens["criteria"]))
    if block_ids:
        properties["block_id"]["enum"] = list(dict.fromkeys(block_ids))
    return schema
