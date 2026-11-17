import { request } from "../api";
import { EMPTY_FACETS, EMPTY_METRICS, EMPTY_SCOPE } from "./types";
import type {
  Context,
  Item,
  ModelProfile,
  Observation,
  Page,
  QueryResult,
  Scope,
  SourceDocument,
} from "./types";
export const record = (value: unknown): Item =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Item)
    : {};
export const list = <T>(value: unknown): T[] =>
  Array.isArray(value) ? (value as T[]) : [];
export const message = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
export function normalizeQuery(raw: QueryResult): QueryResult {
  return {
    ...raw,
    scope: { ...EMPTY_SCOPE, ...raw.scope },
    documents: raw.documents || { items: [], total: 0, next_cursor: null },
    findings: raw.findings || { items: [], total: 0, next_cursor: null },
    metrics: { ...EMPTY_METRICS, ...raw.metrics },
    facets: { ...EMPTY_FACETS, ...raw.facets },
    timeline: list(raw.timeline),
    matrix: list(raw.matrix),
  };
}
export async function workspaceQuery(
  project: string,
  scope: Scope,
  signal?: AbortSignal,
): Promise<QueryResult> {
  return normalizeQuery(
    await request<QueryResult>(
      `/v3/projects/${encodeURIComponent(project)}/query`,
      { method: "POST", body: JSON.stringify(scope), signal },
    ),
  );
}
export async function restoreQuery(id: string): Promise<QueryResult> {
  return normalizeQuery(
    await request<QueryResult>(`/v3/queries/${encodeURIComponent(id)}`),
  );
}
export async function queryPage<T>(
  id: string,
  kind: "documents" | "findings",
  cursor?: string | null,
): Promise<Page<T>> {
  const params = new URLSearchParams({ limit: "50" });
  if (cursor) params.set("cursor", cursor);
  return request<Page<T>>(
    `/v3/queries/${encodeURIComponent(id)}/${kind}?${params}`,
  );
}
export async function contextFor(
  document: string,
  finding?: string,
  block?: string,
  cursor?: number,
): Promise<Context> {
  const params = new URLSearchParams({ radius: "2" });
  if (finding) params.set("finding_id", finding);
  if (block) params.set("block_id", block);
  if (cursor !== undefined) params.set("cursor", String(cursor));
  const raw = await request<Context & SourceDocument>(
    `/v3/documents/${encodeURIComponent(document)}/context?${params}`,
  );
  return {
    ...raw,
    document: raw.document || raw,
    blocks: list(raw.blocks || raw.document?.blocks),
  };
}
export async function models(): Promise<ModelProfile[]> {
  const result = await request<unknown>("/v3/models");
  return list<ModelProfile>(
    Array.isArray(result)
      ? result
      : record(result).profiles || record(result).models,
  );
}
export async function submitReview(
  id: string,
  review: { review_status: string; review_note: string; corrections: Item },
): Promise<Observation> {
  return request(`/v3/findings/${encodeURIComponent(id)}/review`, {
    method: "PATCH",
    body: JSON.stringify({
      ...review,
      reviewer: "Local reviewer",
      reviewer_type: "human",
    }),
  });
}
export async function exportQuery(
  project: string,
  query: string,
  format: string,
  profile: string,
): Promise<void> {
  const response = await fetch(
    `/api/v3/projects/${encodeURIComponent(project)}/reports`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query_id: query, format, profile }),
    },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(String(body.detail || "Report generation failed."));
  }
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download =
    response.headers
      .get("Content-Disposition")
      ?.match(/filename="?([^";]+)/)?.[1] || `doxa-report.${format}`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
