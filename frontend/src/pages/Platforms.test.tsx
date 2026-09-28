import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PlatformsPage } from "./Platforms";
import { baseRoutes, mockApi, renderPage } from "../test/utils";

afterEach(() => vi.unstubAllGlobals());

const account = {
  id: "acc1",
  brand_id: "b1",
  platform: "MASTODON",
  account_type: "INSTANCE_USER",
  handle: "brand@social.example",
  display_name: null,
  timezone: null,
  live_enabled: false,
  sandbox: true,
  settings: { instance_base_url: "https://social.example", visibility: "unlisted" },
  effective_publisher: "MOCK",
  has_open_uncertain: false,
  live_connector_available: true,
  has_credential: false,
  live_blockers: ["PUBLISH_MODE_NOT_LIVE", "ACCOUNT_NOT_LIVE_ENABLED", "NO_CREDENTIAL"],
  created_at: "2026-09-27T00:00:00Z",
};

const TOKEN = "test-token-value-only";

function setup() {
  return mockApi({
    ...baseRoutes("OWNER"),
    "GET /api/v1/platform-accounts": { body: { items: [account], next_cursor: null } },
    "GET /api/v1/platform-accounts/:id/credentials": { body: { items: [], next_cursor: null } },
    "POST /api/v1/platform-accounts/:id/credentials": {
      status: 201,
      body: {
        id: "cred1",
        platform_account_id: "acc1",
        kind: "OAUTH2",
        scopes: ["read:accounts", "read:statuses", "write:statuses"],
        fingerprint: "a1b2c3d4",
        kek_id: "kek-abc",
        expires_at: null,
        revoked_at: null,
        created_by: "u1",
        revoked_by: null,
        created_at: "2026-09-27T00:00:00Z",
      },
    },
    "POST /api/v1/platform-accounts/:id/live": { body: { ...account, live_enabled: true } },
  });
}

describe("Managing a live connection (Phase 13)", () => {
  it("stores the token write-only with least-privilege scopes and clears the field", async () => {
    const api = setup();
    renderPage(<PlatformsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Manage connection" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("No access token is stored.")).toBeInTheDocument();
    const field = within(dialog).getByLabelText("Access token");
    expect(field).toHaveAttribute("type", "password");
    expect(field).toHaveAttribute("autocomplete", "off");
    await userEvent.type(field, TOKEN);
    await userEvent.click(within(dialog).getByRole("button", { name: "Store token" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST" && c.path.endsWith("/credentials"))).toBe(true));
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({
      kind: "OAUTH2",
      secret: TOKEN,
      scopes: ["write:statuses", "read:statuses", "read:accounts"],
    });
    await waitFor(() => expect(field).toHaveValue(""));
    expect(screen.queryByDisplayValue(TOKEN)).not.toBeInTheDocument();
  });

  it("enables live publishing only after the exact phrase is typed", async () => {
    const api = setup();
    renderPage(<PlatformsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Manage connection" }));
    const dialog = await screen.findByRole("dialog");
    const enable = within(dialog).getByRole("button", { name: "Enable live publishing" });
    expect(enable).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Confirmation"), "enable live publishing for mastodon");
    expect(enable).toBeDisabled(); // case matters
    await userEvent.clear(within(dialog).getByLabelText("Confirmation"));
    await userEvent.type(within(dialog).getByLabelText("Confirmation"), "ENABLE LIVE PUBLISHING FOR MASTODON");
    await userEvent.click(enable);
    await waitFor(() => expect(api.calls.some((c) => c.path.endsWith("/live"))).toBe(true));
    expect(api.calls.find((c) => c.path.endsWith("/live"))?.body).toEqual({
      enabled: true,
      confirmation: "ENABLE LIVE PUBLISHING FOR MASTODON",
    });
    // What still blocks a live post is explained in plain words.
    expect(within(dialog).getByText(/not in live publishing mode/)).toBeInTheDocument();
  });

  it("marks sandbox accounts so their numbers are never read as real", async () => {
    setup();
    renderPage(<PlatformsPage />);
    expect(await screen.findByText("Sandbox")).toBeInTheDocument();
  });
});
