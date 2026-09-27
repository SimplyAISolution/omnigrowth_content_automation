/** Regression tests for the Phase 11 review findings on the content review screen. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContentDetailPage } from "./ContentDetail";
import { asset, baseRoutes, mockApi, renderPage, type Handler } from "../test/utils";

afterEach(() => vi.unstubAllGlobals());

const empty = { body: { items: [], next_cursor: null } };

function routes(current: () => unknown, action?: Handler) {
  return {
    ...baseRoutes("EDITOR"),
    "GET /api/v1/content/:id": () => ({ body: current() }),
    "GET /api/v1/content/:id/versions": empty,
    "GET /api/v1/content/:id/derivatives": empty,
    "GET /api/v1/approvals/:id/history": empty,
    "GET /api/v1/publications/jobs": empty,
    "GET /api/v1/publications": empty,
    ...(action ? { "POST /api/v1/approvals/:id/actions": action } : {}),
  };
}

const render = () => renderPage(<ContentDetailPage />, { path: "/content/:assetId", route: "/content/a1" });

it("opens Edit with the current version after the content changed (review MAJOR 1)", async () => {
  let state = asset();
  mockApi(
    routes(
      () => state,
      () => {
        // The writer produced a new version (the action refetches the asset afterwards).
        state = asset({
          current_version_id: "v2",
          current_version: { ...asset().current_version, id: "v2", version_no: 2, body: "Regenerated body text." },
        });
        return { body: { asset: state } };
      },
    ),
  );
  render();
  await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
  let dialog = await screen.findByRole("dialog", { name: "Edit content" });
  expect(within(dialog).getByLabelText("Text")).toHaveValue("Local AI keeps client data on the machine.");
  await userEvent.type(within(dialog).getByLabelText("Text"), " typed but abandoned");
  await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

  await userEvent.click(screen.getByRole("button", { name: "Regenerate" }));
  const regen = await screen.findByRole("dialog", { name: "Regenerate with the writer" });
  await userEvent.click(within(regen).getByRole("button", { name: "Regenerate" }));
  expect(await screen.findByText("Regenerated body text.")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Edit" }));
  dialog = await screen.findByRole("dialog", { name: "Edit content" });
  expect(within(dialog).getByLabelText("Text")).toHaveValue("Regenerated body text.");
});

it("sends a cleared call to action as cleared, not as keep-previous (review MINOR 2)", async () => {
  const api = mockApi(routes(() => asset(), () => ({ body: { asset: asset({ status: "GENERATED" }) } })));
  render();
  await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
  const dialog = await screen.findByRole("dialog", { name: "Edit content" });
  await userEvent.clear(within(dialog).getByLabelText("Call to action"));
  await userEvent.click(within(dialog).getByRole("button", { name: "Save new version" }));
  await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
  const post = api.calls.find((c) => c.method === "POST");
  expect((post?.body as { edit: { cta: string } }).edit.cta).toBe("");
});

it("schedules the approved version on an account of the asset's brand at the chosen time (review MAJOR 5, MINOR 1)", async () => {
  const approved = asset({ status: "APPROVED", approved_version_id: "v1", brand_id: "b2" });
  const accountQueries: string[] = [];
  const api = mockApi({
    ...routes(() => approved, () => ({ body: { asset: approved, publication_job: { due_at: "2030-01-01T10:00:00Z" } } })),
    "GET /api/v1/platform-accounts": ({ search }) => {
      accountQueries.push(search.toString());
      return {
        body: {
          items: [
            {
              id: "acc-b2",
              brand_id: "b2",
              platform: "LINKEDIN",
              handle: "brand-two",
              account_type: "ORGANIZATION",
              live_enabled: false,
              has_open_uncertain: false,
              settings: {},
              display_name: null,
              timezone: null,
              created_at: "2026-01-01T00:00:00Z",
            },
          ],
          next_cursor: null,
        },
      };
    },
  });
  render();
  await userEvent.click(await screen.findByRole("button", { name: "Schedule" }));
  const dialog = await screen.findByRole("dialog", { name: "Schedule publication" });
  await within(dialog).findByRole("option", { name: "@brand-two" });
  const when = within(dialog).getByLabelText("Date and time");
  await userEvent.clear(when);
  await userEvent.click(within(dialog).getByRole("button", { name: "Schedule" }));
  expect(within(dialog).getByRole("alert")).toHaveTextContent("Enter a date and time.");
  await userEvent.type(when, "2030-01-01T10:00");
  await userEvent.click(within(dialog).getByRole("button", { name: "Schedule" }));
  await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
  expect(accountQueries.some((q) => q.includes("brand_id=b2") && q.includes("platform=LINKEDIN"))).toBe(true);
  const body = api.calls.find((c) => c.method === "POST")?.body as Record<string, string>;
  expect(body["action"]).toBe("SCHEDULE");
  expect(body["content_version_id"]).toBe("v1");
  expect(body["platform_account_id"]).toBe("acc-b2");
  expect(new Date(body["scheduled_at"] ?? "").getTime()).toBe(new Date("2030-01-01T10:00").getTime());
});
