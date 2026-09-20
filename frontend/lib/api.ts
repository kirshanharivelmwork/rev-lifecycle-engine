export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export class ApiError extends Error {
  status: number;
  code?: string;
  feature?: string;
  limit?: string;
  body?: Record<string, unknown>;

  constructor(message: string, status: number, body?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    if (typeof body?.error === "string") {
      this.code = body.error;
    }
    if (typeof body?.feature === "string") {
      this.feature = body.feature;
    }
    if (typeof body?.limit === "string") {
      this.limit = body.limit;
    }
  }
}

export function isBillingBlocked(error: unknown): boolean {
  if (error instanceof ApiError && error.status === 402) {
    return true;
  }
  const message = error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase();
  return message.includes("subscription inactive") || message.includes("402");
}

export async function apiFetch<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (!headers.has("Content-Type") && typeof init?.body === "string") {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      const parsed = body && typeof body === "object" ? (body as Record<string, unknown>) : undefined;
      if (typeof body?.detail === "string") {
        detail = body.detail;
      } else if (typeof body?.error === "string") {
        detail = body.error;
      } else if (body?.detail) {
        detail = JSON.stringify(body.detail);
      }
      throw new ApiError(detail || `Request failed (${response.status})`, response.status, parsed);
    } catch (exc) {
      if (exc instanceof ApiError) {
        throw exc;
      }
      detail = (await response.text()) || detail;
    }
    throw new ApiError(detail || `Request failed (${response.status})`, response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}
