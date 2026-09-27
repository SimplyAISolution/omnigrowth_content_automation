/** Phone-sized layout: navigation behind a menu button, no horizontal scrolling. */
import { expect, test } from "@playwright/test";
import { signIn, uiEnv } from "./support";

const SHOTS = "test-results/screens";

test("the app is usable at phone width", async ({ page }) => {
  const env = uiEnv();
  await signIn(page, env.users.EDITOR);
  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav).toBeHidden();
  const menu = page.getByRole("button", { name: "Menu" });
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await menu.click();
  await expect(nav).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/mobile-menu.png`, fullPage: true });
  await nav.getByRole("link", { name: /^Approvals/ }).click();
  await expect(nav).toBeHidden(); // closes after navigating

  for (const path of ["/", "/approvals", "/calendar", "/publications", "/analytics", "/platforms"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(1);
    await page.screenshot({ path: `${SHOTS}/mobile${path === "/" ? "-overview" : path.replace("/", "-")}.png`, fullPage: true });
  }

  await page.goto("/approvals");
  await page.getByRole("link", { name: /Review/ }).first().click();
  await expect(page.getByRole("region", { name: "What you can do" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.screenshot({ path: `${SHOTS}/mobile-review.png`, fullPage: true });
});
