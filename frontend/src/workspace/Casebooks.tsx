import { useCallback, useEffect, useState } from "react";
import {
  ArrowDown,
  ArrowDownToLine,
  ArrowUp,
  BookOpen,
  Check,
  Pin,
  Plus,
  RefreshCw,
  Square,
  Trash2,
} from "lucide-react";
import { request } from "../api";
import {
  list,
  message,
  queryPage,
  record,
  restoreQuery,
  workspaceQuery,
} from "./client";
import { CoverageSummary } from "./Coverage";
import { assessmentOrigins } from "./EvidenceLayers";
import {
  Badge,
  Button,
  Empty,
  ErrorMessage,
  human,
  number,
  Spinner,
} from "./ui";
import type {
  Casebook,
  CasebookCase,
  CaseSelection,
  Item,
  Observation,
  Page,
  QueryResult,
} from "./types";

const allOutcomes = [
  "meets_criterion",
  "does_not_meet",
  "uncertain",
  "not_applicable",
];
const root = (project: string) => `/v3/projects/${encodeURIComponent(project)}`;
const bookRoot = (id: string) => `/v3/casebooks/${encodeURIComponent(id)}`;
const json = (body: unknown, method = "POST") => ({
  method,
  body: JSON.stringify(body),
});
function items<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : list<T>(record(value).items);
}

export function useCasebook(
  project: string,
  query: QueryResult | null,
  onNotice: (text: string, error?: boolean) => void,
) {
  const [book, setActive] = useState<Casebook | null>(null);
  const [pinning, setPinning] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const setBook = useCallback((next: Casebook | null) => {
    setActive(next);
    if (next) localStorage.setItem(`doxa-casebook:${next.project_id}`, next.id);
  }, []);
  useEffect(() => {
    setActive(null);
    if (!project) return;
    let alive = true;
    const saved = localStorage.getItem(`doxa-casebook:${project}`);
    setRestoring(!!saved);
    if (saved)
      void request<Casebook>(bookRoot(saved))
        .then((value) => {
          if (alive && value.project_id === project) setActive(value);
        })
        .catch(() => {
          /* A deleted draft can be replaced by an explicit new draft. */
        })
        .finally(() => {
          if (alive) setRestoring(false);
        });
    return () => {
      alive = false;
    };
  }, [project]);
  const create = useCallback(async () => {
    if (!query) throw new Error("Choose a frozen source scope first.");
    const next = await request<Casebook>(
      `${root(project)}/casebooks`,
      json({
        query_id: query.query_id,
        title: "Source-linked casebook",
        selection_rationale: "",
        publication_type: "casebook",
      }),
    );
    setBook(next);
    return next;
  }, [project, query?.query_id, setBook]);
  const pin = useCallback(
    async (selection: CaseSelection) => {
      if (pinning || restoring || !query) return;
      setPinning(true);
      try {
        const active = book?.project_id === project ? book : await create();
        const next = await request<Casebook>(
          `${bookRoot(active.id)}/cases`,
          json(selection),
        );
        setBook(next);
        onNotice(
          "Case pinned as a frozen source-linked record. No review or model outcome was changed.",
        );
      } catch (reason) {
        onNotice(message(reason), true);
      } finally {
        setPinning(false);
      }
    },
    [book, create, onNotice, pinning, restoring, project, query, setBook],
  );
  return {
    book: book?.project_id === project ? book : null,
    setBook,
    pinning: pinning || restoring,
    pin,
    create,
  };
}

type ComposerProps = {
  project: string;
  query: QueryResult | null;
  book: Casebook | null;
  pinning: boolean;
  onBook: (book: Casebook | null) => void;
  onPin: (selection: CaseSelection) => Promise<void>;
  onCreate: () => Promise<Casebook>;
  onOpenSource: (document: string, block?: string, finding?: string) => void;
  onNotice: (text: string, error?: boolean) => void;
};

