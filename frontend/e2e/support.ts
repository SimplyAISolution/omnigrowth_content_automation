import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export interface UiEnv {
  users: Record<"OWNER" | "EDITOR" | "VIEWER", { email: string; password: string }>;
  brand_id: string;
  campaigns: { published: string; review: string };
}

const HERE = fileURLToPath(new URL(".", import.meta.url));

/** State written by `scripts/ui_env.py prepare` (.pytest-tmp/, git-ignored; throwaway test credentials). */
export function uiEnv(): UiEnv {
  return JSON.parse(readFileSync(`${HERE}../../.pytest-tmp/ui-env.json`, "utf-8")) as UiEnv;
}

/** One scheduler pass with the MockPublisher (simulated; no platform call). */
export function schedulerTick(): string {
  return execFileSync("uv", ["run", "--project", "..", "python", "../scripts/ui_env.py", "tick"], { cwd: `${HERE}..`, encoding: "utf-8" });
}

export async function signIn(page: Page, who: { email: string; password: string }): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(who.email);
  await page.getByLabel("Password").fill(who.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

/** Console errors and failed API calls, to assert a journey ran clean. */
export function watchProblems(page: Page, allowStatus: number[] = []): string[] {
  const problems: string[] = [];
  page.on("console", (m) => {
    // Failed requests are reported with their URL by the response listener below.
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) problems.push(`console: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("response", (r) => {
    const status = r.status();
    // Nothing is expected to fail unless the test says so (allowStatus).
    const expected = allowStatus.includes(status);
    if (status >= 400 && !expected) problems.push(`${status} ${r.url()}`);
  });
  return problems;
}

export async function expectAccessible(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${label}: ${v.id} — ${v.help} (${v.nodes.length})`)).toEqual([]);
}
