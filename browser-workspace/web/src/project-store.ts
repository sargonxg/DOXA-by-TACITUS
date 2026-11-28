import { checkProjectAbort, prepareProjectRecord, prepareProjectSnapshot, ProjectError } from './project-format.ts';
import type { ProjectOptions, ProjectRecord, ProjectSnapshot } from './project-format';
export type { ProjectRecord, ProjectSnapshot } from './project-format';

export function projectDatabaseName(account: string | null): string {
  if (account === null) return 'doxa-review-projects-local';
  if (!/^[a-f0-9]{64}$/.test(account)) throw new ProjectError('unavailable', 'Sign in to open your project storage.');
  return 'doxa-review-projects-' + account;
}
function currentDatabaseName(): string {
  const account = typeof document === 'undefined' ? null : document.querySelector<HTMLMetaElement>('meta[name="doxa-account"]')?.content;
  if (typeof location !== 'undefined' && location.hostname === 'doxa.tacitus.me' && !account) throw new ProjectError('unavailable', 'Sign in to open your project storage.');
  return projectDatabaseName(account || null);
}
const VERSION = 1;
const PROJECTS = 'projects';
const SUMMARIES = 'summaries';

export type ProjectSummary = {
  id: string;
  name: string;
  revision: number;
  createdAt: string;
  updatedAt: string;
  documentCount: number;
  decisionCount: number;
  sourceBytes: number;
};
export type SaveProjectOptions = ProjectOptions & { name: string; id?: string; expectedRevision?: number };
export type DeleteProjectOptions = ProjectOptions & { expectedRevision?: number };

function storageError(error: unknown) {
  if (error instanceof ProjectError) return error;
  const name = error && typeof error === 'object' && 'name' in error ? String(error.name) : '';
  if (name === 'QuotaExceededError') return new ProjectError('quota', 'Browser storage is full. Download a project backup or remove a saved project, then retry.');
  if (name === 'AbortError') return new ProjectError('aborted', 'Project operation canceled.');
  if (name === 'DataCloneError') return new ProjectError('invalid', 'This project contains data the browser could not save.');
  return new ProjectError('unavailable', 'Browser project storage is unavailable. Download a portable project to retain the current review.');
}

function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === 'undefined') return Promise.reject(new ProjectError('unavailable', 'This browser does not provide local project storage.'));
  return new Promise((resolve, reject) => {
    let finished = false;
    const request = indexedDB.open(currentDatabaseName(), VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(PROJECTS)) database.createObjectStore(PROJECTS, { keyPath: 'id' });
      if (!database.objectStoreNames.contains(SUMMARIES)) {
        const store = database.createObjectStore(SUMMARIES, { keyPath: 'id' });
        store.createIndex('updatedAt', 'updatedAt');
      }
    };
    request.onsuccess = () => {
      if (finished) { request.result.close(); return; }
      finished = true;
      request.result.onversionchange = () => request.result.close();
      resolve(request.result);
    };
    request.onerror = () => {
      if (finished) return;
      finished = true;
      reject(storageError(request.error));
    };
    request.onblocked = () => {
      if (finished) return;
      finished = true;
      reject(new ProjectError('unavailable', 'Another tab is holding an older project store open. Close that tab and retry.'));
    };
  });
}

async function operation<T>(stores: string[], mode: IDBTransactionMode,
  execute: (transaction: IDBTransaction, done: (value: T) => void, fail: (error: unknown) => void) => void,
  signal?: AbortSignal): Promise<T> {
  checkProjectAbort(signal);
  const database = await openDatabase();
  try { checkProjectAbort(signal); } catch (error) { database.close(); throw error; }
  return new Promise((resolve, reject) => {
    let transaction: IDBTransaction;
    try { transaction = database.transaction(stores, mode); }
    catch (error) { database.close(); reject(storageError(error)); return; }
    let result: T;
    let failure: ProjectError | undefined;
    const cleanup = () => { signal?.removeEventListener('abort', cancel); database.close(); };
    const fail = (error: unknown) => {
      failure = storageError(error);
      try { transaction.abort(); } catch { /* A completed transaction has already committed its result. */ }
    };
    const cancel = () => fail(new ProjectError('aborted', 'Project operation canceled.'));
    signal?.addEventListener('abort', cancel, { once: true });
    transaction.oncomplete = () => { cleanup(); resolve(result); };
    transaction.onabort = () => { cleanup(); reject(failure ?? storageError(transaction.error)); };
    transaction.onerror = event => {
      const target = event.target as IDBRequest | null;
      failure ??= storageError(target?.error ?? transaction.error);
    };
    try { execute(transaction, value => { result = value; }, fail); }
    catch (error) { fail(error); }
  });
}

