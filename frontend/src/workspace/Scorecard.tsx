import { useEffect, useState } from "react";
import { BarChart3 } from "lucide-react";
import { request } from "../api";
import { permits, useAccessSession } from "./AccessGate";
import { message } from "./client";
import type { QueryResult } from "./types";
import { Button, cueTime, ErrorMessage, human } from "./ui";
import "./Scorecard.css";

type Totals = {
  positive: number; negative: number; uncertain: number; incomplete: number;
  inapplicable: number; determinate: number; eligible: number; retained: number;
  planned: number; rate: number | null; coverage: number | null;
  unresolved_range?: [number, number] | null;
};
type Criterion = Totals & { key: string; label: string; kind: string };
type Witness = {
  assessment_id: string; block_id: string; quote: string; page: number | null;
  cue_start_ms: number | null; cue_end_ms: number | null;
  explanation: string; alternative: string; review_state: string;
};
type Cell = { document_id: string; criterion: string; status: string; evidence: Witness | null; scope_note?: string };
type Snapshot = {
  id: string; question: string; basis: string; created_at: string; snapshot_version: string;
  run: { model: string; status: string };
  sources: { id: string; title: string; published_at: string | null; extraction_complete: boolean; extraction_warnings: string[] }[];
  excluded: { document_id: string; title?: string; reason: string }[];
  criteria: Criterion[]; matrix: Cell[];
  timeline: (Totals & { period: string; criterion: string })[];
  index: null | {
    name: string; status: string; value: number | null; range: [number, number] | null; equal_weight: number | null;
    components: { key: string; weight: number; contribution: number | null; coverage: number | null }[];
  };
  comparison: null | {
    question: string; interpretation: string; excluded: { document_id: string; reason: string }[];
    criteria: { key: string; label: string; resolved: number; total: number; difference_pp: number | null }[];
  };
};
type ReportJob = {
  id: string; status: string; error?: { message: string };
  artifacts: { format: string; download_url: string }[];
};
type SavedSummary = { id: string; question: string; created_at: string };
type OpenSource = (source: string, block?: string, finding?: string) => void;
const examplePath = "/studies/scorecard/";
const percent = (value: number | null) => value === null ? "—" : (value * 100).toFixed(1) + "%";

function Evidence({ cell, onOpen }: { cell: Cell; onOpen?: OpenSource }) {
  const evidence = cell.evidence;
  if (!evidence) return <span>{human(cell.status)}{cell.scope_note && <small>{cell.scope_note}</small>}</span>;
  const where = evidence.cue_start_ms !== null && evidence.cue_end_ms !== null
    ? cueTime(evidence.cue_start_ms) + "–" + cueTime(evidence.cue_end_ms)
    : evidence.page !== null ? "Page " + evidence.page : "Block " + evidence.block_id;
  return <details>
    <summary>{human(cell.status)}</summary>
    {cell.scope_note && <p>{cell.scope_note}</p>}
    <p><small>{where} · {human(evidence.review_state)}</small></p>
    <blockquote>{evidence.quote}</blockquote>
    <p>{evidence.explanation}</p>
    {evidence.alternative && <p>{evidence.alternative}</p>}
    {onOpen && <Button variant="outline" onClick={() => onOpen(cell.document_id, evidence.block_id, evidence.assessment_id)}>
      Open in source
    </Button>}
  </details>;
}

