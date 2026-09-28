import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AutomationsPage } from "./Automations";
import { baseRoutes, mockApi, renderPage } from "../test/utils";

afterEach(() => vi.unstubAllGlobals());

const empty = { body: { items: [], next_cursor: null } };

function routes(role: "OWNER" | "EDITOR", onCreate?: (body: unknown) => { status?: number; body: unknown }) {
  return {
    ...baseRoutes(role),
    "GET /api/v1/webhooks/subscriptions": empty,
    "GET /api/v1/webhooks/endpoints": empty,
    "GET /api/v1/webhooks/deliveries": empty,
    ...(onCreate ? { "POST /api/v1/webhooks/subscriptions": ({ body }: { body: unknown }) => onCreate(body) } : {}),
  };
}

it("is owner-only", async () => {
  mockApi(routes("EDITOR"));
  renderPage(<AutomationsPage />);
  expect(await screen.findByText(/Owners only\./)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Add target" })).not.toBeInTheDocument();
});

it("adds a notification target and shows its secret exactly once", async () => {
  const api = mockApi(
    routes("OWNER", () => ({
      status: 201,
      body: {
        id: "s1",
        target_url: "https://n8n.example.com/webhook/omnigrowth-events",
        brand_ids: ["b1"],
        event_types: ["content.approval_requested"],
        secret: "whsec_shown_once",
        secret_version: 1,
        secret_fingerprint: "abcd1234",
        created_by: "HUMAN:u1",
        created_at: "2026-09-27T00:00:00Z",
      },
    })),
  );
  renderPage(<AutomationsPage />);
  await userEvent.type(await screen.findByLabelText("n8n Webhook URL"), "https://n8n.example.com/webhook/omnigrowth-events");
  await userEvent.click(screen.getByRole("button", { name: "Add target" }));
  const dialog = await screen.findByRole("dialog", { name: "Copy this secret now" });
  expect(within(dialog).getByLabelText("Secret")).toHaveValue("whsec_shown_once");
  expect(within(dialog).getByText(/only time the secret is shown/)).toBeInTheDocument();
  const post = api.calls.find((c) => c.method === "POST");
  expect(post?.body).toEqual({
    target_url: "https://n8n.example.com/webhook/omnigrowth-events",
    brand_ids: ["b1"],
    event_types: ["content.approval_requested", "publication.failed", "publication.uncertain"],
  });
  await userEvent.click(within(dialog).getByRole("button", { name: "I have stored it" }));
  await waitFor(() => expect(screen.queryByDisplayValue("whsec_shown_once")).not.toBeInTheDocument());
});

it("explains a refused (SSRF-blocked) target", async () => {
  mockApi(
    routes("OWNER", () => ({
      status: 422,
      body: { code: "SSRF_BLOCKED", message: "URL not allowed: public webhook targets must use https on port 443", details: {} },
    })),
  );
  renderPage(<AutomationsPage />);
  await userEvent.type(await screen.findByLabelText("n8n Webhook URL"), "http://10.0.0.5:5678/webhook/x");
  await userEvent.click(screen.getByRole("button", { name: "Add target" }));
  expect(await screen.findByText(/public webhook targets must use https/)).toBeInTheDocument();
});
