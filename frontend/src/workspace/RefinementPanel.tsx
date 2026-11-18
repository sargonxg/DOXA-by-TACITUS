import { useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Cpu,
  FileSearch,
  Layers3,
  Play,
  RefreshCw,
} from "lucide-react";
import { request } from "../api";
import { list, message, record } from "./client";
import { permits, useAccessSession } from "./AccessGate";
import { Badge, Button, human, number } from "./ui";
import type { Item, SourceDocument } from "./types";

type Props = {
  project: string;
  runId?: string;
  onImport: () => void;
  onAnalyze: () => void;
  onReports: () => void;
  onRefresh: () => void;
  onOpenSource: (id: string) => void;
};

export function RefinementPanel({
  project,
  runId,
  onImport,
  onAnalyze,
  onReports,
  onRefresh,
  onOpenSource,
}: Props) {
  const access = useAccessSession();
  const canWrite = permits(access, "write"),
    canAnalyze = permits(access, "analyze"),
    canAdmin = permits(access, "admin"),
    canExport = permits(access, "export");
  const current = useRef(project);
  current.current = project;
  const [readiness, setReadiness] = useState<Item | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [activity, setActivity] = useState<Item | null>(null);
  const [cards, setCards] = useState<Item[]>([]);
  const [cardsOpen, setCardsOpen] = useState(false);
  const [cardFilter, setCardFilter] = useState("");
  const [compareOpen, setCompareOpen] = useState(false);
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [left, setLeft] = useState<string[]>([]),
    [right, setRight] = useState<string[]>([]);
  const [event, setEvent] = useState(""),
    [topic, setTopic] = useState("");
  const [question, setQuestion] = useState(
    "How do comparable sources frame the same issue?",
  );
  const [familyLeft, setFamilyLeft] = useState(""),
    [familyRight, setFamilyRight] = useState("");
  const [dates, setDates] = useState<Record<string, string>>({});
  const [sourceSearch, setSourceSearch] = useState("");
  const [comparison, setComparison] = useState<Item | null>(null);
  const [quotes, setQuotes] = useState<Item[]>([]);
  const [quotesOpen, setQuotesOpen] = useState(false);
  const [example, setExample] = useState<Item | null>(null);
  const [exampleOpen, setExampleOpen] = useState(false);
  const [exampleSource, setExampleSource] = useState("");
  const [exampleOutcome, setExampleOutcome] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    request<Item>("/system/readiness", { signal: controller.signal })
      .then(setReadiness)
      .catch((error) => {
        if (!controller.signal.aborted) setStatus(message(error));
      });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    setStatus("");
    setDocuments([]);
    setLeft([]);
    setRight([]);
    setDates({});
    setEvent("");
    setTopic("");
    setComparison(null);
    setActivity(null);
    setQuotes([]);
    setCompareOpen(false);
    setActivityOpen(false);
    setQuotesOpen(false);
  }, [project]);

  async function loadActivity() {
    const identity = project;
    try {
      const result = await request<Item>(
        `/v3/projects/${encodeURIComponent(identity)}/activity`,
      );
      if (current.current === identity) setActivity(result);
    } catch (error) {
      if (current.current === identity) setStatus(message(error));
    }
  }
  useEffect(() => {
    if (!activityOpen || !project) return;
    void loadActivity();
    const interval = window.setInterval(() => void loadActivity(), 5000);
    return () => window.clearInterval(interval);
  }, [activityOpen, project]);

  async function perform(action: () => Promise<void>) {
    const identity = project;
    setBusy(true);
    setStatus("");
    try {
      await action();
    } catch (error) {
      if (current.current === identity) setStatus(message(error));
    } finally {
      if (current.current === identity) setBusy(false);
    }
  }
  async function starter() {
    const identity = project;
    await perform(async () => {
      const pack = await request<Item>("/system/starter-pack");
      const form = new FormData();
      for (const file of list<Item>(pack.files))
        form.append(
          "files",
          new File(
            [String(file.text || "")],
            String(file.filename || file.name || "practice.txt"),
            { type: "text/plain" },
          ),
        );
      await request(`/projects/${encodeURIComponent(identity)}/upload`, {
        method: "POST",
        body: form,
      });
      if (current.current !== identity) return;
      setStatus(
        "Five fictional practice sources are ready. They contain no model results or independent reference labels. Choose a profile, inspect wording, then publish a report.",
      );
      onRefresh();
    });
  }
  async function exploreRecorded() {
    setExampleOpen(!exampleOpen);
    if (example) return;
    await perform(async () => {
      const value = await request<Item>("/system/recorded-example");
      setExample(value);
      setExampleSource(String(list<Item>(value.documents)[0]?.id || ""));
    });
  }
  async function loadCards() {
    setCardsOpen(!cardsOpen);
    if (cards.length) return;
    await perform(async () => {
      const value = await request<Item>("/v3/method-cards");
      setCards(list<Item>(value.cards));
    });
  }
  async function openComparison() {
    setCompareOpen(!compareOpen);
    if (documents.length) return;
    const identity = project;
    await perform(async () => {
      const result = await request<SourceDocument[]>(
        `/projects/${encodeURIComponent(identity)}/documents?limit=2000`,
      );
      if (current.current === identity) setDocuments(result);
    });
  }
  function select(side: "left" | "right", id: string, checked: boolean) {
    if (side === "left") {
      setLeft((old) => (checked ? [...old, id] : old.filter((v) => v !== id)));
      if (checked) setRight((old) => old.filter((v) => v !== id));
    } else {
      setRight((old) => (checked ? [...old, id] : old.filter((v) => v !== id)));
      if (checked) setLeft((old) => old.filter((v) => v !== id));
    }
  }
  async function compare() {
    const identity = project;
    await perform(async () => {
      const perDocument: Record<string, Item> = {};
      for (const id of [...left, ...right]) {
        const values: Item = {};
        if (event.trim()) values.event = event.trim();
        if (topic.trim()) values.topic = topic.trim();
        const family = left.includes(id) ? familyLeft : familyRight;
        if (family.trim()) values.source_family = family.trim();
        if (dates[id]) values.published_at = dates[id];
        if (Object.keys(values).length) perDocument[id] = values;
      }
      const result = await request<Item>(
        `/v3/projects/${encodeURIComponent(identity)}/comparisons`,
        {
          method: "POST",
          body: JSON.stringify({
            question,
            left_document_ids: left,
            right_document_ids: right,
            per_document: perDocument,
            max_date_gap_days: 7,
            ...(runId && runId !== "none" ? { run_id: runId } : {}),
          }),
        },
      );
      if (current.current === identity) setComparison(result);
    });
  }
  async function loadQuotes() {
    setQuotesOpen(!quotesOpen);
    const identity = project;
    await perform(async () => {
      const result = await request<Item>(
        `/pilot/projects/${encodeURIComponent(identity)}/quotes`,
      );
      if (current.current === identity)
        setQuotes(list<Item>(result.quotes || result.items));
    });
  }

  const checks = list<Item>(readiness?.checks);
  const nativePilot = readiness?.storage_backend !== "remote";
  const jobs: Item[] = [
    ...list<Item>(record(activity?.jobs).items),
    ...list<Item>(activity?.runs).map((row) => ({ ...row, kind: "analysis" })),
    ...list<Item>(record(activity?.report_jobs).items).map((row) => ({
      ...row,
      kind: "report",
    })),
  ];
  const visibleDocs = documents
    .filter((doc) =>
      `${doc.title} ${doc.institution}`
        .toLowerCase()
        .includes(sourceSearch.toLowerCase()),
    )
    .slice(0, 100);
  const comparisonSummary = record(comparison?.summary),
    analysis = record(comparison?.analysis);
  const progressSteps = [
    "Sources",
    "Question",
    "Analysis",
    "Evidence",
    "Report",
  ];
  const exampleDocuments = list<Item>(example?.documents);
  const displayedExample = exampleDocuments.find(
    (source) => source.id === exampleSource,
  );
  const displayedRecord = list<Item>(example?.records).find(
    (row) => row.document_id === exampleSource,
  );
  const exampleCounts = record(record(example?.selection).counts);

  return (
    <section
      className="w-refinement"
      aria-label="Guided workflow and research quality"
    >
      <div className="w-refinement-top">
        <div>
          <span className="w-eyebrow">A clear path through your research</span>
          <h2>Start small. Keep the evidence in view.</h2>
        </div>
        <Badge>
          <Cpu size={13} />{" "}
          {human(record(readiness?.profile).name || "low-memory")} profile
        </Badge>
      </div>
      <ol className="w-workflow-steps" aria-label="Research workflow">
        {progressSteps.map((step, index) => (
          <li key={step}>
            <span>{index + 1}</span>
            {step}
            {index < 4 && <ArrowRight size={14} aria-hidden />}
          </li>
        ))}
      </ol>
      <p className="w-refinement-intro">
        Explore wording and saved evidence without loading a large model.
        Contextual analysis uses only the provider and scope you approve;
        uncertainty and unfinished work stay visible.
      </p>
      <div className="w-refinement-actions">
        <Button onClick={() => void exploreRecorded()} disabled={busy}>
          <BookOpen size={14} /> Explore 20 recorded sources
        </Button>
        {canWrite && (
          <Button onClick={() => void starter()} disabled={busy || !project}>
            <Play size={14} /> Try five practice sources
          </Button>
        )}
        {canWrite && (
          <Button variant="secondary" onClick={onImport}>
            Upload your sources
          </Button>
        )}
        {canAnalyze && (
          <Button variant="secondary" onClick={onAnalyze}>
            Define analysis
          </Button>
        )}
        {canExport && (
          <Button variant="secondary" onClick={onReports}>
            Publish a report
          </Button>
        )}
      </div>
      {status && (
        <p className="w-refinement-status" role="status">
          {status}
        </p>
      )}
      {exampleOpen && (
        <div className="w-refinement-detail w-recorded-example">
          <div className="w-refinement-top">
            <h3>Real sources. Recorded readings. Inspectable context.</h3>
            <Badge>Read-only public example</Badge>
          </div>
          <p>
            This example is separate from your project. It contains{" "}
            {number(exampleCounts.documents)} licensed development articles and{" "}
            {number(exampleCounts.assessments)} original model assessments,
            selected to illustrate all four outcomes. The parent analysis
            remains partial; these selected cases do not estimate accuracy or
            prevalence. Opening this example makes no inference requests.
          </p>
          <div className="w-refinement-actions">
            {["pdf", "xlsx", "html", "json.gz"].map((format) => (
              <a
                className="w-recorded-download"
                key={format}
                href={`/api/system/recorded-example/artifacts/${format}`}
                download
              >
                Download {format.toUpperCase()}
              </a>
            ))}
          </div>
          <label className="w-refinement-field">
            Recorded example source
            <select
              value={exampleSource}
              onChange={(event) => setExampleSource(event.target.value)}
            >
              {exampleDocuments.map((source) => (
                <option key={String(source.id)} value={String(source.id)}>
                  {String(source.title)}
                </option>
              ))}
            </select>
          </label>
          {displayedExample && (
            <>
              <p>
                <strong>{String(displayedExample.author)}</strong> ·{" "}
                {String(displayedExample.published_at || "").slice(0, 10)} · CC
                BY 3.0 ·{" "}
                <a
                  href={String(displayedExample.source_url)}
                  target="_blank"
                  rel="noreferrer"
                >
                  Original publication
                </a>
              </p>
              <label className="w-refinement-field">
                Recorded outcome
                <select
                  value={exampleOutcome}
                  onChange={(event) => setExampleOutcome(event.target.value)}
                >
                  <option value="">All retained outcomes</option>
                  {[
                    "meets_criterion",
                    "does_not_meet",
                    "not_applicable",
                    "uncertain",
                  ].map((outcome) => (
                    <option key={outcome} value={outcome}>
                      {human(outcome)}
                    </option>
                  ))}
                </select>
              </label>
              {list<Item>(displayedRecord?.assessments)
                .filter(
                  (row) => !exampleOutcome || row.result === exampleOutcome,
                )
                .slice(0, 12)
                .map((assessment) => (
                  <details
                    className="w-method-card"
                    key={String(assessment.id)}
                  >
                    <summary>
                      <strong>
                        {human(assessment.lens_id)} ·{" "}
                        {human(assessment.criterion || assessment.criterion_id)}
                      </strong>
                      <Badge>{human(assessment.result)}</Badge>
                    </summary>
                    <p className="w-muted">
                      {assessment.detail_supplied === false
                        ? "Software-attached source-unit anchor for a compact decision; no detailed model-selected explanation was retained."
                        : "Source passage retained with the recorded model reading."}
                    </p>
                    <blockquote>{String(assessment.quote || "")}</blockquote>
                    <p>
                      <strong>Criterion type:</strong>{" "}
                      {human(assessment.criterion_kind)}. The outcome applies to
                      this rule and context.
                    </p>
                    <p>
                      <strong>Original model reading:</strong>{" "}
                      {String(
                        assessment.explanation ||
                          "No detailed model-written explanation was retained for this decision.",
                      )}
                    </p>
                    <p>
                      <strong>Attribution:</strong>{" "}
                      {human(assessment.attribution)} ·{" "}
                      <strong>Speech role:</strong>{" "}
                      {human(assessment.speech_role)} ·{" "}
                      <strong>Document stance:</strong>{" "}
                      {human(assessment.document_stance)}
                    </p>
                    {assessment.alternative ? (
                      <p>
                        <strong>Alternative reading:</strong>{" "}
                        {String(assessment.alternative)}
                      </p>
                    ) : null}
                    <p className="w-muted">
                      This is a retained model interpretation, not an
                      independently adjudicated finding. Exact quotations
                      establish source provenance; they do not prove the
                      interpretation.
                    </p>
                  </details>
                ))}
              <p className="w-muted">
                Showing up to 12 matching decisions from this source; the
                downloadable XLSX/JSON preserves the complete selected set,
                references and unresolved unit states.
              </p>
              <details className="w-method-card">
                <summary>Read the frozen source text and credits</summary>
                <p>{String(displayedExample.attribution)}</p>
                {list<Item>(displayedExample.blocks).map((block) => (
                  <p key={String(block.id)}>{String(block.text)}</p>
                ))}
              </details>
            </>
          )}
        </div>
      )}
      <div className="w-refinement-tools">
        <button
          type="button"
          aria-expanded={diagnosticsOpen}
          onClick={() => setDiagnosticsOpen(!diagnosticsOpen)}
        >
          <CheckCircle2 size={15} /> Readiness
        </button>
        <button
          type="button"
          disabled={!nativePilot}
          title={
            nativePilot ? undefined : "Dedicated native installation required"
          }
          aria-expanded={activityOpen}
          onClick={() => setActivityOpen(!activityOpen)}
        >
          <Activity size={15} /> Activity center
        </button>
        <button
          type="button"
          aria-expanded={cardsOpen}
          onClick={() => void loadCards()}
        >
          <BookOpen size={15} /> Method cards
        </button>
        <button
          type="button"
          disabled={!nativePilot}
          title={
            nativePilot ? undefined : "Dedicated native installation required"
          }
          aria-expanded={compareOpen}
          onClick={() => void openComparison()}
        >
          <Layers3 size={15} /> Matched comparisons
        </button>
        <button
          type="button"
          disabled={!nativePilot}
          title={
            nativePilot ? undefined : "Dedicated native installation required"
          }
          aria-expanded={quotesOpen}
          onClick={() => void loadQuotes()}
        >
          <FileSearch size={15} /> Pilot scope
        </button>
      </div>
      {!nativePilot && (
        <p className="w-muted">
          This Cloudflare profile uses the established shared private-workspace
          workflow. Dedicated native installations provide the new pilot
          activity, comparison and service-scope controls.
        </p>
      )}

      {diagnosticsOpen && (
        <div className="w-refinement-detail">
          <h3>What is ready on this computer?</h3>
          {checks.map((check) => (
            <div className="w-readiness-check" key={String(check.id)}>
              <Badge>{String(check.status)}</Badge>
              <div>
                <strong>{String(check.message)}</strong>
                {check.action != null && <p>{String(check.action)}</p>}
              </div>
            </div>
          ))}
          <p className="w-muted">
            These checks do not load a model, request inference or establish
            interpretation quality. A separate model server uses additional
            memory.
          </p>
        </div>
      )}

      {activityOpen && (
        <div className="w-refinement-detail">
          <div className="w-refinement-top">
            <h3>Committed work survives the browser</h3>
            <Button variant="secondary" onClick={() => void loadActivity()}>
              <RefreshCw size={13} /> Refresh
            </Button>
          </div>
          {!jobs.length && (
            <p className="w-muted">
              No activity is recorded in this project yet. Upload sources, run
              an analysis or create a report to begin.
            </p>
          )}
          <div aria-live="polite">
            {jobs.map((job, index) => (
              <div className="w-activity-line" key={String(job.id || index)}>
                <span>
                  <strong>{human(job.kind || job.type || "analysis")}</strong>
                  <small>
                    {String(job.stage || job.phase || job.id || "")}
                  </small>
                </span>
                <Badge>{human(job.status)}</Badge>
              </div>
            ))}
          </div>
          <p className="w-muted">
            A completed upload is not a completed interpretation. Source
            coverage, analysis and review remain separate.
          </p>
        </div>
      )}

      {cardsOpen && (
        <div className="w-refinement-detail">
          <h3>The method behind each observation</h3>
          <p>
            Academic sources motivate each operational rule. They do not certify
            this application or establish accuracy on your collection.
          </p>
          <label className="w-refinement-field">
            Find a criterion
            <input
              value={cardFilter}
              onChange={(e) => setCardFilter(e.target.value)}
              placeholder="Framing, attribution, a named framework…"
            />
          </label>
          {cards
            .filter((card) =>
              `${card.id} ${card.label}`
                .toLowerCase()
                .includes(cardFilter.toLowerCase()),
            )
            .slice(0, 30)
            .map((card) => (
              <details className="w-method-card" key={String(card.id)}>
                <summary>
                  <strong>{String(card.label || card.id)}</strong>
                  <Badge>{human(card.criterion_kind)}</Badge>
                </summary>
                <p>{String(card.operational_rule || "")}</p>
                <p>
                  <strong>Unit:</strong> {String(card.unit_of_analysis || "")}
                </p>
                <p>
                  <strong>Limit:</strong>{" "}
                  {String(
                    record(card.validation).notice ||
                      "Independent interpretation validation is pending.",
                  )}
                </p>
                <ul>
                  {list<Item>(card.references).map((reference) => (
                    <li key={String(reference.url)}>
                      <a
                        href={String(reference.url)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {String(reference.authors)} ({String(reference.year)}):{" "}
                        {String(reference.title)}
                      </a>
                      <p>{String(reference.supported_claim || "")}</p>
                    </li>
                  ))}
                </ul>
              </details>
            ))}
          {cards.length > 30 && !cardFilter && (
            <p className="w-muted">
              Showing the first 30 cards. Search for another criterion or
              framework.
            </p>
          )}
        </div>
      )}

      {compareOpen && (
        <div className="w-refinement-detail">
          <h3>Compare like with like</h3>
          <p>
            Select two source cohorts. Sources must match on declared event,
            topic and genre, fall within seven publication days and have
            distinct source families and content hashes. Matching is descriptive
            and requires your relevance review.
          </p>
          <div className="w-refinement-form">
            <label className="w-refinement-field">
              Comparison question
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                maxLength={4000}
              />
            </label>
            <label className="w-refinement-field">
              Shared event, if you have verified relevance
              <input
                value={event}
                onChange={(e) => setEvent(e.target.value)}
                maxLength={500}
                placeholder="Otherwise use each source’s metadata"
              />
            </label>
            <label className="w-refinement-field">
              Shared topic
              <input
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                maxLength={500}
              />
            </label>
            <label className="w-refinement-field">
              Left source family override
              <input
                value={familyLeft}
                onChange={(e) => setFamilyLeft(e.target.value)}
                maxLength={500}
                placeholder="Otherwise use source metadata"
              />
            </label>
            <label className="w-refinement-field">
              Right source family override
              <input
                value={familyRight}
                onChange={(e) => setFamilyRight(e.target.value)}
                maxLength={500}
                placeholder="Otherwise use source metadata"
              />
            </label>
          </div>
          <label className="w-refinement-field">
            Find sources
            <input
              value={sourceSearch}
              onChange={(e) => setSourceSearch(e.target.value)}
            />
          </label>
          <div className="w-comparison-sources">
            {visibleDocs.map((doc) => (
              <div className="w-comparison-source" key={doc.id}>
                <button
                  type="button"
                  className="w-source-link"
                  onClick={() => onOpenSource(doc.id)}
                >
                  {doc.title}
                </button>
                <label>
                  <input
                    type="checkbox"
                    aria-label={`Left cohort: ${doc.title}`}
                    checked={left.includes(doc.id)}
                    onChange={(e) => select("left", doc.id, e.target.checked)}
                  />
                  Left
                </label>
                <label>
                  <input
                    type="checkbox"
                    aria-label={`Right cohort: ${doc.title}`}
                    checked={right.includes(doc.id)}
                    onChange={(e) => select("right", doc.id, e.target.checked)}
                  />
                  Right
                </label>
                <label className="w-source-date">
                  Publication date
                  <input
                    type="date"
                    value={
                      dates[doc.id] ??
                      String(doc.published_at || "").slice(0, 10)
                    }
                    onChange={(e) =>
                      setDates((old) => ({ ...old, [doc.id]: e.target.value }))
                    }
                  />
                </label>
              </div>
            ))}
          </div>
          <p className="w-muted">
            Showing up to 100 search matches from the first 2,000 project
            sources. Explicit selection only: {left.length} left, {right.length}{" "}
            right. Metadata overrides are stored as your declarations; originals
            remain unchanged.
          </p>
          <Button
            disabled={busy || !canAnalyze || !left.length || !right.length}
            onClick={() => void compare()}
          >
            Freeze matched comparison
          </Button>
          {comparison && (
            <div className="w-comparison-result" role="status">
              <Badge>{human(comparison.status)}</Badge>
              <h4>
                {number(comparisonSummary.matched_pairs)} matched{" "}
                {Number(comparisonSummary.matched_pairs) === 1
                  ? "pair"
                  : "pairs"}{" "}
                · {number(comparisonSummary.excluded_sources)} excluded{" "}
                {Number(comparisonSummary.excluded_sources) === 1
                  ? "source"
                  : "sources"}
              </h4>
              <div
                className="w-pair-chart"
                role="img"
                aria-label={`${comparisonSummary.left_selected} left and ${comparisonSummary.right_selected} right sources selected; ${comparisonSummary.matched_pairs} eligible matched pairs`}
              >
                <div>
                  <span>Left selected</span>
                  <i
                    style={{
                      width: `${Math.max(2, (Number(comparisonSummary.left_selected) / Math.max(Number(comparisonSummary.left_selected), Number(comparisonSummary.right_selected), 1)) * 100)}%`,
                    }}
                  />
                  {number(comparisonSummary.left_selected)}
                </div>
                <div>
                  <span>Matched pairs</span>
                  <i
                    style={{
                      width: `${(Number(comparisonSummary.matched_pairs) / Math.max(Number(comparisonSummary.left_selected), Number(comparisonSummary.right_selected), 1)) * 100}%`,
                    }}
                  />
                  {number(comparisonSummary.matched_pairs)}
                </div>
                <div>
                  <span>Right selected</span>
                  <i
                    style={{
                      width: `${Math.max(2, (Number(comparisonSummary.right_selected) / Math.max(Number(comparisonSummary.left_selected), Number(comparisonSummary.right_selected), 1)) * 100)}%`,
                    }}
                  />
                  {number(comparisonSummary.right_selected)}
                </div>
              </div>
              {list<Item>(comparison.pairs)
                .slice(0, 20)
                .map((pair) => (
                  <div
                    className="w-match-pair"
                    key={String(pair.left_document_id)}
                  >
                    <button
                      type="button"
                      onClick={() =>
                        onOpenSource(String(pair.left_document_id))
                      }
                    >
                      {documents.find((d) => d.id === pair.left_document_id)
                        ?.title || "Left source"}
                    </button>
                    <ArrowRight size={13} />
                    <button
                      type="button"
                      onClick={() =>
                        onOpenSource(String(pair.right_document_id))
                      }
                    >
                      {documents.find((d) => d.id === pair.right_document_id)
                        ?.title || "Right source"}
                    </button>
                    <small>
                      {String(pair.date_gap_days)}{" "}
                      {Number(pair.date_gap_days) === 1 ? "day" : "days"} apart
                    </small>
                  </div>
                ))}
              {!!Object.keys(analysis).length && (
                <p>{String(analysis.notice || "")}</p>
              )}
              {!!list<Item>(comparison.excluded).length && (
                <details>
                  <summary>Inspect exclusions</summary>
                  {list<Item>(comparison.excluded).map((row) => (
                    <p key={String(row.document_id)}>
                      {documents.find((d) => d.id === row.document_id)?.title ||
                        String(row.document_id)}
                      : {human(row.reason)}
                    </p>
                  ))}
                </details>
              )}
              <p className="w-muted">
                No causal effect, source independence or omission verdict has
                been established. Frozen snapshot:{" "}
                {String(comparison.snapshot_sha256 || "").slice(0, 16)}…
              </p>
            </div>
          )}
        </div>
      )}

      {quotesOpen && (
        <div className="w-refinement-detail">
          <h3>Your agreed pilot scope</h3>
          <p>
            Fixed customer prices, processing budgets and provider costs are
            separate. Quotes are issued by the service operator; this interface
            does not collect payments.
          </p>
          {!quotes.length && (
            <p className="w-muted">
              No operator-issued quotes are recorded. Local research remains
              available unless this installation explicitly requires an accepted
              pilot quote.
            </p>
          )}
          {quotes.map((quote) => (
            <div className="w-readiness-check" key={String(quote.id)}>
              <div>
                <strong>
                  {human(quote.profile_id)} · {human(quote.status)}
                </strong>
                <p>
                  ${(Number(quote.fixed_price_cents) / 100).toFixed(2)} USD
                  fixed price · Per-operation limits:{" "}
                  {number(record(quote.scope).documents)} documents ·{" "}
                  {number(record(quote.scope).characters)} characters ·{" "}
                  {number(record(quote.scope).criteria)} criteria ·{" "}
                  {number(record(quote.scope).ocr_pages)} OCR pages
                </p>
                <p>
                  Aggregate internal processing cap: $
                  {(Number(quote.processing_cap_microusd) / 1_000_000).toFixed(
                    2,
                  )}{" "}
                  USD. Accepting scope does not record payment.
                </p>
              </div>
              {canAdmin && quote.status === "offered" && (
                <Button
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      await request(
                        `/pilot/projects/${encodeURIComponent(project)}/quotes/${encodeURIComponent(String(quote.id))}/accept`,
                        {
                          method: "POST",
                          body: JSON.stringify({
                            scope_sha256: quote.scope_sha256,
                          }),
                        },
                      );
                      const value = await request<Item>(
                        `/pilot/projects/${encodeURIComponent(project)}/quotes`,
                      );
                      setQuotes(list<Item>(value.quotes));
                    })
                  }
                >
                  Accept agreed scope
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
