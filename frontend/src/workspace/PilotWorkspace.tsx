import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Building2,
  Check,
  ChevronDown,
  FileCheck2,
  Globe2,
  LockKeyhole,
  Mail,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { request } from "../api";
import { list, message } from "./client";
import { Badge, Button, human, number } from "./ui";
import { permits, useAccessSession } from "./AccessGate";
import { PilotReportPanel } from "./PilotReportPanel";
import type { Context, Item, QueryResult } from "./types";

export type PilotBrief = {
  profile_id: string;
  question: string;
  audience: string;
  perspective: string;
  lens_ids: string[];
  company_policy: { title: string; version: string; text: string } | null;
  comparator: {
    question: string;
    inclusion_rules: string;
    topic?: string;
    date_start?: string;
    date_end?: string;
    [key: string]: unknown;
  } | null;
  revision?: number;
};
type Profile = {
  id: string;
  name?: string;
  description?: string;
  default_question?: string;
  lens_ids?: string[];
  default_lens_ids?: string[];
  limitations?: string[];
  report_sections?: string[];
};
type PrivacyPolicy = {
  raw_retention_days: number;
  derived_retention_days: number;
  allowed_inference_destinations: string[];
  identity_mode: "original" | "pseudonymized";
  allow_identity_reveal: boolean;
  journal_mode: string;
  allow_exports: boolean;
};
type PolicyRecord = {
  revision: number;
  policy: PrivacyPolicy;
  policy_configured?: boolean;
  enforcement?: Item;
};
type Props = {
  project: string;
  projectName: string;
  query: QueryResult | null;
  sourceContext: Context | null;
  onBrief: (brief: PilotBrief | null) => void;
  onAnalyze: () => void;
  onImport: () => void;
  onReports: () => void;
  onRefresh: () => void;
  onOpenSource: (id: string) => void;
  onErased: () => void;
  onSavedSource: (id: string) => void;
  onUnsavedChange: (value: boolean) => void;
};

const STARTERS: Record<
  string,
  {
    name: string;
    description: string;
    question: string;
    audience: string;
    context: string;
    lenses: string[];
  }
> = {
  company: {
    name: "Company",
    description: "Language patterns and declared policy review.",
    question:
      "How does our authorized company language vary across document types and time, and which passages merit review?",
    audience: "Company communications and governance team",
    context:
      "Assess organizational language patterns. Distinguish company assertions from quoted complaints and third-party statements; do not rank employees or infer their private beliefs.",
    lenses: ["discourse"],
  },
  public: {
    name: "Public",
    description: "Political framing, voices and matched comparisons.",
    question:
      "How do comparable sources frame this issue, and who expresses each position?",
    audience: "Research and public-affairs readers",
    context:
      "Separate source selection, framing and target-specific stance. An omission claim needs a declared comparator; reporting a claim does not imply endorsement.",
    lenses: ["political-framing"],
  },
  personal: {
    name: "Personal",
    description: "Selected writing, communicative acts and context.",
    question:
      "Which explicit requests, qualifications and ambiguities occur in my selected writing, and how might they be read in this context?",
    audience: "The author of the selected writing",
    context:
      "Assess wording in its declared relationship and context. Reader interpretations are possibilities, not diagnoses of emotion, personality or intent. Keep quoted history distinct from newly authored text.",
    lenses: ["language-norms"],
  },
};

