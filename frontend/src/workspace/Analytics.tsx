import { useEffect, useId, useMemo, useState } from "react";
import {
  ArrowDownRight,
  ArrowRight,
  BarChart3,
  Check,
  GitBranch,
  Network,
  TrendingUp,
} from "lucide-react";
import { request } from "../api";
import { list, message } from "./client";
import {
  Badge,
  Empty,
  ErrorMessage,
  human,
  number,
  percentage,
  Spinner,
} from "./ui";
import type { Context, Item, QueryResult, Scope, TimePoint } from "./types";

export function Analytics({
  result,
  scope,
  onScope,
  project,
  context,
}: {
  result: QueryResult | null;
  scope: Scope;
  onScope: (patch: Partial<Scope>) => void;
  project: string;
  context: Context | null;
}) {
  const [mode, setMode] = useState("signals");
  const tabId = useId();
  const legacy =
    !!result?.run && result.metrics.analysis_kind === "legacy_unclassified";
  const unassessed = scope.run_id === "none" || !result?.run;
  const chartScope = unassessed
    ? { ...scope, metric: "documents" }
    : legacy
      ? { ...scope, metric: "findings" }
      : scope;
  return (
    <section className="w-analytics">
      {typeof result?.metrics.author_analysis?.notice === "string" && (
        <p className="w-pilot-note w-author-analysis-note">
          {result.metrics.author_analysis.notice}
        </p>
      )}
      <div className="w-section-heading">
        <div>
          <span className="w-eyebrow">PATTERNS, WITH THEIR CONTEXT</span>
          <h2>Discourse intelligence</h2>
        </div>
        <div
          className="w-view-switch"
          role="tablist"
          aria-label="Analytical view"
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
              return;
            const buttons = Array.from(
              event.currentTarget.querySelectorAll<HTMLButtonElement>(
                'button[role="tab"]',
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
          {[
            { id: "signals", label: "Signals", icon: BarChart3 },
            { id: "trajectory", label: "Trajectory", icon: TrendingUp },
            { id: "history", label: "History", icon: GitBranch },
            { id: "map", label: "Evidence map", icon: Network },
          ].map((item) => (
            <button
              key={item.id}
              role="tab"
              id={`${tabId}-${item.id}`}
              aria-controls={`${tabId}-panel`}
              tabIndex={mode === item.id ? 0 : -1}
              aria-selected={mode === item.id}
              onClick={() => setMode(item.id)}
              title={
                item.id === "history"
                  ? "Assessment history, not publication time"
                  : item.label
              }
            >
              <item.icon size={14} />
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      </div>
      <div
        role="tabpanel"
        id={`${tabId}-panel`}
        aria-labelledby={`${tabId}-${mode}`}
      >
        {mode === "signals" && (
          <>
            <Timeline
              points={result?.timeline || []}
              scope={chartScope}
              onScope={onScope}
              legacy={legacy}
              unassessed={unassessed}
            />
            <Matrix cells={result?.matrix || []} onScope={onScope} />
          </>
        )}
        {mode === "trajectory" && (
          <Trajectory project={project} result={result} scope={scope} />
        )}
        {mode === "history" && (
          <AssessmentHistory
            project={project}
            run={
              scope.run_id === "none"
                ? ""
                : scope.run_id || result?.run?.id || ""
            }
            query={result?.query_id}
          />
        )}
        {mode === "map" && <EvidenceMap context={context} />}
      </div>
    </section>
  );
}

function periodBounds(value: string): { from: string; to: string } | null {
  let first: Date;
  let last: Date;
  const week = /^(\d{4})-W(\d{2})$/.exec(value);
  if (week) {
    const year = Number(week[1]);
    const index = Number(week[2]);
    if (index > 53) return null;
    const january = new Date(Date.UTC(year, 0, 1));
    const mondayOffset = (8 - january.getUTCDay()) % 7;
    first = new Date(
      Date.UTC(year, 0, index === 0 ? 1 : 1 + mondayOffset + (index - 1) * 7),
    );
    last = new Date(
      index === 0
        ? Date.UTC(year, 0, Math.max(1, mondayOffset))
        : first.getTime() + 6 * 86400000,
    );
    const yearEnd = new Date(Date.UTC(year, 11, 31));
    if (last > yearEnd) last = yearEnd;
  } else if (/^\d{4}$/.test(value)) {
    first = new Date(Date.UTC(Number(value), 0, 1));
    last = new Date(Date.UTC(Number(value), 11, 31));
  } else if (/^\d{4}-(0[1-9]|1[0-2])$/.test(value)) {
    const [year, month] = value.split("-").map(Number);
    first = new Date(Date.UTC(year, month - 1, 1));
    last = new Date(Date.UTC(year, month, 0));
  } else if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    first = new Date(`${value}T00:00:00Z`);
    last = first;
  } else return null;
  if (!Number.isFinite(first.getTime()) || !Number.isFinite(last.getTime()))
    return null;
  return {
    from: first.toISOString().slice(0, 10),
    to: last.toISOString().slice(0, 10),
  };
}

function Timeline({
  points,
  scope,
  onScope,
  expanded = false,
  legacy = false,
  unassessed = false,
}: {
  points: TimePoint[];
  scope: Scope;
  onScope: (patch: Partial<Scope>) => void;
  expanded?: boolean;
  legacy?: boolean;
  unassessed?: boolean;
}) {
  const [anchor, setAnchor] = useState<number | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [tablePage, setTablePage] = useState(0);
  useEffect(() => setTablePage(0), [points]);
  const maxVolume = Math.max(
    1,
    ...points.map((point) =>
      Math.max(
        point.documents || 0,
        Number((point as unknown as Item)[scope.metric]) || 0,
      ),
    ),
  );
  const maxSignal = maxVolume;
  const plotWidth = 760;
  const left = 30;
  const right = 14;
  const width = plotWidth - left - right;
  const column = width / Math.max(points.length, 1);
  const height = expanded ? 235 : 148;
  const baseline = height - 35;
  const span = height - 62;
  function select(index: number, shift: boolean) {
    const start = shift && anchor !== null ? Math.min(anchor, index) : index;
    const end = shift && anchor !== null ? Math.max(anchor, index) : index;
    setAnchor(index);
    const first = points[start]?.date;
    const last = points[end]?.date;
    if (!first || !last) return;
    const firstRange = periodBounds(first);
    const lastRange = periodBounds(last);
    if (!firstRange || !lastRange) return;
    onScope({ from_date: firstRange.from, to_date: lastRange.to });
  }
  return (
    <div className={`w-timeline ${expanded ? "expanded" : ""}`}>
      <div className="w-chart-header">
        <strong>What changes over time?</strong>
        <select
          aria-label="Timeline measurement"
          value={scope.metric}
          disabled={legacy || unassessed}
          onChange={(event) => onScope({ metric: event.target.value })}
        >
          {unassessed ? (
            <option value="documents">Source volume · not analyzed</option>
          ) : legacy ? (
            <option value="findings">Legacy observation rows</option>
          ) : (
            <>
              <option value="endorsed_indicators">
                Author-endorsed indicator sources
              </option>
              <option value="reported_indicators">
                Reported indicator sources
              </option>
              <option value="indicators">All indicator sources</option>
              <option value="findings">All assessment rows</option>
            </>
          )}
        </select>
        <div>
          <span>
            <i className="w-legend volume" />
            Source volume
          </span>
          {!unassessed && (
            <span>
              <i className="w-legend signal" />
              {legacy
                ? "Legacy observation rows"
                : human(scope.metric).replace(
                    "indicators",
                    "indicator sources",
                  )}
            </span>
          )}
        </div>
      </div>
      {points.length ? (
        <>
          <svg
            viewBox={`0 0 ${plotWidth} ${height}`}
            role="img"
            aria-label={
              unassessed
                ? "Imported source volume over time. Corpus view has no model assessment. Select a dated bucket to filter."
                : "Source volume and retained observations over publication time. Select a time bucket to filter; Shift select extends the range."
            }
            onPointerLeave={() => setHover(null)}
          >
            {[0, 0.5, 1].map((fraction) => (
              <g key={fraction}>
                <line
                  x1={left}
                  x2={plotWidth - right}
                  y1={baseline - span * fraction}
                  y2={baseline - span * fraction}
                  stroke="#e8ece8"
                  strokeDasharray={fraction ? "3 4" : ""}
                />
                <text
                  x={left - 8}
                  y={baseline - span * fraction + 3}
                  textAnchor="end"
                  className="w-chart-tick"
                >
                  {number(maxVolume * fraction)}
                </text>
              </g>
            ))}
            {points.map((point, index) => {
              const x = left + index * column;
              const bar = (point.documents / maxVolume) * span;
              const signal =
                Number((point as unknown as Item)[scope.metric]) || 0;
              const signalHeight = unassessed ? 0 : (signal / maxSignal) * span;
              const incomplete = point.coverage < 100;
              return (
                <g
                  key={`${point.date}-${index}`}
                  role="button"
                  tabIndex={periodBounds(point.date) ? 0 : -1}
                  aria-disabled={!periodBounds(point.date)}
                  aria-label={
                    unassessed
                      ? `${point.date}: ${point.documents} sources; corpus not analyzed`
                      : `${point.date}: ${point.documents} sources, ${signal} retained observations, ${percentage(point.coverage)} coverage`
                  }
                  onFocus={() => setHover(index)}
                  onPointerEnter={() => setHover(index)}
                  onClick={(event) => select(index, event.shiftKey)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      select(index, event.shiftKey);
                    }
                  }}
                >
                  <rect
                    x={x + 2}
                    y={baseline - bar}
                    width={Math.max(1, column - 4)}
                    height={Math.max(1, bar)}
                    rx="2"
                    fill={hover === index ? "#9bb9ae" : "#dce7e1"}
                  />
                  <rect
                    x={x + column * 0.31}
                    y={baseline - signalHeight}
                    width={Math.max(2, column * 0.38)}
                    height={Math.max(0, signalHeight)}
                    rx="2"
                    fill="#447c6e"
                    opacity={incomplete ? 0.5 : 0.9}
                  />
                  {!unassessed && point.spike_candidate && (
                    <path
                      d={`M${x + column / 2 - 4},${baseline - Math.max(signalHeight, bar) - 9}l4,-5 4,5 -4,5Z`}
                      fill="#bd965f"
                    />
                  )}
                  <rect
                    x={x + 1}
                    y={baseline + 5}
                    width={Math.max(1, column - 2)}
                    height="3"
                    rx="1"
                    fill={
                      unassessed
                        ? "#c7d2c7"
                        : incomplete
                          ? "#d7b67d"
                          : "#86ad9e"
                    }
                  />
                  {(points.length < 13 ||
                    index % Math.ceil(points.length / 8) === 0) && (
                    <text
                      x={x + column / 2}
                      y={height - 10}
                      textAnchor="middle"
                      className="w-chart-tick"
                    >
                      {point.date}
                    </text>
                  )}
                  <rect
                    x={x}
                    y="7"
                    width={column}
                    height={height - 5}
                    fill="transparent"
                  >
                    <title>
                      {unassessed
                        ? `${point.date} · ${point.documents} sources · not analyzed`
                        : `${point.date} · ${point.documents} sources · ${signal} ${legacy ? "Legacy observation rows" : human(scope.metric).replace("indicators", "indicator sources")} · coverage ${percentage(point.coverage)}`}
                    </title>
                  </rect>
                </g>
              );
            })}
          </svg>
          <div className="w-chart-footer">
            <span>
              {hover !== null
                ? `${points[hover].date} · ${number(points[hover].documents)} sources · ${unassessed ? "not analyzed" : `${percentage(points[hover].coverage)} coverage`}`
                : "Select a period · Shift select a range"}
            </span>
            <span>
              <i className="w-legend incomplete" />
              {unassessed
                ? "No analysis in corpus view"
                : "Incomplete coverage"}
            </span>
          </div>
          <details className="w-details w-chart-table">
            <summary>
              Read timeline as a data table · {number(points.length)} periods
            </summary>
            <table>
              <caption>
                Same plotted source scope and metric. Processing coverage is
                separate from interpretation accuracy.
              </caption>
              <thead>
                <tr>
                  <th>Period</th>
                  <th>Sources</th>
                  <th>{unassessed ? "Analysis" : human(scope.metric)}</th>
                  <th>Coverage</th>
                  <th>Filter</th>
                </tr>
              </thead>
              <tbody>
                {points
                  .slice(tablePage * 100, (tablePage + 1) * 100)
                  .map((point, offset) => (
                    <tr key={point.date}>
                      <th scope="row">{point.date}</th>
                      <td>{number(point.documents)}</td>
                      <td>
                        {unassessed
                          ? "Not analyzed"
                          : number((point as unknown as Item)[scope.metric])}
                      </td>
                      <td>
                        {unassessed
                          ? "Not analyzed"
                          : percentage(point.coverage)}
                      </td>
                      <td>
                        <button
                          disabled={!periodBounds(point.date)}
                          onClick={() =>
                            select(tablePage * 100 + offset, false)
                          }
                        >
                          Select period {point.date}
                        </button>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {points.length > 100 && (
              <div className="w-case-actions">
                <button
                  disabled={!tablePage}
                  onClick={() => setTablePage(tablePage - 1)}
                >
                  Previous 100 periods
                </button>
                <button
                  disabled={(tablePage + 1) * 100 >= points.length}
                  onClick={() => setTablePage(tablePage + 1)}
                >
                  Next 100 periods
                </button>
              </div>
            )}
          </details>
        </>
      ) : (
        <Empty title="A timeline needs dated sources">
          Import source dates or choose import time. Undated material is never
          assigned an invented publication date.
        </Empty>
      )}
    </div>
  );
}

function Trajectory({
  project,
  result,
  scope,
}: {
  project: string;
  result: QueryResult | null;
  scope: Scope;
}) {
  const [data, setData] = useState<Item | null>(null);
  const [error, setError] = useState("");
  const run =
    scope.run_id === "none" ? "" : scope.run_id || result?.run?.id || "";
  const lens = scope.lens_ids[0] || "";
  const criterion = scope.criterion_ids[0] || "";
  useEffect(() => {
    let alive = true;
    setData(null);
    setError("");
    if (!run) return;
    const params = new URLSearchParams({ mode: "publication", run_id: run });
    if (result?.query_id) params.set("query_id", result.query_id);
    if (lens) params.set("lens_id", lens);
    if (criterion) params.set("criterion_id", criterion);
    void request<Item>(`/v3/projects/${project}/trajectories?${params}`)
      .then((value) => {
        if (alive) setData(value);
      })
      .catch((reason) => {
        if (alive) setError(message(reason));
      });
    return () => {
      alive = false;
    };
  }, [project, run, lens, criterion, result?.query_id]);
  const points = list<Item>(data?.monthly || data?.items);
  const rates = points.filter(
    (point) => point.rate !== null && point.rate !== undefined,
  );
  const candidates = list<Item>(data?.spike_candidates);
  const max = 100;
  const x = (index: number) =>
    48 + (index / Math.max(1, points.length - 1)) * 664;
  const y = (value: number) => 170 - value * 140;
  return (
    <div className="w-trajectory">
      <div className="w-chart-caption">
        <Badge>Publication time</Badge>
        <span>Frozen source scope · selected lens and criterion</span>
      </div>
      {error ? (
        <ErrorMessage text={error} />
      ) : !run ? (
        <Empty title="Select a contextual run">
          Publication trajectories use a frozen model, source snapshot and
          explicit framework.
        </Empty>
      ) : !data ? (
        <Spinner text="Computing eligible source trajectories…" />
      ) : (
        <>
          {!lens && (
            <div className="w-info amber">
              <TrendingUp size={16} />
              <p>
                Select one analytical framework to enable comparable source
                rates. Reported, hypothetical and author-endorsed expression
                remain separate.
              </p>
            </div>
          )}
          {rates.length ? (
            <>
              <svg
                className="w-rate-plot"
                viewBox="0 0 760 210"
                role="img"
                aria-label="Rate of eligible document clusters with author-endorsed non-hypothetical indicators. Vertical intervals show Wilson 95 percent bounds."
              >
                {[0, 0.5, 1].map((value) => (
                  <g key={value}>
                    <line
                      x1="48"
                      x2="730"
                      y1={y(value)}
                      y2={y(value)}
                      stroke="#e4e9e1"
                      strokeDasharray="3 4"
                    />
                    <text
                      x="38"
                      y={y(value) + 4}
                      textAnchor="end"
                      fontSize="10"
                      fill="#65745f"
                    >
                      {value * max}%
                    </text>
                  </g>
                ))}
                {points.map((point, index) =>
                  point.rate === null || point.rate === undefined ? null : (
                    <g key={String(point.date)}>
                      <line
                        x1={x(index)}
                        x2={x(index)}
                        y1={y(Number(point.upper || 0))}
                        y2={y(Number(point.lower || 0))}
                        stroke="#9aafa3"
                        strokeWidth="3"
                      />
                      <circle
                        cx={x(index)}
                        cy={y(Number(point.rate))}
                        r="4"
                        fill="#376b57"
                      >
                        <title>{`${point.date}: ${(Number(point.rate) * 100).toFixed(1)}% · ${point.eligible_documents} eligible sources · ${Number(point.coverage).toFixed(1)}% coverage`}</title>
                      </circle>
                      {(points.length < 12 ||
                        index % Math.ceil(points.length / 7) === 0) && (
                        <text
                          x={x(index)}
                          y="194"
                          textAnchor="middle"
                          fontSize="9"
                          fill="#65745f"
                        >
                          {String(point.date)}
                        </text>
                      )}
                    </g>
                  ),
                )}
              </svg>
              <div className="w-rate-table">
                <table>
                  <caption>
                    Most recent {Math.min(24, rates.length)} of {rates.length}{" "}
                    periods. Endorsed sources, eligible denominator and
                    processing coverage remain separate.
                  </caption>
                  <thead>
                    <tr>
                      <th>Period</th>
                      <th>Endorsed / eligible</th>
                      <th>Source rate</th>
                      <th>Coverage</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rates.slice(-24).map((point) => (
                      <tr key={String(point.date)}>
                        <td>{String(point.date)}</td>
                        <td>
                          {number(point.positive_documents)} /{" "}
                          {number(point.eligible_documents)}
                        </td>
                        <td>{(Number(point.rate) * 100).toFixed(1)}%</td>
                        <td>{percentage(Number(point.coverage))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <Empty title="Comparable source rates are unavailable">
              The selected method needs fully assessed, determinate source
              clusters and an indicator framework. Missing coverage does not
              become a zero rate.
            </Empty>
          )}
          <div className="w-info">
            <ShieldIcon />
            <p>
              {candidates.length
                ? `${number(candidates.length)} statistical review candidates passed the frozen comparison gates. Inspect sources before interpreting a change.`
                : "No statistical review candidate is displayed. Coverage, source composition, baseline size and multiple-comparison gates must all pass."}
            </p>
          </div>
          <p className="w-footnote">
            {String(data.interval_notice || "")} Rates follow the selected
            source scope. Outcome and speaker filters do not redefine the
            determinate-source denominator. They describe this imported corpus,
            never societal prevalence.
          </p>
        </>
      )}
    </div>
  );
}
function ShieldIcon() {
  return <Check size={16} />;
}

function Matrix({
  cells,
  onScope,
}: {
  cells: QueryResult["matrix"];
  onScope: (patch: Partial<Scope>) => void;
}) {
  const authors = useMemo(
    () => [...new Set(cells.map((cell) => cell.author))].slice(0, 10),
    [cells],
  );
  const criteria = useMemo(
    () =>
      [
        ...new Map(
          cells.map((cell) => [
            `${cell.lens_id}|${cell.criterion_id}`,
            {
              id: cell.criterion_id,
              lens: cell.lens_id,
              kind: cell.criterion_kind,
            },
          ]),
        ).values(),
      ].slice(0, 8),
    [cells],
  );
  const max = Math.max(1, ...cells.map((cell) => cell.count));
  const unverifiedMailGroups = cells.some(
    (cell) => cell.author_grouping === "supplied_unverified_document_metadata",
  );
  return (
    <div className="w-matrix">
      <div className="w-chart-header">
        <strong>Where do the patterns appear?</strong>
        <span className="w-subtle">
          {unverifiedMailGroups
            ? "Supplied document group × criterion"
            : "Author × criterion"}{" "}
          · select a cell
        </span>
      </div>
      {authors.length && criteria.length ? (
        <div className="w-matrix-scroll">
          <table
            aria-label={
              unverifiedMailGroups
                ? "Supplied unverified document metadata by analytical criteria"
                : "Source authors by analytical criteria"
            }
          >
            <thead>
              <tr>
                <th>
                  {unverifiedMailGroups
                    ? "Supplied document metadata"
                    : "Author / institution"}
                </th>
                {criteria.map((criterion) => (
                  <th
                    key={`${criterion.lens}-${criterion.id}`}
                    title={`${human(criterion.lens)} · ${human(criterion.id)}`}
                  >
                    {human(criterion.id)}
                    <small>{human(criterion.kind)}</small>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {authors.map((author) => (
                <tr key={author}>
                  <th>{author}</th>
                  {criteria.map((criterion) => {
                    const matches = cells.filter(
                      (cell) =>
                        cell.author === author &&
                        cell.lens_id === criterion.lens &&
                        cell.criterion_id === criterion.id,
                    );
                    const count = matches.reduce(
                      (sum, cell) => sum + cell.count,
                      0,
                    );
                    const coverage = matches.length
                      ? Math.min(...matches.map((cell) => cell.coverage))
                      : 0;
                    return (
                      <td key={`${criterion.lens}-${criterion.id}`}>
                        <button
                          style={{
                            background: count
                              ? `rgba(52, 116, 95, ${0.1 + (count / max) * 0.48})`
                              : "#f5f7f4",
                          }}
                          title={`${number(count)} retained observations · ${percentage(coverage)} ${matches[0]?.coverage_basis || "current-scope coverage"}`}
                          aria-label={`${author}, ${human(criterion.id)}, ${human(criterion.kind)}: ${number(count)} retained observations; ${percentage(coverage)} ${matches[0]?.coverage_basis || "current-scope coverage"}. Select this author and criterion.`}
                          onClick={() =>
                            onScope({
                              authors: [author],
                              lens_ids: [criterion.lens],
                              criterion_ids: [criterion.id],
                            })
                          }
                        >
                          {!count && coverage === 0 ? "—" : number(count)}
                          {coverage < 100 && count > 0 && (
                            <span className="w-cell-partial" />
                          )}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty title="Contextual observations will appear here">
          A corpus, method, and completed source checks are needed. Word matches
          are not contextual bias indicators.
        </Empty>
      )}
      <div className="w-chart-footer">
        <span>
          Excerpt: {authors.length} author groups × {criteria.length} criteria ·
          inspect a cell for its source scope
        </span>
        <span>Source-unit coverage qualifies comparisons</span>
      </div>
    </div>
  );
}

function AssessmentHistory({
  project,
  run,
  query,
}: {
  project: string;
  run: string;
  query?: string;
}) {
  const [data, setData] = useState<Item | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setData(null);
    setError("");
    if (!run) return;
    void request<Item>(
      `/v3/projects/${encodeURIComponent(project)}/trajectories?mode=assessment&run_id=${encodeURIComponent(run)}${query ? `&query_id=${encodeURIComponent(query)}` : ""}`,
    )
      .then((value) => {
        if (alive) setData(value);
      })
      .catch((reason) => {
        if (alive) setError(message(reason));
      });
    return () => {
      alive = false;
    };
  }, [project, run, query]);
  const series = list<Item>(data?.monthly || data?.items || data?.series);
  return (
    <div className="w-history">
      <div className="w-chart-caption">
        <Badge>Assessment time</Badge>
        <span>
          Assessment cohorts in this source scope. Changes in model assessments
          do not imply changes in the original author.
        </span>
      </div>
      {!run ? (
        <Empty title="Select an analysis snapshot">
          Corpus view has no model assessment history.
        </Empty>
      ) : error ? (
        <ErrorMessage text={error} />
      ) : !data ? (
        <Spinner text="Loading assessment history…" />
      ) : series.length ? (
        <div className="w-history-list">
          {series.slice(0, 50).map((point, index) => (
            <div key={String(point.id || index)}>
              <span className="w-history-node">
                <Check size={11} />
              </span>
              <div>
                <strong>
                  {String(
                    point.label ||
                      point.action ||
                      point.review_status ||
                      point.status ||
                      `${number(point.analyzed_documents)} sources assessed`,
                  )}
                </strong>
                <span>
                  {String(
                    point.date || point.created_at || point.timestamp || "",
                  )}
                </span>
                <p>
                  {String(
                    point.description ||
                      point.note ||
                      `${number(point.findings)} criterion matches · ${percentage(Number(point.coverage))} source coverage`,
                  )}
                </p>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Empty title="An audit trail starts with review">
          Saved decisions and method versions will appear here. This view is
          separate from publication-time discourse patterns.
        </Empty>
      )}
    </div>
  );
}

function EvidenceMap({ context }: { context: Context | null }) {
  const finding = context?.finding;
  if (!context || !finding)
    return (
      <Empty title="Select an observation to trace its evidence">
        The map connects an actual source, statement, and criterion. It does not
        infer relationships or private beliefs between people.
      </Empty>
    );
  const nodes = [
    {
      label: "SOURCE",
      title: context.document.title,
      detail:
        context.document.author ||
        context.document.institution ||
        "Unknown author",
    },
    {
      label: "STATEMENT",
      title: finding.speaker || "Speaker unresolved",
      detail: human(finding.attribution),
    },
    {
      label: "ASSESSMENT",
      title: human(finding.criterion),
      detail: human(finding.result || finding.certainty),
    },
  ];
  return (
    <div className="w-evidence-map">
      <div className="w-chart-caption">
        <Badge>Typed evidence links</Badge>
        <span>Model proposals remain distinct from source facts.</span>
      </div>
      <div className="w-map-nodes">
        {nodes.map((node, index) => (
          <div key={node.label}>
            <article>
              <span className="w-eyebrow">{node.label}</span>
              <strong>{node.title}</strong>
              <p>{node.detail}</p>
            </article>
            {index < nodes.length - 1 && (
              <span className="w-map-edge">
                <ArrowDownRight size={20} />
                {index === 0 ? "contains / attributes" : "assessed under"}
              </span>
            )}
          </div>
        ))}
      </div>
      <blockquote>“{finding.quote}”</blockquote>
      <div className="w-chart-footer">
        <span>
          {human(finding.review_status || "unreviewed")} · exact source span
        </span>
        <ArrowRight size={14} />
      </div>
    </div>
  );
}
