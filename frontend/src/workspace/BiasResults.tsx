import "./BiasResults.css";
import { useRef, useState } from "react";
import { ArrowRight, ArrowUpRight, BookOpen, Download, X } from "lucide-react";
import { human, number } from "./ui";
import type { Lens, QueryResult } from "./types";

import { StudyReader, studyRoot } from "./StudyReader";

export function PublishedStudy() {
  const dialog = useRef<HTMLDialogElement>(null);
  const [opened, setOpened] = useState(false);
  return (
    <section className="w-study-entry" aria-label="Published bias study">
      <div>
        <span className="w-eyebrow">
          FIELD STUDY · GLOBAL VOICES · 2020–2025
        </span>
        <h2>When the news reports anti-Muslim bias, whose words are they?</h2>
        <p>
          Explore 60 real articles, six reading criteria, and the passages
          behind the counts.
        </p>
      </div>
      <div className="w-study-actions">
        <button
          className="w-study-open"
          onClick={() => {
            setOpened(true);
            dialog.current?.showModal();
          }}
        >
          <BookOpen size={15} /> Read the 60-article study
        </button>
        <a href={studyRoot + "/report.pdf"} download>
          <Download size={14} /> Study PDF
        </a>
      </div>
      <dialog
        ref={dialog}
        className="w-study-dialog"
        aria-label="60-article bias study"
        onClose={() => setOpened(false)}
      >
        {opened && (
          <>
            <div className="w-study-toolbar">
              <strong>Published study</strong>
              <a
                href={studyRoot + "/index.html"}
                target="_blank"
                rel="noreferrer"
              >
                Open standalone <ArrowUpRight size={14} />
              </a>
              <button autoFocus onClick={() => dialog.current?.close()}>
                <X size={16} /> Back to workspace
              </button>
            </div>
            <StudyReader />
          </>
        )}
      </dialog>
    </section>
  );
}

export function BiasResults({
  query,
  lenses,
  onEvidence,
}: {
  query: QueryResult;
  lenses: Lens[];
  onEvidence: (documentId: string, assessmentId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (query.metrics.analysis_kind !== "contextual_v3") return null;
  const rows = query.metrics.indicator_readings;
  if (!rows) return null;
  const top = rows[0];
  const total = query.metrics.documents;
  const visible = expanded ? rows : rows.slice(0, 3);
  const lensName = (id: string) =>
    lenses.find((lens) => lens.id === id)?.name || human(id);
  return (
    <section
      className="w-bias-readout"
      aria-label="Bias indicators in this selection"
    >
      <span className="w-eyebrow">YOUR SELECTED SOURCES</span>
      <h2>Which bias indicators surfaced?</h2>
      {top ? (
        <>
          <p>
            {top.flagged > 0 ? (
              <>
                <strong>{top.label}</strong> appeared in {number(top.flagged)}{" "}
                of the {number(top.with_decision)} sources with a matching
                decision for that criterion.
              </>
            ) : (
              <>
                The current selection contains no positive indicator reading
                after rejected decisions are removed.
              </>
            )}{" "}
            The source selection contains {number(total)}{" "}
            {total === 1 ? "document" : "documents"}.
          </p>
          <ul>
            {visible.map((row) => (
              <li key={row.lens_id + "/" + row.criterion_id}>
                <div className="w-bias-row-title">
                  <strong>{row.label}</strong>
                  <small>{lensName(row.lens_id)}</small>
                </div>
                <div className="w-bias-row-count">
                  <strong>
                    {number(row.flagged)} / {number(row.with_decision)}
                  </strong>
                  <small>
                    sources flagged
                    {row.uncertain > 0
                      ? " · " + number(row.uncertain) + " with uncertainty"
                      : ""}
                  </small>
                </div>
                {row.example && (
                  <button
                    onClick={() =>
                      onEvidence(
                        row.example!.document_id,
                        row.example!.assessment_id,
                      )
                    }
                  >
                    {row.example.result === "uncertain"
                      ? "Read uncertain passage"
                      : "Read passage"}{" "}
                    <ArrowRight size={13} />
                  </button>
                )}
              </li>
            ))}
          </ul>
          {rows.length > 3 && (
            <button
              className="w-bias-more"
              aria-expanded={expanded}
              onClick={() => setExpanded(!expanded)}
            >
              {expanded
                ? "Show fewer criteria"
                : "See all " + number(rows.length) + " criteria"}
            </button>
          )}
          <small>
            Each row counts distinct sources with saved decisions under your
            filters. A source can appear in several rows. Rejected readings
            contribute no flags. Check the speaker and proposition before
            attributing a flag to the writer.
          </small>
        </>
      ) : (
        <p>
          No indicator decisions match these filters. Descriptive frames,
          unchecked sources and missing decisions provide no count of prejudice.
        </p>
      )}
    </section>
  );
}
