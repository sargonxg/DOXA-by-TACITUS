import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  ExternalLink,
  FlaskConical,
  LoaderCircle,
  Play,
  ShieldCheck,
} from "lucide-react";
import { request } from "../api";
import { list, message, record } from "./client";
import {
  Badge,
  Button,
  date,
  ErrorMessage,
  human,
  number,
  percentage,
} from "./ui";
import type { AnalysisRun, Item } from "./types";

type Difference = {
  primary_assessment_id: string;
  verifier_assessment_id: string;
  document_id: string;
  unit_id: string;
  lens_id: string;
  criterion_id: string;
  criterion_kind: string;
  differences: Record<string, { primary: unknown; verifier: unknown }>;
  primary: Item;
  verifier: Item;
};
type Comparison = {
  primary_run_id: string;
  verifier_run_id: string;
  primary_run_profile: Item;
  verifier_run_profile: Item;
  selected_units: number;
  verifier_units_present: number;
  verifier_units_accepted: number;
  verifier_units_missing: number;
  comparison_available: boolean;
  comparison_complete_on_selected_units: boolean;
  comparable_settings: boolean;
  comparability_notices: string[];
  counts: Record<string, number>;
  selection_counts: Record<string, number>;
  disagreements: Difference[];
  disagreement_limit: number;
  disagreements_truncated: boolean;
  notice: string;
};
type Props = {
  project: string;
  runs: AnalysisRun[];
  preferredRun?: string;
  onStarted: (run: AnalysisRun) => void;
  onNotice: (text: string, error?: boolean) => void;
  onOpenSource: (document: string, block?: string) => void;
};
const contextual = (run: AnalysisRun) =>
  String(
    run.config?.methodology_version || run.config?.schema_version || "",
  ).startsWith("3") && run.config?.provider !== "lexical";
const display = (value: unknown) =>
  value === null || value === undefined
    ? "Not supplied"
    : typeof value === "boolean"
      ? value
        ? "Yes"
        : "No"
      : human(value);

