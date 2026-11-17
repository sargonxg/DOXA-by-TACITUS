import { list, record } from "./client";
import { Badge, human } from "./ui";
import type { AnalysisRun, Item, Lens, Observation } from "./types";

export function assessmentOrigins(finding: Observation) {
  const origins = record(finding.origin_labels);
  const supplied = finding.detail_supplied;
  return {
    detail: String(
      origins.model_interpretation ||
        finding.explanation_origin ||
        (supplied === true
          ? "persisted_model_detail"
          : supplied === false
            ? "not_recorded"
            : "unknown_missing_capture"),
    ),
    selection: String(
      origins.evidence_selection ||
        finding.evidence_selection_origin ||
        (supplied === true
          ? "persisted_detail_sentence_ids"
          : supplied === false
            ? "software_attached_unit_anchors"
            : "unknown_missing_capture"),
    ),
    hasDetail:
      supplied === true ||
      origins.model_interpretation === "persisted_model_detail",
  };
}
export function FrameworkCondition({
  finding,
  run,
  lenses,
}: {
  finding: Observation;
  run?: AnalysisRun | null;
  lenses: Lens[];
}) {
  const frozen = list<Item>(run?.config?.lens_snapshots).find(
    (item) => item.id === finding.lens_id,
  );
  const current = lenses.find((lens) => lens.id === finding.lens_id);
  const pack = frozen || record(current);
  const criterion = list<Item>(pack.criteria).find(
    (item) => item.id === (finding.criterion_id || finding.criterion),
  );
  const source =
    typeof pack.source_url === "string" ? pack.source_url : undefined;
  return (
    <section
      className="w-origin-layer framework"
      aria-label="Framework condition"
    >
      <div>
        <span className="w-eyebrow">FRAMEWORK CONDITION</span>
        <Badge>{human(finding.criterion_kind || "kind not supplied")}</Badge>
      </div>
      <h3>
        {String(
          criterion?.label ||
            finding.criterion_id ||
            finding.criterion ||
            "Condition not supplied",
        )}
      </h3>
      <p>
        {String(
          criterion?.description ||
            "The full condition was not supplied in this view. Consult the frozen method before adjudicating.",
        )}
      </p>
      <small>
        Recorded assessment run {finding.run_id?.slice(0, 12) || "not supplied"}{" "}
        ·{" "}
        {frozen
          ? "Frozen run definition"
          : "Current registry reference · original frozen condition unavailable"}
        {pack.version ? ` · version ${String(pack.version)}` : ""}
      </small>
      {source && (
        <a href={source} target="_blank" rel="noopener noreferrer">
          Read the framework source ↗
        </a>
      )}
      {finding.criterion_kind === "boundary" && (
        <p className="w-footnote">
          Meeting a boundary condition identifies a limit on classification. It
          is not a positive prejudice flag.
        </p>
      )}
      {finding.criterion_kind === "descriptive" && (
        <p className="w-footnote">
          A descriptive match identifies a textual feature. It does not
          establish unfairness, factual falsity or an author's private beliefs.
        </p>
      )}
    </section>
  );
}
export function OriginSummary({ finding }: { finding: Observation }) {
  const origins = assessmentOrigins(finding);
  return (
    <div className="w-origin-summary" aria-label="Assessment origins">
      <p>
        <strong>Source:</strong> validated extracted wording; an exact anchor
        does not verify a source's factual allegation.
      </p>
      <p>
        <strong>Evidence selection:</strong>{" "}
        {origins.selection === "software_attached_unit_anchors"
          ? "software attached this source-unit anchor; it was not selected as a detailed model rationale."
          : origins.selection === "persisted_detail_sentence_ids"
            ? "persisted detail evidence references."
            : "selection origin was not captured; do not infer a model-selected rationale."}
      </p>
      <p>
        <strong>Model explanation:</strong>{" "}
        {origins.hasDetail
          ? "recorded model detail, still an interpretation for review."
          : origins.detail === "not_recorded"
            ? "not recorded. Any generic outcome sentence is a software template, not a model-written explanation."
            : "detail origin was not captured. No additional rationale has been inferred."}
      </p>
      <p>
        <strong>Review:</strong> {human(finding.review_status || "unreviewed")}.
        A model or an editorial note is not an independent human reference
        label.
      </p>
    </div>
  );
}
