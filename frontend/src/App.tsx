import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronDown,
  CircleHelp,
  Copy,
  Database,
  ExternalLink,
  FileText,
  Filter,
  FlaskConical,
  FolderOpen,
  Globe2,
  Layers3,
  LoaderCircle,
  PanelLeft,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import { pendingUploadImport, request, uploadInParts } from "./api";
import { BiasResults, PublishedStudy } from "./workspace/BiasResults";
import { Analytics } from "./workspace/Analytics";
import { ScorecardPanel } from "./workspace/Scorecard";
import { Inspector } from "./workspace/Inspector";
import { DoxaCompanion } from "./workspace/DoxaCompanion";
import { DrawerBody } from "./workspace/Drawers";
import { useCasebook } from "./workspace/Casebooks";
import { CoverageSummary } from "./workspace/Coverage";
import {
  AccessControls,
  AccessGate,
  permits,
  useAccessSession,
} from "./workspace/AccessGate";
import { PilotWorkspace } from "./workspace/PilotWorkspace";
import { RefinementPanel } from "./workspace/RefinementPanel";
import type { PilotBrief } from "./workspace/PilotWorkspace";
import {
  contextFor,
  list,
  message,
  models as loadModels,
  queryPage,
  record,
  restoreQuery,
  unitContext,
  workspaceQuery,
} from "./workspace/client";
import {
  Badge,
  Button,
  date,
  Drawer,
  Empty,
  ErrorMessage,
  human,
  IconButton,
  number,
  Pager,
  percentage,
  Spinner,
  VirtualList,
} from "./workspace/ui";
import { EMPTY_METRICS, EMPTY_SCOPE } from "./workspace/types";
import type {
  AnalysisRun,
  Context,
  Drawer as DrawerName,
  Item,
  Lens,
  ModelProfile,
  Notice,
  Observation,
  Page,
  Project,
  QueryResult,
  Scope,
  SourceDocument,
} from "./workspace/types";

const DRAWER_TITLES: Record<string, string> = {
  import: "Bring your sources together",
  analyze: "Define a contextual inquiry",
  models: "Models & processing location",
  jobs: "Processing & provenance",
  reports: "Export this research scope",
  learning: "Reviewed examples & method versions",
  filters: "Refine the research scope",
  "review-queue": "Review queues · recorded evidence",
};
const active = (run?: AnalysisRun | null) =>
  !!run && ["queued", "running"].includes(run.status);
function currentUrl() {
  return new URL(window.location.href);
}

export default function App() {
  return (
    <AccessGate>
      <Workspace />
    </AccessGate>
  );
}

