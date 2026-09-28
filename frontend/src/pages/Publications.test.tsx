import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PublicationsPage } from "./Publications";
import { baseRoutes, mockApi, renderPage } from "../test/utils";

afterEach(() => vi.unstubAllGlobals());

const uncertain = {
  id: "pub1",
  brand_id: "b1",
  content_asset_id: "a1",
  content_version_id: "v1",
  platform: "LINKEDIN",
  platform_account_id: "acc",
  publication_job_id: "j1",
  publish_mode: "MOCK",
  evidence_class: "SIMULATED",
  status: "PUBLICATION_UNCERTAIN",
  scheduled_at: "2026-09-01T10:00:00Z",
  published_at: null,
  reconciled_at: null,
  external_post_id: null,
  canonical_url: null,
  error: null,
};

function setup(role: "EDITOR" | "OWNER") {
  return mockApi({
    ...baseRoutes(role),
    "GET /api/v1/publications/jobs": { body: { items: [], next_cursor: null } },
    "GET /api/v1/publications": { body: { items: [uncertain], next_cursor: null } },
    "POST /api/v1/publications/:id/reconcile": { body: { ...uncertain, status: "PUBLISHED" } },
  });
}

describe("Resolving an uncertain publication (review MAJOR 2)", () => {
  it("lets an editor confirm it was published with the required link, but not record 'not published'", async () => {
    const api = setup("EDITOR");
    renderPage(<PublicationsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Resolve" }));
    const dialog = await screen.findByRole("dialog", { name: "Resolve an uncertain publication" });
    expect(within(dialog).getByRole("radio", { name: /It was not published/ })).toBeDisabled();
    expect(within(dialog).getByText(/only an owner can record this/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Record" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Paste the link");
    await userEvent.type(within(dialog).getByLabelText("Link to the post (required)"), "https://www.linkedin.com/feed/update/1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
    expect(api.calls.find((c) => c.method === "POST")?.body).toMatchObject({
      method: "MANUAL_CONFIRM_PUBLISHED",
      canonical_url: "https://www.linkedin.com/feed/update/1",
    });
  });

  it("requires an owner's note for 'not published'", async () => {
    const api = setup("OWNER");
    renderPage(<PublicationsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Resolve" }));
    const dialog = await screen.findByRole("dialog", { name: "Resolve an uncertain publication" });
    await userEvent.click(within(dialog).getByRole("radio", { name: /It was not published/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Record" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Say how you checked");
    await userEvent.type(within(dialog).getByLabelText("How you checked (required)"), "Checked the page feed.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({ method: "MANUAL_CONFIRM_NOT_PUBLISHED", note: "Checked the page feed." });
  });
});

describe("Live publications (Phase 13)", () => {
  const live = { ...uncertain, id: "pub2", publish_mode: "LIVE", evidence_class: "REAL", platform: "MASTODON" };
  const job = {
    id: "j2",
    brand_id: "b1",
    content_asset_id: "a2",
    content_version_id: "v2",
    platform_account_id: "acc",
    idempotency_key: "k",
    status: "PENDING",
    due_at: "2026-09-28T10:00:00Z",
    schedule_timezone: "UTC",
    publish_mode: null,
    attempt_count: 0,
    max_attempts: 5,
    dispatch_started_at: null,
    last_error_code: null,
    last_error: null,
    completed_at: null,
    created_at: "2026-09-27T10:00:00Z",
  };

  it("looks an uncertain live post up on the platform and never offers it for simulated ones", async () => {
    const api = mockApi({
      ...baseRoutes("EDITOR"),
      "GET /api/v1/publications/jobs": { body: { items: [], next_cursor: null } },
      "GET /api/v1/publications": { body: { items: [live], next_cursor: null } },
      "POST /api/v1/publications/:id/lookup": {
        body: { status: "NOT_FOUND", searched: 12, found_post_ids: [], detail: "not among the 12 most recent post(s) compared", publication: live },
      },
    });
    renderPage(<PublicationsPage />);
    expect(await screen.findByText("Real")).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Resolve" }));
    const dialog = await screen.findByRole("dialog", { name: "Resolve an uncertain publication" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Look up on the platform" }));
    expect(await within(dialog).findByRole("status")).toHaveTextContent("That is not proof it was not published");
    expect(api.calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/api/v1/publications/pub2/lookup"]);
  });

  it("previews exactly what will be sent before it is sent", async () => {
    mockApi({
      ...baseRoutes("VIEWER"),
      "GET /api/v1/publications/jobs": { body: { items: [job], next_cursor: null } },
      "GET /api/v1/publications": { body: { items: [], next_cursor: null } },
      "GET /api/v1/publications/jobs/:id/preview": {
        body: {
          publication_job_id: "j2",
          brand_id: "b1",
          platform: "MASTODON",
          platform_account_id: "acc",
          account_handle: "brand@social.example",
          due_at: "2026-09-28T10:00:00Z",
          publish_mode: "LIVE",
          evidence_class: "SANDBOX",
          simulated: false,
          content_version_id: "v2",
          details: {
            instance: "https://social.example",
            visibility: "unlisted",
            segments: ["First post", "Second post"],
            media_not_attached: ["a chart"],
          },
          blockers: [],
        },
      },
    });
    renderPage(<PublicationsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Preview" }));
    const dialog = await screen.findByRole("dialog", { name: "What will be sent" });
    expect(await within(dialog).findByText("First post")).toBeInTheDocument();
    expect(within(dialog).getByText("Second post")).toBeInTheDocument();
    expect(within(dialog).getByText(/on https:\/\/social.example/)).toBeInTheDocument();
    expect(within(dialog).getByText("Unlisted")).toBeInTheDocument();
    expect(within(dialog).getByText(/suggested but not uploaded: a chart/)).toBeInTheDocument();
    expect(within(dialog).getByText("Sandbox")).toBeInTheDocument();
  });
});

