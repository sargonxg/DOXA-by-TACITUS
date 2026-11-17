import { useEffect, useRef, useState } from "react";
import { ArrowDownToLine, FileCheck2, RefreshCw } from "lucide-react";
import { request } from "../api";
import { list, message } from "./client";
import { Badge, Button, date, number } from "./ui";
import { permits, useAccessSession } from "./AccessGate";
import type { Context, Item } from "./types";

type Props = {
  project: string;
  documentIds: string[];
  briefRevision?: number;
  runId?: string;
  sourceContext: Context | null;
  disabled?: boolean;
  exportsAllowed: boolean;
  onOpenSource: (id: string) => void;
};

/** Small guided briefings are distinct from the full persistent evidence export. */
export function PilotReportPanel({
  project,
  documentIds,
  briefRevision,
  runId,
  sourceContext,
  disabled,
  exportsAllowed,
  onOpenSource,
}: Props) {
  const access = useAccessSession();
  const currentProject = useRef(project);
  currentProject.current = project;
  const [formats, setFormats] = useState(["pdf", "html", "xlsx", "json"]);
  const [reports, setReports] = useState<Item[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [quote, setQuote] = useState("");
  const [blockId, setBlockId] = useState("");
  const [comment, setComment] = useState(false);
  const [reviewAck, setReviewAck] = useState(false);
  const [includeDecisions, setIncludeDecisions] = useState(false);
  const context =
    sourceContext && documentIds.includes(sourceContext.document.id)
      ? sourceContext
      : null;
  const block = context?.blocks.find((item) => item.id === blockId);
  useEffect(() => {
    setReports([]);
    setError("");
    setBusy(false);
  }, [project]);
  useEffect(() => {
    setIncludeDecisions(false);
  }, [project, runId, briefRevision]);
  useEffect(() => {
    setBlockId("");
    setQuote("");
    setNote("");
    setReviewAck(false);
    setComment(false);
  }, [context?.document.id]);
  async function loadHistory() {
    try {
      const value = await request<Item[]>(`/pilot/projects/${project}/reports`);
      if (currentProject.current === project) setReports(value);
    } catch (reason) {
      if (currentProject.current === project) setError(message(reason));
    }
  }
  async function generate() {
    setError("");
    const commentary: Item[] = [];
    if (comment) {
      const start = block?.text.indexOf(quote) ?? -1;
      if (!context || !block || !quote || start < 0 || !note.trim()) {
        setError(
          "Choose a source block, an unchanged exact quotation and a review note before including a commented case.",
        );
        return;
      }
      if (block.text.indexOf(quote, start + 1) >= 0) {
        setError(
          "This quotation repeats in the source block. Include enough unchanged context to identify one exact occurrence.",
        );
        return;
      }
      // Server evidence offsets use Unicode code points, not UTF-16 positions.
      const codepointStart = [...block.text.slice(0, start)].length;
      commentary.push({
        document_id: context.document.id,
        block_id: block.id,
        start: codepointStart,
        end: codepointStart + [...quote].length,
        quote,
        note: note.trim(),
      });
    }
    setBusy(true);
    try {
      const value = await request<Item>(`/pilot/projects/${project}/reports`, {
        method: "POST",
        body: JSON.stringify({
          document_ids: documentIds,
          formats,
          ...(includeDecisions && runId ? { run_id: runId } : {}),
          expert_commentary: commentary,
          review_ack: comment && reviewAck,
        }),
      });
      if (currentProject.current === project)
        setReports((previous) => [
          value,
          ...previous.filter((item) => item.id !== value.id),
        ]);
    } catch (reason) {
      if (currentProject.current === project) setError(message(reason));
    } finally {
      if (currentProject.current === project) setBusy(false);
    }
  }
  return (
    <details
      className="w-pilot-details w-pilot-service-report"
      onToggle={(event) => {
        if (event.currentTarget.open && !reports.length) void loadHistory();
      }}
    >
      <summary>
        <FileCheck2 size={14} /> Generate a guided service report
      </summary>
      <p>
        A task-specific briefing for the first {number(documentIds.length)}{" "}
        sources in this query. It combines the saved brief, actual wording cues
        and optional evidence-linked commentary. Saved contextual decisions are
        included only when explicitly selected below. It starts as provisional.
      </p>
      <label className="w-pilot-check">
        <input
          type="checkbox"
          checked={includeDecisions}
          disabled={busy || !runId}
          onChange={(event) => setIncludeDecisions(event.target.checked)}
        />
        Include saved contextual decisions
      </label>
      <p>
        Including decisions requires the selected run's frozen service brief and
        source metadata to match this saved inquiry. Legacy runs or analyses of
        a different question stay in their original casebook and persistent
        evidence report workflow. Leave this off for a wording-only guided
        report.
      </p>
      <p>
        Maximum 100 sources, 500,000 extracted characters, 2,000 blocks and
        2,000 saved decisions. For a full corpus, use the persistent evidence
        report jobs.
      </p>
      <fieldset className="w-pilot-destinations">
        <legend>Download formats</legend>
        {["pdf", "html", "xlsx", "json"].map((format) => (
          <label key={format}>
            <input
              type="checkbox"
              checked={formats.includes(format)}
              disabled={busy}
              onChange={(event) =>
                setFormats(
                  event.target.checked
                    ? [...formats, format]
                    : formats.filter((item) => item !== format),
                )
              }
            />
            {format.toUpperCase()}
          </label>
        ))}
      </fieldset>
      <details className="w-pilot-details">
        <summary>Add a commented evidence case</summary>
        {!context ? (
          <p>
            Open a source from this preview selection to inspect its exact text.{" "}
            {documentIds[0] && (
              <button onClick={() => onOpenSource(documentIds[0])}>
                Inspect first source
              </button>
            )}
          </p>
        ) : (
          <>
            <p>
              Inspected source: <strong>{context.document.title}</strong>. Only
              supplied context blocks are available here; the source inspector
              can navigate to more.
            </p>
            <label className="w-pilot-check">
              <input
                type="checkbox"
                checked={comment}
                onChange={(event) => {
                  setComment(event.target.checked);
                  setReviewAck(false);
                }}
                disabled={busy}
              />
              Include one evidence-linked note
            </label>
            {comment && (
              <>
                <label>
                  Commentary source block
                  <select
                    aria-label="Commentary source block"
                    value={blockId}
                    disabled={busy}
                    onChange={(event) => {
                      setBlockId(event.target.value);
                      setQuote(
                        [
                          ...(context.blocks.find(
                            (item) => item.id === event.target.value,
                          )?.text || ""),
                        ]
                          .slice(0, 4000)
                          .join(""),
                      );
                      setReviewAck(false);
                    }}
                  >
                    <option value="">Choose an inspected source block</option>
                    {context.blocks.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.id} · {item.text.slice(0, 75)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Unchanged evidence quotation
                  <textarea
                    rows={4}
                    maxLength={4000}
                    value={quote}
                    disabled={busy || !block}
                    onChange={(event) => {
                      setQuote(event.target.value);
                      setReviewAck(false);
                    }}
                  />
                </label>
                <label>
                  Evidence-linked reviewer note
                  <textarea
                    rows={3}
                    maxLength={4000}
                    value={note}
                    disabled={busy}
                    onChange={(event) => {
                      setNote(event.target.value);
                      setReviewAck(false);
                    }}
                  />
                </label>
                <label className="w-pilot-check">
                  <input
                    type="checkbox"
                    checked={reviewAck}
                    disabled={
                      busy ||
                      !permits(access, "review") ||
                      !quote ||
                      !note.trim()
                    }
                    onChange={(event) => setReviewAck(event.target.checked)}
                  />
                  Record my declared operator review of this note
                </label>
                <p>
                  Review authority and identity come from the server. A declared
                  operator review covers this submitted commentary; it does not
                  establish independent reference labels, reviewer
                  qualifications or the accuracy of every automated reading.
                </p>
              </>
            )}
          </>
        )}
      </details>
      <div className="w-pilot-actions">
        <Button
          disabled={
            disabled ||
            !exportsAllowed ||
            busy ||
            !permits(access, "export") ||
            !briefRevision ||
            !documentIds.length ||
            documentIds.length > 100 ||
            !formats.length
          }
          onClick={() => void generate()}
        >
          {busy ? "Building guided report…" : "Generate guided report"}
        </Button>
        <Button
          variant="quiet"
          disabled={busy}
          onClick={() => void loadHistory()}
        >
          <RefreshCw size={12} /> Report history
        </Button>
      </div>
      {!exportsAllowed && (
        <p>
          Report generation and downloads are disabled by this project's privacy
          policy.
        </p>
      )}
      {!briefRevision && (
        <p>Save an inquiry brief before generating its service report.</p>
      )}
      {error && (
        <p className="w-pilot-error" role="alert">
          {error}
        </p>
      )}
      <div aria-live="polite">
        {reports.map((report) => (
          <article key={String(report.id)} className="w-pilot-report-record">
            <header>
              <strong>
                DOXA {String(report.profile_id || "")} ·{" "}
                {date(String(report.created_at || ""))}
              </strong>
              <Badge tone="amber">
                {report.review_status === "operator_signed"
                  ? "Operator commentary signed"
                  : "Provisional report"}
              </Badge>
            </header>
            <p>
              Brief revision {String(report.brief_revision ?? "not supplied")} ·{" "}
              {number(list(report.source_ids).length)} frozen sources ·
              independent interpretation validation pending.
            </p>
            <div className="w-pilot-artifact-links">
              {list<Item>(report.artifacts).map((artifact) => {
                const url = String(artifact.download_url || "");
                return exportsAllowed &&
                  permits(access, "export") &&
                  url.startsWith(`/api/pilot/projects/${project}/reports/`) ? (
                  <a key={String(artifact.format)} href={url} download>
                    <ArrowDownToLine size={13} />
                    <span>
                      Download {String(artifact.format).toUpperCase()}
                    </span>
                    <small>
                      {new Intl.NumberFormat("en", {
                        maximumFractionDigits: 1,
                      }).format(Number(artifact.bytes) / 1024)}{" "}
                      KiB
                    </small>
                  </a>
                ) : null;
              })}
            </div>
            <details>
              <summary>Frozen report manifest and checksums</summary>
              <pre>{JSON.stringify(report, null, 2)}</pre>
            </details>
          </article>
        ))}
      </div>
    </details>
  );
}
