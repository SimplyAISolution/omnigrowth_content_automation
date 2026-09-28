// OmniGrowth → n8n: verify the signature, reject stale or replayed deliveries, deduplicate
// on the event id (delivery is at-least-once), and prepare a message per event type.
// Needs: NODE_FUNCTION_ALLOW_BUILTIN=crypto, and the subscription secret in the
// OMNIGROWTH_WEBHOOK_SECRET environment variable (never paste it into the workflow).
// Output: one item { httpStatus, route, message, event } — route 0..2 notify, 3 ignore.
const crypto = require("crypto");

const TOLERANCE_S = 300;
const KEEP_IDS_S = 7 * 24 * 3600;

const secret = $env.OMNIGROWTH_WEBHOOK_SECRET || "";
const input = $input.first();
const headers = Object.fromEntries(
  Object.entries(input.json.headers || {}).map(([k, v]) => [k.toLowerCase(), String(v)]),
);
const raw = input.binary && input.binary.data ? Buffer.from(input.binary.data.data, "base64") : Buffer.from("");

function reject(reason) {
  return [{ json: { httpStatus: 401, route: 3, message: `rejected: ${reason}`, event: null } }];
}

if (!secret) return reject("OMNIGROWTH_WEBHOOK_SECRET is not set");
const timestamp = headers["x-omnigrowth-timestamp"] || "";
const deliveryId = headers["x-omnigrowth-delivery-id"] || "";
const signatures = (headers["x-omnigrowth-signature"] || "").split(",").map((s) => s.trim()).filter(Boolean);
if (!/^\d+$/.test(timestamp) || Math.abs(Date.now() / 1000 - Number(timestamp)) > TOLERANCE_S) {
  return reject("stale or missing timestamp");
}
if (!deliveryId) return reject("missing delivery id");
// Signed: "<timestamp>.<delivery id>.<raw body>". During a secret rotation OmniGrowth sends one
// v1= per active secret; any match is enough.
const expected = Buffer.from(
  "v1=" + crypto.createHmac("sha256", secret).update(`${timestamp}.${deliveryId}.`).update(raw).digest("hex"),
);
const valid = signatures.some((s) => {
  const b = Buffer.from(s);
  return b.length === expected.length && crypto.timingSafeEqual(expected, b);
});
if (!valid) return reject("bad signature");

const event = JSON.parse(raw.toString("utf8"));
if (headers["x-omnigrowth-event-id"] !== event.id) return reject("event id mismatch");

// At-least-once delivery: acknowledge duplicates (200) but do not notify twice.
const store = $getWorkflowStaticData("global");
store.seen = store.seen || {};
const now = Math.floor(Date.now() / 1000);
for (const [id, at] of Object.entries(store.seen)) if (now - at > KEEP_IDS_S) delete store.seen[id];
if (store.seen[event.id]) {
  return [{ json: { httpStatus: 200, route: 3, message: "duplicate event, already handled", event } }];
}
store.seen[event.id] = now;

const d = event.data || {};
const MODES = {
  MOCK: "SIMULATED (mock publisher: no real post, no real audience)",
  LIVE: "REAL publication (live platform API)",
  MANUAL: "MANUAL publication (a person posts it)",
};
const mode = MODES[d.publish_mode] || (d.simulated === true ? MODES.MOCK : "publication mode unknown");
const links = event.links || {};
let route = 3;
let message = `OmniGrowth ${event.type} (${event.id})`;
if (event.type === "content.approval_requested") {
  route = 0;
  message =
    `Content needs human approval: ${d.platform} ${d.format}. ` +
    `Review and decide in OmniGrowth: ${links.content || links.approvals || ""}`;
} else if (event.type === "publication.failed") {
  route = 1;
  message =
    `Publication failed [${mode}] on ${d.platform} after ${d.attempt_count} attempt(s)` +
    `${d.last_error_code ? ` (${d.last_error_code})` : ""}. Open: ${links.publishing || ""}`;
} else if (event.type === "publication.uncertain") {
  route = 2;
  message =
    `Publication outcome UNKNOWN [${mode}] on ${d.platform}: reconcile it in OmniGrowth before ` +
    `this account publishes again. Open: ${links.publishing || ""}`;
}
return [{ json: { httpStatus: 200, route, message, event } }];
