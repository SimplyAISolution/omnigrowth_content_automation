import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setToken } from "../api/client";
import { AuthProvider } from "../auth/auth";
import { BrandProvider } from "../lib/brand";

export type Handler = (req: { method: string; path: string; search: URLSearchParams; body: unknown; headers: Headers }) => { status?: number; body: unknown } | undefined;

export interface MockApi {
  calls: { method: string; path: string; body: unknown; headers: Headers }[];
}

/** Replace fetch with a router over path patterns ("/api/v1/content/:id"). Unmatched → 404. */
export function mockApi(routes: Record<string, Handler | { status?: number; body: unknown }>): MockApi {
  const api: MockApi = { calls: [] };
  const compiled = Object.entries(routes).map(([key, handler]) => {
    const [method, pattern] = key.split(" ") as [string, string];
    const re = new RegExp(`^${pattern.replace(/:[^/]+/g, "[^/]+")}$`);
    return { method, re, handler };
  });
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(String(input), init);
    const url = new URL(request.url, "http://localhost");
    const text = request.method === "GET" ? "" : await request.clone().text();
    const body = text ? JSON.parse(text) : undefined;
    api.calls.push({ method: request.method, path: url.pathname, body, headers: request.headers });
    const route = compiled.find((r) => r.method === request.method && r.re.test(url.pathname));
    const out = route
      ? typeof route.handler === "function"
        ? route.handler({ method: request.method, path: url.pathname, search: url.searchParams, body, headers: request.headers })
        : route.handler
      : { status: 404, body: { code: "NOT_FOUND", message: `no mock for ${request.method} ${url.pathname}` } };
    const res = out ?? { status: 404, body: { code: "NOT_FOUND", message: "not found" } };
    return new Response(JSON.stringify(res.body), { status: res.status ?? 200, headers: { "Content-Type": "application/json" } });
  });
  return api;
}

export const BRAND = {
  id: "b1",
  name: "Demo",
  display_name: "Demo brand",
  slug: "demo",
  timezone: "America/New_York",
  auto_approve_enabled: false,
  default_routing_profile: "LOCAL_ONLY",
  archived_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

export function baseRoutes(role: "VIEWER" | "EDITOR" | "OWNER" = "EDITOR") {
  return {
    "GET /api/v1/auth/me": { body: { email: `${role.toLowerCase()}@example.test`, role, user_id: "u1" } },
    "GET /api/v1/brands": { body: { items: [BRAND], next_cursor: null } },
    "GET /api/v1/campaigns": { body: { items: [], next_cursor: null } },
    "GET /api/v1/platforms": {
      body: {
        items: [
          {
            platform: "LINKEDIN",
            has_writer: true,
            formats: ["TEXT_POST", "CAROUSEL"],
            limits: { max_chars: 3000, max_hashtags: 5 },
            capability_status: "UNVERIFIED",
            effective_publisher: "MOCK",
            live_publishing_available: false,
          },
        ],
        next_cursor: null,
      },
    },
    "GET /api/v1/platform-accounts": { body: { items: [], next_cursor: null } },
  };
}

/** Render a page inside the real providers, signed in (token set before mount). */
export function renderPage(ui: ReactElement, { path = "/", route = "/" } = {}) {
  setToken("og_test_token");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <AuthProvider>
          <BrandProvider>
            <Routes>
              <Route path={path} element={ui} />
            </Routes>
          </BrandProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export function asset(overrides: Record<string, unknown> = {}) {
  return {
    id: "a1",
    brand_id: "b1",
    campaign_id: "c1",
    plan_item_id: "p1",
    content_idea_id: null,
    platform: "LINKEDIN",
    format: "TEXT_POST",
    status: "AWAITING_APPROVAL",
    angle: "TACTICAL",
    funnel_stage: "AWARENESS",
    current_version_id: "v1",
    approved_version_id: null,
    parent_asset_id: null,
    root_asset_id: "a1",
    lineage_depth: 0,
    source_version_id: null,
    repurpose_instructions: null,
    quality_score: "90",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    current_version: {
      id: "v1",
      content_asset_id: "a1",
      version_no: 1,
      origin: "GENERATED",
      based_on_version_id: null,
      title: null,
      hook: "Your data can stay home.",
      body: "Local AI keeps client data on the machine.",
      cta: "Follow for more",
      media_requirements: [],
      platform_payload: { hashtags: ["#LocalAI"], cited_claims: [1] },
      body_hash: "x",
      word_count: 8,
      char_count: 44,
      hook_type: "statement",
      cta_type: "follow",
      prompt_version_id: null,
      model_run_id: null,
      agent_run_id: null,
      author_actor_type: "AGENT",
      author_actor_id: "linkedin_writer",
      created_at: "2026-09-01T00:00:00Z",
    },
    qa: [
      { id: "q1", content_version_id: "v1", stage: "STRUCTURAL", outcome: "PASS", score: "100", findings: [], checker: "c", created_at: "2026-09-01T00:00:00Z" },
      {
        id: "q2",
        content_version_id: "v1",
        stage: "BRAND",
        outcome: "WARN",
        score: "90",
        findings: [{ code: "NEAR_DUPLICATE", severity: "WARN", message: "very similar to existing content" }],
        checker: "c",
        created_at: "2026-09-01T00:00:00Z",
      },
    ],
    ...overrides,
  };
}
