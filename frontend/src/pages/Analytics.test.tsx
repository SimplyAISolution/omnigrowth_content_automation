import { screen } from "@testing-library/react";
import { AnalyticsPage } from "./Analytics";
import { baseRoutes, mockApi, renderPage } from "../test/utils";

afterEach(() => vi.unstubAllGlobals());

const row = {
  publication_id: "p1",
  content_asset_id: "a1",
  snapshot_id: "s1",
  source: "MANUAL",
  platform: "X",
  format: "THREAD",
  hook: "Hook",
  metrics: { impressions: 100 },
  derived: {},
};

function report(provenance: Record<string, unknown>) {
  return {
    campaign_id: "c1",
    coverage: { published: 1, with_snapshot: 1 },
    provenance,
    sufficiency: { satisfied: false, min_sample: 5 },
    derived: {},
    totals: {},
    publications: [row],
    by_platform: {},
    by_format: {},
    definitions: [],
    range: {},
  };
}

function routes(publishMode: string) {
  return {
    ...baseRoutes(),
    "GET /api/v1/campaigns": { body: { items: [{ id: "c1", name: "Launch", brand_id: "b1" }], next_cursor: null } },
    // The API's snapshot-source provenance says "real" for MANUAL numbers...
    "GET /api/v1/analytics/campaigns/:id/report": { body: report({ real: true, simulated: false, sources: { MANUAL: 1 } }) },
    "GET /api/v1/publications": { body: {
        items: [
          {
            id: "p1",
            brand_id: "b1",
            publish_mode: publishMode,
            evidence_class: publishMode === "MOCK" ? "SIMULATED" : publishMode === "SANDBOX" ? "SANDBOX" : "REAL",
            status: "PUBLISHED",
          },
        ],
        next_cursor: null,
      } },
    "GET /api/v1/recommendations": {
      body: {
        items: [{ id: "r1", statement: "Do more threads.", rationale: null, status: "PROPOSED", evidence_snapshot_ids: ["s1"], evidence_query: { simulated_evidence: false } }],
        next_cursor: null,
      },
    },
  };
}

describe("Analytics provenance (review MAJOR 4)", () => {
  it("treats hand-entered numbers for a mock-published post as simulated", async () => {
    mockApi(routes("MOCK"));
    renderPage(<AnalyticsPage />);
    expect(await screen.findByText("Simulated data.")).toBeInTheDocument();
    expect(screen.queryByText("Real data")).not.toBeInTheDocument();
    expect(await screen.findByText("Based on simulated data")).toBeInTheDocument();
  });

  it("calls manual numbers real only for a post published outside the mock publisher", async () => {
    mockApi(routes("MANUAL"));
    renderPage(<AnalyticsPage />);
    expect(await screen.findByText("Real data")).toBeInTheDocument();
    expect(await screen.findByText("Based on real data")).toBeInTheDocument();
  });

  it("never calls sandbox-account numbers real (Phase 13)", async () => {
    mockApi(routes("SANDBOX"));
    renderPage(<AnalyticsPage />);
    expect(await screen.findByText("Sandbox data.")).toBeInTheDocument();
    expect(screen.queryByText("Real data")).not.toBeInTheDocument();
  });

  it("labels a suggestion built on sandbox numbers as sandbox, never real (review MAJOR 3)", async () => {
    const r = routes("MANUAL");
    mockApi({
      ...r,
      "GET /api/v1/recommendations": {
        body: {
          items: [
            {
              id: "r2",
              statement: "Post threads at noon.",
              rationale: null,
              status: "PROPOSED",
              evidence_snapshot_ids: ["s9"],
              evidence_query: { simulated_evidence: false, sandbox_evidence: true, real_evidence: false },
            },
          ],
          next_cursor: null,
        },
      },
    });
    renderPage(<AnalyticsPage />);
    expect(await screen.findByText("Based on sandbox data")).toBeInTheDocument();
    expect(screen.queryByText("Based on real data")).not.toBeInTheDocument();
  });
});
