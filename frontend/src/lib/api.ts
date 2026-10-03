// Client for the local API (proxied at /api). The X-Annotator header selects the annotator namespace
// (A1, A2 or adjudicator); it is a workflow switch on a local-only service, not authentication.
export type Viewer = "A1" | "A2" | "adjudicator";
export const VIEWERS: Viewer[] = ["A1", "A2", "adjudicator"];

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(`API ${status}`);
  }
}

export function getViewer(): Viewer {
  try {
    const v = window.localStorage.getItem("redactor.viewer");
    return v === "A2" || v === "adjudicator" ? v : "A1";
  } catch {
    return "A1";
  }
}

export function setViewer(v: Viewer): void {
  try {
    window.localStorage.setItem("redactor.viewer", v);
  } catch {
    /* per-viewer convenience only */
  }
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const headers: Record<string, string> = { "X-Annotator": getViewer() };
  let body = init?.body;
  if (init?.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const r = await fetch(`/api${path}`, { ...init, body, headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) }, cache: "no-store" });
  const ct = r.headers.get("content-type") ?? "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new ApiError(r.status, data);
  return data as T;
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const b = e.body as { error?: string; detail?: unknown } | string;
    if (typeof b === "string") return `${e.status}: ${b.slice(0, 200)}`;
    return `${e.status}: ${b?.error ?? ""} ${b?.detail ? JSON.stringify(b.detail) : ""}`.trim();
  }
  return String(e);
}