function ScorecardView({ value, onOpen }: { value: Snapshot; onOpen?: OpenSource }) {
  const [timelineKey, setTimelineKey] = useState(value.criteria[0]?.key || "");
  const [shown, setShown] = useState(12);
  const cells = new Map(value.matrix.map((cell) => [cell.document_id + "/" + cell.criterion, cell]));
  const labels = new Map(value.criteria.map((row) => [row.key, row.label]));
  const index = value.index;
  return <>
    <p className="w-score-question">{value.question}</p>
    <p className="w-footnote">{value.sources.length} distinct sources · {human(value.basis)} · {human(value.run.status)}</p>
    {index && <div className="w-score-index">
      <h3>{index.name}</h3>
      <strong>{index.value === null ? human(index.status) : index.value.toFixed(1) + " / 100"}</strong>
      {index.range && <p>{index.range.map((n) => n.toFixed(1)).join("–")} across unresolved-outcome scenarios · Equal weights {index.equal_weight?.toFixed(1)}</p>}
      <details><summary>Weights and contributions</summary><ul>{index.components.map((component) =>
        <li key={component.key}>{labels.get(component.key)} · {component.weight}% weight · {percent(component.coverage)} coverage
          {component.contribution !== null && index.value !== null ? " · " + component.contribution.toFixed(1) + " points" : ""}
        </li>)}</ul></details>
    </div>}
    <div className="w-score-scroll">
      <table className="w-score-table">
        <caption>Criterion profile</caption>
        <thead><tr><th>Criterion</th><th>Rate · positive / determinate</th><th>Uncertain / incomplete / inapplicable</th><th>Coverage</th></tr></thead>
        <tbody>{value.criteria.map((item) => <tr key={item.key}>
          <th scope="row">{item.label}<small>{human(item.kind)}</small></th>
          <td>{item.rate !== null && <meter aria-label={item.label + " positive rate"} min={0} max={1} value={item.rate} />}
            {percent(item.rate)} · {item.positive}/{item.determinate}
            {item.unresolved_range && (item.uncertain > 0 || item.incomplete > 0) &&
              <small>Unresolved range {item.unresolved_range.map(percent).join("–")}</small>}</td>
          <td>{item.uncertain} / {item.incomplete} / {item.inapplicable}</td>
          <td>{percent(item.coverage)}<small>{item.retained}/{item.planned} decisions retained</small></td>
        </tr>)}</tbody>
      </table>
    </div>
    <details className="w-details">
      <summary>Source matrix and evidence</summary>
      <div className="w-score-scroll">
        <table className="w-score-table">
          <thead><tr><th>Source</th>{value.criteria.map((row) => <th key={row.key}>{row.label}</th>)}</tr></thead>
          <tbody>{value.sources.slice(0, shown).map((source) => <tr key={source.id}>
            <th scope="row">{source.title}<small>{source.published_at || "Undated"}</small>
              {!source.extraction_complete && <small>Extraction requires review. {source.extraction_warnings.join(" ")}</small>}
            </th>
            {value.criteria.map((criterion) => {
              const cell = cells.get(source.id + "/" + criterion.key);
              return <td key={criterion.key} data-outcome={cell?.status}>{cell && <Evidence cell={cell} onOpen={onOpen} />}</td>;
            })}
          </tr>)}</tbody>
        </table>
      </div>
      {value.sources.length > shown && <Button variant="outline" onClick={() => setShown(shown + 12)}>Show more sources</Button>}
      {!!value.excluded.length && <details><summary>{value.excluded.length} excluded sources</summary>
        <ul>{value.excluded.map((row) => <li key={row.document_id}>{row.title || row.document_id} · {human(row.reason)}</li>)}</ul>
      </details>}
    </details>
    <details className="w-details">
      <summary>Publication timeline</summary>
      <label>Criterion <select value={timelineKey} onChange={(event) => setTimelineKey(event.target.value)}>
        {value.criteria.map((row) => <option key={row.key} value={row.key}>{row.label}</option>)}
      </select></label>
      <div className="w-score-scroll"><table className="w-score-table">
        <thead><tr><th>Month</th><th>Rate · positive / determinate</th><th>Coverage</th></tr></thead>
        <tbody>{value.timeline.filter((row) => row.criterion === timelineKey).map((row) => <tr key={row.period}>
          <th scope="row">{row.period}</th>
          <td>{row.rate !== null && <meter aria-label={row.period + " positive rate"} min={0} max={1} value={row.rate} />}
            {percent(row.rate)} · {row.positive}/{row.determinate}</td><td>{percent(row.coverage)}</td>
        </tr>)}</tbody>
      </table></div>
    </details>
    {value.comparison && <details className="w-details" open>
      <summary>Matched comparison</summary>
      <p>{value.comparison.question}</p>
      <div className="w-score-scroll"><table className="w-score-table">
        <thead><tr><th>Criterion</th><th>Resolved pairs</th><th>Left minus right</th></tr></thead>
        <tbody>{value.comparison.criteria.map((row) => <tr key={row.key}><th scope="row">{row.label}</th>
          <td>{row.resolved}/{row.total}</td><td>{row.difference_pp === null ? "—" : row.difference_pp.toFixed(1) + " pp"}</td>
        </tr>)}</tbody>
      </table></div>
      <p>{value.comparison.interpretation}</p>
      {!!value.comparison.excluded.length && <details><summary>{value.comparison.excluded.length} comparison exclusions</summary>
        <ul>{value.comparison.excluded.map((row, index) => <li key={row.document_id + index}>{row.document_id} · {human(row.reason)}</li>)}</ul>
      </details>}
    </details>}
  </>;
}

