import { useEffect, useId, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  ExternalLink,
  Layers3,
  Pin,
  Save,
  ShieldCheck,
  X,
} from "lucide-react";
import { request } from "../api";
import { submitReview } from "./client";
import { FrameworkCondition, OriginSummary } from "./EvidenceLayers";
import { ExtractionQuality } from "./ExtractionQuality";
import {
  Badge,
  Button,
  date,
  Empty,
  ErrorMessage,
  human,
  IconButton,
  Spinner,
} from "./ui";
import type {
  AnalysisRun,
  Block,
  Context,
  Item,
  Lens,
  Observation,
} from "./types";

export function Inspector({
  context,
  loading,
  lenses,
  run,
  pinned,
  pinning,
  onPin,
  onCasebook,
  onClose,
  onReview,
  onCandidate,
  onNavigate,
}: {
  context: Context | null;
  loading: boolean;
  lenses: Lens[];
  run?: AnalysisRun | null;
  pinned: boolean;
  pinning: boolean;
  onPin: (finding: Observation) => void;
  onCasebook: () => void;
  onClose: () => void;
  onReview: (finding: Observation) => void;
  onCandidate: (finding: Observation) => void;
  onNavigate: (cursor: number) => void;
}) {
  const finding = context?.finding;
  const tabId = useId();

  const legacy =
    !!finding && (!finding.result || finding.result === "legacy_unclassified");
  const [tab, setTab] = useState("evidence");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState("unreviewed");
  const [corrections, setCorrections] = useState<Item>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const anchor = useRef<HTMLElement>(null);
  useEffect(() => {
    setNote(finding?.review_note || "");
    setStatus(finding?.review_status || "unreviewed");
    setCorrections({
      hypothetical: Boolean(finding?.hypothetical),
      result: finding?.result || "uncertain",
      attribution: finding?.attribution || "unknown",
      speaker: finding?.speaker || "",
      target: finding?.target || "",
      proposition: finding?.proposition || "",
      speaker_stance: finding?.speaker_stance || "unclear",
      document_stance: finding?.document_stance || "unclear",
    });
    setError("");
    setTab("evidence");
  }, [finding?.id]);
  useEffect(() => {
    if (tab === "evidence") {
      const timer = setTimeout(() => {
        const mark = anchor.current?.querySelector("mark");
        const pane = anchor.current?.querySelector(".w-source-passage");
        if (mark && pane)
          pane.scrollTop +=
            mark.getBoundingClientRect().top -
            pane.getBoundingClientRect().top -
            24;
      }, 40);
      return () => clearTimeout(timer);
    }
  }, [finding?.id, tab, context]);
  async function save(nextStatus = status) {
    if (!finding) return;
    setSaving(true);
    setError("");
    try {
      const corrected = legacy
        ? {
            ...finding,
            ...(await request<Observation>(`/findings/${finding.id}/review`, {
              method: "PATCH",
              body: JSON.stringify({
                review_status: nextStatus,
                review_note: note,
              }),
            })),
          }
        : await submitReview(finding.id, {
            review_status: nextStatus,
            review_note: note,
            corrections,
          });
      onReview(corrected);
      setStatus(nextStatus);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Review could not be saved.",
      );
    } finally {
      setSaving(false);
    }
  }
  return (
    <section
      className="w-inspector"
      aria-label="Evidence inspector"
      ref={anchor}
    >
      <header className="w-inspector-header">
        <span>
          <BookOpen size={16} />
          Context & evidence
        </span>
        <IconButton label="Close evidence inspector" onClick={onClose}>
          <X size={17} />
        </IconButton>
      </header>
      {loading ? (
        <Spinner text="Opening the source context…" />
      ) : !context ? (
        <Empty title="An observation is a starting point">
          Select a source or retained observation. Read who said what, inspect
          the criterion, and preserve your own judgment.
        </Empty>
      ) : (
        <>
          <div className="w-source-meta">
            <h2>{context.document.title}</h2>
            <p>
              {context.document.author ||
                context.document.institution ||
                "Unknown author"}{" "}
              <span>·</span> {date(context.document.published_at)}
            </p>
            <div>
              <Badge>{human(context.document.document_type)}</Badge>
              {finding && (
                <Badge
                  tone={
                    finding.review_status === "confirmed" ? "green" : "amber"
                  }
                >
                  {human(finding.review_status || "unreviewed")}
                </Badge>
              )}
              {context.document.source_url && (
                <a
                  href={context.document.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label="Open original source in a new tab"
                >
                  <ExternalLink size={13} />
                </a>
              )}
            </div>
            {context.document.error && (
              <ErrorMessage text={context.document.error} />
            )}
            {context.unit_id && (
              <div className="w-origin-summary">
                <Badge tone="amber">
                  Unfinished source unit · decisions incomplete
                </Badge>
                <p>
                  This is the actual frozen source context for unit{" "}
                  {context.unit_id}. Missing decisions remain unresolved; no
                  positive, negative or uncertain model outcome has been
                  invented.
                </p>
              </div>
            )}
            <ExtractionQuality
              key={context.document.id}
              document={context.document}
            />
            {finding && !legacy && (
              <div className="w-case-actions">
                <Button
                  variant="outline"
                  disabled={pinning || pinned}
                  onClick={() => onPin(finding)}
                >
                  <Pin size={13} />
                  {pinned ? "Pinned to casebook" : "Pin this case"}
                </Button>
                <Button variant="quiet" onClick={onCasebook}>
                  <BookOpen size={13} />
                  Open casebook
                </Button>
              </div>
            )}
          </div>
          <div
            className="w-inspector-tabs"
            role="tablist"
            aria-label="Evidence detail"
            onKeyDown={(event) => {
              if (
                !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
              )
                return;
              const buttons = Array.from(
                event.currentTarget.querySelectorAll<HTMLButtonElement>(
                  'button[role="tab"]:not(:disabled)',
                ),
              );
              const current = buttons.indexOf(
                document.activeElement as HTMLButtonElement,
              );
              if (current < 0) return;
              const next =
                event.key === "Home"
                  ? 0
                  : event.key === "End"
                    ? buttons.length - 1
                    : (current +
                        (event.key === "ArrowRight" ? 1 : -1) +
                        buttons.length) %
                      buttons.length;
              event.preventDefault();
              buttons[next].focus();
              buttons[next].click();
            }}
          >
            <button
              role="tab"
              id={`${tabId}-evidence-tab`}
              aria-controls={`${tabId}-evidence-panel`}
              tabIndex={tab === "evidence" ? 0 : -1}
              aria-selected={tab === "evidence"}
              onClick={() => setTab("evidence")}
            >
              Source passage
            </button>
            <button
              role="tab"
              id={`${tabId}-assessment-tab`}
              aria-controls={`${tabId}-assessment-panel`}
              tabIndex={tab === "assessment" ? 0 : -1}
              aria-selected={tab === "assessment"}
              onClick={() => setTab("assessment")}
              disabled={!finding}
            >
              Interpretation
            </button>
            <button
              role="tab"
              id={`${tabId}-review-tab`}
              aria-controls={`${tabId}-review-panel`}
              tabIndex={tab === "review" ? 0 : -1}
              aria-selected={tab === "review"}
              onClick={() => setTab("review")}
              disabled={!finding}
            >
              Review & correct
            </button>
          </div>
          {tab === "evidence" && (
            <div
              className="w-evidence-panel"
              role="tabpanel"
              id={`${tabId}-evidence-panel`}
              aria-labelledby={`${tabId}-evidence-tab`}
            >
              <div className="w-source-passage">
                {!context.blocks.length &&
                  typeof context.unit?.text !== "string" && (
                    <Empty title="Extracted source text is unavailable">
                      Inspect the extraction issue above or import a readable
                      text version.
                    </Empty>
                  )}
                {!context.blocks.length &&
                  typeof context.unit?.text === "string" && (
                    <article>
                      <small>
                        Scored source-unit text · full source block exceeds
                        preview limits
                      </small>
                      <p>{context.unit.text}</p>
                    </article>
                  )}
                {context.blocks.map((block) => (
                  <article
                    className={block.id === finding?.block_id ? "active" : ""}
                    key={block.id}
                  >
                    <small>
                      {block.page
                        ? `Page ${block.page}`
                        : `¶ ${block.paragraph || block.id}`}
                    </small>
                    <p>
                      <QuoteText block={block} finding={finding} />
                    </p>
                  </article>
                ))}
              </div>
              <div className="w-context-controls">
                <Button
                  variant="quiet"
                  onClick={() =>
                    onNavigate(Math.max(0, (context.start || 0) - 5))
                  }
                  disabled={!context.start}
                >
                  <ArrowLeft size={13} />
                  Earlier context
                </Button>
                <span>
                  {context.total_blocks
                    ? `${context.total_blocks} source blocks`
                    : "Original source text"}
                </span>
                <Button
                  variant="quiet"
                  onClick={() => onNavigate(context.next_cursor || 0)}
                  disabled={
                    context.next_cursor === null ||
                    context.next_cursor === undefined
                  }
                >
                  Later
                  <ArrowRight size={13} />
                </Button>
              </div>
              {finding && (
                <div className="w-passage-assessment">
                  <span className="w-eyebrow">
                    {finding.detail_supplied === false
                      ? "SOFTWARE OUTCOME TEMPLATE · NO RECORDED DETAIL"
                      : "RECORDED MODEL PROPOSAL · REVIEW PENDING"}
                  </span>
                  <h3>{finding.title}</h3>
                  <p>{finding.explanation}</p>
                  <Button variant="quiet" onClick={() => setTab("assessment")}>
                    Examine the interpretation
                    <ArrowRight size={13} />
                  </Button>
                </div>
              )}
            </div>
          )}
          {tab === "assessment" && finding && (
            <div
              className="w-assessment-scroll"
              role="tabpanel"
              id={`${tabId}-assessment-panel`}
              aria-labelledby={`${tabId}-assessment-tab`}
            >
              <div className="w-assessment-title">
                <Badge>{human(finding.result || finding.certainty)}</Badge>
                <h3>{finding.title}</h3>
                <p>{finding.explanation}</p>
              </div>
              <FrameworkCondition finding={finding} run={run} lenses={lenses} />
              <dl className="w-facts">
                <div>
                  <dt>Framework</dt>
                  <dd>
                    {lenses.find((lens) => lens.id === finding.lens_id)?.name ||
                      human(finding.lens_id)}
                  </dd>
                </div>
                <div>
                  <dt>Criterion</dt>
                  <dd>{human(finding.criterion)}</dd>
                </div>
                <div>
                  <dt>Speech role</dt>
                  <dd>{human(finding.speech_role || finding.attribution)}</dd>
                </div>
                <div>
                  <dt>Hypothetical statement</dt>
                  <dd>{finding.hypothetical ? "Yes" : "No"}</dd>
                </div>
                <div>
                  <dt>Speaker</dt>
                  <dd>{finding.speaker || "Unresolved"}</dd>
                </div>
                <div>
                  <dt>Speaker stance</dt>
                  <dd>{human(finding.speaker_stance || finding.stance)}</dd>
                </div>
                <div>
                  <dt>Document stance</dt>
                  <dd>{human(finding.document_stance || "unclear")}</dd>
                </div>
                <div>
                  <dt>Target</dt>
                  <dd>{finding.target || "Unspecified"}</dd>
                </div>
                <div>
                  <dt>Proposition</dt>
                  <dd>{finding.proposition || "Unspecified"}</dd>
                </div>
              </dl>
              <OriginSummary finding={finding} />
              {(finding.original ||
                finding.corrections ||
                finding.review_event) && (
                <details className="w-details">
                  <summary>
                    Original model record & separate review overlay
                  </summary>
                  <pre>
                    {JSON.stringify(
                      {
                        original:
                          finding.original ||
                          "Original capture not supplied in this view",
                        corrections: finding.corrections || {},
                        review_event: finding.review_event || null,
                      },
                      null,
                      2,
                    )}
                  </pre>
                </details>
              )}
              <div className="w-alternative">
                <Layers3 size={16} />
                <div>
                  <strong>Alternative interpretation</strong>
                  <p>
                    {finding.alternative ||
                      "No alternative interpretation was supplied. This does not establish certainty."}
                  </p>
                </div>
              </div>
              <p className="w-footnote">
                The source span is exact. This interpretation remains a model
                proposal until reviewed.
              </p>
              <Button onClick={() => setTab("review")}>
                <ShieldCheck size={14} />
                Review this observation
              </Button>
            </div>
          )}
          {tab === "review" && finding && (
            <form
              className="w-review-form"
              role="tabpanel"
              id={`${tabId}-review-panel`}
              aria-labelledby={`${tabId}-review-tab`}
              onSubmit={(event) => {
                event.preventDefault();
                void save();
              }}
            >
              <p>
                Correct what the evidence supports. Original model output and
                your decision remain in the audit trail.
              </p>
              <fieldset disabled={legacy}>
                <legend className="w-sr-only">
                  Structured interpretation corrections
                </legend>
                <div className="w-form-grid">
                  <label>
                    Outcome
                    <select
                      aria-label="Outcome"
                      value={String(corrections.result)}
                      onChange={(event) =>
                        setCorrections((previous) => ({
                          ...previous,
                          result: event.target.value,
                        }))
                      }
                    >
                      {[
                        "meets_criterion",
                        "does_not_meet",
                        "uncertain",
                        "not_applicable",
                      ].map((value) => (
                        <option key={value} value={value}>
                          {human(value)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Attribution
                    <select
                      aria-label="Attribution"
                      value={String(corrections.attribution)}
                      onChange={(event) =>
                        setCorrections((previous) => ({
                          ...previous,
                          attribution: event.target.value,
                        }))
                      }
                    >
                      {[
                        "author",
                        "quoted_speaker",
                        "reported_speaker",
                        "unknown",
                      ].map((value) => (
                        <option key={value} value={value}>
                          {human(value)}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                {["speaker", "target", "proposition"].map((field) => (
                  <label key={field}>
                    {human(field)}
                    <input
                      aria-label={human(field).replace(/^./, (value) =>
                        value.toUpperCase(),
                      )}
                      value={String(corrections[field] || "")}
                      onChange={(event) =>
                        setCorrections((previous) => ({
                          ...previous,
                          [field]: event.target.value,
                        }))
                      }
                    />
                  </label>
                ))}
                <div className="w-form-grid">
                  {["speaker_stance", "document_stance"].map((field) => (
                    <label key={field}>
                      {human(field)}
                      <select
                        aria-label={human(field)}
                        value={String(corrections[field])}
                        onChange={(event) =>
                          setCorrections((previous) => ({
                            ...previous,
                            [field]: event.target.value,
                          }))
                        }
                      >
                        {[
                          "supports",
                          "opposes",
                          "mixed",
                          "neutral",
                          "unclear",
                          "not_applicable",
                        ].map((value) => (
                          <option key={value} value={value}>
                            {human(value)}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>
                <label className="w-hypothetical">
                  <input
                    type="checkbox"
                    checked={Boolean(corrections.hypothetical)}
                    onChange={(event) =>
                      setCorrections((previous) => ({
                        ...previous,
                        hypothetical: event.target.checked,
                      }))
                    }
                  />
                  Hypothetical or imagined statement
                </label>
              </fieldset>
              {legacy && (
                <div className="w-info amber">
                  <ShieldCheck size={15} />
                  <p>
                    Legacy observations support notes and review state. A v3 run
                    is required for contextual field corrections and reviewed
                    learning.
                  </p>
                </div>
              )}
              <label>
                Review note
                <textarea
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  rows={3}
                  placeholder="Record the evidence and reasoning behind your decision."
                />
              </label>
              <label>
                Review state
                <select
                  aria-label="Review state"
                  value={status}
                  onChange={(event) => setStatus(event.target.value)}
                >
                  {["unreviewed", "confirmed", "rejected", "revised"].map(
                    (value) => (
                      <option key={value}>{value}</option>
                    ),
                  )}
                </select>
              </label>
              {error && <ErrorMessage text={error} />}
              <div className="w-review-actions">
                <Button type="submit" disabled={saving}>
                  <Save size={14} />
                  {saving ? "Saving…" : "Save review & corrections"}
                </Button>
                <Button
                  variant="outline"
                  disabled={saving}
                  onClick={() => void save("confirmed")}
                >
                  <Check size={14} />
                  Confirm
                </Button>
              </div>
              <Button
                variant="quiet"
                disabled={legacy || !finding.review_event?.id}
                onClick={() => onCandidate(finding)}
              >
                <ChevronDown size={13} />
                Propose as a reviewed example
              </Button>
              <p className="w-footnote">
                An example candidate does not train or modify a model
                automatically.
              </p>
            </form>
          )}
        </>
      )}
    </section>
  );
}

function QuoteText({
  block,
  finding,
}: {
  block: Block;
  finding?: Observation;
}) {
  if (!finding || block.id !== finding.block_id) return <>{block.text}</>;
  const chars = Array.from(block.text);
  let start = finding.start ?? -1;
  let end = finding.end ?? -1;
  if (chars.slice(start, end).join("") !== finding.quote) {
    const found = block.text.indexOf(finding.quote);
    start = found < 0 ? -1 : Array.from(block.text.slice(0, found)).length;
    end = start + Array.from(finding.quote).length;
  }
  return start < 0 ? (
    <>{block.text}</>
  ) : (
    <>
      {chars.slice(0, start).join("")}
      <mark>{chars.slice(start, end).join("")}</mark>
      {chars.slice(end).join("")}
    </>
  );
}
