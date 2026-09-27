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