export function ReportComposer(props: ComposerProps) {
  const { project, query, book, onBook, onCreate, onNotice } = props;
  const [books, setBooks] = useState<Casebook[]>([]);
  const [title, setTitle] = useState(book?.title || "");
  const [rationale, setRationale] = useState(book?.selection_rationale || "");
  const [publication, setPublication] = useState(
    book?.cases.length ? "casebook" : "briefing",
  );
  const [profile, setProfile] = useState("research");
  const [format, setFormat] = useState("pdf");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const usesBook = ["casebook", "teaching"].includes(publication);
  useEffect(() => {
    let alive = true;
    void request(`${root(project)}/casebooks?limit=30`)
      .then((value) => {
        if (alive) setBooks(items<Casebook>(value));
      })
      .catch((reason) => {
        if (alive) setError(message(reason));
      });
    return () => {
      alive = false;
    };
  }, [project, book?.id, refresh]);
  useEffect(() => {
    setTitle(book?.title || "");
    setRationale(book?.selection_rationale || "");
  }, [book?.id]);
  async function saveDraft() {
    if (!book) return;
    const next = await request<Casebook>(
      bookRoot(book.id),
      json({ title, selection_rationale: rationale }, "PATCH"),
    );
    onBook(next);
    return next;
  }
  async function action(key: string, work: () => Promise<unknown>) {
    setBusy(key);
    setError("");
    try {
      await work();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy("");
    }
  }
  async function prepare() {
    if (!query) return;
    if (usesBook && (!book?.cases.length || !rationale.trim()))
      throw new Error(
        "Pin at least one case and explain the selection before preparing a casebook.",
      );
    const frozen = usesBook ? await saveDraft() : null;
    const archiveScope =
      publication === "archive" &&
      query.run &&
      query.metrics.analysis_kind === "contextual_v3"
        ? await workspaceQuery(project, {
            ...query.scope,
            results: allOutcomes,
          })
        : query;
    await request(
      `${root(project)}/report-jobs`,
      json({
        query_id: frozen?.query_id || archiveScope.query_id,
        format,
        profile,
        publication_type: publication,
        ...(frozen ? { casebook_id: frozen.id } : {}),
      }),
    );
    setRefresh((value) => value + 1);
    onNotice(
      "Publication queued. Its frozen inputs and progress persist when this panel or browser is closed. No inference was requested.",
    );
  }
  return (
    <section
      className="w-publication-composer"
      aria-label="Publication composer"
    >
      <div className="w-query-summary">
        <Badge>Recorded evidence · offline generation</Badge>
        <strong>
          {number(query?.documents.total)} matching sources ·{" "}
          {number(query?.findings.total)} visible observations
        </strong>
        <span>
          Source scope {query?.scope_hash.slice(0, 12)}. A complete archive and
          a selected casebook serve different purposes.
        </span>
      </div>
      <CoverageSummary query={query} />
      <label>
        Publication purpose
        <select
          aria-label="Publication purpose"
          value={publication}
          onChange={(event) => {
            const value = event.target.value;
            setPublication(value);
            if (value === "archive") setFormat("xlsx");
            if (value === "teaching") setProfile("teaching");
          }}
        >
          <option value="briefing">
            Briefing · scope, findings and review priorities
          </option>
          <option value="casebook">
            Casebook · selected source-linked explanations
          </option>
          <option value="teaching">
            Teaching · cases and discussion questions
          </option>
          <option value="archive">
            Research archive · recorded ledger and source register
          </option>
        </select>
      </label>
      <p className="w-footnote">
        Audience changes presentation, not the source record. PDF and HTML
        retain a disclosed evidence appendix; machine-readable formats preserve
        the selected data. Preparation uses recorded results and makes no model
        calls.
      </p>
      {publication === "archive" && (
        <p className="w-footnote">
          {query?.run
            ? "Archive includes all four recorded outcomes while retaining the frozen source, framework, criterion, speech-role, review-state and stance filters. An outcome-only workspace filter is explicitly expanded for this archive."
            : "This unassessed Corpus archive contains source metadata. No model decisions are available or inferred."}
        </p>
      )}
      {usesBook && (
        <>
          <div className="w-field-heading">
            <h3>1. Choose a casebook draft</h3>
            <Button
              variant="outline"
              disabled={!!busy}
              onClick={() =>
                void action("create", async () => {
                  await onCreate();
                  setRefresh((value) => value + 1);
                })
              }
            >
              <Plus size={13} />
              New from current source scope
            </Button>
          </div>
          <label>
            Saved casebook
            <select
              aria-label="Saved casebook"
              value={book?.id || ""}
              onChange={(event) =>
                void action("load", async () => {
                  const next = await request<Casebook>(
                    bookRoot(event.target.value),
                  );
                  onBook(next);
                })
              }
            >
              <option value="" disabled>
                Select or create a draft
              </option>
              {[
                ...(book && !books.some((item) => item.id === book.id)
                  ? [book]
                  : []),
                ...books,
              ].map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title} · revision {item.revision}
                </option>
              ))}
            </select>
          </label>
          {book && (
            <>
              <p className="w-footnote">
                Draft scope {book.scope_hash.slice(0, 12)} · revision{" "}
                {book.revision}. Changing workspace filters does not rewrite its
                frozen cases. Cases must belong to this run and source cohort;
                all four recorded outcomes are eligible. Frozen framework,
                criterion, speech-role, review-state and stance filters still
                apply.
              </p>
              <label>
                Casebook title
                <input
                  aria-label="Casebook title"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  maxLength={180}
                />
              </label>
              <label>
                Why these cases?
                <textarea
                  aria-label="Case selection rationale"
                  value={rationale}
                  onChange={(event) => setRationale(event.target.value)}
                  rows={3}
                  maxLength={4000}
                  placeholder="Explain the classes illustrated and the selection limits. Curated cases are not a representative error sample."
                />
              </label>
              <Button
                variant="outline"
                disabled={!!busy}
                onClick={() => void action("save", saveDraft)}
              >
                <Check size={13} />
                Save draft details
              </Button>
              <div className="w-casebook-ledger" aria-label="Pinned cases">
                {book.cases.length ? (
                  book.cases.map((item, index) => (
                    <PinnedCase
                      key={item.id}
                      item={item}
                      index={index}
                      total={book.cases.length}
                      book={book}
                      onBook={onBook}
                      onOpenSource={props.onOpenSource}
                      onError={setError}
                    />
                  ))
                ) : (
                  <Empty title="Choose varied, inspectable cases">
                    Pin a source-linked observation in the inspector, or browse
                    the four recorded outcomes below. An unfinished unit remains
                    unresolved rather than becoming an invented assessment.
                  </Empty>
                )}
              </div>
            </>
          )}
          <CaseBrowser {...props} />
          <div className="w-origin-summary">
            <p>
              <strong>Source:</strong> exact frozen wording and locators.
            </p>
            <p>
              <strong>Framework:</strong> the recorded criterion and its
              conditions.
            </p>
            <p>
              <strong>Model:</strong> original typed decision, recorded detail
              or explicitly absent rationale.
            </p>
            <p>
              <strong>Review:</strong> separate events and corrections; human
              labels remain pending unless actually supplied.
            </p>
            <p>
              <strong>Editorial:</strong> optional operator notes remain
              attributed guidance and do not become model output or independent
              reference labels.
            </p>
          </div>
        </>
      )}
      <div className="w-form-grid">
        <label>
          Audience
          <select
            aria-label="Publication audience"
            value={profile}
            onChange={(event) => setProfile(event.target.value)}
          >
            <option value="research">Research & civil society</option>
            <option value="briefing">Newsrooms & policy</option>
            <option value="teaching">Universities & teaching</option>
          </select>
        </label>
        <label>
          Format
          <select
            aria-label="Publication format"
            value={format}
            onChange={(event) => setFormat(event.target.value)}
          >
            <option value="pdf">PDF · readable publication</option>
            <option value="html">HTML · standalone accessible reading</option>
            <option value="xlsx">XLSX · complete workbook</option>
            <option value="csv">CSV · structured records</option>
            <option value="jsonl">JSONL · research archive</option>
          </select>
        </label>
      </div>
      {usesBook && (
        <p className="w-footnote">
          Preview: title and scope → selection rationale → source / framework /
          model / review layers for {number(book?.cases.length)} cases →
          editorial notes with authorship → method, coverage and provenance. No
          inference or review approval happens during export.
        </p>
      )}
      {error && <ErrorMessage text={error} />}
      <Button
        disabled={
          !!busy ||
          !query ||
          (usesBook && (!book?.cases.length || !rationale.trim()))
        }
        onClick={() => void action("prepare", prepare)}
      >
        <BookOpen size={14} />
        {busy === "prepare"
          ? "Freezing publication inputs…"
          : "Prepare publication"}
      </Button>
      <ReportJobs project={project} refresh={refresh} onNotice={onNotice} />
    </section>
  );
}

