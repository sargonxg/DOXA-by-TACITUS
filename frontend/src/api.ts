export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`/api${path}`, { ...options, headers });
  if (!response.ok) {
    if (response.status === 401 && path !== "/access/session")
      window.dispatchEvent(new Event("doxa-access-required"));
    let message = `Request failed (${response.status})`;
    try {
      const detail = await response.json();
      const error = detail.detail ?? detail.error ?? detail.message;
      message =
        typeof error === "string" ? error : JSON.stringify(error || detail);
    } catch {
      /* Keep the HTTP error when the server has no JSON body. */
    }
    throw new ApiError(message, response.status);
  }
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>);
}

export async function downloadReport(
  projectId: string,
  format: string,
  profile: string,
  filters: Record<string, string>,
): Promise<void> {
  const response = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/reports`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format, profile, filters }),
    },
  );
  if (!response.ok) {
    let detail = `Export failed (${response.status})`;
    try {
      const data = await response.json();
      detail = String(data.detail || data.error || detail);
    } catch {
      /* HTTP status is enough. */
    }
    throw new Error(detail);
  }
  const file = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const filename =
    disposition.match(/filename="?([^";]+)"?/)?.[1] ||
    `doxa-${profile}.${format}`;
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

type UploadSession = { id: string; upload_id: string; part_size: number };
type UploadPart = { partNumber: number; etag: string };
type UploadCheckpoint = {
  import_id?: string;
  upload: UploadSession;
  parts: UploadPart[];
  saved_at: number;
};

/** Reuse the logical import when a single large file is reselected. */
export function pendingUploadImport(
  projectId: string,
  file: File,
): string | undefined {
  try {
    const value = JSON.parse(
      localStorage.getItem(
        `doxa-upload:${projectId}:${file.name}:${file.size}:${file.lastModified}`,
      ) || "null",
    ) as UploadCheckpoint | null;
    if (
      value?.upload?.id &&
      typeof value.import_id === "string" &&
      Date.now() - value.saved_at < 86400000
    )
      return value.import_id;
  } catch {
    /* Missing or unavailable storage starts a fresh import. */
  }
  return undefined;
}

/** Resume a large upload when the same file is selected after interruption.
 * Only upload identifiers/checksums are cached; document contents stay in File.
 */
export async function uploadInParts(
  projectId: string,
  file: File,
  onProgress: (message: string) => void,
  importId?: string,
): Promise<void> {
  const cacheKey = `doxa-upload:${projectId}:${file.name}:${file.size}:${file.lastModified}`;
  const clear = () => {
    try {
      localStorage.removeItem(cacheKey);
    } catch {
      /* Private browser storage may be unavailable. */
    }
  };
  let checkpoint: UploadCheckpoint | null = null;
  try {
    const saved = localStorage.getItem(cacheKey);
    if (saved) {
      const parsed = JSON.parse(saved) as UploadCheckpoint;
      if (
        parsed.upload?.id &&
        parsed.upload.part_size > 0 &&
        Array.isArray(parsed.parts) &&
        parsed.parts.every(
          (part, i) =>
            part.partNumber === i + 1 && typeof part.etag === "string",
        ) &&
        Date.now() - parsed.saved_at < 86400000
      )
        checkpoint = parsed;
    }
  } catch {
    clear();
  }
  if (!checkpoint)
    checkpoint = {
      upload: await request<UploadSession>(
        `/projects/${encodeURIComponent(projectId)}/uploads/init`,
        {
          method: "POST",
          body: JSON.stringify({
            filename: file.name,
            size: file.size,
            mime_type: file.type || "application/octet-stream",
            ...(importId ? { import_id: importId } : {}),
          }),
        },
      ),
      parts: [],
      import_id: importId,
      saved_at: Date.now(),
    };
  const { upload, parts } = checkpoint;
  const save = () => {
    try {
      localStorage.setItem(
        cacheKey,
        JSON.stringify({
          upload,
          parts,
          import_id: checkpoint?.import_id || importId,
          saved_at: Date.now(),
        }),
      );
    } catch {
      /* Upload still works without cross-refresh recovery. */
    }
  };
  save();
  const partSize = upload.part_size || 8388608;
  try {
    for (
      let offset = parts.length * partSize, partNumber = parts.length + 1;
      offset < file.size;
      offset += partSize, partNumber++
    ) {
      onProgress(
        `${file.name} · ${Math.floor((offset / file.size) * 100)}% uploaded${parts.length ? " · checkpoint saved" : ""}`,
      );
      let result: UploadPart | null = null;
      for (let attempt = 0; attempt < 3 && !result; attempt++) {
        try {
          const response = await fetch(
            `/api/uploads/${encodeURIComponent(upload.id)}/parts/${partNumber}`,
            {
              method: "PUT",
              headers: { "Content-Type": "application/octet-stream" },
              body: file.slice(offset, offset + partSize),
            },
          );
          if (response.status === 404 || response.status === 409) {
            clear();
            throw new ApiError(
              "The previous upload is no longer available. Select the file again to restart.",
              response.status,
            );
          }
          if (!response.ok)
            throw new Error(
              `Part ${partNumber} could not be uploaded (${response.status}).`,
            );
          result = (await response.json()) as UploadPart;
        } catch (error) {
          if (error instanceof ApiError || attempt === 2) throw error;
          await new Promise((resolve) =>
            window.setTimeout(resolve, 400 * (attempt + 1)),
          );
        }
      }
      if (!result?.etag)
        throw new Error("The server did not return an upload checkpoint.");
      parts.push({ partNumber, etag: result.etag });
      save();
    }
    onProgress(`${file.name} · upload complete · extracting source text…`);
    await request(`/uploads/${encodeURIComponent(upload.id)}/complete`, {
      method: "POST",
      body: JSON.stringify({ parts }),
    });
    clear();
  } catch (error) {
    if (error instanceof ApiError && error.status >= 400 && error.status < 500)
      clear();
    const detail =
      error instanceof Error ? error.message : "Connection interrupted.";
    throw new Error(
      `${detail} Select the same file to resume any saved upload parts.`,
    );
  }
}