export function VerificationPanel({
  project,
  runs,
  preferredRun,
  onStarted,
  onNotice,
  onOpenSource,
}: Props) {
  const primaries = runs.filter(
    (run) =>
      contextual(run) &&
      run.status === "completed" &&
      !run.config?.verification_of,
  );
  const verifiers = runs.filter(
    (run) => contextual(run) && !!run.config?.verification_of,
  );
  const [primaryId, setPrimaryId] = useState("");
  const [verifierId, setVerifierId] = useState("");
  const [budget, setBudget] = useState("1.5");
  const [consent, setConsent] = useState(false);
  const [starting, setStarting] = useState(false);
  const [reading, setReading] = useState(false);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [error, setError] = useState("");
  const [visible, setVisible] = useState(20);
  useEffect(() => {
    if (!primaries.some((run) => run.id === primaryId))
      setPrimaryId(
        primaries.find((run) => run.id === preferredRun)?.id ||
          primaries[0]?.id ||
          "",
      );
    if (!verifiers.some((run) => run.id === verifierId))
      setVerifierId(verifiers[0]?.id || "");
  }, [runs, preferredRun, primaryId, verifierId]);
  const validBudget =
    Number.isFinite(Number(budget)) &&
    Number(budget) >= 0.1 &&
    Number(budget) <= 2;
  async function start() {
    if (!primaryId || !consent || !validBudget) return;
    setStarting(true);
    setError("");
    try {
      const run = await request<AnalysisRun>(
        `/v3/projects/${encodeURIComponent(project)}/verification`,
        {
          method: "POST",
          body: JSON.stringify({
            run_id: primaryId,
            allow_remote_text: true,
            budget_usd: Number(budget),
          }),
        },
      );
      onStarted(run);
      setVerifierId(run.id);
      setComparison(null);
      setConsent(false);
      onNotice(
        "Independent verifier started. Follow its separate processing record, then inspect the saved comparison.",
      );
    } catch (reason) {
      setError(message(reason));
    } finally {
      setStarting(false);
    }
  }
  async function inspect() {
    if (!verifierId) return;
    setReading(true);
    setError("");
    setComparison(null);
    try {
      setComparison(
        await request<Comparison>(
          `/v3/projects/${encodeURIComponent(project)}/verification/${encodeURIComponent(verifierId)}`,
        ),
      );
      setVisible(20);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setReading(false);
    }
  }
  const differences = list<Difference>(comparison?.disagreements).slice(0, 200);
  return (
    <details className="w-details w-verification">
      <summary>
        <FlaskConical size={15} />
        Independent model verification
      </summary>
      <div className="w-verification-body">
        <Badge>Independent model opinion</Badge>
        <p>
          A separate GPT-OSS 120B run reviews the frozen primary method. It
          selects original indicator-positive or uncertain source units, plus a
          5% target sample of other accepted units, stratified by document.
          Other units are not human-validated negatives.
        </p>
        <p className="w-footnote">
          Primary results, verifier results and human review remain separate.
          Model agreement is not accuracy; disagreements require judgment.
        </p>
        <label>
          Completed primary contextual run
          <select
            aria-label="Verification primary run"
            value={primaryId}
            onChange={(event) => {
              setPrimaryId(event.target.value);
              setConsent(false);
            }}
          >
            <option value="">Choose a completed primary run</option>
            {primaries.map((run) => (
              <option key={run.id} value={run.id}>
                {date(run.created_at)} ·{" "}
                {String(
                  run.config?.model || run.config?.provider || run.id,
                ).slice(0, 65)}{" "}
                · {run.id.slice(0, 10)}
              </option>
            ))}
          </select>
        </label>
        {!primaries.length && (
          <p className="w-footnote">
            Finish a primary contextual v3 run to enable a new verification.
            Saved verifier comparisons can still be inspected below.
          </p>
        )}
        <label>
          Verification budget · USD
          <input
            aria-label="Verification budget in USD"
            type="number"
            min="0.1"
            max="2"
            step="0.1"
            value={budget}
            onChange={(event) => setBudget(event.target.value)}
          />
        </label>
        <p className="w-footnote">
          Maximum $2 for this separate cloud run. The configured monthly cap
          also applies.
        </p>
        <label className="w-transfer-consent">
          <input
            type="checkbox"
            checked={consent}
            onChange={(event) => setConsent(event.target.checked)}
          />
          <span>
            <strong>Allow verification source text to Cloudflare</strong>
            <small>
              Selected source passages and frozen analytical instructions are
              sent to the independent GPT-OSS 120B model for this run.
            </small>
          </span>
        </label>
        <Button
          disabled={starting || !primaryId || !validBudget || !consent}
          onClick={() => void start()}
        >
          {starting ? (
            <LoaderCircle size={14} className="spin" />
          ) : (
            <Play size={14} />
          )}
          Start independent verification
        </Button>
        <div className="w-field-heading">
          <h3>Inspect a saved comparison</h3>
        </div>
        <label>
          Verifier processing record
          <select
            aria-label="Saved verifier run"
            value={verifierId}
            onChange={(event) => {
              setVerifierId(event.target.value);
              setComparison(null);
            }}
          >
            <option value="">Choose a verifier run</option>
            {verifiers.map((run) => (
              <option key={run.id} value={run.id}>
                {human(run.status)} · {date(run.created_at)} ·{" "}
                {run.id.slice(0, 12)}
              </option>
            ))}
          </select>
        </label>
        <Button
          variant="outline"
          disabled={!verifierId || reading}
          onClick={() => void inspect()}
        >
          {reading ? (
            <LoaderCircle size={14} className="spin" />
          ) : (
            <ShieldCheck size={14} />
          )}
          Inspect saved comparison
        </Button>
        {error && <ErrorMessage text={error} />}
        {comparison && (
          <section
            className="w-verification-report"
            aria-label="Independent verification comparison"
          >
            <div className="w-field-heading">
              <h3>Paired raw-model decisions</h3>
              <Badge
                tone={
                  comparison.comparison_complete_on_selected_units
                    ? "green"
                    : "amber"
                }
              >
                {comparison.comparison_complete_on_selected_units
                  ? "Selected units complete"
                  : "Selected units incomplete"}
              </Badge>
            </div>
            <dl className="w-verification-counts">
              <div>
                <dt>Selected source units</dt>
                <dd>{number(comparison.selected_units)}</dd>
              </div>
              <div>
                <dt>Verifier units accepted</dt>
                <dd>
                  {number(comparison.verifier_units_accepted)} /{" "}
                  {number(comparison.selected_units)}
                </dd>
              </div>
              <div>
                <dt>Paired decisions</dt>
                <dd>{number(comparison.counts.paired_assessments)}</dd>
              </div>
              <div>
                <dt>Literal decision differences</dt>
                <dd>{number(comparison.counts.any_mismatches)}</dd>
              </div>
            </dl>
            <p className="w-footnote">
              Coverage applies to this frozen selection.{" "}
              {number(comparison.verifier_units_missing)} selected units are
              missing from the verifier ledger.{" "}
              {number(comparison.selection_counts.sampled_other_units)} other
              accepted units were sampled (
              {percentage(
                Number(
                  comparison.selection_counts.other_sample_fraction_actual,
                ) * 100,
              )}{" "}
              of eligible others); this is not whole-corpus validation.
            </p>
            <dl className="w-verification-models">
              <div>
                <dt>Primary model</dt>
                <dd>
                  {String(
                    comparison.primary_run_profile.model ||
                      comparison.primary_run_id,
                  )}{" "}
                  · {human(comparison.primary_run_profile.status)}
                </dd>
              </div>
              <div>
                <dt>Verifier model</dt>
                <dd>
                  {String(
                    comparison.verifier_run_profile.model ||
                      comparison.verifier_run_id,
                  )}{" "}
                  · {human(comparison.verifier_run_profile.status)}
                </dd>
              </div>
            </dl>
            {!comparison.comparable_settings && (
              <div className="w-info amber">
                <ShieldCheck size={16} />
                <div>
                  <strong>Analytical settings need attention</strong>
                  {list<string>(comparison.comparability_notices).map(
                    (value) => (
                      <p key={value}>{value}</p>
                    ),
                  )}
                </div>
              </div>
            )}
            {!comparison.comparison_available && (
              <p className="w-footnote">
                No paired decisions are available yet. Refresh this saved
                comparison after processing advances.
              </p>
            )}
            <p className="w-footnote">{comparison.notice}</p>
            {differences.length > 0 && (
              <>
                <h4>Differences requiring inspection</h4>
                <div className="w-verification-differences">
                  {differences.slice(0, visible).map((row, index) => (
                    <details key={`${row.primary_assessment_id}-${index}`}>
                      <summary>
                        {human(row.criterion_id)}{" "}
                        <span>
                          {Object.keys(row.differences).length} changed fields
                        </span>
                      </summary>
                      <p>
                        {human(row.lens_id)} · {human(row.criterion_kind)} ·
                        source {row.document_id.slice(0, 12)}
                      </p>
                      <table>
                        <thead>
                          <tr>
                            <th>Field</th>
                            <th>Primary</th>
                            <th>Verifier</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(row.differences)
                            .slice(0, 6)
                            .map(([field, values]) => (
                              <tr key={field}>
                                <th>{human(field)}</th>
                                <td>{display(values.primary)}</td>
                                <td>{display(values.verifier)}</td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                      <Button
                        variant="quiet"
                        onClick={() => {
                          const refs = list<Item>(row.primary.evidence_refs)
                            .length
                            ? list<Item>(row.primary.evidence_refs)
                            : list<Item>(row.verifier.evidence_refs);
                          const block = record(refs[0]).block_id;
                          onOpenSource(
                            row.document_id,
                            typeof block === "string" ? block : undefined,
                          );
                        }}
                      >
                        Open source context
                        <ArrowUpRight size={13} />
                      </Button>
                    </details>
                  ))}
                </div>
              </>
            )}
            {visible < differences.length && (
              <Button
                variant="outline"
                onClick={() => setVisible((value) => Math.min(value + 20, 200))}
              >
                Show 20 more differences
              </Button>
            )}
            <p className="w-footnote">
              Showing {Math.min(visible, differences.length)} of{" "}
              {number(comparison.counts.any_mismatches)} differing decision
              pairs. The server returns at most{" "}
              {number(comparison.disagreement_limit || 200)} detailed rows
              {comparison.disagreements_truncated
                ? "; additional differences are counted in the summary"
                : ""}
              .
            </p>
            <a
              className="w-link"
              href={`/api/v3/projects/${encodeURIComponent(project)}/verification/${encodeURIComponent(comparison.verifier_run_id)}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open comparison JSON
              <ExternalLink size={13} />
            </a>
          </section>
        )}
      </div>
    </details>
  );
}