function PinnedCase({
  item,
  index,
  total,
  book,
  onBook,
  onOpenSource,
  onError,
}: {
  item: CasebookCase;
  index: number;
  total: number;
  book: Casebook;
  onBook: (value: Casebook) => void;
  onOpenSource: ComposerProps["onOpenSource"];
  onError: (message: string) => void;
}) {
  const [note, setNote] = useState(item.editorial_note || "");
  const [reason, setReason] = useState(item.selection_rationale || "");
  const [busy, setBusy] = useState(false);
  const source = record(item.source_snapshot);
  const assessment = record(item.assessment_snapshot);
  const frozenBlock = list<Item>(record(item.frozen_snapshot).blocks)[0];
  const condition = record(item.framework_condition);
  async function mutate(path: string, body?: Item, method = "POST") {
    setBusy(true);
    try {
      onBook(
        await request<Casebook>(path, body ? json(body, method) : { method }),
      );
    } catch (error) {
      onError(message(error));
    } finally {
      setBusy(false);
    }
  }
  function move(direction: number) {
    const ids = book.cases.map((value) => value.id);
    [ids[index], ids[index + direction]] = [ids[index + direction], ids[index]];
    void mutate(`${bookRoot(book.id)}/order`, { case_ids: ids }, "PUT");
  }
  return (
    <article className="w-pinned-case">
      <header>
        <Badge>
          {item.kind === "unresolved"
            ? "Unresolved unit · incomplete processing"
            : human(item.result)}
        </Badge>
        <span>Case {index + 1}</span>
      </header>
      <h4>{String(source.title || assessment.title || item.document_id)}</h4>
      <small>
        {human(item.criterion_kind)} ·{" "}
        {String(assessment.criterion_id || item.unit_id || "")}
      </small>
      <details className="w-details w-case-layer-preview">
        <summary>Preview captured layers</summary>
        <section className="w-origin-layer">
          <span className="w-eyebrow">SOURCE WORDING</span>
          {typeof assessment.quote === "string" && assessment.quote ? (
            <p>
              “{Array.from(assessment.quote).slice(0, 400).join("")}
              {Array.from(assessment.quote).length > 400 ? "…" : ""}”
            </p>
          ) : (
            <p>
              {item.kind === "unresolved"
                ? "This is an actual unfinished source unit. Open its captured source block; no model-selected quotation or criterion outcome has been invented."
                : "A source quotation was not supplied in this bounded preview."}
            </p>
          )}
          <small>
            Bounded reading preview. The publication retains the frozen capture
            and complete locators.
          </small>
        </section>
        <section className="w-origin-layer framework">
          <span className="w-eyebrow">FRAMEWORK CONDITION</span>
          <h4>
            {String(
              condition.label ||
                assessment.criterion_id ||
                "No criterion decision for this unresolved unit",
            )}
          </h4>
          <p>
            {typeof condition.description === "string"
              ? condition.description
              : "The full frozen condition is not supplied in this browser preview. Consult the recorded method before adjudicating."}
          </p>
          {condition.lens_version ? (
            <small>
              Frozen {String(condition.lens_name || "framework")} · version{" "}
              {String(condition.lens_version)}
            </small>
          ) : null}
        </section>
        <section className="w-origin-layer">
          <span className="w-eyebrow">MODEL INFERENCE</span>
          <p>
            {item.kind === "unresolved"
              ? "This case documents incomplete processing. Retained criterion decisions stay in the ledger; no additional outcome has been invented for the missing decisions."
              : assessment.detail_supplied === false
                ? "Detailed rationale was not recorded. Generic outcome sentences and software-attached unit anchors are not model-written explanations."
                : typeof assessment.explanation === "string" &&
                    assessment.explanation
                  ? assessment.explanation
                  : "No model explanation was recorded in this preview; none has been inferred."}
          </p>
          {assessment.detail_supplied === true &&
            typeof assessment.alternative === "string" && (
              <p>Recorded alternative: {assessment.alternative}</p>
            )}
        </section>
        <section className="w-origin-layer">
          <span className="w-eyebrow">REVIEW & EDITORIAL</span>
          <p>
            Stored review state:{" "}
            {human(
              item.review_status || assessment.review_status || "unreviewed",
            )}
            . Human reference labels remain pending unless actually supplied.
            Editorial notes below remain attributed guidance and change no model
            or review outcome.
          </p>
        </section>
      </details>
      <div className="w-case-actions">
        <Button
          variant="quiet"
          onClick={() =>
            onOpenSource(
              item.document_id,
              typeof item.block_id === "string"
                ? item.block_id
                : typeof assessment.block_id === "string"
                  ? assessment.block_id
                  : typeof frozenBlock?.id === "string"
                    ? frozenBlock.id
                    : undefined,
              item.assessment_id,
            )
          }
        >
          Inspect source
        </Button>
        <Button
          variant="quiet"
          title="Move case earlier"
          disabled={busy || index === 0}
          onClick={() => move(-1)}
        >
          <ArrowUp size={13} />
        </Button>
        <Button
          variant="quiet"
          title="Move case later"
          disabled={busy || index + 1 === total}
          onClick={() => move(1)}
        >
          <ArrowDown size={13} />
        </Button>
        <Button
          variant="quiet"
          disabled={busy}
          onClick={() =>
            void mutate(
              `${bookRoot(book.id)}/cases/${item.id}`,
              undefined,
              "DELETE",
            )
          }
        >
          <Trash2 size={13} />
          Remove
        </Button>
      </div>
      <details className="w-details">
        <summary>Selection reason & attributed editorial note</summary>
        <label>
          Reason for selecting case {index + 1}
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={2}
            maxLength={2000}
          />
        </label>
        <label>
          Operator editorial note for case {index + 1}
          <textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            rows={3}
            maxLength={4000}
          />
        </label>
        <p className="w-footnote">
          This is an operator-authored editorial note, not model detail, an
          independent human reference label or an adjudication. It changes no
          review status.
        </p>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() =>
            void mutate(
              `${bookRoot(book.id)}/cases/${item.id}`,
              { selection_rationale: reason, editorial_note: note },
              "PATCH",
            )
          }
        >
          Save case notes
        </Button>
      </details>
    </article>
  );
}

