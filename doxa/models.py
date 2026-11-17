"""Versioned, transport-independent contracts for evidence-based analysis.

Citation offsets are Unicode code points, inclusive start/exclusive end, against
the unmodified extracted block. They are not byte offsets or PDF coordinates.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0"
ASSESSMENT_CONTRACT_VERSION = "3.0"
AssessmentResult = Literal["meets_criterion", "does_not_meet", "uncertain", "not_applicable"]
CriterionKind = Literal["indicator", "boundary", "descriptive"]
PropositionStance = Literal["supports", "opposes", "neutral", "mixed", "unclear", "not_applicable"]


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


class UnitDecision(BaseModel):
    """Compact explicit outcome. Absence of a row is never a negative result."""
    model_config = ConfigDict(extra="forbid")
    unit_id: str
    criterion_key: str
    result: AssessmentResult


class DenseUnitDecision(BaseModel):
    """One explicit code per criterion, in the request's fixed criterion order."""
    model_config = ConfigDict(extra="forbid")
    unit_id: str
    results: list[Literal[0, 1, 2, 3]]


class AssessmentDetail(BaseModel):
    """Model chooses supplied sentence IDs; it cannot generate quote text."""
    model_config = ConfigDict(extra="forbid")
    unit_id: str
    criterion_key: str
    evidence_sentence_ids: list[str] = Field(min_length=1, max_length=8)
    title: str = Field(min_length=1, max_length=120)
    explanation: str = Field(min_length=1, max_length=320)
    alternative: str = Field(min_length=1, max_length=240)
    target: str = Field(max_length=160)
    proposition: str = Field(max_length=240)
    speaker: str = Field(max_length=120)
    attribution: Literal["author", "quoted_speaker", "reported_speaker", "unknown"]
    speaker_stance: PropositionStance
    document_stance: PropositionStance
    hypothetical: bool
    certainty: Literal["supported", "ambiguous", "insufficient_context"]


class UnitAssessmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: list[DenseUnitDecision]
    details: list[AssessmentDetail]


def assessment_schema(unit_ids: list[str], criterion_keys: list[str], sentence_ids: list[str], *, phase: str = "details") -> dict:
    """Bound identifiers dynamically, including contextual evidence references."""
    schema = UnitAssessmentResponse.model_json_schema()
    schema["properties"]["decisions"]["minItems"] = len(set(unit_ids))
    schema["properties"]["decisions"]["maxItems"] = len(set(unit_ids))
    for name in ["DenseUnitDecision", "AssessmentDetail"]:
        properties = schema["$defs"][name]["properties"]
        properties["unit_id"]["enum"] = list(dict.fromkeys(unit_ids))
        if name == "AssessmentDetail":
            properties["criterion_key"]["enum"] = list(dict.fromkeys(criterion_keys))
    results = schema["$defs"]["DenseUnitDecision"]["properties"]["results"]
    results["minItems"] = results["maxItems"] = len(criterion_keys)
    schema["$defs"]["AssessmentDetail"]["properties"]["evidence_sentence_ids"]["items"]["enum"] = list(dict.fromkeys(sentence_ids))
    if phase == "classify":
        # Some structured decoders loop on whitespace inside a forced-empty
        # array. Omit this field altogether instead of constraining maxItems=0.
        schema["properties"].pop("details")
        schema["required"] = [name for name in schema["required"] if name != "details"]
        schema["$defs"].pop("AssessmentDetail")
    return schema
