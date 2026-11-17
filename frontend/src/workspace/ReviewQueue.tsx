import { useEffect, useState } from "react";
import { Pin, RefreshCw } from "lucide-react";
import { request } from "../api";
import { list, message, record, unitContext } from "./client";
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
  CaseSelection,
  Context,
  Item,
  QueryResult,
} from "./types";

type QueueItem = Item & {
  id: string;
  type: string;
  document_id: string;
  unit_id: string;
  assessment_id: string | null;
};
type QueuePage = {
  items: QueueItem[];
  total: number;
  next_cursor: string | null;
  scope_note?: string;
  unapplied_filters?: unknown;
};
export function ReviewQueue({
  project,
  query,
  casebook,
  pinning,
  onPin,
  onOpenSource,
  onOpenContext,
}: {
  project: string;
  query: QueryResult | null;
  casebook: Casebook | null;
  pinning: boolean;
  onPin: (selection: CaseSelection) => Promise<void>;
  onOpenSource: (document: string, block?: string, finding?: string) => void;
  onOpenContext: (context: Context, unitId?: string) => void;
}) {
  const [kind, setKind] = useState("uncertainty");
  const [page, setPage] = useState<QueuePage | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [history, setHistory] = useState<(string | null)[]>([null]);
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [opening, setOpening] = useState("");
  async function inspect(item: QueueItem) {
    if (
      item.type !== "unfinished" ||
      item.unit_context_available !== true ||
      !query
    ) {
      onOpenSource(
        item.document_id,
        typeof item.block_id === "string" ? item.block_id : undefined,
        item.assessment_id || undefined,
      );
      return;
    }
    setOpening(item.id);
    setError("");
    try {
      onOpenContext(
        await unitContext(
          project,
          query.query_id,
          item.document_id,
          item.unit_id,
        ),
        item.unit_id,
      );
    } catch (reason) {
      setError(message(reason));
    } finally {
      setOpening("");
    }
  }
  useEffect(() => {
    setCursor(null);
    setHistory([null]);
    setIndex(0);
  }, [kind, query?.query_id]);
  useEffect(() => {
    if (!query?.run || query.scope.run_id === "none") {
      setPage(null);
      setLoading(false);
      setError("");
      return;
    }
    let alive = true;
    setLoading(true);
    setError("");
    const params = new URLSearchParams({
      query_id: query.query_id,
      kind,
      limit: "50",
    });
    if (cursor) params.set("cursor", cursor);
    void request<QueuePage>(
      `/v3/projects/${encodeURIComponent(project)}/review-queue?${params}`,
    )
      .then((value) => {
        if (alive) setPage(value);
      })
      .catch((reason) => {
        if (alive) setError(message(reason));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [project, query?.query_id, kind, cursor, revision]);
  function next() {
    if (!page?.next_cursor) return;
    setHistory((values) => [...values.slice(0, index + 1), page.next_cursor]);
    setIndex(index + 1);
    setCursor(page.next_cursor);
  }
  return (
    <section aria-label="Bounded evidence review queues">
      <div className="w-query-summary">
        <Badge>Source-linked tasks · no new inference</Badge>
        <strong>Choose the question that needs review</strong>
        <span>
          Queues preserve the frozen source scope. A disagreement is not an
          error label; an unfinished unit is not an uncertain model assessment.
        </span>
      </div>
      <label>
        Review queue
        <select
          aria-label="Review queue"
          value={kind}
          onChange={(event) => setKind(event.target.value)}
        >
          <option value="uncertainty">Criterion uncertainty</option>
          <option value="disagreement">Independent model disagreements</option>
          <option value="unfinished">Unfinished source units</option>
        </select>
      </label>
      <Button variant="quiet" onClick={() => setRevision((value) => value + 1)}>
        <RefreshCw size={13} />
        Refresh queue
      </Button>
      {!query?.run ? (
        <Empty title="Select a contextual analysis">
          Unassessed Corpus view has no model decisions. Select a frozen run to
          inspect its uncertainty, disagreements or unfinished units.
        </Empty>
      ) : null}
      {loading && <Spinner text="Loading at most 50 review tasks…" />}
      {error && <ErrorMessage text={error} />}
      {page && (
        <>
          <p className="w-footnote">{page.scope_note}</p>
          {page.unapplied_filters && (
            <details className="w-details">
              <summary>Queue scope qualifications</summary>
              <pre>{JSON.stringify(page.unapplied_filters, null, 2)}</pre>
            </details>
          )}
          <p aria-live="polite">
            {number(page.total)} tasks · page {index + 1} · at most 50 loaded
          </p>
          {!page.items.length && (
            <Empty title="No tasks in this queue">
              This describes the selected run and source scope. It does not
              establish an absence of relevant discourse or a validated
              interpretation.
            </Empty>
          )}
          <div className="w-review-queue-list">
            {page.items.map((item) => {
              const unfinished = item.type === "unfinished";
              const pinned = casebook?.cases.some((value) =>
                item.assessment_id
                  ? value.assessment_id === item.assessment_id
                  : value.document_id === item.document_id &&
                    value.unit_id === item.unit_id,
              );
              return (
                <article key={item.id} className="w-review-task">
                  <header>
                    <Badge tone={unfinished ? "amber" : ""}>
                      {unfinished
                        ? "Unresolved processing · incomplete decisions"
                        : human(item.result)}
                    </Badge>
                    <span>{human(item.type)}</span>
                  </header>
                  <h3>
                    {String(
                      item.source_title || item.title || item.document_id,
                    )}
                  </h3>
                  <small>
                    {String(item.source_author || "Unknown author")} ·{" "}
                    {human(item.lens_id)} / {human(item.criterion_id)}
                  </small>
                  {typeof item.quote === "string" && (
                    <p>
                      “{Array.from(item.quote).slice(0, 600).join("")}
                      {Array.from(item.quote).length > 600 ? "…" : ""}”
                    </p>
                  )}
                  {unfinished ? (
                    <>
                      <p>
                        This source unit is not fully assessed. Missing
                        decisions are not negative findings, and no model
                        outcome has been invented.
                      </p>
                      <p className="w-footnote">
                        Missing criteria:{" "}
                        {list<string>(item.missing_criterion_keys).join(", ") ||
                          "not supplied"}
                      </p>
                    </>
                  ) : (
                    <p>
                      {typeof item.explanation === "string"
                        ? item.explanation
                        : "Read the source and framework condition before making a decision."}
                    </p>
                  )}
                  {item.type === "disagreement" && (
                    <details className="w-details">
                      <summary>Original primary and verifier readings</summary>
                      <p className="w-footnote">
                        Comparison basis: {human(item.comparison_basis)}. Human
                        review remains separate; agreement and disagreement do
                        not measure accuracy.
                      </p>
                      <pre>
                        {JSON.stringify(
                          {
                            changed_fields: item.changed_fields,
                            primary: record(item.primary_model),
                            verifier: record(item.verifier_model),
                          },
                          null,
                          2,
                        )}
                      </pre>
                    </details>
                  )}
                  {list<Item>(item.errors).length > 0 && (
                    <details className="w-details">
                      <summary>Recorded processing issues</summary>
                      <pre>{JSON.stringify(item.errors, null, 2)}</pre>
                    </details>
                  )}
                  <div className="w-case-actions">
                    <Button
                      variant="outline"
                      disabled={opening === item.id}
                      onClick={() => void inspect(item)}
                    >
                      {opening === item.id
                        ? "Opening exact unit context…"
                        : unfinished && item.unit_context_available === true
                          ? "Open unfinished unit context"
                          : unfinished && typeof item.block_id !== "string"
                            ? "Open source document"
                            : "Inspect source & context"}
                    </Button>
                    <Button
                      variant="quiet"
                      disabled={pinning || pinned}
                      onClick={() =>
                        void onPin({
                          document_id: item.document_id,
                          unit_id: item.unit_id,
                          ...(item.assessment_id
                            ? { assessment_id: item.assessment_id }
                            : {}),
                        })
                      }
                    >
                      <Pin size={12} />
                      {pinned ? "Pinned to casebook" : "Pin review case"}
                    </Button>
                  </div>
                </article>
              );
            })}
          </div>
          <div className="w-case-actions">
            <Button
              variant="outline"
              disabled={loading || index === 0}
              onClick={() => {
                setIndex(index - 1);
                setCursor(history[index - 1]);
              }}
            >
              Previous tasks
            </Button>
            <Button
              variant="outline"
              disabled={loading || !page.next_cursor}
              onClick={next}
            >
              Next 50 tasks
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