function Workspace() {
  const access = useAccessSession();
  const [pilotBrief, setPilotBrief] = useState<PilotBrief | null>(null);
  const [erasureWorkspace, setErasureWorkspace] = useState("");
  const [unsavedPilotBrief, setUnsavedPilotBrief] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState("");
  const [lenses, setLenses] = useState<Lens[]>([]);
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [scope, setScope] = useState<Scope>(EMPTY_SCOPE);
  const [query, setQuery] = useState<QueryResult | null>(null);
  const [search, setSearch] = useState("");
  const [drawer, setDrawer] = useState<DrawerName>(null);
  const [help, setHelp] = useState(false);
  const [context, setContext] = useState<Context | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [candidate, setCandidate] = useState<Observation | null>(null);
  const [loading, setLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const [revision, setRevision] = useState(0);
  const [stale, setStale] = useState(false);
  const [sourceSheet, setSourceSheet] = useState(false);
  const [narrow, setNarrow] = useState(window.innerWidth < 1100);
  const projectRef = useRef("");
  const signature = useRef("");
  const queryRef = useRef<QueryResult | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const contextRequest = useRef(0);
  const notify = useCallback(
    (text: string, isError = false) => setNotice({ text, error: isError }),
    [],
  );
  const {
    book: casebook,
    setBook: setCasebook,
    pinning,
    pin,
    create: createCasebook,
  } = useCasebook(project, query, notify);
  const refresh = useCallback(() => {
    signature.current = "";
    setRevision((value) => value + 1);
    setStale(false);
  }, []);
  const patchScope = useCallback((patch: Partial<Scope>) => {
    setScope((previous) => ({ ...previous, ...patch }));
    setStale(false);
  }, []);

  function saveUrl(
    result: QueryResult,
    doc?: string,
    finding?: string,
    push = false,
    unitId?: string,
  ) {
    const url = currentUrl();
    url.hash = "";
    url.searchParams.set("workspace", projectRef.current);
    url.searchParams.set("query", result.query_id);
    if (doc) url.searchParams.set("doc", doc);
    else url.searchParams.delete("doc");
    if (finding) url.searchParams.set("finding", finding);
    else url.searchParams.delete("finding");
    if (unitId) url.searchParams.set("unit", unitId);
    else url.searchParams.delete("unit");
    if (push) window.history.pushState({}, "", url);
    else window.history.replaceState({}, "", url);
  }
  const openSource = useCallback(
    async (
      document: SourceDocument | string,
      finding?: Observation | string,
      block?: string,
      cursor?: number,
    ) => {
      const id = typeof document === "string" ? document : document.id;
      const token = ++contextRequest.current;
      setContextLoading(true);
      try {
        const findingId = typeof finding === "string" ? finding : finding?.id;
        const next = await contextFor(id, findingId, block, cursor);
        if (token !== contextRequest.current) return;
        setContext({ ...next, finding: next.finding || (typeof finding === "string" ? undefined : finding) });
        if (queryRef.current) saveUrl(queryRef.current, id, findingId, true);
      } catch (reason) {
        notify(message(reason), true);
      } finally {
        if (token === contextRequest.current) setContextLoading(false);
      }
    },
    [notify],
  );

  const openUnit = useCallback(
    async (documentId: string, unitId: string) => {
      const current = queryRef.current;
      if (!current) return;
      const token = ++contextRequest.current;
      setContextLoading(true);
      try {
        const next = await unitContext(
          projectRef.current,
          current.query_id,
          documentId,
          unitId,
        );
        if (token !== contextRequest.current) return;
        setContext(next);
        saveUrl(current, documentId, undefined, false, unitId);
      } catch (reason) {
        notify(message(reason), true);
      } finally {
        if (token === contextRequest.current) setContextLoading(false);
      }
    },
    [notify],
  );

  useEffect(() => {
    let alive = true;
    // Unit links resolve actual frozen source context without creating a finding.
    void Promise.allSettled([
      request<Project[]>("/projects"),
      request<Lens[]>("/lenses"),
      loadModels(),
    ])
      .then(async ([p, l, m]) => {
        if (!alive) return;
        if (p.status !== "fulfilled") throw p.reason;
        let ps = list<Project>(p.value);
        const initial = currentUrl();
        const preferred =
          initial.searchParams.get("workspace") ||
          localStorage.getItem("doxa-project");
        if (
          preferred &&
          !ps.some((item) => item.id === preferred) &&
          permits(access, "admin")
        ) {
          try {
            const jobs = await request<Item[]>(
              `/pilot/projects/${encodeURIComponent(preferred)}/erase-jobs`,
            );
            if (!alive) return;
            if (jobs.length) {
              ps = [
                ...ps,
                {
                  id: preferred,
                  name: "Project erasure record",
                  description:
                    "Retained lifecycle receipt; source project no longer exists",
                },
              ];
              setErasureWorkspace(preferred);
            }
          } catch {
            /* A stale or unauthorized workspace URL grants no access. */
          }
        }
        if (!ps.length && permits(access, "admin"))
          ps = [
            await request<Project>("/projects", {
              method: "POST",
              body: JSON.stringify({ name: "Research workspace" }),
            }),
          ];
        if (!ps.length) {
          setProjects([]);
          setError(
            "No workspace has been granted to this account. Contact your workspace operator.",
          );
          setLoading(false);
          setReady(true);
          return;
        }
        const id = ps.some((item) => item.id === preferred)
          ? preferred!
          : ps[0].id;
        setProjects(ps);
        setLenses(l.status === "fulfilled" ? l.value : []);
        setProfiles(m.status === "fulfilled" ? m.value : []);
        setProject(id);
        projectRef.current = id;
        localStorage.setItem("doxa-project", id);
        const saved = initial.searchParams.get("query");
        if (saved) {
          try {
            const restored = await restoreQuery(saved);
            if (alive) {
              setScope(restored.scope);
              setSearch(restored.scope.q);
              setQuery(restored);
              queryRef.current = restored;
              signature.current = JSON.stringify([id, restored.scope]);
            }
          } catch {
            /* An expired saved scope is replaced by a new server query. */
          }
        }
        if (initial.searchParams.get("doc") && initial.searchParams.get("unit"))
          void openUnit(
            initial.searchParams.get("doc")!,
            initial.searchParams.get("unit")!,
          );
        else if (initial.searchParams.get("doc"))
          void openSource(
            initial.searchParams.get("doc")!,
            initial.searchParams.get("finding")
              ? ({ id: initial.searchParams.get("finding")! } as Observation)
              : undefined,
          );
        const hash = initial.hash.slice(1);
        const entryPanels: Record<string, DrawerName> = {
          import: "import",
          analyze: "analyze",
          runs: "analyze",
          models: "models",
          jobs: "jobs",
          reports: "reports",
          learning: "learning",
          filters: "filters",
        };
        if (entryPanels[hash]) setDrawer(entryPanels[hash]);
        if (alive) setReady(true);
      })
      .catch((reason) => {
        if (alive) {
          setError(message(reason));
          setLoading(false);
        }
      });
    const resize = () => setNarrow(window.innerWidth < 1100);
    window.addEventListener("resize", resize);
    return () => {
      alive = false;
      window.removeEventListener("resize", resize);
    };
  }, [openSource, openUnit]);

  useEffect(() => {
    if (!ready || !project) return;
    if (project === erasureWorkspace) {
      setLoading(false);
      return;
    }
    const key = JSON.stringify([project, scope]);
    if (signature.current === key && queryRef.current) {
      setLoading(false);
      return;
    }
    signature.current = key;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void workspaceQuery(project, scope, controller.signal)
      .then((result) => {
        if (controller.signal.aborted || projectRef.current !== project) return;
        setQuery(result);
        queryRef.current = result;
        setLoading(false);
        setStale(false);
        saveUrl(
          result,
          context?.document.id,
          context?.finding?.id,
          false,
          context?.unit_id,
        );
        if (result.run)
          setRuns((previous) =>
            [
              result.run!,
              ...previous.filter((run) => run.id !== result.run!.id),
            ].slice(0, 30),
          );
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(message(reason));
          setLoading(false);
          signature.current = "";
        }
      });
    return () => controller.abort();
  }, [ready, project, scope, revision, erasureWorkspace]);
  useEffect(() => {
    if (!ready || !project) return;
    if (project === erasureWorkspace) return;
    let alive = true;
    void request<AnalysisRun[]>(`/v3/projects/${project}/runs?limit=30`)
      .then((value) => {
        if (alive) setRuns(value);
      })
      .catch((reason) => notify(message(reason), true));
    return () => {
      alive = false;
    };
  }, [ready, project, revision, notify, erasureWorkspace]);
  useEffect(() => {
    const timer = setTimeout(() => {
      if (search !== scope.q) patchScope({ q: search });
    }, 350);
    return () => clearTimeout(timer);
  }, [search, scope.q, patchScope]);
  useEffect(() => {
    const pop = () => {
      const id = currentUrl().searchParams.get("query");
      if (!id) return;
      void restoreQuery(id)
        .then((result) => {
          signature.current = JSON.stringify([
            projectRef.current,
            result.scope,
          ]);
          setScope(result.scope);
          setSearch(result.scope.q);
          setQuery(result);
          queryRef.current = result;
          const doc = currentUrl().searchParams.get("doc");
          if (doc && currentUrl().searchParams.get("unit"))
            void openUnit(doc, currentUrl().searchParams.get("unit")!);
          else if (doc)
            void openSource(
              doc,
              currentUrl().searchParams.get("finding")
                ? ({
                    id: currentUrl().searchParams.get("finding")!,
                  } as Observation)
                : undefined,
            );
          else setContext(null);
        })
        .catch(() => refresh());
    };
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, [openSource, openUnit, refresh]);
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape") setHelp(false);
    };
    window.addEventListener("keydown", keyboard);
    return () => window.removeEventListener("keydown", keyboard);
  }, []);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(
      () => setNotice(null),
      notice.error ? 12000 : 6000,
    );
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    const running = runs.find(active);
    if (!running) return;
    const timer = setInterval(() => {
      void request<AnalysisRun>(`/v3/runs/${running.id}/status`)
        .then((next) => {
          setRuns((previous) =>
            previous.map((item) =>
              item.id === next.id ? { ...item, ...next } : item,
            ),
          );
          if (
            next.status !== running.status ||
            JSON.stringify(next.progress) !== JSON.stringify(running.progress)
          )
            setStale(true);
        })
        .catch(() => undefined);
    }, 3000);
    return () => clearInterval(timer);
  }, [runs]);

  async function importFiles(files: FileList | File[]) {
    const selectedFiles = Array.from(files);
    if (!selectedFiles.length || !project) return;
    setBusy(true);
    let imported = false;
    const showImportedCorpus = () => {
      if (projectRef.current !== project) return;
      setScope({ ...EMPTY_SCOPE, run_id: "none" });
      setSearch("");
      contextRequest.current++;
      setContextLoading(false);
      setContext(null);
    };
    try {
      const resumedImport =
        selectedFiles.length === 1
          ? pendingUploadImport(project, selectedFiles[0])
          : undefined;
      const session = resumedImport
        ? { id: resumedImport }
        : await request<Item>(`/v3/projects/${project}/imports`, {
            method: "POST",
            body: JSON.stringify({
              name: `Import ${new Date().toLocaleString()}`,
              total_files: selectedFiles.length,
            }),
          });
      const importId = String(session.id);
      let group: File[] = [];
      let bytes = 0;
      let index = 0;
      async function flush() {
        if (!group.length) return;
        index++;
        setProgress(`Import group ${index} · ${group.length} source files`);
        const form = new FormData();
        group.forEach((file) => form.append("files", file));
        await request(
          `/projects/${project}/upload?import_id=${encodeURIComponent(importId)}`,
          { method: "POST", body: form },
        );
        imported = true;
        group = [];
        bytes = 0;
      }
      for (const file of selectedFiles) {
        if (/\.zip$/i.test(file.name)) {
          await flush();
          setProgress(`Importing archive ${file.name}…`);
          const form = new FormData();
          form.append("file", file);
          form.append("import_id", importId);
          await request(`/v3/projects/${project}/jobs/import-archive`, {
            method: "POST",
            headers: { "Idempotency-Key": `archive-${importId}-${file.size}-${file.lastModified}` },
            body: form,
          });
          notify("Archive accepted for background extraction. The Activity center tracks committed files, failures and recovery.");
          imported = true;
        } else if (file.size > 10 * 1024 * 1024) {
          await flush();
          await uploadInParts(project, file, setProgress, importId);
          imported = true;
        } else {
          if (group.length >= 100 || bytes + file.size > 20 * 1024 * 1024)
            await flush();
          group.push(file);
          bytes += file.size;
        }
      }
      await flush();
      showImportedCorpus();
      notify(
        `${number(selectedFiles.length)} selected files imported. Duplicate content and versions remain traceable.`,
      );
      refresh();
    } catch (reason) {
      if (imported) showImportedCorpus();
      notify(message(reason), true);
      refresh();
    } finally {
      setBusy(false);
      setProgress("");
    }
  }
  async function demo(collection: string) {
    setBusy(true);
    setProgress(
      collection === "news"
        ? "Importing licensed news sources…"
        : "Importing public case studies and saved results…",
    );
    try {
      const result = await request<Item>(`/v3/projects/${project}/demo`, {
        method: "POST",
        body: JSON.stringify({ collection }),
      });
      const run = record(result.run);
      setSearch("");
      setContext(null);
      const batch = record(result.batch);
      patchScope({
        ...EMPTY_SCOPE,
        run_id: String(run.id || "none"),
        batch_ids: typeof batch.id === "string" && batch.id ? [batch.id] : [],
      });
      refresh();
      notify(
        typeof result.notice === "string" && result.notice.trim()
          ? result.notice
          : "Collection imported. Source counts and coverage come from the recorded data.",
      );
    } catch (reason) {
      notify(message(reason), true);
    } finally {
      setBusy(false);
      setProgress("");
    }
  }
  async function run(config: Item) {
    setBusy(true);
    try {
      const created = await request<AnalysisRun>(
        `/v3/projects/${project}/runs`,
        { method: "POST", body: JSON.stringify(config) },
      );
      setRuns((previous) => [created, ...previous]);
      patchScope({ run_id: created.id });
      setDrawer("jobs");
      notify(
        "Analysis started with the selected source scope and frozen method.",
      );
    } catch (reason) {
      notify(message(reason), true);
    } finally {
      setBusy(false);
    }
  }
  function review(updated: Observation) {
    setContext((previous) =>
      previous ? { ...previous, finding: updated } : previous,
    );
    setStale(true);
    notify(
      "Structured review saved. Refresh the scope to update aggregate counts.",
    );
  }
  function switchProject(id: string) {
    setProject(id);
    projectRef.current = id;
    localStorage.setItem("doxa-project", id);
    setScope(EMPTY_SCOPE);
    setSearch("");
    setQuery(null);
    queryRef.current = null;
    signature.current = "";
    setContext(null);
    setRuns([]);
  }
  async function newProject() {
    const name = window.prompt("Name this research workspace");
    if (!name?.trim()) return;
    try {
      const created = await request<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ name: name.trim() }),
      });
      setProjects((previous) => [...previous, created]);
      switchProject(created.id);
    } catch (reason) {
      notify(message(reason), true);
    }
  }
  const metrics = query?.metrics || EMPTY_METRICS;
  const runInfo = query?.run;
  const saved = !!runInfo?.config?.saved_snapshot;
  const legacy =
    !!query?.run && metrics.analysis_kind === "legacy_unclassified";
  const unassessed = list<string>(
    metrics.not_assessed_lenses ||
      record(metrics.coverage_detail).not_assessed_lenses,
  );
  const unavailable = !runInfo || legacy || unassessed.length > 0;
  const live = runs.find(active);
  const detail = record(metrics.coverage_detail);
  const inspectorRun = context?.finding?.run_id
    ? runs.find((item) => item.id === context.finding?.run_id) ||
      (query?.run?.id === context.finding.run_id ? query.run : null)
    : query?.run;
  const matching = query?.documents.total || 0;
  const activeFilters = Object.entries(scope).filter(
    ([key, value]) =>
      !["run_id", "metric", "bin", "date_basis", "duplicate_policy"].includes(
        key,
      ) && (Array.isArray(value) ? value.length > 0 : !!value),
  );
  const selectedSource = context?.document.id;

  return (
    <div className="workspace-app">
      <a className="w-skip" href="#workspace-main">
        Skip to research workspace
      </a>
      <header className="w-commandbar">
        <a className="w-brand" href="/" aria-label="DOXA research workspace">
          <DoxaCompanion />
          <strong>
            DOXA<span>.</span>
          </strong>
        </a>
        <a
          className="w-tacitus"
          href="https://tacitus.me"
          target="_blank"
          rel="noopener noreferrer"
        >
          BY TACITUS
          <ArrowUpRight size={10} />
        </a>
        <div className="w-command-separator" />
        <select
          className="w-project-select"
          aria-label="Research workspace"
          value={project}
          onChange={(event) => switchProject(event.target.value)}
        >
          {projects.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
        <IconButton
          label="Create workspace"
          onClick={() => void newProject()}
          disabled={!permits(access, "admin")}
        >
          <Plus size={15} />
        </IconButton>
        <div className="w-command-search">
          <Search size={16} />
          <input
            ref={searchRef}
            aria-label="Search the complete corpus"
            placeholder="Search the complete corpus…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <kbd>⌘ K</kbd>
        </div>
        <button className="w-engine-status" onClick={() => setDrawer("models")}>
          <span />
          <Globe2 size={14} />
          <span>Cloud / local</span>
          <ChevronDown size={11} />
        </button>
        <Button
          variant="outline"
          onClick={() => setDrawer("import")}
          disabled={
            !project ||
            project === erasureWorkspace ||
            !permits(access, "write")
          }
        >
          <Upload size={14} />
          <span>Import</span>
        </Button>
        <Button
          onClick={() => setDrawer("analyze")}
          disabled={
            !matching || unsavedPilotBrief || !permits(access, "analyze")
          }
          title={
            unsavedPilotBrief
              ? "Save the inquiry brief before starting analysis."
              : undefined
          }
        >
          <Sparkles size={14} />
          <span>Analyze</span>
        </Button>
        <IconButton
          label="Export query report"
          onClick={() => setDrawer("reports")}
          disabled={!query || !permits(access, "export")}
        >
          <ArrowDownToLine size={17} />
        </IconButton>
        <div className="w-help-container">
          <IconButton
            label="Help and TACITUS links"
            onClick={() => setHelp(!help)}
          >
            <CircleHelp size={18} />
          </IconButton>
          {help && (
            <nav className="w-help-menu" aria-label="Help">
              {[
                { text: "How DOXA works", href: "/how-it-works.html" },
                {
                  text: "About · Bias Detection as a Service",
                  href: "/about.html",
                },
                { text: "Methodology & limits", href: "/methodology.html" },
                { text: "DOXA Quest · illustrated guide", href: "/guide.html" },
                { text: "TACITUS", href: "https://tacitus.me" },
                {
                  text: "Source code & documentation",
                  href: "https://github.com/sargonxg/DOXA-by-TACITUS",
                },
              ].map((item) => (
                <a
                  key={item.href}
                  href={item.href}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {item.text}
                  <ExternalLink size={12} />
                </a>
              ))}
            </nav>
          )}
        </div>
      </header>

      <section className="w-scopebar" aria-label="Global analytical scope">
        <button
          className="w-mobile-sources"
          onClick={() => setSourceSheet(true)}
        >
          <PanelLeft size={16} />
          Sources
        </button>
        <label className="w-scope-run">
          <span>ANALYSIS SNAPSHOT</span>
          <select
            aria-label="Analysis snapshot"
            value={scope.run_id || query?.run?.id || ""}
            onChange={(event) => patchScope({ run_id: event.target.value })}
          >
            <option value="">Latest primary run</option>
            <option value="none">Corpus · all imported sources</option>
            {[
              ...new Map(
                [...(query?.run ? [query.run] : []), ...runs].map((item) => [
                  item.id,
                  item,
                ]),
              ).values(),
            ].map((item) => (
              <option key={item.id} value={item.id}>
                {item.config?.verification_of ? "Verifier · " : ""}
                {human(item.config?.provider)} · {date(item.created_at)} ·{" "}
                {human(item.status)}
              </option>
            ))}
          </select>
        </label>
        <div className="w-scope-divider" />
        <label>
          <span>PERIOD</span>
          <div className="w-period-inputs">
            <input
              aria-label="Period start date"
              type="date"
              value={scope.from_date}
              onChange={(event) =>
                patchScope({ from_date: event.target.value })
              }
            />
            <span>—</span>
            <input
              aria-label="Period end date"
              type="date"
              value={scope.to_date}
              onChange={(event) => patchScope({ to_date: event.target.value })}
            />
          </div>
        </label>
        <label>
          <span>FRAMEWORK</span>
          <select
            aria-label="Analytical framework"
            value={scope.lens_ids[0] || ""}
            onChange={(event) =>
              patchScope({
                lens_ids: event.target.value ? [event.target.value] : [],
                criterion_ids: [],
              })
            }
          >
            <option value="">All declared lenses</option>
            {lenses.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="w-scope-speech">
          <span>SPEECH ROLE</span>
          <select
            aria-label="Speech role"
            value={scope.speech_roles[0] || ""}
            onChange={(event) =>
              patchScope({
                speech_roles: event.target.value ? [event.target.value] : [],
              })
            }
          >
            <option value="">All source roles</option>
            {(query?.facets.speech_roles || []).map((item) => (
              <option key={item.name} value={item.name}>
                {human(item.name)}
              </option>
            ))}
          </select>
        </label>
        <Button variant="quiet" onClick={() => setDrawer("filters")}>
          <SlidersHorizontal size={15} />
          Filters
          {activeFilters.length > 0 && <Badge>{activeFilters.length}</Badge>}
        </Button>
        <IconButton label="Refresh query snapshot" onClick={refresh}>
          <RefreshCw size={15} className={loading ? "spin" : ""} />
        </IconButton>
        <IconButton
          label="Copy scoped workspace link"
          onClick={() => {
            void navigator.clipboard
              .writeText(window.location.href)
              .then(() => notify("Scoped workspace link copied."));
          }}
        >
          <Copy size={15} />
        </IconButton>
      </section>
      <section className="w-statusbar">
        <div>
          <span
            className={`w-health-dot ${metrics.coverage < 100 ? "amber" : ""}`}
          />
          <strong>
            {!runInfo
              ? "Corpus · not analyzed"
              : legacy
                ? "Contextual checks unavailable"
                : `${percentage(metrics.coverage)} fully assessed sources`}
          </strong>
          {!legacy && !!runInfo && (
            <span>
              {number(detail.units_done ?? detail.blocks_done)} /{" "}
              {number(detail.units_total ?? detail.blocks_total)} source units
            </span>
          )}
          {saved && <Badge>Saved results · historical usage</Badge>}
          {runInfo?.config?.verification_of ? (
            <Badge tone="amber">Independent verifier selection</Badge>
          ) : null}
          {legacy && (
            <Badge tone="amber">
              Legacy observations · contextual rates unavailable
            </Badge>
          )}
        </div>
        <div>
          {live ? (
            <button onClick={() => setDrawer("jobs")}>
              <LoaderCircle size={12} className="spin" />
              Processing sources
            </button>
          ) : (
            <button onClick={() => setDrawer("jobs")}>
              <FlaskConical size={13} />
              Processing record
            </button>
          )}
          <button onClick={() => setDrawer("learning")}>
            <ShieldCheck size={13} />
            Reviewed learning
          </button>
          <button onClick={() => setDrawer("review-queue")}>
            <BookOpen size={12} />
            Review queues
          </button>
          {stale && (
            <button className="w-new-results" onClick={refresh}>
              Updated results available
              <RefreshCw size={12} />
            </button>
          )}
        </div>
      </section>
      {activeFilters.length > 0 && (
        <div className="w-active-filters">
          {activeFilters.map(([key, value]) => (
            <button
              key={key}
              onClick={() => {
                patchScope({ [key]: Array.isArray(value) ? [] : "" });
                if (key === "q") setSearch("");
              }}
            >
              {human(key)}:{" "}
              {Array.isArray(value)
                ? value.map((item) => human(item)).join(", ")
                : human(value)}
              <X size={11} />
            </button>
          ))}
          <button
            onClick={() => {
              setScope((previous) => ({
                ...EMPTY_SCOPE,
                run_id: previous.run_id,
              }));
              setSearch("");
            }}
          >
            Clear filters
            <X size={11} />
          </button>
        </div>
      )}
      <main
        id="workspace-main"
        className={`w-main ${context || contextLoading ? "has-inspector" : ""}`}
      >
        <aside className="w-source-rail">
          <SourceList
            query={query}
            loading={loading}
            selected={selectedSource}
            onSelect={(document) => void openSource(document)}
            onFocus={(id) => patchScope({ document_ids: [id] })}
            onImport={() => setDrawer("import")}
            onStale={() => setStale(true)}
          />
        </aside>
        <div className="w-center">
          <div className="w-workspace-title">
            <div>
              <span className="w-eyebrow">DOCUMENT AND SPEECH ANALYSIS</span>
              <h1>Bias analysis workspace</h1>
            </div>
            <Badge>Research preview</Badge>
          </div>
          {error && <ErrorMessage text={error} />}
          {project && <ScorecardPanel key={project + "/" + (query?.run?.id || "")} project={project}
            query={query} onRefresh={refresh} onOpen={(source, block, finding) => void openSource(source, finding, block)} />}
          {query && !loading && <BiasResults key={query.query_id} query={query} lenses={lenses}
            onEvidence={(doc, finding) => void openSource(doc, finding)} />}
          <PublishedStudy />
          {project && <RefinementPanel project={project} runId={query?.run?.id}
            onImport={() => setDrawer("import")} onAnalyze={() => setDrawer("analyze")}
            onReports={() => setDrawer("reports")} onRefresh={refresh}
            onOpenSource={(id) => void openSource(id)} />}
          {project && (
            <PilotWorkspace
              project={project}
              projectName={
                projects.find((item) => item.id === project)?.name || ""
              }
              query={query}
              sourceContext={context}
              onBrief={setPilotBrief}
              onUnsavedChange={setUnsavedPilotBrief}
              onAnalyze={() => setDrawer("analyze")}
              onImport={() => setDrawer("import")}
              onReports={() => setDrawer("reports")}
              onRefresh={refresh}
              onOpenSource={(id) => void openSource(id)}
              onErased={() => {
                contextRequest.current++;
                setContext(null);
                setContextLoading(false);
                setCandidate(null);
                setCasebook(null);
                setQuery(null);
                queryRef.current = null;
                setRuns([]);
                setPilotBrief(null);
                setLoading(false);
              }}
              onSavedSource={(id) => {
                contextRequest.current++;
                setContext(null);
                setContextLoading(false);
                setScope({
                  ...EMPTY_SCOPE,
                  run_id: "none",
                  document_ids: [id],
                });
                setSearch("");
                refresh();
              }}
            />
          )}
          <div className="w-metric-row">
            <Metric
              label="Matching sources"
              value={number(matching)}
              detail={`${number(metrics.total_words)} words`}
            />
            <Metric
              label="Sources with recorded flags"
              value={unavailable ? "—" : number(metrics.indicators)}
              detail={
                unavailable
                  ? unassessed.length
                    ? "Selected framework not assessed"
                    : "A v3 contextual run is needed"
                  : "Includes subsequently rejected readings"
              }
            />
            <Metric
              label="Reported / author supports"
              value={
                unavailable
                  ? "—"
                  : `${number(metrics.reported_indicators)} / ${number(metrics.endorsed_indicators)}`
              }
              detail="Reported or hypothetical / author supports the proposition. Source counts can overlap."
            />
            <Metric
              label="Retained observations"
              value={number(metrics.findings)}
              detail={`${number(metrics.reviewed)} reviewed · ${number(metrics.uncertain)} uncertain`}
            />
          </div>
          <CoverageSummary query={query} compact />
          {metrics.coverage < 100 && query?.run && (
            <div className="w-coverage-note">
              <ShieldCheck size={14} />
              <span>
                {unassessed.length
                  ? `Not assessed in this run: ${unassessed.map((id) => lenses.find((lens) => lens.id === id)?.name || human(id)).join(", ")}. Start a new run before interpreting an absence.`
                  : "Partial coverage: unchecked or rejected material is not evidence of an absence. Retained observations require review."}
              </span>
              <button onClick={() => setDrawer("jobs")}>
                Details
                <ArrowUpRight size={12} />
              </button>
            </div>
          )}
          <Analytics
            result={query}
            scope={scope}
            onScope={patchScope}
            project={project}
            context={context}
          />
          <EvidenceList
            query={query}
            selected={context?.finding?.id}
            loading={loading}
            onSelect={(finding) =>
              void openSource(finding.document_id, finding)
            }
            onStale={() => setStale(true)}
          />
        </div>
        {!narrow && (
          <aside className="w-inspector-rail">
            <Inspector
              context={context}
              loading={contextLoading}
              lenses={lenses}
              run={inspectorRun}
              pinned={
                !!context?.finding &&
                !!casebook?.cases.some(
                  (item) => item.assessment_id === context.finding?.id,
                )
              }
              pinning={pinning}
              onPin={(finding) =>
                void pin({
                  assessment_id: finding.id,
                  document_id: finding.document_id,
                  unit_id: finding.unit_id,
                })
              }
              onCasebook={() => setDrawer("reports")}
              onClose={() => setContext(null)}
              onReview={review}
              onCandidate={(finding) => {
                setCandidate(finding);
                setDrawer("learning");
              }}
              onNavigate={(cursor) => {
                if (context)
                  void openSource(
                    context.document.id,
                    undefined,
                    undefined,
                    cursor,
                  );
              }}
            />
          </aside>
        )}
      </main>
      <footer className="w-footer">
        <AccessControls />
        <span>
          <ShieldCheck size={11} />
          Source-linked observations · explicit methods · human review
        </span>
        <a href="/how-it-works.html" target="_blank" rel="noopener noreferrer">
          <BookOpen size={12} />
          How DOXA works
        </a>
        <span>
          {query
            ? `Scope ${query.scope_hash.slice(0, 10)} · snapshot ${query.snapshot_version}`
            : "Private research workspace"}
        </span>
      </footer>
      {drawer && (
        <Drawer
          title={DRAWER_TITLES[drawer] || "Workspace"}
          eyebrow="DOXA RESEARCH WORKSPACE"
          onClose={() => setDrawer(null)}
          wide={drawer === "analyze" || drawer === "reports"}
          notification={
            notice && (
              <Notification
                notice={notice}
                onDismiss={() => setNotice(null)}
                inline
              />
            )
          }
        >
          <DrawerBody
            drawer={drawer}
            project={project}
            query={query}
            scope={scope}
            lenses={lenses}
            models={profiles}
            runs={runs}
            progress={progress}
            busy={busy}
            candidate={context?.finding || candidate}
            sourceContext={context}
            casebook={casebook}
            pinning={pinning}
            onCasebook={setCasebook}
            onPin={pin}
            onCreateCasebook={createCasebook}
            onOpenSource={(document, block, finding) => {
              setDrawer(null);
              void openSource(
                document,
                finding ? ({ id: finding } as Observation) : undefined,
                block,
              );
            }}
            onOpenContext={(next, unitId) => {
              contextRequest.current++;
              setContext(next);
              setContextLoading(false);
              setDrawer(null);
              if (queryRef.current)
                saveUrl(
                  queryRef.current,
                  next.document.id,
                  undefined,
                  true,
                  unitId,
                );
            }}
            onScope={patchScope}
            onImport={importFiles}
            onDemo={demo}
            onRun={run}
            onVerificationStarted={(created) => {
              const selectedRun = queryRef.current?.run?.id;
              if (selectedRun)
                setScope((previous) =>
                  previous.run_id
                    ? previous
                    : { ...previous, run_id: selectedRun },
                );
              setRuns((previous) =>
                [
                  created,
                  ...previous.filter((item) => item.id !== created.id),
                ].slice(0, 30),
              );
              setDrawer("jobs");
            }}
            onRefresh={refresh}
            onNotice={notify}
            pilotBrief={pilotBrief}
          />
        </Drawer>
      )}
      {sourceSheet && (
        <Drawer
          title="Source collection"
          onClose={() => setSourceSheet(false)}
          notification={
            notice && (
              <Notification
                notice={notice}
                onDismiss={() => setNotice(null)}
                inline
              />
            )
          }
        >
          <SourceList
            query={query}
            loading={loading}
            selected={selectedSource}
            onSelect={(document) => {
              void openSource(document);
              setSourceSheet(false);
            }}
            onFocus={(id) => {
              patchScope({ document_ids: [id] });
              setSourceSheet(false);
            }}
            onImport={() => {
              setSourceSheet(false);
              setDrawer("import");
            }}
            onStale={() => setStale(true)}
          />
        </Drawer>
      )}
      {narrow && !drawer && (context || contextLoading) && (
        <Drawer
          title="Context & evidence"
          notification={
            notice && (
              <Notification
                notice={notice}
                onDismiss={() => setNotice(null)}
                inline
              />
            )
          }
          onClose={() => {
            contextRequest.current++;
            setContext(null);
            setContextLoading(false);
          }}
        >
          <Inspector
            context={context}
            loading={contextLoading}
            lenses={lenses}
            run={inspectorRun}
            pinned={
              !!context?.finding &&
              !!casebook?.cases.some(
                (item) => item.assessment_id === context.finding?.id,
              )
            }
            pinning={pinning}
            onPin={(finding) =>
              void pin({
                assessment_id: finding.id,
                document_id: finding.document_id,
                unit_id: finding.unit_id,
              })
            }
            onCasebook={() => {
              setContext(null);
              setDrawer("reports");
            }}
            onClose={() => setContext(null)}
            onReview={review}
            onCandidate={(finding) => {
              setCandidate(finding);
              setContext(null);
              setDrawer("learning");
            }}
            onNavigate={(cursor) => {
              if (context)
                void openSource(
                  context.document.id,
                  undefined,
                  undefined,
                  cursor,
                );
            }}
          />
        </Drawer>
      )}
      {notice &&
        !drawer &&
        !sourceSheet &&
        !(narrow && (context || contextLoading)) && (
          <Notification notice={notice} onDismiss={() => setNotice(null)} />
        )}
    </div>
  );
}

function Notification({
  notice,
  onDismiss,
  inline = false,
}: {
  notice: NonNullable<Notice>;
  onDismiss: () => void;
  inline?: boolean;
}) {
  return (
    <div
      className={`${inline ? "w-panel-notice" : "w-toast"} ${notice.error ? "error" : ""}`}
      role={notice.error ? "alert" : "status"}
    >
      {notice.error ? <CircleHelp size={17} /> : <Check size={17} />}
      <span>{notice.text}</span>
      <IconButton label="Dismiss notification" onClick={onDismiss}>
        <X size={14} />
      </IconButton>
    </div>
  );
}

function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="w-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}
function usePage<T>(query: QueryResult | null, kind: "documents" | "findings") {
  const initial = (query?.[kind] || {
    items: [],
    total: 0,
    next_cursor: null,
  }) as Page<T>;
  const [page, setPage] = useState<Page<T>>(initial);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const queryId = useRef(query?.query_id);
  useEffect(() => {
    queryId.current = query?.query_id;
    setPage(
      (query?.[kind] || { items: [], total: 0, next_cursor: null }) as Page<T>,
    );
    setCursors([null]);
    setIndex(0);
    setError("");
  }, [query?.query_id, kind]);
  async function move(next: number) {
    if (!query) return;
    const cursor = next > index ? page.next_cursor : cursors[next];
    if (next > index && !cursor) return;
    setLoading(true);
    setError("");
    const id = query.query_id;
    try {
      const value = await queryPage<T>(id, kind, cursor);
      if (queryId.current !== id) return;
      if (next > index)
        setCursors((previous) => [...previous.slice(0, next), cursor]);
      setPage(value);
      setIndex(next);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setLoading(false);
    }
  }
  return { page, index, loading, error, move };
}
function SourceList({
  query,
  loading,
  selected,
  onSelect,
  onFocus,
  onImport,
  onStale,
}: {
  query: QueryResult | null;
  loading: boolean;
  selected?: string;
  onSelect: (doc: SourceDocument) => void;
  onFocus: (id: string) => void;
  onImport: () => void;
  onStale: () => void;
}) {
  const state = usePage<SourceDocument>(query, "documents");
  const access = useAccessSession();
  return (
    <section className="w-source-list">
      <header>
        <div>
          <FolderOpen size={16} />
          <strong>Sources</strong>
          <Badge>{number(state.page.total)}</Badge>
        </div>
        <IconButton
          label="Import sources"
          onClick={onImport}
          disabled={!permits(access, "write")}
        >
          <Plus size={15} />
        </IconButton>
      </header>
      <div className="w-source-list-label">
        COMPLETE SERVER QUERY · {number(state.page.total)} MATCHING
      </div>
      {state.error && (
        <div onClick={onStale}>
          <ErrorMessage text={state.error} />
        </div>
      )}
      {loading && !query ? (
        <Spinner text="Opening the corpus…" />
      ) : state.page.items.length ? (
        <VirtualList
          items={state.page.items}
          rowHeight={87}
          label="Matching source documents"
          render={(doc) => (
            <div
              className={`w-source-row ${selected === doc.id ? "selected" : ""}`}
            >
              <button onClick={() => onSelect(doc)}>
                <FileText size={18} strokeWidth={1.35} />
                <div>
                  <strong>{doc.title}</strong>
                  <span>
                    {doc.author || doc.institution || "Unknown author"}
                  </span>
                  <small>
                    {doc.status && doc.status !== "ready"
                      ? `${human(doc.status)} · `
                      : ""}
                    {date(doc.published_at)} · {number(doc.word_count)} words
                  </small>
                </div>
              </button>
              <IconButton
                label={`Focus scope on ${doc.title}`}
                onClick={() => onFocus(doc.id)}
              >
                <Filter size={12} />
              </IconButton>
            </div>
          )}
        />
      ) : (
        <Empty title="Start with your sources">
          Import documents or explore a licensed collection.
          <Button
            variant="outline"
            onClick={onImport}
            disabled={!permits(access, "write")}
          >
            <Database size={13} />
            Explore collections
          </Button>
        </Empty>
      )}
      <Pager
        total={state.page.total}
        page={state.index}
        next={state.page.next_cursor}
        loading={state.loading}
        onPrevious={() => void state.move(state.index - 1)}
        onNext={() => void state.move(state.index + 1)}
      />
    </section>
  );
}
function EvidenceList({
  query,
  selected,
  loading,
  onSelect,
  onStale,
}: {
  query: QueryResult | null;
  selected?: string;
  loading: boolean;
  onSelect: (finding: Observation) => void;
  onStale: () => void;
}) {
  const state = usePage<Observation>(query, "findings");
  return (
    <section className="w-evidence-list">
      <header>
        <div>
          <Layers3 size={15} />
          <h2>Retained observations</h2>
          <Badge>{number(state.page.total)}</Badge>
        </div>
        <span>Exact source spans · contextual review</span>
      </header>
      {state.error && (
        <div onClick={onStale}>
          <ErrorMessage text={state.error} />
        </div>
      )}
      {loading && !query ? (
        <Spinner text="Querying the research scope…" />
      ) : state.page.items.length ? (
        <VirtualList
          items={state.page.items}
          rowHeight={112}
          label="Retained observations"
          render={(finding) => (
            <button
              className={`w-observation-row ${selected === finding.id ? "selected" : ""}`}
              onClick={() => onSelect(finding)}
            >
              <div>
                <Badge tone={finding.result === "uncertain" ? "amber" : ""}>
                  {human(finding.result || "legacy observation")}
                </Badge>
                <span>{human(finding.speech_role || finding.attribution)}</span>
                <ArrowUpRight size={13} />
              </div>
              <h3>{finding.title || human(finding.criterion)}</h3>
              <p>“{finding.quote}”</p>
            </button>
          )}
        />
      ) : (
        <Empty title="No retained observations in this scope">
          A contextual run is required for indicator measurements. Adjust the
          source scope or start an explicit analysis.
        </Empty>
      )}
      <Pager
        total={state.page.total}
        page={state.index}
        next={state.page.next_cursor}
        loading={state.loading}
        onPrevious={() => void state.move(state.index - 1)}
        onNext={() => void state.move(state.index + 1)}
      />
    </section>
  );
}
