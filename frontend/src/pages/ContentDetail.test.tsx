import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContentDetailPage } from "./ContentDetail";
import { asset, baseRoutes, mockApi, renderPage, type Handler } from "../test/utils";

afterEach(() => vi.unstubAllGlobals());

const empty = { body: { items: [], next_cursor: null } };

function routes(role: "VIEWER" | "EDITOR", current: () => unknown, action?: Handler) {
  return {
    ...baseRoutes(role),
    "GET /api/v1/content/:id": () => ({ body: current() }),
    "GET /api/v1/content/:id/versions": empty,
    "GET /api/v1/content/:id/derivatives": empty,
    "GET /api/v1/approvals/:id/history": empty,
    "GET /api/v1/publications/jobs": empty,
    "GET /api/v1/publications": empty,
    ...(action ? { "POST /api/v1/approvals/:id/actions": action } : {}),
  };
}

const render = () => renderPage(<ContentDetailPage />, { path: "/content/:assetId", route: "/content/a1?from=approvals" });

describe("Content review", () => {
  it("offers the approval decisions and approves with the reviewed version and an Idempotency-Key", async () => {
    let state = asset();
    const api = mockApi(
      routes("EDITOR", () => state, ({ body }) => {
        state = asset({ status: "APPROVED", approved_version_id: "v1" });
        return { body: { asset: state, job: null, publication_job: null, version: null, request: body } };
      }),
    );
    render();
    const actions = await screen.findByRole("region", { name: "What you can do" });
    for (const label of ["Approve", "Request revision", "Reject", "Edit", "Regenerate"]) {
      expect(within(actions).getByRole("button", { name: label })).toBeEnabled();
    }
    await userEvent.click(within(actions).getByRole("button", { name: "Approve" }));
    expect(await within(actions).findByText(/Approved\. Next, schedule it/)).toBeInTheDocument();
    const post = api.calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ action: "APPROVE", content_version_id: "v1" });
    expect(post?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(post?.headers.get("Authorization")).toBe("Bearer og_test_token");
    // After approval the screen offers scheduling, and says it is simulated.
    expect(await within(actions).findByRole("button", { name: "Schedule" })).toBeInTheDocument();
    expect(within(actions).getByRole("button", { name: "Publish now (simulated)" })).toBeInTheDocument();
  });

  it("requires a reason before rejecting, and shows the API's refusal in the dialog", async () => {
    mockApi(
      routes("EDITOR", () => asset(), () => ({
        status: 409,
        body: { code: "INVALID_STATE_TRANSITION", message: "The asset changed.", correlation_id: "c-9" },
      })),
    );
    render();
    await userEvent.click(await screen.findByRole("button", { name: "Reject" }));
    const dialog = await screen.findByRole("dialog", { name: "Reject this content" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Please say why");
    await userEvent.type(within(dialog).getByLabelText("Reason (required)"), "Off-brand tone");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(within(dialog).getByRole("alert")).toHaveTextContent("Not possible in the current state"));
    expect(within(dialog).getByText("Reference: c-9")).toBeInTheDocument();
  });

  it("keeps decision buttons visible but disabled for a viewer, with the reason", async () => {
    mockApi(routes("VIEWER", () => asset()));
    render();
    const approve = await screen.findByRole("button", { name: "Approve" });
    expect(approve).toBeDisabled();
    expect(approve).toHaveAccessibleDescription("Requires the editor role.");
  });

  it("explains why failed content cannot be approved and shows the failing check", async () => {
    mockApi(
      routes("EDITOR", () =>
        asset({
          status: "QA_FAILED",
          qa: [
            {
              id: "q1",
              content_version_id: "v1",
              stage: "BRAND",
              outcome: "FAIL",
              score: "40",
              findings: [{ code: "BANNED_PHRASE", severity: "FAIL", message: "uses banned phrase 'game-changer'" }],
              checker: "c",
              created_at: "2026-09-01T00:00:00Z",
            },
          ],
        }),
      ),
    );
    render();
    expect(await screen.findByText(/cannot be approved until the failed checks are fixed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByText(/uses banned phrase 'game-changer'/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeEnabled();
  });

  it("labels published content as simulated", async () => {
    mockApi(routes("EDITOR", () => asset({ status: "PUBLISHED", approved_version_id: "v1" })));
    render();
    expect(await screen.findAllByText("Published (simulated)")).not.toHaveLength(0);
    expect(screen.getByText(/no platform was contacted and no real audience saw it/)).toBeInTheDocument();
  });

  it("shows thread posts separately and platform production notes", async () => {
    mockApi(
      routes("EDITOR", () =>
        asset({
          platform: "MASTODON",
          format: "THREAD",
          current_version: {
            ...asset().current_version,
            hook: "First",
            body: "First post\n\n---\n\nSecond post",
            platform_payload: { hashtags: ["#LocalAI"], cited_claims: [], content_warning: "AI talk" },
          },
        }),
      ),
    );
    render();
    expect(await screen.findByText("Posts (2)")).toBeInTheDocument();
    expect(screen.getByText("Second post")).toBeInTheDocument();
    expect(screen.getByText("AI talk")).toBeInTheDocument();
  });
});