function summary(project: ProjectRecord): ProjectSummary {
  return { id: project.id, name: project.name, revision: project.revision,
    createdAt: project.createdAt, updatedAt: project.updatedAt,
    documentCount: project.snapshot.documents.length, decisionCount: project.snapshot.decisions.length,
    sourceBytes: project.snapshot.documents.reduce((total, document) => total + document.file.size, 0) };
}

function revision(value: number | undefined) {
  if (value !== undefined && (!Number.isSafeInteger(value) || value < 0)) throw new ProjectError('invalid', 'Project revision is invalid.');
  return value ?? 0;
}

export async function saveProject(value: ProjectSnapshot, options: SaveProjectOptions): Promise<ProjectRecord> {
  const name = options.name.trim();
  if (!name || name.length > 120) throw new ProjectError('invalid', 'Use a project name between one and 120 characters.');
  const id = options.id ?? crypto.randomUUID();
  if (!id || id.length > 200 || !/^[\w.-]+$/.test(id)) throw new ProjectError('invalid', 'Project ID is invalid.');
  const expected = revision(options.expectedRevision);
  const snapshot = await prepareProjectSnapshot(value, options);
  return operation<ProjectRecord>([PROJECTS, SUMMARIES], 'readwrite', (transaction, done, fail) => {
    const projects = transaction.objectStore(PROJECTS);
    const request = projects.get(id);
    request.onsuccess = () => {
      try {
        const current = request.result as ProjectRecord | undefined;
        if ((current?.revision ?? 0) !== expected) {
          throw new ProjectError('conflict', 'This project changed in another tab. Reopen it or save a new copy.');
        }
        const now = new Date().toISOString();
        const saved: ProjectRecord = { format: 'doxa-project', version: 1, id, name,
          revision: expected + 1, createdAt: current?.createdAt ?? now, updatedAt: now, snapshot };
        projects.put(saved);
        transaction.objectStore(SUMMARIES).put(summary(saved));
        done(saved);
      } catch (error) { fail(error); }
    };
  }, options.signal);
}

export async function listProjects(options: ProjectOptions = {}): Promise<ProjectSummary[]> {
  const summaries = await operation<ProjectSummary[]>([SUMMARIES], 'readonly', (transaction, done) => {
    const request = transaction.objectStore(SUMMARIES).getAll();
    request.onsuccess = () => done(request.result as ProjectSummary[]);
  }, options.signal);
  return summaries.sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
}

export async function loadProject(id: string, options: ProjectOptions = {}): Promise<ProjectRecord> {
  const value = await operation<unknown>([PROJECTS], 'readonly', (transaction, done) => {
    const request = transaction.objectStore(PROJECTS).get(id);
    request.onsuccess = () => done(request.result);
  }, options.signal);
  if (!value) throw new ProjectError('not_found', 'This saved project is unavailable.');
  return prepareProjectRecord(value, options);
}

export async function deleteProject(id: string, options: DeleteProjectOptions = {}): Promise<boolean> {
  const expected = options.expectedRevision === undefined ? undefined : revision(options.expectedRevision);
  return operation<boolean>([PROJECTS, SUMMARIES], 'readwrite', (transaction, done, fail) => {
    const summaries = transaction.objectStore(SUMMARIES);
    const request = summaries.get(id);
    request.onsuccess = () => {
      const current = request.result as ProjectSummary | undefined;
      if (!current) { fail(new ProjectError('not_found', 'This saved project is unavailable.')); return; }
      if (expected !== undefined && current.revision !== expected) {
        fail(new ProjectError('conflict', 'This project changed in another tab. Reopen it before deleting.'));
        return;
      }
      transaction.objectStore(PROJECTS).delete(id);
      summaries.delete(id);
      done(true);
    };
  }, options.signal);
}

export async function projectStorageStatus() {
  if (!navigator.storage?.estimate) return undefined;
  const estimate = await navigator.storage.estimate();
  const persistent = navigator.storage.persisted ? await navigator.storage.persisted() : false;
  return { ...estimate, persistent };
}

export async function requestProjectRetention() {
  return navigator.storage?.persist ? navigator.storage.persist() : false;
}
