import { record } from "./client";
import { number, percentage } from "./ui";
import type { QueryResult } from "./types";

function supplied(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}
export function coverageMeasures(query: QueryResult | null) {
  const detail = record(query?.metrics.coverage_detail);
  return [
    {
      label: "Sources with all units checked",
      done: supplied(detail.sources_done ?? detail.complete_documents),
      total: supplied(detail.sources_total ?? query?.metrics.documents),
    },
    {
      label: "Accepted source units",
      done: supplied(detail.units_done),
      total: supplied(detail.units_total),
    },
    {
      label: "Retained criterion decisions",
      done: supplied(detail.decisions_done),
      total: supplied(detail.decisions_total),
    },
  ];
}
export function CoverageSummary({
  query,
  compact = false,
}: {
  query: QueryResult | null;
  compact?: boolean;
}) {
  if (!query?.run || query.metrics.analysis_kind === "legacy_unclassified")
    return null;
  return (
    <div
      className={`w-coverage-measures ${compact ? "compact" : ""}`}
      aria-label="Processing coverage denominators"
    >
      {coverageMeasures(query).map((measure) => (
        <div key={measure.label}>
          <span>{measure.label}</span>
          <strong>
            {measure.done === null || measure.total === null
              ? "Not supplied"
              : `${number(measure.done)} / ${number(measure.total)}`}
          </strong>
          <small>
            {measure.done !== null &&
            measure.total !== null &&
            measure.total > 0
              ? percentage((100 * measure.done) / measure.total)
              : "Percentage unavailable"}
          </small>
        </div>
      ))}
      <p>
        Checks cover extracted text only; source extraction completeness is
        separate.
        {!compact && (
          <>
            {" "}
            These are processing checks. They do not measure whether the model's
            interpretation is correct. Missing decisions are not negative
            findings.
          </>
        )}
      </p>
    </div>
  );
}