function CaseBrowser({
  project,
  query,
  book,
  onPin,
  pinning,
  onOpenSource,
}: ComposerProps) {
  const [open, setOpen] = useState(false);
  const [outcome, setOutcome] = useState("");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [page, setPage] = useState<Page<Observation> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open || (!book && !query?.run)) return;
    let alive = true;
    setLoading(true);
    setError("");
    void (async () => {
      const base = book ? await restoreQuery(book.query_id) : query!;
      const next = await workspaceQuery(project, {
        ...base.scope,
        results: outcome ? [outcome] : allOutcomes,
      });
      if (alive) {
        setResult(next);
        setPage(next.findings);
      }
    })()
      .catch((reason) => {
        if (alive) setError(message(reason));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open, outcome, query?.query_id, book?.id, project]);
  return (
    <details
      className="w-details"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Browse cases across all four recorded outcomes</summary>
      <p className="w-footnote">
        Bounded pages preserve the draft's source scope, frozen run and
        analytical filters while browsing all four outcomes. An ordinary
        non-match, non-applicability, uncertainty and missing processing have
        different meanings.
      </p>
      <label>
        Recorded outcome
        <select
          aria-label="Case browser outcome"
          value={outcome}
          onChange={(event) => setOutcome(event.target.value)}
        >
          <option value="">All four outcomes</option>
          {allOutcomes.map((value) => (
            <option key={value} value={value}>
              {human(value)}
            </option>
          ))}
        </select>
      </label>
      {!book && !query?.run && (
        <Empty title="Select a contextual analysis">
          Corpus view has no model assessments. You can still export source
          metadata.
        </Empty>
      )}
      {loading && <Spinner text="Loading a bounded case page…" />}
      {error && <ErrorMessage text={error} />}
      {page && (
        <>
          <p>{number(page.total)} matching decisions · at most 50 loaded</p>
          <div
            className="w-case-candidates"
            role="list"
            aria-label="Recorded case candidates"
          >
            {page.items.map((finding) => {
              const pinned = book?.cases.some(
                (item) => item.assessment_id === finding.id,
              );
              const origin = assessmentOrigins(finding);
              return (
                <article role="listitem" key={finding.id}>
                  <Badge>{human(finding.result)}</Badge>
                  <small>
                    {human(finding.criterion_kind)} · {human(finding.lens_id)}
                  </small>
                  <h4>{finding.title}</h4>
                  <p>
                    “
                    {Array.from(finding.quote || "")
                      .slice(0, 220)
                      .join("")}
                    {Array.from(finding.quote || "").length > 220 ? "…" : ""}”
                  </p>
                  {!origin.hasDetail && (
                    <small>
                      Detailed rationale not established by this view; unit
                      anchors are not an invented explanation.
                    </small>
                  )}
                  <div className="w-case-actions">
                    <Button
                      variant="quiet"
                      onClick={() =>
                        onOpenSource(
                          finding.document_id,
                          finding.block_id,
                          finding.id,
                        )
                      }
                    >
                      Inspect source
                    </Button>
                    <Button
                      variant="outline"
                      disabled={pinning || pinned}
                      onClick={() =>
                        void onPin({
                          assessment_id: finding.id,
                          document_id: finding.document_id,
                          unit_id: finding.unit_id,
                        })
                      }
                    >
                      <Pin size={12} />
                      {pinned ? "Pinned" : "Pin case"}
                    </Button>
                  </div>
                </article>
              );
            })}
          </div>
          {page.next_cursor && result && (
            <Button
              variant="outline"
              disabled={loading}
              onClick={() => {
                setLoading(true);
                void queryPage<Observation>(
                  result.query_id,
                  "findings",
                  page.next_cursor,
                )
                  .then(setPage)
                  .catch((reason) => setError(message(reason)))
                  .finally(() => setLoading(false));
              }}
            >
              Next 50 decisions
            </Button>
          )}
        </>
      )}
    </details>
  );
}

