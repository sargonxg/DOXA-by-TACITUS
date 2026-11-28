import { deleteProject, listProjects, loadProject, saveProject } from './project-store';
import { exportProject, importProject, ProjectError } from './project-format';
import type { ProjectRecord, ProjectSnapshot } from './project-format';

type ProjectControls = {
  snapshot: () => ProjectSnapshot | undefined;
  restore: (snapshot: ProjectSnapshot) => void;
  canReplace: () => boolean;
  rerun: () => void;
  busy: (value: boolean) => void;
};

function node<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  return item;
}

export function mountProjectControls(options: ProjectControls) {
  const panel = document.getElementById('project-controls') as HTMLDetailsElement;
  const list = document.getElementById('project-list')!;
  const name = document.getElementById('project-name') as HTMLInputElement;
  const status = document.getElementById('project-status')!;
  const save = document.getElementById('project-save') as HTMLButtonElement;
  const copy = document.getElementById('project-copy') as HTMLButtonElement;
  const portable = document.getElementById('project-portable') as HTMLButtonElement;
  const importer = document.getElementById('project-import') as HTMLInputElement;
  const cancel = document.getElementById('project-cancel') as HTMLButtonElement;
  const rerun = document.getElementById('project-rerun') as HTMLButtonElement;
  let current: ProjectRecord | undefined;
  let generation = 0;
  let savedGeneration = 0;
  let operation: AbortController | undefined;
  let locked = false;

  function refreshButtons() {
    const hasSources = Boolean(options.snapshot()?.documents.length);
    save.disabled = portable.disabled = rerun.disabled = !hasSources || Boolean(operation) || locked;
    copy.disabled = !hasSources || Boolean(operation) || locked;
    importer.disabled = Boolean(operation) || locked;
    name.disabled = Boolean(operation) || locked;
    for (const button of list.querySelectorAll('button')) button.disabled = Boolean(operation) || locked;
    cancel.hidden = !operation;
  }

  async function run(action: (signal: AbortSignal) => Promise<void>) {
    if (operation || locked) return;
    operation = new AbortController();
    options.busy(true);
    refreshButtons();
    try { await action(operation.signal); }
    catch (error) {
      status.textContent = error instanceof ProjectError ? error.message : 'The project operation could not finish. Your current audit remains available.';
      panel.open = true;
    } finally {
      operation = undefined;
      options.busy(false);
      refreshButtons();
    }
  }

  async function refreshList() {
    try {
      const projects = await listProjects();
      list.replaceChildren();
      if (!projects.length) list.append(node('p', 'No projects saved in this browser.'));
      for (const project of projects) {
        const row = node('div');
        row.className = 'saved-project';
        row.append(node('p', `${project.name} · ${project.documentCount} sources · ${project.decisionCount} decisions · revision ${project.revision}`));
        const open = node('button', 'Open ' + project.name);
        open.type = 'button';
        open.className = 'outline';
        open.onclick = () => {
          if (!options.canReplace()) return;
          void run(async signal => {
            status.textContent = 'Checking the saved project and original files…';
            const record = await loadProject(project.id, { signal });
            options.restore(record.snapshot);
            current = record;
            name.value = record.name;
            generation++;
            savedGeneration = generation;
            status.textContent = `Opened ${record.name}, revision ${record.revision}. Saved results retain their recorded model versions.`;
          });
        };
        const remove = node('button', 'Delete ' + project.name);
        remove.type = 'button';
        remove.className = 'outline';
        remove.onclick = () => {
          if (!window.confirm('Delete the saved browser project “' + project.name + '”? Keep a downloaded backup if you need this saved version.')) return;
          void run(async signal => {
            await deleteProject(project.id, { expectedRevision: project.revision, signal });
            if (current?.id === project.id) current = undefined;
            status.textContent = 'Saved browser project deleted.';
            await refreshList();
          });
        };
        const actions = node('div');
        actions.className = 'actions';
        actions.append(open, remove);
        row.append(actions);
        list.append(row);
      }
      refreshButtons();
    } catch (error) {
      list.replaceChildren(node('p', error instanceof Error ? error.message : 'Saved browser projects could not be listed.'));
    }
  }

  function projectName(snapshot: ProjectSnapshot) {
    return name.value.trim() || (snapshot.documents.length === 1 ? snapshot.documents[0].file.name : `${snapshot.documents.length} document review`).slice(0, 120);
  }

  function saveCurrent(asCopy: boolean) {
    const snapshot = options.snapshot();
    if (!snapshot?.documents.length) return;
    const captured = structuredClone(snapshot);
    const revision = generation;
    void run(async signal => {
      status.textContent = 'Saving sources, analysis and recorded decisions in this browser…';
      const record = await saveProject(captured, { name: projectName(captured),
        id: asCopy ? undefined : current?.id, expectedRevision: asCopy ? undefined : current?.revision, signal });
      current = record;
      name.value = record.name;
      savedGeneration = revision;
      status.textContent = `Saved ${record.name}, revision ${record.revision}.` + (generation !== revision ? ' Later edits are pending.' : '');
      await refreshList();
    });
  }

  save.onclick = () => saveCurrent(false);
  copy.onclick = () => saveCurrent(true);
  portable.onclick = () => {
    const snapshot = options.snapshot();
    if (!snapshot?.documents.length) return;
    const captured = structuredClone(snapshot);
    void run(async signal => {
      status.textContent = 'Preparing the project with its original source files…';
      const now = new Date().toISOString();
      const record: ProjectRecord = { format: 'doxa-project', version: 1, id: crypto.randomUUID(), name: projectName(captured),
        revision: 1, createdAt: now, updatedAt: now, snapshot: captured };
      const blob = await exportProject(record, { signal });
      const url = URL.createObjectURL(blob);
      const link = node('a');
      link.href = url;
      link.download = record.name.replace(/[^\p{L}\p{N} ._-]/gu, '-').slice(0, 100) + '.doxa';
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      status.textContent = 'Project backup downloaded with sources, saved decisions and recorded model results.';
    });
  };
  importer.onchange = () => {
    const file = importer.files?.[0];
    if (!file || !options.canReplace()) { importer.value = ''; return; }
    void run(async signal => {
      status.textContent = 'Checking the archive, source hashes and saved result references…';
      const record = await importProject(file, { signal });
      options.restore(record.snapshot);
      current = undefined;
      name.value = record.name;
      generation++;
      savedGeneration = 0;
      status.textContent = 'Opened the archive as an analysis snapshot. Save it to keep a new browser copy, or audit the source files again.';
    });
    importer.value = '';
  };
  cancel.onclick = () => operation?.abort();
  rerun.onclick = options.rerun;
  name.oninput = () => { generation++; };
  panel.addEventListener('toggle', () => { if (panel.open) void refreshList(); });
  refreshButtons();
  return {
    changed: () => { generation++; refreshButtons(); },
    reset: () => { current = undefined; name.value = ''; generation++; savedGeneration = 0; refreshButtons(); },
    setRunning: (value: boolean) => { locked = value; refreshButtons(); },
    isDirty: () => generation !== savedGeneration,
  };
}
