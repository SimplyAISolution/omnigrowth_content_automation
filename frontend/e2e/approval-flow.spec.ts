/**
 * Phase 11 gate: the approval flow driven through the UI against the running API.
 * Data is a fixture-replay seed (no real LLM); publication is the MockPublisher (simulated).
 */
import { expect, test, type Page } from "@playwright/test";
import { expectAccessible, schedulerTick, signIn, uiEnv, watchProblems } from "./support";

const SHOTS = "test-results/screens";

async function openFromQueue(page: Page, name: RegExp): Promise<void> {
  await page.getByRole("link", { name: /^Approvals/ }).click();
  await expect(page.getByRole("heading", { name: "Approvals", level: 1 })).toBeVisible();
  await page.getByRole("link", { name }).first().click();
  await expect(page.getByRole("region", { name: "What you can do" })).toBeVisible();
}

async function waiting(page: Page): Promise<number> {
  await page.getByRole("link", { name: /^Approvals/ }).click();
  const text = await page.getByText(/items? waiting\./).textContent();
  return Number(/(\d+)/.exec(text ?? "")?.[1] ?? 0);
}

test("an editor reviews the queue: rejects, requests a revision, approves and schedules; publication is shown as simulated", async ({ page }) => {
  const env = uiEnv();
  const problems = watchProblems(page);
  await signIn(page, env.users.EDITOR);

  await expect(page.getByRole("heading", { level: 1, name: /Simply AI Solutions/ })).toBeVisible();
  await expect(page.getByRole("note").filter({ hasText: "no real audience" }).first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/desktop-overview.png`, fullPage: true });
  const before = await waiting(page);
  expect(before).toBeGreaterThanOrEqual(3);
  await page.screenshot({ path: `${SHOTS}/desktop-approvals.png`, fullPage: true });

  // 1. Reject: a reason is required, then the decision is recorded.
  await openFromQueue(page, /Review YouTube/);
  await expect(page.getByText("Script / outline", { exact: false })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/desktop-review-youtube.png`, fullPage: true });
  await page.getByRole("button", { name: "Reject" }).click();
  const reject = page.getByRole("dialog", { name: "Reject this content" });
  await reject.getByRole("button", { name: "Reject" }).click();
  await expect(reject.getByRole("alert")).toContainText("Please say why");
  await reject.getByLabel("Reason (required)").fill("Too long for this week's slot.");
  await reject.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText(/^Rejected\. It will not be published/)).toBeVisible();
  await expect(page.locator(".page-header .badge")).toHaveText("Rejected");
  await expect(page.getByText("“Too long for this week's slot.”")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);

  // 2. Request a revision.
  await openFromQueue(page, /Review LinkedIn Carousel/);
  await page.getByRole("button", { name: "Request revision" }).click();
  const revise = page.getByRole("dialog", { name: "Request a revision" });
  await revise.getByLabel("Reason (required)").fill("Add a concrete example on slide 3.");
  await revise.getByRole("button", { name: "Request revision" }).click();
  await expect(page.locator(".page-header .badge")).toHaveText("Revision requested");

  // 3. Approve, then schedule on the brand's mock X account.
  await openFromQueue(page, /Review X Thread/);
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(/^Approved\. Next, schedule it/)).toBeVisible();
  await expect(page.locator(".page-header .badge")).toHaveText("Approved");
  await page.getByRole("button", { name: "Schedule" }).click();
  const schedule = page.getByRole("dialog", { name: "Schedule publication" });
  await expect(schedule.getByRole("note")).toContainText("no real audience");
  await expect(schedule.getByLabel("X account")).toHaveValue(/.+/);
  await page.screenshot({ path: `${SHOTS}/desktop-schedule-dialog.png` });
  await schedule.getByRole("button", { name: "Schedule" }).click();
  await expect(page.getByText(/^Scheduled for .*simulated by the mock publisher/)).toBeVisible();
  await expect(page.locator(".page-header .badge")).toHaveText("Scheduled");
  const publishing = page.getByRole("region", { name: "Publishing" });
  await expect(publishing.getByText("Waiting for its time")).toBeVisible();
  const assetUrl = page.url();

  expect(await waiting(page)).toBe(before - 3);

  // 4. The scheduler runs (mock publisher): the outcome is labelled simulated everywhere.
  schedulerTick();
  await page.goto(assetUrl);
  await expect(page.locator(".page-header .badge")).toHaveText("Published (simulated)");
  await expect(publishing.getByText("Done (simulated)")).toBeVisible();
  await expect(publishing.getByText(/Mock post id mock_/)).toBeVisible();
  await expect(publishing.getByRole("note")).toContainText("no real audience");
  await page.screenshot({ path: `${SHOTS}/desktop-published-simulated.png`, fullPage: true });

  await page.getByRole("link", { name: "Publishing" }).click();
  await expect(page.getByRole("cell", { name: "Simulated (mock publisher)" }).first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/desktop-publishing.png`, fullPage: true });

  expect(problems).toEqual([]);
});

test("a viewer sees decisions disabled with the reason, and the API refuses them anyway", async ({ page }) => {
  const env = uiEnv();
  await signIn(page, env.users.VIEWER);
  await openFromQueue(page, /Review LinkedIn Text post/);
  const approve = page.getByRole("button", { name: "Approve" });
  await expect(approve).toBeDisabled();
  await expect(page.getByText("Requires the editor role.").first()).toBeVisible();

  // Hiding or disabling a control is not authorization: call the API directly as the viewer.
  const token = await page.evaluate(() => window.sessionStorage.getItem("og.token"));
  const assetId = /content\/([^?]+)/.exec(page.url())?.[1] ?? "";
  const asset = await (await page.request.get(`/api/v1/content/${assetId}`, { headers: { Authorization: `Bearer ${token}` } })).json();
  const refused = await page.request.post(`/api/v1/approvals/${assetId}/actions`, {
    headers: { Authorization: `Bearer ${token}`, "Idempotency-Key": `viewer-${Date.now()}` },
    data: { action: "APPROVE", content_version_id: asset.current_version_id },
  });
  expect(refused.status()).toBe(403);
  expect((await refused.json()).code).toBe("FORBIDDEN");
  await page.reload();
  await expect(page.locator(".page-header .badge")).toHaveText("Needs your approval");
});

test("keyboard: skip link, focus management and Escape closes dialogs", async ({ page }) => {
  const env = uiEnv();
  await signIn(page, env.users.EDITOR);
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();

  await openFromQueue(page, /Review LinkedIn Text post/);
  const edit = page.getByRole("button", { name: "Edit" });
  await edit.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Edit content" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(":focus")).toHaveCount(1); // focus moved into the dialog
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("key screens have no serious accessibility violations", async ({ page }) => {
  const env = uiEnv();
  await signIn(page, env.users.EDITOR);
  const screens: [string, string, string][] = [
    ["/", "overview", "Overview"],
    ["/approvals", "approvals", "Approvals"],
    ["/content", "content", "Content"],
    ["/calendar", "calendar", "Calendar"],
    ["/publications", "publishing", "Publishing"],
    ["/campaigns", "campaigns", "Campaigns"],
    ["/analytics", "analytics", "Analytics"],
    ["/experiments", "experiments", "Experiments"],
    ["/platforms", "platforms", "Platforms"],
    ["/sources", "sources", "Sources"],
    ["/brand", "brand", "Brand profile"],
  ];
  for (const [path, file, heading] of screens) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 }).first()).toContainText(heading === "Overview" ? "Simply AI" : heading);
    await page.waitForLoadState("networkidle");
    await expectAccessible(page, path);
    await page.screenshot({ path: `${SHOTS}/desktop-${file}.png`, fullPage: true });
  }
  // A content review screen and a campaign screen too.
  await page.goto(`/campaigns/${env.campaigns.published}`);
  await expect(page.getByRole("tablist", { name: "Campaign details" })).toBeVisible();
  await expectAccessible(page, "campaign");
  await page.screenshot({ path: `${SHOTS}/desktop-campaign.png`, fullPage: true });
});

test("analytics explain simulated provenance and missing inputs", async ({ page }) => {
  const env = uiEnv();
  await signIn(page, env.users.EDITOR);
  await page.goto("/analytics");
  await page.getByLabel("Campaign").selectOption(env.campaigns.published);
  await expect(page.getByRole("note").filter({ hasText: "Simulated data." })).toBeVisible();
  await expect(page.getByText(/Not calculated: .*not captured/).first()).toBeVisible();
  await expect(page.getByText("Based on simulated data").first()).toBeVisible();
  await expect(page.getByText(/nothing changes in your campaigns automatically/)).toBeVisible();
});
