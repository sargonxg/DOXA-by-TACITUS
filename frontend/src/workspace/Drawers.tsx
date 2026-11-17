import { useEffect, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  Check,
  Database,
  ExternalLink,
  FileArchive,
  FlaskConical,
  Globe2,
  GraduationCap,
  HardDrive,
  Layers3,
  LoaderCircle,
  Microscope,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Square,
  Upload,
} from "lucide-react";
import { request } from "../api";
import { VerificationPanel } from "./Verification";
import { exportQuery, list, message, record } from "./client";
import {
  Badge,
  Button,
  date,
  Empty,
  ErrorMessage,
  human,
  number,
  percentage,
  Spinner,
} from "./ui";
import type {
  AnalysisRun,
  Drawer as DrawerName,
  Item,
  Lens,
  ModelProfile,
  Observation,
  QueryResult,
  Scope,
} from "./types";

type Props = {
  drawer: DrawerName;
  project: string;
  query: QueryResult | null;
  scope: Scope;
  lenses: Lens[];
  models: ModelProfile[];
  runs: AnalysisRun[];
  progress: string;
  busy: boolean;
  candidate: Observation | null;
  onOpenSource: (document: string, block?: string) => void;
  onScope: (patch: Partial<Scope>) => void;
  onImport: (files: FileList | File[]) => Promise<void>;
  onDemo: (collection: string) => Promise<void>;
  onRun: (config: Item) => Promise<void>;
  onVerificationStarted: (run: AnalysisRun) => void;
  onRefresh: () => void;
  onNotice: (text: string, error?: boolean) => void;
};
export function DrawerBody(props: Props) {
  if (props.drawer === "import") return <ImportPanel {...props} />;
  if (props.drawer === "analyze") return <AnalysisPanel {...props} />;
  if (props.drawer === "models") return <ModelsPanel {...props} />;
  if (props.drawer === "jobs") return <JobsPanel {...props} />;
  if (props.drawer === "reports") return <ReportsPanel {...props} />;
  if (props.drawer === "filters") return <FiltersPanel {...props} />;
  if (props.drawer === "learning") return <LearningPanel {...props} />;
  return null;
}

