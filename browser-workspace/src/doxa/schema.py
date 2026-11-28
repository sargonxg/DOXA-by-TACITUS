from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Decision = Literal["present", "absent", "insufficient_context"]
Criterion = Literal[
    "evaluative_framing", "collective_blame", "identity_stereotyping",
    "identity_derogation_exclusion", "group_conspiracy", "evidential_overclaim",
    "material_omission", "comparative_double_standard",
]
Split = Literal["train", "validation", "test", "challenge"]
Priority = Literal["P0", "P1", "P2", "P3", "P4", "unassigned"]
Origin = Literal["synthetic", "authentic", "adapted"]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0.0"] = "1.0.0"


class Segment(Record):
    segment_id: str
    text: str
    provenance: Literal["fictional_dossier", "user_supplied", "source_record"]


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    title: str
    issuing_body: str
    publication_date: str | None
    retrieved_at: str
    locator: str
    content_sha256: str
    reuse_basis: str
    excerpt_limits: str | None


class Case(Record):
    case_id: str
    document_id: str
    thread_id: str | None
    family_id: str
    event_id: str | None
    language: Literal["en"]
    text: str = Field(min_length=1, max_length=6000)
    context_segments: list[Segment]
    focal_speaker: str
    assessment_scope: Literal["focal_speaker_endorsement"]
    source_origin: Origin
    source_record: SourceRecord | None
    source_license: str
    topic_tags: list[str]
    explicit_target_context: str
    created_by: str
    created_at: str | None
    split: Split

    @model_validator(mode="after")
    def unique_segments(self):
        ids = [segment.segment_id for segment in self.context_segments]
        if len(set(ids)) != len(ids) or "passage" in ids:
            raise ValueError("context segments require unique IDs distinct from passage")
        return self


class Evidence(Record):
    segment_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    role: Literal["supporting", "countervailing"]

    @model_validator(mode="after")
    def offsets(self):
        if self.end - self.start != len(self.quote):
            raise ValueError("offset range and quotation length differ")
        return self


class Judgment(Record):
    judgment_id: str
    case_id: str
    family_id: str
    text: str
    criterion_id: Criterion
    framework_id: Literal["doxa-rubric-v0.1"]
    reference_decision: Decision
    assessment_scope: Literal["focal_speaker_endorsement"]
    speaker_stance: Literal["endorses", "reports", "rebuts", "unclear"]
    evidence: list[Evidence]
    counterevidence: list[Evidence]
    missing_context: list[str]
    rationale: str
    annotation_status: Literal["ai_provisional"]
    source_origin: Origin
    human_review_count: Literal[0]
    review_priority_reference: Priority
    recommended_action: str
    explicit_target_context: str
    topic_tags: list[str]
    split: Split


class BehavioralPair(Record):
    pair_id: str
    case_a_id: str
    case_b_id: str
    family_id: str
    criterion_id: Criterion
    operator: str
    changed_fields: list[str]
    expected_relation: Literal["invariant", "directional"]
    reference_decisions: list[Decision] = Field(min_length=2, max_length=2)
    equivalence_rationale: str
    validity_status: Literal["valid", "disputed", "invalid"]
    review_status: Literal["ai_provisional"]
    split: Split


class Revision(Record):
    revision_id: str
    case_id: str
    family_id: str
    original_text: str
    revised_text: str
    changed_mechanism: Criterion
    preserved_meaning: str
    rationale: str
    recommended_action: str
    annotation_status: Literal["ai_provisional"]
    split: Split


CONTRACTS = {
    "cases": Case,
    "judgments": Judgment,
    "behavioral_pairs": BehavioralPair,
    "revision_examples": Revision,
}
