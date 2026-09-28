/** Typed HTTP client for the document-gen backend API. */

const API_URL = (
  process.env.DOCUMENT_GEN_API_URL ?? "http://127.0.0.1:8000"
).replace(/\/+$/, "");

/** Snapshot of a backend generation job (mirrors server JobStatus). */
export interface JobStatus {
  id: string;
  /** "running" | "done" | "error" */
  status: string;
  total: number;
  completed: number;
  error: string | null;
  company_ids: number[];
  result: unknown;
  logs: string[];
}

/** Returned by {@link apiWaitJob} when the job did not finish in time. */
export interface JobTimeout {
  __timeout: true;
}

export function isJobTimeout(value: unknown): value is JobTimeout {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { __timeout?: unknown }).__timeout === true
  );
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(API_URL + path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data: unknown = text;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      // Non-JSON body (e.g. plain-text error): keep the raw string.
    }
  }
  if (!res.ok) {
    const detail = typeof data === "string" ? data : JSON.stringify(data);
    throw new Error(
      `backend ${method} ${path} failed (${res.status}): ${detail || res.statusText}`,
    );
  }
  return data as T;
}

/** GET a backend endpoint and return the parsed JSON body. */
export function apiGet<T>(path: string): Promise<T> {
  return request<T>("GET", path);
}

/** Send a request with an optional JSON body; return the parsed JSON body. */
export function apiSend<T>(method: string, path: string, body?: unknown): Promise<T> {
  return request<T>(method, path, body);
}

/**
 * Poll a generation job until it finishes.
 *
 * Resolves with the job's `result` when `status` is `done`; rejects with the
 * job's error message when `status` is `error`; resolves with
 * `{ __timeout: true }` when *timeoutMs* elapses (the job may still finish
 * server-side).
 */
export async function apiWaitJob<T>(
  jobId: string,
  timeoutMs = 180_000,
): Promise<T | JobTimeout> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const job = await apiGet<JobStatus>(`/api/companies/jobs/${jobId}`);
    if (job.status === "done") return job.result as T;
    if (job.status === "error") {
      throw new Error(`job ${jobId} failed: ${job.error ?? "unknown error"}`);
    }
    if (Date.now() >= deadline) return { __timeout: true };
    const { promise: tick, resolve } = Promise.withResolvers<void>();
    setTimeout(resolve, 1000);
    await tick;
  }
}