export function ScorecardPanel({ project, query, onOpen, onRefresh }: {
  project: string; query: QueryResult | null; onOpen: OpenSource; onRefresh: () => void;
}) {
  const access = useAccessSession();
  const [saved, setSaved] = useState<Snapshot | null>(null);
  const [example, setExample] = useState<Snapshot | null>(null);
  const [history, setHistory] = useState<SavedSummary[] | null>(null);
  const [basis, setBasis] = useState("model");
  const [comparison, setComparison] = useState("");
  const [options, setOptions] = useState<{ id: string; question: string }[]>([]);
  const [format, setFormat] = useState("pdf");
  const [job, setJob] = useState<ReportJob | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const root = "/v3/projects/" + encodeURIComponent(project);
  const contextual = query?.metrics.analysis_kind === "contextual_v3";
  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void request<ReportJob>("/v3/report-jobs/" + encodeURIComponent(job.id), { signal: controller.signal })
        .then(setJob).catch((reason) => { if (!controller.signal.aborted) setError(message(reason)); });
    }, 750);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [job]);

  async function create() {
    if (!query) return;
    setBusy(true); setError("");
    try {
      const result = await request<Snapshot>(root + "/scorecards", {
        method: "POST", body: JSON.stringify({ query_id: query.query_id, basis, comparison_id: comparison || null }),
      });
      setSaved(result); setJob(null);
    } catch (reason) { setError(message(reason)); }
    finally { setBusy(false); }
  }
  async function exportSnapshot() {
    if (!saved) return;
    setBusy(true); setError("");
    try {
      setJob(await request<ReportJob>(root + "/report-jobs", {
        method: "POST", body: JSON.stringify({ scorecard_id: saved.id, format }),
      }));
    } catch (reason) { setError(message(reason)); }
    finally { setBusy(false); }
  }
  async function openSaved(id: string) {
    if (!id) return;
    setBusy(true); setError("");
    try {
      setSaved(await request<Snapshot>(root + "/scorecards/" + encodeURIComponent(id)));
      setJob(null);
    } catch (reason) { setError(message(reason)); }
    finally { setBusy(false); }
  }
  async function showExample() {
    if (example) { setExample(null); return; }
    setError("");
    try {
      const response = await fetch(examplePath + "scorecard.json");
      if (!response.ok) throw new Error("The bundled scorecard could not be opened.");
      setExample(await response.json() as Snapshot);
    } catch (reason) { setError(message(reason)); }
  }

  return <section className="w-scorecard" aria-label="Analysis scorecard">
    <h2><BarChart3 size={19} /> Research scorecard</h2>
    <div className="w-score-actions">
      {contextual && permits(access, "analyze") && <>
        <label>Decision basis <select value={basis} onChange={(event) => setBasis(event.target.value)}>
          <option value="model">Model outcomes</option><option value="review_overlay">Model outcomes with reviewed corrections</option>
        </select></label>
        <label>Matched comparison <select value={comparison} onFocus={() => {
          void request<{ items: { id: string; question: string; analysis?: { run_id?: string } }[] }>(root + "/comparisons")
            .then((result) => setOptions(result.items.filter((item) => !item.analysis?.run_id || item.analysis.run_id === query?.run?.id)))
            .catch((reason) => setError(message(reason)));
        }} onChange={(event) => setComparison(event.target.value)}>
          <option value="">None</option>
          {options.map((option) => <option key={option.id} value={option.id}>{option.question}</option>)}
        </select></label>
        <Button disabled={busy} onClick={() => void create()}>Save scorecard</Button>
      </>}
      <Button variant="outline" onClick={() => void showExample()}>{example ? "Close worked example" : "Open worked example"}</Button>
      <Button variant="outline" onClick={() => {
        if (history) { setHistory(null); return; }
        void request<{ items: SavedSummary[] }>(root + "/scorecards")
          .then((result) => setHistory(result.items)).catch((reason) => setError(message(reason)));
      }}>{history ? "Close saved list" : "Browse saved scorecards"}</Button>
    </div>
    {history && <label>Saved scorecard <select value={saved?.id || ""} onChange={(event) => void openSaved(event.target.value)}>
      <option value="">{history.length ? "Choose a snapshot" : "No saved scorecards"}</option>
      {history.map((item) => <option key={item.id} value={item.id}>{item.question} · {item.created_at.slice(0, 10)}</option>)}
    </select></label>}
    {!contextual && <p>Import a document or speech transcript and run a contextual analysis to score your sources.</p>}
    {error && <div><ErrorMessage text={error} />{error.includes("Refresh") && <Button variant="outline" onClick={onRefresh}>Refresh workspace</Button>}</div>}
    {saved && <>
      <ScorecardView key={saved.id} value={saved} onOpen={onOpen} />
      <p className="w-footnote">Frozen workspace revision {saved.snapshot_version} · {saved.id}</p>
      {permits(access, "export") && <div className="w-score-actions">
        <label>Format <select value={format} onChange={(event) => setFormat(event.target.value)}>
          {["pdf", "html", "xlsx", "csv", "jsonl"].map((item) => <option key={item} value={item}>{item.toUpperCase()}</option>)}
        </select></label>
        <Button variant="outline" disabled={busy || !!job && ["queued", "running"].includes(job.status)} onClick={() => void exportSnapshot()}>Generate export</Button>
      </div>}
      {job && <p role="status">{human(job.status)} {job.error?.message}
        {job.artifacts.map((item) => <a className="w-score-link" key={item.format} href={item.download_url}>Download {item.format.toUpperCase()}</a>)}
      </p>}
    </>}
    {example && <div className="w-score-example">
      <h3>Fictional teaching corpus</h3>
      <p>Compare two briefings and two captioned speeches about a proposed restriction on public demonstrations. The saved labels were authored for this exercise.</p>
      <ScorecardView key={example.id} value={example} />
      <p><a className="w-score-link" href={examplePath + "civic-square-sources.zip"} download>Download the import-ready sources</a>
        <a className="w-score-link" href={examplePath + "starter.zip"} download>Download sources, rubric, and metadata</a>
        <a className="w-score-link" href={examplePath + "doxa-scorecard.pdf"} download>Read the PDF</a>
        <a className="w-score-link" href={examplePath + "scorecard-evidence.jsonld"} download>Download JSON-LD</a></p>
    </div>}
  </section>;
}
