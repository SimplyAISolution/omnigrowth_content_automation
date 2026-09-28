// n8n → OmniGrowth: build and sign an inbound request (API_PLAN §6.6).
// Needs: NODE_FUNCTION_ALLOW_BUILTIN=crypto and, as environment variables (never in the
// workflow): OMNIGROWTH_INBOUND_SECRET (the endpoint secret), OMNIGROWTH_BRAND_ID.
// The endpoint may only run the operations and brands it was created for; it can never
// approve, schedule or publish.
const crypto = require("crypto");

const secret = $env.OMNIGROWTH_INBOUND_SECRET || "";
if (!secret) throw new Error("OMNIGROWTH_INBOUND_SECRET is not set");
const item = $input.first().json;

const envelope = {
  operation: "source.text_ingest",
  payload: {
    brand_id: $env.OMNIGROWTH_BRAND_ID,
    title: item.title || "From n8n",
    content: item.content,
  },
};
const body = JSON.stringify(envelope);
const timestamp = String(Math.floor(Date.now() / 1000));
// A stable delivery id per logical request makes retries safe (the API replays, not re-runs).
const deliveryId = item.delivery_id || `n8n-${crypto.createHash("sha256").update(body).digest("hex").slice(0, 32)}`;
// Signed: "<timestamp>.<delivery id>.<body>" — the id cannot be swapped on a captured request.
const signature =
  "v1=" + crypto.createHmac("sha256", secret).update(`${timestamp}.${deliveryId}.${body}`).digest("hex");

return [{ json: { body, timestamp, signature, deliveryId } }];
