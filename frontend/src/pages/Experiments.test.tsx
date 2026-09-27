import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExperimentDetailPage } from "./Experiments";
import { baseRoutes, mockApi, renderPage } from "../test/utils";

afterEach(() => vi.unstubAllGlobals());

function experiment(status: string) {
  return {
    id: "e1",
    brand_id: "b1",
    campaign_id: null,
    name: "Hook test",
    hypothesis: "Questions beat statements.",
    variable: "HOOK",
    metric: { key: "engagement_rate" },
    assignment: "RANDOMIZED",
    design: {},
    min_units_per_variant: 30,
    confidence_level: 0.95,
    start_at: "2026-09-01T00:00:00Z",
    end_at: "2030-01-01T00:00:00Z",
    status,
    verdict: null,
    winner_variant_id: null,
    variants: [{ id: "v1", label: "A", description: "Statement", is_control: true, units: 3, sample_size: null, observed_value: null }],
  };
}

const noResult = { status: 404, body: { code: "NOT_FOUND", message: "not evaluated" } };
const render = () => renderPage(<ExperimentDetailPage />, { path: "/experiments/:experimentId", route: "/experiments/e1" });

describe("Experiment evaluation (review MAJOR 3)", () => {
  it("offers no early look while posts are still being allocated", async () => {
    mockApi({ ...baseRoutes(), "GET /api/v1/experiments/:id": { body: experiment("RUNNING") }, "GET /api/v1/experiments/:id/results": noResult });
    render();
    expect(await screen.findByText(/There are no early looks/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /evaluat/i })).not.toBeInTheDocument();
  });

  it("explains that evaluating before the fixed end changed nothing (WINDOW_OPEN)", async () => {
    mockApi({
      ...baseRoutes(),
      "GET /api/v1/experiments/:id": { body: experiment("COLLECTING") },
      "GET /api/v1/experiments/:id/results": noResult,
      "POST /api/v1/experiments/:id/evaluate": {
        body: { id: "job1", kind: "EXPERIMENT_EVALUATION", status: "QUEUED", queued_at: "2026-09-01T00:00:00Z" },
      },
      "GET /api/v1/jobs/:id": {
        body: { id: "job1", kind: "EXPERIMENT_EVALUATION", status: "SUCCEEDED", result: { no_transition: "WINDOW_OPEN" }, queued_at: "2026-09-01T00:00:00Z" },
      },
    });
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Run the final evaluation" }));
    expect(await screen.findByText(/Nothing was evaluated: the collection period is still open/)).toBeInTheDocument();
  });
});