type ReportJob = Item & {
  id: string;
  status: string;
  phase?: string;
  artifacts?: Item[];
  progress?: Item | number;
  error?: unknown;
};
function ReportJobs({
  project,
  refresh,
  onNotice,
}: {
  project: string;
  refresh: number;
  onNotice: ComposerProps["onNotice"];
}) {
  const [jobs, setJobs] = useState<ReportJob[]>([]);
  const [error, setError] = useState("");
  const [updating, setUpdating] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const value = await request(`${root(project)}/report-jobs?limit=15`);
        if (!alive) return;
        const next = items<ReportJob>(value);
        setJobs(next);
        setError("");
        if (next.some((item) => ["queued", "running"].includes(item.status)))
          timer = setTimeout(() => void load(), 2000);
      } catch (reason) {
        if (alive) setError(message(reason));
      }
    }
    void load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [project, refresh, revision]);
  async function action(job: ReportJob, type: string) {
    setUpdating(job.id);
    try {
      await request(`/v3/report-jobs/${encodeURIComponent(job.id)}/${type}`, {
        method: "POST",
      });
      setRevision((value) => value + 1);
      onNotice(
        type === "cancel"
          ? "Publication cancellation requested."
          : "Publication restarted from its frozen inputs.",
      );
    } catch (reason) {
      setError(message(reason));
    } finally {
      setUpdating("");
    }
  }
  return (
    <section className="w-report-jobs" aria-label="Persistent publication jobs">
      <div className="w-field-heading">
        <h3>Publication history & downloads</h3>
        <Button
          variant="quiet"
          onClick={() => setRevision((value) => value + 1)}
        >
          <RefreshCw size={12} />
          Refresh jobs
        </Button>
      </div>
      <p className="w-footnote">
        Jobs and artifacts survive closing this browser. Restart uses the
        recorded publication input. Closing the panel does not cancel a job.
      </p>
      {error && <ErrorMessage text={error} />}
      {!jobs.length && !error && (
        <Empty title="No publication jobs yet">
          Prepare a publication from a recorded scope. No new model analysis is
          needed.
        </Empty>
      )}
      {jobs.map((job) => {
        const progress = record(job.progress);
        const raw =
          typeof job.progress === "number" ? job.progress : progress.percent;
        const percent =
          typeof raw === "number" && Number.isFinite(raw)
            ? Math.max(0, Math.min(100, raw))
            : undefined;
        return (
          <article key={job.id} className="w-report-job">
            <header>
              <strong>
                {human(job.publication_type || "publication")} ·{" "}
                {String(job.format || "").toUpperCase()}
              </strong>
              <Badge tone={job.status === "failed" ? "amber" : ""}>
                {human(job.status)}
              </Badge>
            </header>
            <p>
              {human(job.phase || job.status)}
              {percent !== undefined ? ` · ${percent.toFixed(0)}%` : ""}
            </p>
            {["queued", "running"].includes(job.status) && (
              <progress
                aria-label={`Publication ${job.id} progress`}
                value={percent}
                max={100}
              />
            )}
            <small>
              Scope {String(job.scope_hash || "not supplied").slice(0, 12)} ·
              snapshot {String(job.snapshot_version || "not supplied")}
            </small>
            {job.casebook_revision != null && (
              <p className="w-footnote">
                Frozen casebook revision {String(job.casebook_revision)}. Later
                draft edits do not change this publication.
              </p>
            )}
            {typeof progress.message === "string" && (
              <p className="w-footnote">{progress.message}</p>
            )}
            {job.error ? (
              <ErrorMessage
                text={
                  typeof job.error === "string"
                    ? job.error
                    : String(
                        record(job.error).message || JSON.stringify(job.error),
                      )
                }
              />
            ) : null}
            <div className="w-case-actions">
              {["queued", "running"].includes(job.status) && (
                <Button
                  variant="outline"
                  disabled={updating === job.id}
                  onClick={() => void action(job, "cancel")}
                >
                  <Square size={12} />
                  Cancel publication
                </Button>
              )}
              {["failed", "cancelled"].includes(job.status) &&
                job.can_restart !== false && (
                  <Button
                    variant="outline"
                    disabled={updating === job.id}
                    onClick={() => void action(job, "restart")}
                  >
                    <RefreshCw size={12} />
                    Restart publication
                  </Button>
                )}
              {list<Item>(job.artifacts).map((artifact) => (
                <a
                  className="w-artifact-download"
                  key={String(artifact.format)}
                  href={`/api/v3/report-jobs/${encodeURIComponent(job.id)}/artifacts/${encodeURIComponent(String(artifact.format))}`}
                  download={String(artifact.filename || "")}
                >
                  <ArrowDownToLine size={13} />
                  Download {String(artifact.format).toUpperCase()} ·{" "}
                  {number(artifact.bytes)} bytes
                </a>
              ))}
            </div>
            <details className="w-details">
              <summary>Artifact integrity & frozen input</summary>
              <pre>
                {JSON.stringify(
                  {
                    job_id: job.id,
                    query_id: job.query_id,
                    scope_hash: job.scope_hash,
                    snapshot_version: job.snapshot_version,
                    casebook_revision: job.casebook_revision,
                    artifacts: job.artifacts || [],
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
          </article>
        );
      })}
    </section>
  );
}
