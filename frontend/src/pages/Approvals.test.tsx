import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalsPage } from "./Approvals";
import { asset, baseRoutes, mockApi, renderPage } from "../test/utils";

afterEach(() => vi.unstubAllGlobals());

describe("Approvals queue", () => {
  it("shows a loading state, then the items waiting for a person with their check summary", async () => {
    mockApi({
      ...baseRoutes(),
      "GET /api/v1/approvals": { body: { items: [asset(), asset({ id: "a2", platform: "X", format: "THREAD" })], next_cursor: null } },
    });
    renderPage(<ApprovalsPage />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
    const list = await screen.findByRole("list", { name: "Approval queue" });
    expect(list.querySelectorAll("li.asset-card")).toHaveLength(2);
    expect(screen.getByText("2 items waiting.")).toBeInTheDocument();
    expect(screen.getAllByText("Needs your approval")).toHaveLength(2);
    expect(screen.getAllByText("Passed with 1 warning")).toHaveLength(2);
    expect(screen.getByRole("link", { name: /Review LinkedIn Text post/ })).toHaveAttribute("href", "/content/a1?from=approvals");
  });

  it("explains an empty queue instead of showing a blank page", async () => {
    mockApi({ ...baseRoutes(), "GET /api/v1/approvals": { body: { items: [], next_cursor: null } } });
    renderPage(<ApprovalsPage />);
    expect(await screen.findByRole("heading", { name: "Nothing waits for approval" })).toBeInTheDocument();
  });

  it("shows a recoverable error with the support reference, and retries", async () => {
    let fail = true;
    mockApi({
      ...baseRoutes(),
      "GET /api/v1/approvals": () =>
        fail
          ? { status: 500, body: { code: "INTERNAL_ERROR", message: "Internal error", correlation_id: "corr-123" } }
          : { body: { items: [asset()], next_cursor: null } },
    });
    renderPage(<ApprovalsPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This could not be loaded");
    expect(alert).toHaveTextContent("corr-123");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByText("1 item waiting.")).toBeInTheDocument());
  });
});