function ImportPanel({
  onImport,
  onDemo,
  busy,
  progress,
  project,
  onRefresh,
  onNotice,
}: Props) {
  const [drag, setDrag] = useState(false);
  async function metadata(file?: File) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const result = await request<Item>(
        `/projects/${project}/metadata/import`,
        { method: "POST", body: form },
      );
      onNotice(`${number(result.updated)} source records updated.`);
      onRefresh();
    } catch (error) {
      onNotice(message(error), true);
    }
  }
  return (
    <>
      <p className="w-intro">
        Bring your sources together. Originals stay in this workspace; inference
        runs only when you explicitly start an analysis.
      </p>
      <label
        className={`w-dropzone ${drag ? "dragging" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDrag(false);
          void onImport(event.dataTransfer.files);
        }}
      >
        <Upload size={29} strokeWidth={1.4} />
        <strong>Drop documents or a TXT archive</strong>
        <span>TXT, Markdown, PDF, DOCX, saved HTML, or ZIP</span>
        <input
          type="file"
          multiple
          accept=".txt,.md,.pdf,.docx,.html,.htm,.zip"
          disabled={busy}
          onChange={(event) => {
            if (event.target.files) void onImport(event.target.files);
            event.target.value = "";
          }}
        />
        <small>Select files from your computer</small>
      </label>
      {busy && (
        <div className="w-upload-progress" role="status">
          <LoaderCircle className="spin" size={18} />
          <span>{progress || "Importing source material…"}</span>
        </div>
      )}
      <div className="w-data-options">
        <h3>Start with real research material</h3>
        <button disabled={busy} onClick={() => void onDemo("news")}>
          <span>
            <Database size={22} />
          </span>
          <div>
            <strong>Licensed news collection</strong>
            <p>
              Explore the 5,000-source collection with dates, authors, rights,
              and source references.
            </p>
          </div>
          <ArrowRight size={17} />
        </button>
        <button disabled={busy} onClick={() => void onDemo("institutions")}>
          <span>
            <ShieldCheck size={22} />
          </span>
          <div>
            <strong>20 institutional case studies</strong>
            <p>
              Publications about prejudice, protection, integration, and freedom
              of expression.
            </p>
          </div>
          <ArrowRight size={17} />
        </button>
      </div>
      <div className="w-info">
        <FileArchive size={17} />
        <p>
          Archives and multi-file selections share an import session. Repeated
          content can appear in multiple imports without being counted as a new
          source version.
        </p>
      </div>
      <label className="w-file-action">
        <Upload size={15} />
        Import metadata CSV
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(event) => {
            void metadata(event.target.files?.[0]);
            event.target.value = "";
          }}
        />
      </label>
      <p className="w-footnote">
        Use id, filename or content checksum to match sources; include
        publication date, author, institution and document type.
      </p>
    </>
  );
}

function AnalysisPanel({
  project,
  query,
  scope,
  lenses,
  models,
  busy,
  onRun,
  onNotice,
}: Props) {
  const [question, setQuestion] = useState(
    "Which contextual indicators and competing frames appear in this corpus, and who expresses them?",
  );
  const [provider, setProvider] = useState("cloudflare");
  const [model, setModel] = useState("@cf/google/gemma-4-26b-a4b-it");
  const [selected, setSelected] = useState(
    scope.lens_ids.length ? scope.lens_ids : ["discourse"],
  );
  const [budget, setBudget] = useState("5");
  const [consent, setConsent] = useState(false);
  const [role, setRole] = useState(
    "Discourse researcher; distinguish author assertions, reported speech, counterspeech, and uncertain context.",
  );
  const [custom, setCustom] = useState("");
  const [customName, setCustomName] = useState("");
  const [customKind, setCustomKind] = useState("descriptive");
  const [health, setHealth] = useState<Item | null>(null);
  const [checking, setChecking] = useState(false);
  const [memory, setMemory] = useState("active");
  const [learning, setLearning] = useState<Item>({});
  useEffect(() => {
    void request<Item>(`/v3/projects/${project}/learning`)
      .then(setLearning)
      .catch((reason) => onNotice(message(reason), true));
  }, [project]);
  async function check() {
    setChecking(true);
    try {
      setHealth(
        await request("/v3/models/health", {
          method: "POST",
          body: JSON.stringify({ provider, model }),
        }),
      );
    } catch (error) {
      onNotice(message(error), true);
    } finally {
      setChecking(false);
    }
  }
  async function submit() {
    if (!selected.length && !custom.trim())
      return onNotice(
        "Select a framework or supply explicit custom criteria.",
        true,
      );
    if (!["ollama", "lexical"].includes(provider) && !consent)
      return onNotice(
        "Explicitly enable text transfer to start remote inference.",
        true,
      );
    const body: Item = {
      query_id: query?.query_id,
      methodology_version: "3.0",
      provider,
      model,
      lens_ids: custom.trim() ? [...selected, "custom"] : selected,
      question,
      persona: role,
      budget_usd: Number(budget),
      allow_remote_text: consent,
    };
    if (memory !== "active") body.learning_snapshot_id = memory || null;
    if (custom.trim())
      body.custom_lens = {
        id: "custom",
        name: customName || "Custom inquiry",
        description: question.slice(0, 2000),
        criteria: custom
          .split("\n")
          .filter((line) => line.trim())
          .map((line, index) => ({
            id: `custom-${index + 1}`,
            label: line.slice(0, 100),
            description: line,
            kind: customKind,
          })),
      };
    await onRun(body);
  }
  return (
    <>
      <div className="w-query-summary">
        <Badge>Methodology v3</Badge>
        <strong>{number(query?.documents.total)} matching sources</strong>
        <span>
          Analysis freezes this source selection and the declared method.
        </span>
      </div>
      <label>
        Research question
        <textarea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          rows={3}
          maxLength={4000}
        />
      </label>
      <div className="w-field-heading">
        <h3>Explicit analytical frameworks</h3>
        <span>Select one or compare several</span>
      </div>
      <div className="w-lens-selection">
        {lenses.map((lens) => (
          <label key={lens.id}>
            <input
              type="checkbox"
              checked={selected.includes(lens.id)}
              onChange={(event) =>
                setSelected((previous) =>
                  event.target.checked
                    ? [...previous, lens.id]
                    : previous.filter((id) => id !== lens.id),
                )
              }
            />
            <span>
              <strong>{lens.name}</strong>
              <small>{lens.description}</small>
            </span>
          </label>
        ))}
      </div>
      <details className="w-details">
        <summary>Add a custom rubric</summary>
        <label>
          Rubric name
          <input
            value={customName}
            onChange={(event) => setCustomName(event.target.value)}
            maxLength={160}
          />
        </label>
        <label>
          Criterion role
          <select
            aria-label="Custom criterion role"
            value={customKind}
            onChange={(event) => setCustomKind(event.target.value)}
          >
            <option value="descriptive">
              Descriptive frame · not a prejudice indicator
            </option>
            <option value="indicator">
              Observable indicator under this declared rubric
            </option>
            <option value="boundary">Boundary or exclusion condition</option>
          </select>
        </label>
        <label>
          One observable criterion per line
          <textarea
            value={custom}
            onChange={(event) => setCustom(event.target.value)}
            rows={4}
            placeholder="State observable criteria, context requirements, and exclusions."
          />
        </label>
      </details>
      <label>
        Reviewed example memory
        <select
          aria-label="Reviewed example memory"
          value={memory}
          onChange={(event) => setMemory(event.target.value)}
        >
          <option value="active">
            Current active method
            {learning.active_snapshot_id
              ? ` · ${String(learning.active_snapshot_id).slice(0, 10)}`
              : " · no approved memory"}
          </option>
          <option value="">No reviewed memory · explicit baseline</option>
          {list<Item>(learning.snapshots).map((item) => (
            <option key={String(item.id)} value={String(item.id)}>
              Frozen snapshot {String(item.id).slice(0, 14)}
            </option>
          ))}
        </select>
      </label>
      <p className="w-footnote">
        A draft snapshot can be selected for a candidate evaluation run.
        Activation remains separately gated and affects future runs only.
      </p>
      <label>
        Reviewer role and priorities
        <textarea
          value={role}
          onChange={(event) => setRole(event.target.value)}
          rows={2}
          maxLength={1200}
        />
      </label>
      <p className="w-footnote">
        A declared analytical role is not a simulation of a demographic group.
        Report audience is chosen separately when exporting.
      </p>
      <div className="w-form-grid">
        <label>
          Provider
          <select
            value={provider}
            onChange={(event) => {
              const value = event.target.value;
              setProvider(value);
              setModel(
                value === "ollama"
                  ? "qwen3.5:4b"
                  : value === "lexical"
                    ? "lexical-baseline-v1"
                    : "@cf/google/gemma-4-26b-a4b-it",
              );
              setHealth(null);
            }}
          >
            <option value="cloudflare">Cloudflare · cloud default</option>
            <option value="ollama">Ollama · local fallback</option>
            <option value="openai">Configured OpenAI-compatible</option>
            <option value="runpod">Configured RunPod GPU</option>
            <option value="lexical">Lexical navigation only</option>
          </select>
        </label>
        <label>
          Run budget · USD
          <input
            type="number"
            value={budget}
            min="0"
            max="10000"
            step="0.1"
            onChange={(event) => setBudget(event.target.value)}
          />
        </label>
      </div>
      <label>
        Model
        <input
          list="w-model-options"
          value={model}
          onChange={(event) => setModel(event.target.value)}
        />
        <datalist id="w-model-options">
          {models
            .filter((item) => !item.provider || item.provider === provider)
            .map((item) => (
              <option key={item.id} value={String(item.model || item.id)}>
                {item.name}
              </option>
            ))}
        </datalist>
      </label>
      {!["ollama", "lexical"].includes(provider) && (
        <label className="w-transfer-consent">
          <input
            type="checkbox"
            checked={consent}
            onChange={(event) => setConsent(event.target.checked)}
          />
          <span>
            <strong>Allow selected source text to this cloud provider</strong>
            <small>
              Originals and workspace records stay here. Selected text and
              context are sent to the configured model for this run.
            </small>
          </span>
        </label>
      )}
      {provider === "lexical" && (
        <div className="w-info amber">
          <ShieldCheck size={16} />
          <p>
            Word matches support navigation. They do not establish contextual
            indicators or prejudice prevalence.
          </p>
        </div>
      )}
      {health && (
        <div className={`w-info ${health.ok ? "" : "amber"}`}>
          <ShieldCheck size={17} />
          <p>
            {health.ok ? "Provider is ready." : "Provider needs attention."}{" "}
            {typeof health.details === "string" ? health.details : ""}
          </p>
        </div>
      )}
      <div className="w-drawer-actions">
        <Button
          variant="outline"
          disabled={checking}
          onClick={() => void check()}
        >
          {checking ? (
            <LoaderCircle className="spin" size={14} />
          ) : (
            <RefreshCw size={14} />
          )}
          Check provider
        </Button>
        <Button
          disabled={busy || !query?.documents.total}
          onClick={() => void submit()}
        >
          <Play size={14} />
          Start matching-source analysis
        </Button>
      </div>
    </>
  );
}

function ModelsPanel({
  models,
  runs,
  onVerificationStarted,
  project,
  query,
  candidate,
  onOpenSource,
  onNotice,
}: Props) {
  const [health, setHealth] = useState<Item | null>(null);
  const [checking, setChecking] = useState("");
  const [monthlyBudget, setMonthlyBudget] = useState("");
  const [savingBudget, setSavingBudget] = useState(false);
  useEffect(() => {
    void request<Item>("/settings")
      .then((value) => setMonthlyBudget(String(value.monthly_budget_usd ?? "")))
      .catch((reason) => onNotice(message(reason), true));
  }, []);
  async function saveBudget() {
    setSavingBudget(true);
    try {
      await request("/settings", {
        method: "PUT",
        body: JSON.stringify({ monthly_budget_usd: Number(monthlyBudget) }),
      });
      onNotice("Monthly processing cap updated.");
    } catch (reason) {
      onNotice(message(reason), true);
    } finally {
      setSavingBudget(false);
    }
  }
  const [operation, setOperation] = useState("");
  const [search, setSearch] = useState("");
  const [hypothesis, setHypothesis] = useState(
    "The passage attributes a claim to a quoted speaker.",
  );
  const [toolResult, setToolResult] = useState<Item | null>(null);
  const [toolError, setToolError] = useState("");
  async function tool(path: string, body: Item) {
    setOperation(path);
    setToolError("");
    setToolResult(null);
    try {
      setToolResult(
        await request<Item>(`/v3/projects/${project}/${path}`, {
          method: "POST",
          body: JSON.stringify(body),
        }),
      );
    } catch (reason) {
      setToolError(message(reason));
    } finally {
      setOperation("");
    }
  }
  async function check(profile: ModelProfile) {
    setChecking(profile.id);
    try {
      setHealth(
        await request("/v3/models/health", {
          method: "POST",
          body: JSON.stringify(
            ["minilm", "deberta"].includes(profile.id)
              ? { model_key: profile.id }
              : {
                  provider: profile.provider,
                  model: profile.model || profile.id,
                },
          ),
        }),
      );
    } catch (error) {
      onNotice(message(error), true);
    } finally {
      setChecking("");
    }
  }
  return (
    <>
      <p className="w-intro">
        Choose cloud inference for larger workloads, or a conservative local
        profile for a 16 GB computer. Provider credentials remain on the server.
      </p>
      <VerificationPanel
        project={project}
        runs={runs}
        preferredRun={query?.run?.id}
        onStarted={onVerificationStarted}
        onNotice={onNotice}
        onOpenSource={onOpenSource}
      />
      <div className="w-model-list">
        {models.length ? (
          models.map((profile) => (
            <article key={profile.id}>
              <div>
                <Badge>{profile.provider || "Configured provider"}</Badge>
                {profile.recommended && <Badge tone="green">Recommended</Badge>}
              </div>
              <h3>{profile.name || profile.model || profile.id}</h3>
              <p>{String(profile.description || profile.intended_use || "")}</p>
              <div className="w-model-meta">
                <span>
                  {String(
                    profile.memory ||
                      profile.memory_note ||
                      "Preflight checks memory and context",
                  )}
                </span>
                <span>{String(profile.license || "")}</span>
              </div>
              <Button
                variant="outline"
                disabled={!!checking}
                onClick={() => void check(profile)}
              >
                {checking === profile.id ? (
                  <LoaderCircle className="spin" size={14} />
                ) : (
                  <ShieldCheck size={14} />
                )}
                Check readiness
              </Button>
            </article>
          ))
        ) : (
          <Empty title="No registered profiles are available">
            Check the configured model registry. The app will not silently
            substitute a different model.
          </Empty>
        )}
      </div>
      {health && (
        <div className={`w-info ${health.ok ? "" : "amber"}`}>
          <HardDrive size={17} />
          <div>
            <strong>
              {health.ok ? "Profile is ready" : "Setup needs attention"}
            </strong>
            <p>
              {typeof health.details === "string"
                ? health.details
                : JSON.stringify(health, null, 2)}
            </p>
          </div>
        </div>
      )}
      <details className="w-details">
        <summary>Processing budget</summary>
        <p>
          The server enforces the monthly cap in addition to each run budget.
          Reported charges are recorded or estimated usage, not provider
          invoices.
        </p>
        <label>
          Monthly budget cap · USD
          <input
            type="number"
            min="0"
            max="100000"
            step="1"
            value={monthlyBudget}
            onChange={(event) => setMonthlyBudget(event.target.value)}
          />
        </label>
        <Button
          variant="outline"
          disabled={savingBudget || monthlyBudget === ""}
          onClick={() => void saveBudget()}
        >
          <Check size={14} />
          Save monthly cap
        </Button>
      </details>
      <details className="w-details">
        <summary>Optional CPU retrieval & rubric experiments</summary>
        <p>
          Explicit local actions use pinned artifacts already installed on this
          machine. They do not start a download or create analytical findings.
        </p>
        <Button
          variant="outline"
          disabled={!!operation || !query?.documents.total}
          onClick={() =>
            void tool("semantic-index", { query_id: query?.query_id })
          }
        >
          <Database size={14} />
          Build selected-scope semantic index
        </Button>
        <label>
          Semantic search
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            maxLength={2000}
            placeholder="Search by meaning across indexed passages"
          />
        </label>
        <Button
          variant="outline"
          disabled={!!operation || !search.trim()}
          onClick={() =>
            void tool("semantic-search", {
              q: search,
              query_id: query?.query_id,
              limit: 20,
            })
          }
        >
          <Globe2 size={14} />
          Search indexed passages
        </Button>
        <label>
          Experimental rubric proposition
          <textarea
            value={hypothesis}
            onChange={(event) => setHypothesis(event.target.value)}
            rows={2}
          />
        </label>
        <Button
          variant="outline"
          disabled={!!operation || !candidate || !hypothesis.trim()}
          onClick={() =>
            void tool("rubric-score", {
              document_id: candidate?.document_id,
              block_id: candidate?.block_id,
              hypotheses: [{ id: "explicit-proposition", text: hypothesis }],
            })
          }
        >
          <FlaskConical size={14} />
          Score the selected source passage
        </Button>
        <p className="w-footnote">
          Select an observation before scoring. Entailment scores are
          uncalibrated model outputs, never probabilities of prejudice.
        </p>
        {operation && <Spinner text="Running the explicit CPU task…" />}
        {toolError && <ErrorMessage text={toolError} />}
        {toolResult && (
          <div className="w-cpu-results">
            {list<Item>(toolResult.hits).map((hit, index) => (
              <button
                key={String(hit.block_id || index)}
                onClick={() =>
                  onOpenSource(String(hit.document_id), String(hit.block_id))
                }
              >
                <strong>{String(hit.title)}</strong>
                <p>“{String(hit.quote)}”</p>
                <small>
                  Similarity {Number(hit.score).toFixed(3)} · retrieval only
                </small>
              </button>
            ))}
            <details className="w-details">
              <summary>Actual task output & provenance</summary>
              <pre>{JSON.stringify(toolResult, null, 2)}</pre>
            </details>
          </div>
        )}
      </details>
      <details className="w-details">
        <summary>Local fallback setup</summary>
        <p>
          When Ollama is already running, the readiness check verifies the
          selected model. Installation of a machine-level service still requires
          the normal operating-system installer.
        </p>
        <a
          className="w-link"
          href="https://ollama.com/download"
          target="_blank"
          rel="noopener noreferrer"
        >
          Open the Ollama installer
          <ExternalLink size={13} />
        </a>
        <p className="w-footnote">
          Use native Ollama on Apple silicon for acceleration. CPU-only
          processing is supported with estimates based on actual pilot
          throughput.
        </p>
      </details>
    </>
  );
}

function JobsPanel({
  runs,
  project,
  query,
  onVerificationStarted,
  onOpenSource,
  onNotice,
  onRefresh,
}: Props) {
  const [budget, setBudget] = useState<Record<string, string>>({});
  async function action(run: AnalysisRun, type: "cancel" | "resume") {
    if (type === "resume" && run.config?.verification_of && Number(budget[run.id] ?? run.config?.budget_usd ?? 1.5) > 2) { onNotice("Independent verification is capped at $2 per run.", true); return; }
    try {
      await request(`/runs/${run.id}/${type}`, {
        method: "POST",
        body: JSON.stringify(
          type === "resume"
            ? {
                budget_usd: Number(
                  budget[run.id] ?? run.config?.budget_usd ?? 5,
                ),
              }
            : {},
        ),
      });
      onRefresh();
      onNotice(
        type === "cancel"
          ? "Cancellation requested; committed work stays available."
          : "Run resumed from saved checkpoints.",
      );
    } catch (error) {
      onNotice(message(error), true);
    }
  }
  return (
    <>
      <p className="w-intro">
        Processing status is live. Analytical views remain bound to their query
        snapshot until you refresh them.
      </p>
      <VerificationPanel
        project={project}
        runs={runs}
        preferredRun={query?.run?.id}
        onStarted={onVerificationStarted}
        onNotice={onNotice}
        onOpenSource={onOpenSource}
      />
      {runs.length ? (
        <div className="w-job-list">
          {runs.slice(0, 30).map((run) => {
            const p = record(run.progress);
            const done = Number(p.blocks_done || 0);
            const total = Number(p.blocks_total || 0);
            return (
              <article key={run.id}>
                <div>
                  <Badge tone={run.status === "completed" ? "green" : "amber"}>
                    {human(run.status)}
                  </Badge>
                  <span>{date(run.created_at)}</span>
                </div>
                <h3>{String(run.config?.question || "Corpus analysis")}</h3>
                <p>
                  {String(run.config?.provider || run.provider || "")} ·{" "}
                  {String(run.model_profile?.model || run.config?.model || "")}
                </p>
                <div className="w-progress-bar">
                  <span
                    style={{ width: `${total ? (done / total) * 100 : 0}%` }}
                  />
                </div>
                <div className="w-job-stats">
                  <span>
                    {number(done)} / {number(total)} source blocks passed checks
                  </span>
                  <span>
                    {typeof run.usage?.cost_usd === "number"
                      ? `$${run.usage.cost_usd.toFixed(3)}`
                      : "Cost not supplied"}{" "}
                    {run.config?.saved_snapshot
                      ? "historical usage"
                      : "recorded usage"}
                  </span>
                </div>
                <div className="w-job-actions">
                  {["running", "queued"].includes(run.status) && (
                    <Button
                      variant="outline"
                      onClick={() => void action(run, "cancel")}
                    >
                      <Square size={12} />
                      Cancel
                    </Button>
                  )}
                  {!run.config?.saved_snapshot &&
                    [
                      "partial",
                      "failed",
                      "paused_budget",
                      "cancelled",
                    ].includes(run.status) && (
                      <>
                        <input
                          aria-label={`Resume budget for ${run.id}`}
                          type="number"
                          min="0"
                          max={run.config?.verification_of ? 2 : 10000}
                          value={
                            budget[run.id] ??
                            String(run.config?.budget_usd || 5)
                          }
                          onChange={(event) =>
                            setBudget((previous) => ({
                              ...previous,
                              [run.id]: event.target.value,
                            }))
                          }
                        />
                        <Button
                          variant="outline"
                          onClick={() => void action(run, "resume")}
                        >
                          <Play size={12} />
                          Resume
                        </Button>
                      </>
                    )}
                </div>
                <details className="w-details">
                  <summary>
                    Method and processing issues ({run.errors?.length || 0})
                  </summary>
                  <pre>
                    {JSON.stringify(
                      {
                        model: run.model_profile,
                        methodology: run.config?.methodology_version,
                        issues: run.errors?.slice(0, 8),
                      },
                      null,
                      2,
                    )}
                  </pre>
                </details>
              </article>
            );
          })}
        </div>
      ) : (
        <Empty title="Your first analysis is waiting">
          Import sources and choose a declared analytical method. No inference
          starts merely by opening the workspace.
        </Empty>
      )}
    </>
  );
}

function ReportsPanel({ query, project, onNotice }: Props) {
  const [profile, setProfile] = useState("research");
  const [format, setFormat] = useState("pdf");
  const [busy, setBusy] = useState(false);
  async function download() {
    if (!query) return;
    setBusy(true);
    try {
      await exportQuery(project, query.query_id, format, profile);
      onNotice(
        `${format.toUpperCase()} report downloaded from this query snapshot.`,
      );
    } catch (error) {
      onNotice(message(error), true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="w-query-summary">
        <Badge>Frozen query snapshot</Badge>
        <strong>
          {number(query?.documents.total)} sources ·{" "}
          {number(query?.findings.total)} retained observations
        </strong>
        <span>
          Scope {query?.scope_hash.slice(0, 12) || "not selected"} · coverage{" "}
          {percentage(query?.metrics.coverage)}
        </span>
      </div>
      <p className="w-intro">
        Every export uses the same scope as the workspace. Audience changes
        presentation, not evidence or measurements.
      </p>
      <div className="w-report-profiles">
        {[
          {
            id: "research",
            name: "Research & civil society",
            icon: Microscope,
            detail:
              "Methods, criteria, evidence, uncertainty and reviewer decisions.",
          },
          {
            id: "briefing",
            name: "Newsrooms & policy",
            icon: Globe2,
            detail: "A concise briefing, changes and verification priorities.",
          },
          {
            id: "teaching",
            name: "Universities & teaching",
            icon: GraduationCap,
            detail:
              "Annotated examples, competing readings and discussion prompts.",
          },
        ].map((item) => (
          <button
            className={profile === item.id ? "selected" : ""}
            key={item.id}
            onClick={() => setProfile(item.id)}
          >
            <item.icon size={20} />
            <div>
              <strong>{item.name}</strong>
              <span>{item.detail}</span>
            </div>
            {profile === item.id && <Check size={15} />}
          </button>
        ))}
      </div>
      <label>
        Export format
        <select
          aria-label="Export format"
          value={format}
          onChange={(event) => setFormat(event.target.value)}
        >
          <option value="pdf">PDF · print-ready report</option>
          <option value="html">HTML · standalone report</option>
          <option value="xlsx">XLSX · charts and filterable evidence</option>
          <option value="csv">CSV · complete selected evidence</option>
          <option value="jsonl">JSONL · structured archive</option>
        </select>
      </label>
      <div className="w-info">
        <ShieldCheck size={16} />
        <p>
          Partial coverage, model identity, source references, review state and
          analytical definitions remain attached to the report.
        </p>
      </div>
      <Button disabled={!query || busy} onClick={() => void download()}>
        {busy ? (
          <LoaderCircle className="spin" size={14} />
        ) : (
          <ArrowDownToLine size={14} />
        )}
        {busy ? "Preparing report…" : `Download ${format.toUpperCase()}`}
      </Button>
    </>
  );
}

function FiltersPanel({ query, scope, onScope }: Props) {
  const fields = [
    {
      field: "authors",
      label: "Author / institution",
      facets: query?.facets.authors,
    },
    {
      field: "document_types",
      label: "Document type",
      facets: query?.facets.document_types,
    },
    {
      field: "speech_roles",
      label: "Speech role",
      facets: query?.facets.speech_roles,
    },
    {
      field: "batch_ids",
      label: "Import batch",
      facets: query?.facets.batches,
    },
  ] as const;
  return (
    <>
      <p className="w-intro">
        These filters apply to source lists, retained observations, analytical
        charts and exports together.
      </p>
      {fields.map(({ field, label, facets }) => (
        <label key={field}>
          {label}
          <select
            aria-label={label}
            value={scope[field][0] || ""}
            onChange={(event) =>
              onScope({
                [field]: event.target.value ? [event.target.value] : [],
              })
            }
          >
            <option value="">All {label.toLowerCase()}</option>
            {(facets || []).slice(0, 150).map((item) => (
              <option
                key={item.value || item.id || item.name}
                value={item.value || item.id || item.name}
              >
                {item.label || human(item.name)}
                {item.count !== undefined ? ` (${number(item.count)})` : ""}
              </option>
            ))}
          </select>
        </label>
      ))}
      <label>
        Review state
        <select
          aria-label="Review state filter"
          value={scope.review_status}
          onChange={(event) => onScope({ review_status: event.target.value })}
        >
          <option value="">All review states</option>
          {["unreviewed", "confirmed", "rejected", "revised"].map((value) => (
            <option key={value} value={value}>
              {human(value)}
            </option>
          ))}
        </select>
      </label>
      <label>
        Outcome
        <select
          aria-label="Outcome filter"
          value={scope.results[0] || ""}
          onChange={(event) =>
            onScope({ results: event.target.value ? [event.target.value] : [] })
          }
        >
          <option value="">All contextual outcomes</option>
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
        Stance
        <select
          aria-label="Stance filter"
          value={scope.stance}
          onChange={(event) => onScope({ stance: event.target.value })}
        >
          <option value="">All speaker stances</option>
          {[
            "supports",
            "opposes",
            "mixed",
            "neutral",
            "unclear",
            "not_applicable",
          ].map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
      </label>
      <label>
        Date basis
        <select
          aria-label="Date basis"
          value={scope.date_basis}
          onChange={(event) =>
            onScope({ date_basis: event.target.value as Scope["date_basis"] })
          }
        >
          <option value="publication">Publication date</option>
          <option value="ingestion">Import time</option>
        </select>
      </label>
      <div className="w-form-grid">
        <label>
          From date
          <input
            type="date"
            value={scope.from_date}
            onChange={(event) => onScope({ from_date: event.target.value })}
          />
        </label>
        <label>
          Through date
          <input
            type="date"
            value={scope.to_date}
            onChange={(event) => onScope({ to_date: event.target.value })}
          />
        </label>
      </div>
      <label>
        Timeline bins
        <select
          aria-label="Timeline bins"
          value={scope.bin}
          onChange={(event) => onScope({ bin: event.target.value })}
        >
          {["week", "month", "year"].map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
      </label>
      <label>
        Repeated content
        <select
          aria-label="Repeated content"
          value={scope.duplicate_policy}
          onChange={(event) =>
            onScope({
              duplicate_policy: event.target.value as Scope["duplicate_policy"],
            })
          }
        >
          <option value="exact_content">Count exact content once</option>
          <option value="sources">Count each source record</option>
        </select>
      </label>
      <div className="w-info">
        <Layers3 size={16} />
        <p>
          Unknown publication dates remain undated. An absent observation is not
          evidence that unchecked material contains no concerns.
        </p>
      </div>
    </>
  );
}

function LearningPanel({ project, candidate, runs, onNotice }: Props) {
  const [data, setData] = useState<Item | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [snapshot, setSnapshot] = useState("");
  const [baseline, setBaseline] = useState("");
  const [comparison, setComparison] = useState("");
  const [reports, setReports] = useState<Record<string, Item>>({});
  async function load() {
    try {
      setData(await request<Item>(`/v3/projects/${project}/learning`));
      setError("");
    } catch (reason) {
      setError(message(reason));
    }
  }
  useEffect(() => {
    setData(null);
    setReports({});
    void load();
  }, [project]);
  async function action(
    path: string,
    body: Item = {},
  ): Promise<Item | undefined> {
    setBusy(true);
    setError("");
    try {
      const result = await request<Item>(
        `/v3/projects/${project}/learning/${path}`,
        { method: "POST", body: JSON.stringify(body) },
      );
      await load();
      onNotice("Learning record updated. No model weights were changed.");
      return result;
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }
  const snapshots = list<Item>(data?.snapshots);
  const candidates = list<Item>(data?.candidates);
  const contextualRuns = runs.filter(
    (run) => run.config?.methodology_version === "3.0",
  );
  const reviewId = String(candidate?.review_event?.id || "");
  return (
    <>
      <p className="w-intro">
        Human-reviewed examples become frozen method candidates. Paired
        evaluation and explicit activation are required. Reviews never train
        model weights automatically.
      </p>
      <div className="w-learning-steps">
        <span>
          <ShieldCheck size={17} />
          Review
        </span>
        <ArrowRight size={13} />
        <span>
          <FlaskConical size={17} />
          Evaluate
        </span>
        <ArrowRight size={13} />
        <span>
          <Check size={17} />
          Activate
        </span>
      </div>
      {error && <ErrorMessage text={error} />}
      {!data && !error ? (
        <Spinner text="Loading reviewed examples…" />
      ) : (
        <>
          <div className="w-query-summary">
            <strong>
              {number(data?.review_count)} reviews ·{" "}
              {number(data?.approved_examples)} approved examples
            </strong>
            <span>
              {data?.active_snapshot_id
                ? `Active snapshot ${String(data.active_snapshot_id).slice(0, 12)}`
                : "No reviewed memory is active"}
            </span>
          </div>
          {candidate && (
            <div className="w-learning-candidate">
              <Badge>Selected observation</Badge>
              <h3>{candidate.title}</h3>
              <blockquote>“{candidate.quote.slice(0, 220)}”</blockquote>
              <Button
                disabled={
                  busy ||
                  !reviewId ||
                  !["confirmed", "revised"].includes(
                    candidate.review_status || "",
                  )
                }
                onClick={() => void action("examples", { review_id: reviewId })}
              >
                <Plus size={14} />
                Approve this reviewed example
              </Button>
              <p className="w-footnote">
                Save a confirmed or revised v3 human review first. Approval
                records the source, criterion and review provenance.
              </p>
            </div>
          )}
          <div className="w-field-heading">
            <h3>1. Freeze reviewed examples</h3>
          </div>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => {
              void action("snapshots").then((result) => {
                if (result?.id) setSnapshot(String(result.id));
              });
            }}
          >
            <Layers3 size={14} />
            Create immutable snapshot
          </Button>
          <p className="w-footnote">
            Freezes all currently approved project examples. An empty baseline
            snapshot is permitted.
          </p>
          <label>
            Snapshot for a new method candidate
            <select
              aria-label="Snapshot for a new method candidate"
              value={snapshot}
              onChange={(event) => setSnapshot(event.target.value)}
            >
              <option value="">Choose a frozen snapshot</option>
              {snapshots.map((item) => (
                <option key={String(item.id)} value={String(item.id)}>
                  {String(item.id).slice(0, 14)} · frozen examples
                </option>
              ))}
            </select>
          </label>
          <Button
            variant="outline"
            disabled={busy || !snapshot}
            onClick={() => void action("candidates", { snapshot_id: snapshot })}
          >
            <Plus size={14} />
            Create method candidate
          </Button>
          <div className="w-field-heading">
            <h3>2. Compare paired contextual runs</h3>
          </div>
          <div className="w-form-grid">
            <label>
              Baseline run
              <select
                aria-label="Baseline run"
                value={baseline}
                onChange={(event) => setBaseline(event.target.value)}
              >
                <option value="">Choose baseline</option>
                {contextualRuns.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.id.slice(0, 10)} · {human(run.status)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Candidate run
              <select
                aria-label="Candidate run"
                value={comparison}
                onChange={(event) => setComparison(event.target.value)}
              >
                <option value="">Choose candidate run</option>
                {contextualRuns.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.id.slice(0, 10)} · {human(run.status)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="w-footnote">
            Runs must use compatible source and method provenance. Agreement is
            not accuracy; the server blocks ineligible activation.
          </p>
          {candidates.map((item) => {
            const id = String(item.id);
            const report =
              reports[id] ||
              list<Item>(data?.reports).find(
                (value) => value.candidate_id === id,
              );
            const activeCandidate = record(data?.active).candidate_id === id;
            return (
              <article className="w-learning-snapshot" key={id}>
                <div>
                  <strong>Candidate {id.slice(0, 12)}</strong>
                  <Badge tone={activeCandidate ? "green" : ""}>
                    {activeCandidate ? "Active" : human(item.status || "draft")}
                  </Badge>
                </div>
                <p>Snapshot {String(item.snapshot_id || "").slice(0, 14)}</p>
                {report && (
                  <div
                    className={`w-info ${report.eligible_for_activation ? "" : "amber"}`}
                  >
                    <FlaskConical size={16} />
                    <div>
                      <strong>
                        {report.eligible_for_activation
                          ? "Eligible for explicit activation"
                          : "Activation blocked"}
                      </strong>
                      <p>
                        {number(report.paired_assessments)} paired assessments ·{" "}
                        {number(report.changed_results)} changed outcomes
                      </p>
                      {list<string>(report.blocking_reasons).map((reason) => (
                        <p key={reason}>{reason}</p>
                      ))}
                      <p>No interpretation-accuracy estimate is established.</p>
                    </div>
                  </div>
                )}
                <div>
                  <Button
                    variant="outline"
                    disabled={
                      busy ||
                      !baseline ||
                      !comparison ||
                      baseline === comparison
                    }
                    onClick={() => {
                      void action(`candidates/${id}/evaluate`, {
                        baseline_run_id: baseline,
                        candidate_run_id: comparison,
                      }).then((result) => {
                        if (result)
                          setReports((previous) => ({
                            ...previous,
                            [id]: result,
                          }));
                      });
                    }}
                  >
                    <FlaskConical size={13} />
                    Evaluate pair
                  </Button>
                  <Button
                    disabled={
                      busy ||
                      !report?.eligible_for_activation ||
                      !report.id ||
                      activeCandidate
                    }
                    onClick={() =>
                      void action(`candidates/${id}/activate`, {
                        report_id: report?.id,
                      })
                    }
                  >
                    <Check size={13} />
                    Activate candidate
                  </Button>
                </div>
              </article>
            );
          })}
          <div className="w-field-heading">
            <h3>3. Keep changes reversible</h3>
          </div>
          <Button
            variant="outline"
            disabled={busy || !data?.active_snapshot_id}
            onClick={() =>
              void action("rollback", { target_candidate_id: null })
            }
          >
            <RotateCcw size={14} />
            Roll back to no reviewed memory
          </Button>
        </>
      )}
    </>
  );
}
