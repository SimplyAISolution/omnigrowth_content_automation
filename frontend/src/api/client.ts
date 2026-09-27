/**
 * Typed API client generated from the committed OpenAPI snapshot (ADR-0007).
 *
 * - Bearer token on every request (API_PLAN §1.2); no cookies are ever used.
 * - Every POST carries an Idempotency-Key (API_PLAN §1.6). Callers that may retry the same
 *   user action pass their own key so a retry is recognised as the same request.
 * - Non-2xx responses become an `ApiError` carrying the glossary envelope (code, message,
 *   correlation_id, details) so screens can explain what happened.
 *
 * The client holds no business rules: the API decides what is allowed.
 */
import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

export interface ErrorEnvelope {
  code: string;
  message: string;
  correlation_id?: string;
  details?: Record<string, unknown>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId: string | undefined;
  readonly details: Record<string, unknown>;

  constructor(status: number, body: Partial<ErrorEnvelope> | undefined) {
    super(body?.message ?? `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.code ?? (status === 0 ? "NETWORK_ERROR" : "UNKNOWN_ERROR");
    this.correlationId = body?.correlation_id;
    this.details = body?.details ?? {};
  }

  /** Field-level messages from a 422 VALIDATION_FAILURE, keyed by the last path segment. */
  fieldErrors(): Record<string, string> {
    const errors = this.details["errors"];
    const out: Record<string, string> = {};
    if (Array.isArray(errors)) {
      for (const e of errors as { loc?: unknown[]; msg?: string }[]) {
        const key = String(e.loc?.[e.loc.length - 1] ?? "form");
        out[key] = (e.msg ?? "Invalid value").replace(/^Value error, /, "");
      }
    }
    return out;
  }
}

const TOKEN_KEY = "og.token";
let token: string | null = readToken();
const unauthorizedListeners = new Set<() => void>();

function readToken(): string | null {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // storage blocked: the user simply signs in again
  }
}

export function setToken(value: string | null): void {
  token = value;
  try {
    if (value) window.sessionStorage.setItem(TOKEN_KEY, value);
    else window.sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage unavailable: the token lives in memory for this page only.
  }
}

export function getToken(): string | null {
  return token;
}

export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

export function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const auth: Middleware = {
  onRequest({ request }) {
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    if (request.method === "POST" && !request.headers.has("Idempotency-Key")) {
      request.headers.set("Idempotency-Key", newIdempotencyKey());
    }
    return request;
  },
  onResponse({ response, request }) {
    if (response.status === 401 && !request.url.endsWith("/auth/login")) {
      unauthorizedListeners.forEach((fn) => fn());
    }
    return response;
  },
};

// Same-origin API (the dev/preview server proxies /api); absolute so it also works under test runners.
export const api = createClient<paths>({
  baseUrl: typeof window === "undefined" ? "" : window.location.origin,
  // Late-bound so tests (and any instrumentation) can replace fetch after import.
  fetch: (request) => globalThis.fetch(request),
});
api.use(auth);

/**
 * Unwrap an openapi-fetch result: return data or throw `ApiError`. Network failures (no
 * response at all) become status 0 so screens can offer a retry.
 */
export async function call<T>(
  promise: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result: { data?: T; error?: unknown; response: Response };
  try {
    result = await promise;
  } catch (cause) {
    const err = new ApiError(0, {
      code: "NETWORK_ERROR",
      message: "The OmniGrowth API could not be reached. Check that it is running, then retry.",
    });
    (err as Error & { cause?: unknown }).cause = cause;
    throw err;
  }
  if (!result.response.ok) {
    throw new ApiError(result.response.status, result.error as Partial<ErrorEnvelope>);
  }
  return result.data as T;
}

/** Request init for a caller-chosen Idempotency-Key (a retry of the same user action). */
export function idem(key: string): { headers: { "Idempotency-Key": string } } {
  return { headers: { "Idempotency-Key": key } };
}