function Facts({ value }: { value: Item }) {
  const values = Object.entries(value).filter(
    ([, item]) =>
      typeof item === "number" ||
      typeof item === "boolean" ||
      typeof item === "string",
  );
  if (!values.length) return null;
  return (
    <dl className="w-pilot-facts">
      {values.map(([key, item]) => (
        <div key={key}>
          <dt>{human(key)}</dt>
          <dd>
            {typeof item === "boolean"
              ? item
                ? "Yes"
                : "No"
              : typeof item === "number"
                ? new Intl.NumberFormat("en", {
                    maximumFractionDigits: 6,
                  }).format(item)
                : String(item)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function PilotWorkspace({
  project,
  projectName,
  query,
  sourceContext,
  onBrief,
  onAnalyze,
  onImport,
  onReports,
  onRefresh,
  onOpenSource,
  onErased,
  onSavedSource,
  onUnsavedChange,
}: Props) {
  const access = useAccessSession();
  const canWrite = permits(access, "write");
  const canAnalyze = permits(access, "analyze");
  const canAdmin = permits(access, "admin");
  const currentProject = useRef(project);
  currentProject.current = project;
  const currentQuery = useRef(query?.query_id);
  currentQuery.current = query?.query_id;
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [brief, setBrief] = useState<PilotBrief | null>(null);
  const [savedBrief, setSavedBrief] = useState<PilotBrief | null>(null);
  const [policyRecord, setPolicyRecord] = useState<PolicyRecord | null>(null);
  const [policy, setPolicy] = useState<PrivacyPolicy | null>(null);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState("brief");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const [estimate, setEstimate] = useState<Item | null>(null);
  const [previewInput, setPreviewInput] = useState("");
  const [preview, setPreview] = useState<Item | null>(null);
  const [signals, setSignals] = useState<Item | null>(null);
  const [inputRate, setInputRate] = useState("");
  const [outputRate, setOutputRate] = useState("");
  const [eraseName, setEraseName] = useState("");
  const [eraseJob, setEraseJob] = useState<Item | null>(null);
  const [draftText, setDraftText] = useState("");
  const [draftTitle, setDraftTitle] = useState("Selected writing");
  const eraseKey = useRef("");
  const clearedErase = useRef("");
  useEffect(() => {
    if (!project) return;
    let alive = true;
    setLoading(true);
    setBrief(null);
    setSavedBrief(null);
    setPolicy(null);
    setPolicyRecord(null);
    setEstimate(null);
    setSignals(null);
    setPreview(null);
    setPreviewInput("");
    setEraseName("");
    setDraftText("");
    setDraftTitle("Selected writing");
    setEraseJob(null);
    eraseKey.current = "";
    clearedErase.current = "";
    setBusy("");
    setError("");
    setNotice("");
    onBrief(null);
    void Promise.allSettled([
      request<Profile[]>("/pilot/profiles"),
      request<PilotBrief>(`/pilot/projects/${project}/brief`),
      request<PolicyRecord>(`/pilot/projects/${project}/privacy-policy`),
      canAdmin
        ? request<Item[]>(`/pilot/projects/${project}/erase-jobs`)
        : Promise.resolve([] as Item[]),
    ]).then(([profileResult, briefResult, policyResult, eraseResult]) => {
      if (!alive) return;
      const errors: string[] = [];
      const priorErasure =
        eraseResult.status === "fulfilled" ? eraseResult.value[0] : null;
      if (priorErasure) {
        setEraseJob(priorErasure);
        setOpen(true);
        setTab("privacy");
      }
      if (profileResult.status === "fulfilled")
        setProfiles(list<Profile>(profileResult.value));
      else errors.push(message(profileResult.reason));
      if (briefResult.status === "fulfilled") {
        setBrief(briefResult.value);
        setSavedBrief(briefResult.value);
        onBrief(briefResult.value);
      } else if (!priorErasure) errors.push(message(briefResult.reason));
      if (policyResult.status === "fulfilled") {
        setPolicyRecord(policyResult.value);
        setPolicy(policyResult.value.policy);
      } else if (!priorErasure) errors.push(message(policyResult.reason));
      if (eraseResult.status === "rejected")
        errors.push(message(eraseResult.reason));
      setError([...new Set(errors)].join(" · "));
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [project, reload, onBrief, canAdmin]);
  useEffect(() => {
    setEstimate(null);
    setSignals(null);
  }, [query?.query_id]);
  useEffect(() => {
    if (
      !eraseJob?.id ||
      ["completed", "failed"].includes(String(eraseJob.status))
    )
      return;
    let alive = true;
    const jobId = String(eraseJob.id);
    const timer = window.setInterval(() => {
      void request<Item>(`/pilot/erase-jobs/${jobId}`)
        .then((value) => {
          if (alive && currentProject.current === project) setEraseJob(value);
        })
        .catch((reason) => {
          if (alive) setError(message(reason));
        });
    }, 2000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [eraseJob?.id, eraseJob?.status, project]);
  useEffect(() => {
    if (
      eraseJob?.status !== "completed" ||
      !eraseJob.id ||
      clearedErase.current === eraseJob.id
    )
      return;
    clearedErase.current = String(eraseJob.id);
    setDraftText("");
    setDraftTitle("Selected writing");
    setPreviewInput("");
    setPreview(null);
    setSignals(null);
    setEstimate(null);
    setBrief(null);
    setSavedBrief(null);
    setPolicy(null);
    setPolicyRecord(null);
    onBrief(null);
    onErased();
  }, [eraseJob?.id, eraseJob?.status, onErased, onBrief]);

  const ids = query?.documents.items.map((document) => document.id) || [];
  const selected = brief?.profile_id || "";
  const dirty = !!brief && JSON.stringify(brief) !== JSON.stringify(savedBrief);
  useEffect(() => {
    onUnsavedChange(dirty);
    return () => onUnsavedChange(false);
  }, [dirty, onUnsavedChange]);
  const busyNow = !!busy;
  const scopeDescription = `First ${number(ids.length)} sources of the current query · ${number(query?.documents.total)} matching sources in total`;
  const policyDirty =
    !!policy && JSON.stringify(policy) !== JSON.stringify(policyRecord?.policy);
  const policyNeedsSave =
    policyDirty || policyRecord?.policy_configured === false;
  const profile = profiles.find((item) => item.id === selected);
  const signalSources = list<Item>(signals?.sources);
  const observations: Item[] = signalSources.flatMap((source) =>
    list<Item>(source.observations).map(
      (item): Item => ({
        ...item,
        document_id: source.document_id,
        source_title: source.title,
      }),
    ),
  );
  const cueCounts = signalSources.reduce<Record<string, number>>(
    (counts, source) => {
      for (const [key, value] of Object.entries(
        source.counts && typeof source.counts === "object" ? source.counts : {},
      ))
        if (typeof value === "number") counts[key] = (counts[key] || 0) + value;
      return counts;
    },
    {},
  );
  function edit(patch: Partial<PilotBrief>) {
    if (brief) setBrief({ ...brief, ...patch });
  }
  function selectProfile(id: string) {
    const defaults = STARTERS[id];
    const server = profiles.find((item) => item.id === id);
    if (!brief || !defaults || !server || !canWrite) return;
    setBrief({
      ...brief,
      profile_id: id,
      question: server.default_question || defaults.question,
      audience: defaults.audience,
      perspective: defaults.context,
      lens_ids: server.lens_ids || server.default_lens_ids || defaults.lenses,
      comparator: null,
      company_policy: null,
    });
    setEstimate(null);
    setSignals(null);
    setNotice("Profile selected. Save the inquiry brief before analysis.");
    setOpen(true);
    setTab("brief");
  }
  async function saveBrief(continueToAnalysis = false) {
    if (!brief) return;
    if (continueToAnalysis && !dirty && savedBrief?.revision) {
      onAnalyze();
      return;
    }
    setBusy("brief");
    setError("");
    setNotice("");
    try {
      const next = await request<PilotBrief>(
        `/pilot/projects/${project}/brief`,
        {
          method: "PUT",
          body: JSON.stringify({
            profile_id: brief.profile_id,
            question: brief.question,
            audience: brief.audience,
            perspective: brief.perspective,
            lens_ids: brief.lens_ids,
            company_policy: brief.company_policy,
            comparator: brief.comparator,
            expected_revision: savedBrief?.revision,
          }),
        },
      );
      if (currentProject.current !== project) return;
      setSavedBrief(next);
      setBrief(next);
      onBrief(next);
      setNotice("Inquiry brief saved. Interpretations still require review.");
      if (continueToAnalysis) onAnalyze();
    } catch (reason) {
      if (currentProject.current === project) setError(message(reason));
    } finally {
      if (currentProject.current === project) setBusy("");
    }
  }
  async function savePolicy() {
    if (!policy || !policyRecord) return;
    setBusy("policy");
    setError("");
    setNotice("");
    try {
      const next = await request<PolicyRecord>(
        `/pilot/projects/${project}/privacy-policy`,
        {
          method: "PUT",
          body: JSON.stringify({
            policy,
            expected_revision: policyRecord.revision,
          }),
        },
      );
      if (currentProject.current !== project) return;
      setPolicyRecord(next);
      setPolicy(next.policy);
      setNotice(
        "Workspace privacy policy saved. See the enforcement record below for its active controls.",
      );
    } catch (reason) {
      if (currentProject.current === project) setError(message(reason));
    } finally {
      if (currentProject.current === project) setBusy("");
    }
  }
  async function getEstimate() {
    if (!ids.length) return;
    const hasRates = inputRate.trim() !== "" || outputRate.trim() !== "";
    if (
      hasRates &&
      (!inputRate.trim() ||
        !outputRate.trim() ||
        !Number.isFinite(Number(inputRate)) ||
        !Number.isFinite(Number(outputRate)) ||
        Number(inputRate) < 0 ||
        Number(outputRate) < 0)
    ) {
      setError(
        "Enter both finite, non-negative token rates, or leave both blank for a volume-only estimate.",
      );
      return;
    }
    setBusy("estimate");
    setError("");
    try {
      const scopeId = currentQuery.current;
      const value = await request<Item>(`/pilot/projects/${project}/estimate`, {
        method: "POST",
        body: JSON.stringify({
          document_ids: ids,
          ...(hasRates
            ? {
                prices: {
                  input_usd_per_million: Number(inputRate),
                  output_usd_per_million: Number(outputRate),
                },
              }
            : {}),
        }),
      });
      if (
        currentProject.current === project &&
        currentQuery.current === scopeId
      )
        setEstimate(value);
    } catch (reason) {
      if (currentProject.current === project) setError(message(reason));
    } finally {
      if (currentProject.current === project) setBusy("");
    }
  }
  async function previewText() {
    setBusy("preview");
    setError("");
    try {
      const value = await request<Item>(
        `/pilot/projects/${project}/privacy-preview`,
        {
          method: "POST",
          body: JSON.stringify({ text: previewInput }),
        },
      );
      if (currentProject.current === project) setPreview(value);
    } catch (reason) {
      if (currentProject.current === project) setError(message(reason));
    } finally {
      if (currentProject.current === project) setBusy("");
    }
  }
  async function transformSources() {
    if (
      !ids.length ||
      !window.confirm(
        `Create pseudonymized source versions for the first ${ids.length} sources in this query? Originals remain stored. This does not silently replace the source selection of an existing run.`,
      )
    )
      return;
    setBusy("transform");
    setError("");
    setNotice("");
    try {
      const value = await request<Item>(
        `/pilot/projects/${project}/privacy/transforms`,
        {
          method: "POST",
          body: JSON.stringify({ document_ids: ids }),
        },
      );
      if (currentProject.current !== project) return;
      const created = list(value.created_documents);
      setNotice(
        `${created.length ? number(created.length) : "New"} pseudonymized source versions created. Inspect them in the source collection before selecting a new analysis scope. Originals remain in this workspace.`,
      );
      onRefresh();
    } catch (reason) {
      if (currentProject.current === project) setError(message(reason));
    } finally {
      if (currentProject.current === project) setBusy("");
    }
  }
  async function scanSignals() {
    setBusy("signals");
    setError("");
    try {
      const scopeId = currentQuery.current;
      const value = await request<Item>(`/pilot/projects/${project}/signals`, {
        method: "POST",
        body: JSON.stringify({ document_ids: ids }),
      });
      if (
        currentProject.current === project &&
        currentQuery.current === scopeId
      )
        setSignals(value);
    } catch (reason) {
      if (currentProject.current === project) setError(message(reason));
    } finally {
      if (currentProject.current === project) setBusy("");
    }
  }
  async function eraseProject() {
    if (
      !canAdmin ||
      eraseName !== projectName ||
      !projectName ||
      !window.confirm(
        `Permanently erase the live application data for “${projectName}”? Its sources and derived reports will be removed. Backups, provider copies and downloaded copies are outside this action.`,
      )
    )
      return;
    setBusy("erase");
    setError("");
    if (!eraseKey.current) eraseKey.current = crypto.randomUUID();
    try {
      const value = await request<Item>(
        `/pilot/projects/${project}/erase-jobs`,
        {
          method: "POST",
          body: JSON.stringify({
            confirm_project_name: eraseName,
            idempotency_key: eraseKey.current,
          }),
        },
      );
      if (currentProject.current === project) {
        setEraseJob(value);
        setNotice(
          "Project erasure requested. New processing is blocked while live data is removed.",
        );
      }
    } catch (reason) {
      if (currentProject.current === project) setError(message(reason));
    } finally {
      if (currentProject.current === project) setBusy("");
    }
  }
  async function resumeErase() {
    if (!eraseJob?.id) return;
    setBusy("erase");
    setError("");
    try {
      const value = await request<Item>(
        `/pilot/erase-jobs/${String(eraseJob.id)}/resume`,
        { method: "POST" },
      );
      if (currentProject.current === project) setEraseJob(value);
    } catch (reason) {
      if (currentProject.current === project) setError(message(reason));
    } finally {
      if (currentProject.current === project) setBusy("");
    }
  }
  async function saveDraft() {
    if (!draftText.trim()) return;
    setBusy("draft");
    setError("");
    try {
      const value = await request<Item>(`/pilot/projects/${project}/drafts`, {
        method: "POST",
        body: JSON.stringify({ text: draftText, title: draftTitle }),
      });
      if (currentProject.current !== project) return;
      setDraftText("");
      setNotice(
        "Selected writing saved as a retained source. No analysis or model request was started.",
      );
      onSavedSource(String(value.id));
    } catch (reason) {
      if (currentProject.current === project) setError(message(reason));
    } finally {
      if (currentProject.current === project) setBusy("");
    }
  }

  return (
    <section
      className={`w-pilot ${open ? "is-open" : ""}`}
      aria-label="Guided pilot workbench"
    >
      <div className="w-pilot-heading">
        <div>
          <span className="w-eyebrow">
            ONE EVIDENCE ENGINE · THREE INQUIRIES
          </span>
          <h2>Choose the question that matters.</h2>
        </div>
        <Badge tone="amber">Interpretation validation pending</Badge>
      </div>
      <div className="w-pilot-profiles" aria-label="Service profiles">
        {Object.entries(STARTERS).map(([id, defaults]) => {
          const Icon =
            id === "company" ? Building2 : id === "public" ? Globe2 : Mail;
          const personalBoundary =
            id === "personal" &&
            access?.mode === "managed" &&
            access.workspace_kind !== "personal";
          return (
            <button
              key={id}
              aria-pressed={selected === id}
              title={
                personalBoundary
                  ? "Personal review requires a dedicated personal deployment, separate from organizational administrators."
                  : undefined
              }
              disabled={
                personalBoundary ||
                loading ||
                busyNow ||
                !brief ||
                !profiles.some((item) => item.id === id) ||
                !canWrite
              }
              onClick={() => selectProfile(id)}
            >
              <Icon size={18} />
              <span>
                <strong>DOXA {defaults.name}</strong>
                <small>{defaults.description}</small>
              </span>
              {selected === id && <Check size={14} />}
            </button>
          );
        })}
      </div>
      {access?.mode === "managed" &&
        access.workspace_kind === "organization" && (
          <p className="w-pilot-note">
            Personal review uses a separate personal deployment. Organization
            administrator access does not become private by choosing an analysis
            profile.
          </p>
        )}
      <div className="w-pilot-summary">
        <span>
          <FileCheck2 size={13} />{" "}
          {eraseJob
            ? `Project erasure · ${human(eraseJob.status)}`
            : savedBrief
              ? `${STARTERS[savedBrief.profile_id]?.name || human(savedBrief.profile_id)} brief · revision ${savedBrief.revision ?? "not supplied"}`
              : loading
                ? "Loading inquiry settings…"
                : "Inquiry settings unavailable"}
        </span>
        <button
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-controls="pilot-settings"
        >
          <span>
            {open ? "Hide settings" : "Question, privacy & estimates"}
          </span>
          <ChevronDown size={14} />
        </button>
      </div>
      {open && (
        <div id="pilot-settings" className="w-pilot-settings">
          <div
            className="w-pilot-tabs"
            role="group"
            aria-label="Pilot settings"
          >
            {[
              ["brief", "Inquiry brief"],
              ["privacy", "Privacy controls"],
              ["scope", "Scope & estimate"],
            ].map(([id, label]) => (
              <button
                key={id}
                id={`pilot-tab-${id}`}
                aria-pressed={tab === id}
                aria-controls={`pilot-panel-${id}`}
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
          </div>
          {tab === "brief" && (
            <div id="pilot-panel-brief" aria-labelledby="pilot-tab-brief">
              {brief && (
                <>
                  <label>
                    Inquiry question
                    <textarea
                      value={brief.question}
                      onChange={(event) =>
                        edit({ question: event.target.value })
                      }
                      rows={3}
                      maxLength={4000}
                      disabled={!canWrite || busyNow}
                    />
                  </label>
                  <div className="w-pilot-grid">
                    <label>
                      Intended report audience
                      <input
                        value={brief.audience}
                        onChange={(event) =>
                          edit({ audience: event.target.value })
                        }
                        maxLength={500}
                        disabled={!canWrite || busyNow}
                      />
                    </label>
                    <div className="w-pilot-fixed-field">
                      <span>Review requirement</span>
                      <strong>Expert sign-off for delivered claims</strong>
                      <small>Saving this brief does not record sign-off.</small>
                    </div>
                  </div>
                  <label>
                    Communication context and analytical perspective
                    <textarea
                      value={brief.perspective}
                      onChange={(event) =>
                        edit({ perspective: event.target.value })
                      }
                      rows={3}
                      maxLength={2000}
                      disabled={!canWrite || busyNow}
                    />
                  </label>
                  {brief.profile_id === "company" && (
                    <fieldset className="w-pilot-method-field">
                      <legend>Customer policy for review</legend>
                      <label className="w-pilot-check">
                        <input
                          type="checkbox"
                          checked={!!brief.company_policy}
                          disabled={!canWrite || busyNow}
                          onChange={(event) =>
                            edit({
                              company_policy: event.target.checked
                                ? { title: "", version: "", text: "" }
                                : null,
                            })
                          }
                        />
                        Record a supplied, versioned company policy for review
                      </label>
                      {brief.company_policy && (
                        <>
                          <div className="w-pilot-grid">
                            <label>
                              Policy title
                              <input
                                maxLength={200}
                                value={brief.company_policy.title}
                                disabled={!canWrite || busyNow}
                                onChange={(event) =>
                                  edit({
                                    company_policy: {
                                      ...brief.company_policy!,
                                      title: event.target.value,
                                    },
                                  })
                                }
                              />
                            </label>
                            <label>
                              Policy version
                              <input
                                maxLength={100}
                                value={brief.company_policy.version}
                                disabled={!canWrite || busyNow}
                                onChange={(event) =>
                                  edit({
                                    company_policy: {
                                      ...brief.company_policy!,
                                      version: event.target.value,
                                    },
                                  })
                                }
                              />
                            </label>
                          </div>
                          <label>
                            Supplied policy text
                            <textarea
                              rows={4}
                              maxLength={20000}
                              value={brief.company_policy.text}
                              disabled={!canWrite || busyNow}
                              onChange={(event) =>
                                edit({
                                  company_policy: {
                                    ...brief.company_policy!,
                                    text: event.target.value,
                                  },
                                })
                              }
                            />
                          </label>
                        </>
                      )}
                    </fieldset>
                  )}
                  <fieldset className="w-pilot-method-field">
                    <legend>Comparison design</legend>
                    <label className="w-pilot-check">
                      <input
                        type="checkbox"
                        checked={!!brief.comparator}
                        disabled={!canWrite || busyNow}
                        onChange={(event) =>
                          edit({
                            comparator: event.target.checked
                              ? {
                                  question: brief.question,
                                  inclusion_rules: "",
                                }
                              : null,
                          })
                        }
                      />
                      Declare a matched comparison
                    </label>
                    {brief.comparator && (
                      <>
                        <label>
                          Comparison question
                          <textarea
                            rows={2}
                            maxLength={4000}
                            value={brief.comparator.question}
                            disabled={!canWrite || busyNow}
                            onChange={(event) =>
                              edit({
                                comparator: {
                                  ...brief.comparator!,
                                  question: event.target.value,
                                },
                              })
                            }
                          />
                        </label>
                        <label>
                          Source inclusion and matching rules
                          <textarea
                            rows={3}
                            maxLength={4000}
                            value={brief.comparator.inclusion_rules}
                            disabled={!canWrite || busyNow}
                            placeholder="Specify comparable topics, events, source groups, genres and opportunities for coverage."
                            onChange={(event) =>
                              edit({
                                comparator: {
                                  ...brief.comparator!,
                                  inclusion_rules: event.target.value,
                                },
                              })
                            }
                          />
                        </label>
                        <div className="w-pilot-grid">
                          <label>
                            Comparison start date
                            <input
                              type="date"
                              value={brief.comparator.date_start || ""}
                              disabled={!canWrite || busyNow}
                              onChange={(event) =>
                                edit({
                                  comparator: {
                                    ...brief.comparator!,
                                    date_start: event.target.value,
                                  },
                                })
                              }
                            />
                          </label>
                          <label>
                            Comparison end date
                            <input
                              type="date"
                              value={brief.comparator.date_end || ""}
                              disabled={!canWrite || busyNow}
                              onChange={(event) =>
                                edit({
                                  comparator: {
                                    ...brief.comparator!,
                                    date_end: event.target.value,
                                  },
                                })
                              }
                            />
                          </label>
                        </div>
                      </>
                    )}
                  </fieldset>
                  <div className="w-pilot-actions">
                    <Button
                      variant="outline"
                      onClick={() => void saveBrief()}
                      disabled={!canWrite || busyNow || !dirty}
                    >
                      {busy === "brief" ? "Saving…" : "Save inquiry brief"}
                    </Button>
                    <Button
                      onClick={() => void saveBrief(true)}
                      disabled={
                        !canWrite || !canAnalyze || busyNow || !ids.length
                      }
                    >
                      Continue to analysis <ArrowRight size={14} />
                    </Button>
                    <span>{dirty ? "Unsaved changes" : "Saved settings"}</span>
                  </div>
                  <p className="w-pilot-note">
                    Your brief records a declared method. Expert review and
                    independent validation are separate from saving a brief or
                    exporting a report. Tone observations do not reveal a
                    person's emotions or beliefs.
                  </p>
                  {brief.profile_id === "personal" && (
                    <p className="w-pilot-note">
                      Personal is an analysis profile, not a separate security
                      boundary. Use a dedicated personal deployment for writing
                      that must remain separate from organization
                      administrators.
                    </p>
                  )}
                  {brief.profile_id === "personal" && (
                    <details className="w-pilot-details w-pilot-preview">
                      <summary>Paste selected writing as a source</summary>
                      <p>
                        Writing is retained in this project until erased; this
                        is not transient mode. Save only material you are
                        authorized to store. Saving does not start analysis or
                        contact a model.
                      </p>
                      <label>
                        Selected writing title
                        <input
                          maxLength={200}
                          value={draftTitle}
                          disabled={!canWrite || busyNow}
                          onChange={(event) =>
                            setDraftTitle(event.target.value)
                          }
                        />
                      </label>
                      <label>
                        Selected writing text
                        <textarea
                          rows={5}
                          maxLength={100000}
                          value={draftText}
                          disabled={!canWrite || busyNow}
                          onChange={(event) => setDraftText(event.target.value)}
                          placeholder="Paste a draft, an authorized message or a selected conversation excerpt."
                        />
                      </label>
                      <Button
                        variant="outline"
                        disabled={
                          !canWrite ||
                          busyNow ||
                          !draftText.trim() ||
                          !draftTitle.trim()
                        }
                        onClick={() => void saveDraft()}
                      >
                        {busy === "draft"
                          ? "Saving selected writing…"
                          : "Save selected writing as source"}
                      </Button>
                    </details>
                  )}
                  {!!profile?.report_sections?.length && (
                    <details className="w-pilot-details">
                      <summary>What belongs in this service report</summary>
                      <ul>
                        {profile.report_sections.map((section) => (
                          <li key={section}>{section}</li>
                        ))}
                      </ul>
                      <Button
                        variant="outline"
                        onClick={onReports}
                        disabled={!query || !permits(access, "export")}
                      >
                        Open evidence reports
                      </Button>
                    </details>
                  )}
                </>
              )}
            </div>
          )}
          {tab === "privacy" && (
            <div id="pilot-panel-privacy" aria-labelledby="pilot-tab-privacy">
              {!eraseJob && (
                <>
                  <p className="w-pilot-note">
                    <LockKeyhole size={15} /> Server-side preview happens after
                    upload to this workspace. Use a local DOXA installation to
                    mask text before sending it to a cloud workspace. Automated
                    detection can miss identifiers; reversible substitutions are
                    pseudonymization.
                  </p>
                  {policy && (
                    <>
                      {access?.mode === "local" &&
                        policyRecord?.policy_configured === false && (
                          <p className="w-pilot-note">
                            Profiled analyses use these local-only defaults.
                            Save to activate or edit this policy for the whole
                            project. Existing unprofiled local workflows retain
                            their prior settings until a policy is saved.
                          </p>
                        )}
                      <div className="w-pilot-grid">
                        <label>
                          Raw-source retention target · days
                          <input
                            type="number"
                            min="1"
                            max="3650"
                            value={policy.raw_retention_days}
                            disabled={!canAdmin || busyNow}
                            onChange={(event) =>
                              setPolicy({
                                ...policy,
                                raw_retention_days: Number(event.target.value),
                              })
                            }
                          />
                        </label>
                        <label>
                          Derived-data retention target · days
                          <input
                            type="number"
                            min="1"
                            max="3650"
                            value={policy.derived_retention_days}
                            disabled={!canAdmin || busyNow}
                            onChange={(event) =>
                              setPolicy({
                                ...policy,
                                derived_retention_days: Number(
                                  event.target.value,
                                ),
                              })
                            }
                          />
                        </label>
                        <label>
                          Analysis identity policy
                          <select
                            value={policy.identity_mode}
                            disabled={!canAdmin || busyNow}
                            onChange={(event) =>
                              setPolicy({
                                ...policy,
                                identity_mode: event.target
                                  .value as PrivacyPolicy["identity_mode"],
                              })
                            }
                          >
                            <option value="original">
                              Authorized original text
                            </option>
                            <option value="pseudonymized">
                              Require pseudonymized source versions
                            </option>
                          </select>
                        </label>
                        <label>
                          Report exports
                          <select
                            value={policy.allow_exports ? "yes" : "no"}
                            disabled={!canAdmin || busyNow}
                            onChange={(event) =>
                              setPolicy({
                                ...policy,
                                allow_exports: event.target.value === "yes",
                              })
                            }
                          >
                            <option value="yes">
                              Allowed with export permission
                            </option>
                            <option value="no">
                              Disabled by this workspace policy
                            </option>
                          </select>
                        </label>
                      </div>
                      <fieldset className="w-pilot-destinations">
                        <legend>Permitted inference destinations</legend>
                        {[
                          ["local", "Local model"],
                          ["cloudflare", "Cloudflare Workers AI"],
                          ["openai", "Configured compatible endpoint"],
                          ["runpod", "Configured Runpod endpoint"],
                        ].map(([id, label]) => (
                          <label key={id}>
                            <input
                              type="checkbox"
                              disabled={!canAdmin || busyNow}
                              checked={policy.allowed_inference_destinations.includes(
                                id,
                              )}
                              onChange={(event) =>
                                setPolicy({
                                  ...policy,
                                  allowed_inference_destinations: event.target
                                    .checked
                                    ? [
                                        ...policy.allowed_inference_destinations,
                                        id,
                                      ]
                                    : policy.allowed_inference_destinations.filter(
                                        (item) => item !== id,
                                      ),
                                })
                              }
                            />
                            {label}
                          </label>
                        ))}
                      </fieldset>
                      <Button
                        variant="outline"
                        disabled={!canAdmin || busyNow || !policyNeedsSave}
                        onClick={() => void savePolicy()}
                      >
                        {busy === "policy"
                          ? "Saving policy…"
                          : "Save privacy policy"}
                      </Button>
                      <p className="w-pilot-note">
                        Retention targets are recorded for operator review. This
                        release does not automatically erase data when those
                        dates pass. Use the explicit project erasure workflow
                        and your operator's backup policy.
                      </p>
                      {!!policyRecord?.enforcement && (
                        <details className="w-pilot-details">
                          <summary>
                            Active enforcement and lifecycle limits
                          </summary>
                          <Facts value={policyRecord.enforcement} />
                          <pre>
                            {JSON.stringify(policyRecord.enforcement, null, 2)}
                          </pre>
                        </details>
                      )}
                    </>
                  )}
                  <div className="w-pilot-preview">
                    <h3>Inspect a text substitution preview</h3>
                    <label>
                      Text to inspect
                      <textarea
                        value={previewInput}
                        onChange={(event) => {
                          setPreviewInput(event.target.value);
                          setPreview(null);
                        }}
                        rows={4}
                        maxLength={100000}
                        placeholder="Paste an authorized excerpt. This preview does not alter a stored source or start inference."
                      />
                    </label>
                    <Button
                      variant="outline"
                      onClick={() => void previewText()}
                      disabled={!previewInput.trim() || busyNow}
                    >
                      {busy === "preview"
                        ? "Inspecting…"
                        : "Preview pseudonymization"}
                    </Button>
                    {preview && (
                      <div className="w-pilot-result" aria-live="polite">
                        <h4>Preview output · source text remains unchanged</h4>
                        <pre>
                          {String(
                            preview.transformed_text ??
                              preview.text ??
                              "The server did not return transformed text.",
                          )}
                        </pre>
                        {list<string>(preview.limitations).map((limitation) => (
                          <p key={limitation}>{limitation}</p>
                        ))}
                        <details>
                          <summary>
                            Detector provenance and span mapping
                          </summary>
                          <pre>{JSON.stringify(preview, null, 2)}</pre>
                        </details>
                      </div>
                    )}
                    <p className="w-pilot-note">
                      Creating stored transformed versions requires a separately
                      configured encrypted identity vault. Originals remain
                      readable to users authorized for this project; reveal
                      permission protects the separate identity map, not access
                      to those originals. Inspect and explicitly select the new
                      versions before analysis.
                    </p>
                    <Button
                      variant="outline"
                      onClick={() => void transformSources()}
                      disabled={
                        !canWrite || busyNow || !ids.length || ids.length > 100
                      }
                    >
                      Create pseudonymized versions · query preview
                    </Button>
                  </div>
                </>
              )}
              {canAdmin && (
                <details className="w-pilot-erasure" open={!!eraseJob}>
                  <summary>Erase this project's live application data</summary>
                  <p>
                    Remove this project's sources and derived records, including
                    reports and identity maps. This is irreversible for live
                    data. Backups, provider retention, physical-media remanence
                    and already-downloaded copies are outside this action.
                  </p>
                  {!eraseJob && (
                    <>
                      <label>
                        Confirm project name
                        <input
                          value={eraseName}
                          onChange={(event) => setEraseName(event.target.value)}
                          placeholder={projectName}
                          autoComplete="off"
                        />
                      </label>
                      <Button
                        variant="outline"
                        onClick={() => void eraseProject()}
                        disabled={
                          busyNow || !projectName || eraseName !== projectName
                        }
                      >
                        Erase live project data
                      </Button>
                    </>
                  )}
                  {eraseJob && (
                    <div className="w-pilot-result" aria-live="polite">
                      <Facts
                        value={{
                          status: eraseJob.status,
                          phase: eraseJob.phase,
                        }}
                      />
                      <p>{String(eraseJob.notice || "")}</p>
                      {!["completed"].includes(String(eraseJob.status)) && (
                        <Button
                          variant="outline"
                          onClick={() => void resumeErase()}
                          disabled={busyNow}
                        >
                          Resume erasure after active work drains
                        </Button>
                      )}
                      <details>
                        <summary>Erasure progress and receipt</summary>
                        <pre>{JSON.stringify(eraseJob, null, 2)}</pre>
                      </details>
                      {eraseJob.status === "completed" && (
                        <Button
                          variant="outline"
                          onClick={() => window.location.reload()}
                        >
                          Reload remaining workspaces
                        </Button>
                      )}
                    </div>
                  )}
                </details>
              )}
            </div>
          )}
          {tab === "scope" && (
            <div id="pilot-panel-scope" aria-labelledby="pilot-tab-scope">
              <p className="w-pilot-scope-label">
                {scopeDescription}. These bounded preview tools use the first
                query page's source IDs. Narrow the query or focus a source to
                change that selection. Full-corpus analysis and persistent
                evidence reports remain available through the main workbench.
              </p>
              <div className="w-pilot-actions">
                <Button
                  variant="outline"
                  onClick={onImport}
                  disabled={!canWrite}
                >
                  Import authorized sources
                </Button>
                <Button
                  variant="outline"
                  onClick={onReports}
                  disabled={!query || !permits(access, "export")}
                >
                  Open evidence reports
                </Button>
              </div>
              <PilotReportPanel
                project={project}
                documentIds={ids}
                runId={query?.run?.id}
                briefRevision={savedBrief?.revision}
                sourceContext={sourceContext}
                disabled={dirty || !!eraseJob}
                exportsAllowed={policy?.allow_exports !== false}
                onOpenSource={onOpenSource}
              />
              <div className="w-pilot-grid">
                <label>
                  Input price · USD / million tokens
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={inputRate}
                    onChange={(event) => setInputRate(event.target.value)}
                    placeholder="Optional"
                  />
                </label>
                <label>
                  Output price · USD / million tokens
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={outputRate}
                    onChange={(event) => setOutputRate(event.target.value)}
                    placeholder="Optional"
                  />
                </label>
              </div>
              <Button
                variant="outline"
                onClick={() => void getEstimate()}
                disabled={busyNow || !ids.length || ids.length > 100 || dirty}
              >
                {busy === "estimate" ? "Estimating…" : "Estimate query preview"}
              </Button>
              <p className="w-pilot-note">
                A planning estimate is not a customer quote, vendor invoice or
                reserved budget. Models, repeated context, output and retries
                affect actual cost. Save brief changes before estimating.
              </p>
              {estimate && (
                <div className="w-pilot-result" aria-live="polite">
                  <h3>Observed scope and planning estimate</h3>
                  <div className="w-pilot-grid">
                    <div>
                      <h4>Supplied sources</h4>
                      <Facts value={(estimate.source as Item) || {}} />
                    </div>
                    <div>
                      <h4>Requested work</h4>
                      <Facts value={(estimate.work as Item) || {}} />
                    </div>
                    <div>
                      <h4>Token scenarios · unmeasured heuristic</h4>
                      <Facts value={(estimate.tokens as Item) || {}} />
                    </div>
                    <div>
                      <h4>Inference cost scenarios</h4>
                      {estimate.modeled_cost ? (
                        <Facts value={estimate.modeled_cost as Item} />
                      ) : (
                        <p>
                          No token rates supplied. No dollar cost was
                          calculated.
                        </p>
                      )}
                    </div>
                  </div>
                  <details>
                    <summary>Assumptions and full estimate record</summary>
                    <pre>{JSON.stringify(estimate, null, 2)}</pre>
                  </details>
                </div>
              )}
              <div className="w-pilot-preview">
                <h3>Find explicit communication cues</h3>
                <p>
                  Inspect deterministic patterns such as requests and
                  qualifications in the first query page. These are lexical
                  cues, not validated tone or bias judgments.
                </p>
                <Button
                  variant="outline"
                  onClick={() => void scanSignals()}
                  disabled={busyNow || !ids.length || ids.length > 100}
                >
                  {busy === "signals"
                    ? "Inspecting source cues…"
                    : "Inspect communication cues"}
                </Button>
                {signals && (
                  <div className="w-pilot-result" aria-live="polite">
                    <Facts value={signals} />
                    <h4>Eligible authored cue matches</h4>
                    <div className="w-pilot-cue-bars">
                      {Object.entries(cueCounts).map(([kind, count]) => (
                        <div key={kind}>
                          <span>{human(kind)}</span>
                          <meter
                            min={0}
                            max={Math.max(1, ...Object.values(cueCounts))}
                            value={count}
                            aria-label={`${human(kind)}: ${count} eligible cue matches`}
                          />
                          <strong>{number(count)}</strong>
                        </div>
                      ))}
                    </div>
                    <p>
                      Showing {Math.min(40, observations.length)} of{" "}
                      {number(observations.length)} returned matches. Quoted or
                      forwarded matches remain context; eligibility is shown per
                      case. No match is not a negative tone or bias judgment.
                    </p>
                    {observations.slice(0, 40).map((item, index) => (
                      <article
                        key={String(item.id || index)}
                        className="w-pilot-signal"
                      >
                        <strong>{human(item.label ?? item.kind)}</strong>
                        <Badge>
                          {item.count_eligible
                            ? "Eligible authored cue"
                            : "Context · excluded from authored count"}
                        </Badge>
                        {typeof item.quote === "string" && (
                          <blockquote>{item.quote}</blockquote>
                        )}
                        <p>{String(item.source_title || "")}</p>
                        {typeof item.document_id === "string" && (
                          <button
                            onClick={() =>
                              onOpenSource(String(item.document_id))
                            }
                          >
                            Open source context <ArrowRight size={12} />
                          </button>
                        )}
                      </article>
                    ))}
                    <details>
                      <summary>Complete cue results and coverage</summary>
                      <pre>{JSON.stringify(signals, null, 2)}</pre>
                    </details>
                  </div>
                )}
              </div>
            </div>
          )}
          <div className="w-pilot-review-note">
            <ShieldCheck size={15} />
            <span>
              Reviewed observations: {number(query?.metrics.reviewed)} in this
              frozen research scope. This count is not expert sign-off of a
              client report or independent validation.
            </span>
          </div>
        </div>
      )}
      {error && (
        <div className="w-pilot-error" role="alert">
          <span>Pilot settings: {error}</span>
          <button onClick={() => setReload((value) => value + 1)}>
            <RefreshCw size={13} /> Reload settings
          </button>
        </div>
      )}
      {notice && (
        <p className="w-pilot-notice" role="status">
          {notice}
        </p>
      )}
    </section>
  );
}
