import type {
  AnalysisState,
  AnalyzeResponse,
  FileDetail,
  GraphLevel,
  GraphResponse,
  Insights,
  SourceFileSummary,
} from "./types";

/**
 * The backend's CORS allowlist already covers the dev server (5173) and the
 * containerised frontend (3000), so requests go straight to it rather than
 * through a proxy that would have to be configured twice.
 */
export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ??
  "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    // fetch only rejects on a transport failure, which for this app almost
    // always means the backend is not running -- say that rather than "Failed
    // to fetch".
    throw new ApiError(`Cannot reach the RepoMap API at ${API_BASE}.`, 0);
  }

  if (!response.ok) {
    throw new ApiError(await readError(response), response.status);
  }
  return (await response.json()) as T;
}

/** FastAPI puts the message in `detail`, which is a string or a list of validation errors. */
async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      return detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ");
    }
  } catch {
    /* fall through to the status text */
  }
  return `${response.status} ${response.statusText}`;
}

export const api = {
  analyze: (repoUrl: string) =>
    request<AnalyzeResponse>("/analyze", {
      method: "POST",
      body: JSON.stringify({ repo_url: repoUrl }),
    }),

  status: (id: string, signal?: AbortSignal) =>
    request<AnalysisState>(`/analysis/${id}`, { signal }),

  graph: (id: string, level: GraphLevel) =>
    request<GraphResponse>(`/analysis/${id}/graph?level=${level}`),

  insights: (id: string) => request<Insights>(`/analysis/${id}/insights`),

  files: (id: string) => request<SourceFileSummary[]>(`/analysis/${id}/files`),

  fileDetail: (id: string, fileId: string) =>
    request<FileDetail>(`/analysis/${id}/files/${fileId}`),
};
