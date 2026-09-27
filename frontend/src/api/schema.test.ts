// @vitest-environment node
/**
 * The generated client types match the committed OpenAPI snapshot (which the Python test
 * `tests/unit/test_openapi_snapshot.py` checks against the API). Together they catch a stale
 * `schema.d.ts` that would otherwise still typecheck.
 */
import openapiTS, { astToString } from "openapi-typescript";
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));

it("schema.d.ts is generated from the current openapi.json", async () => {
  const ast = await openapiTS(pathToFileURL(`${here}openapi.json`));
  const committed = readFileSync(`${here}schema.d.ts`, "utf-8").replace(/\r\n/g, "\n");
  expect(committed.endsWith(astToString(ast)), "run `npm --prefix frontend run gen:api`").toBe(true);
}, 60_000);
