import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

/** Drop the throwaway database created by `npm run e2e` (set OG_UI_KEEP=1 to keep it). */
export default function globalTeardown(): void {
  if (process.env.OG_UI_KEEP === "1") return;
  const cwd = fileURLToPath(new URL("..", import.meta.url));
  execFileSync("uv", ["run", "--project", "..", "python", "../scripts/ui_env.py", "drop"], { cwd, stdio: "inherit" });
}
