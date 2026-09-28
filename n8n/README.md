# OmniGrowth ↔ n8n (Phase 12)

Two importable example workflows. They contain **no secrets and no real notification
channel**: the "Notify …" steps are No-Op placeholders. Nothing is sent to email, chat or
social platforms until you replace them yourself. They are exported inactive.

| File | What it does |
|---|---|
| `workflows/omnigrowth-event-notifications.json` | Receives OmniGrowth events, verifies the signature, rejects stale or replayed deliveries, ignores duplicates, and routes the three events to placeholder "Notify" steps with a ready message. |
| `workflows/omnigrowth-send-text-source.json` | Signs and sends a text source to OmniGrowth (manual trigger, example text). |

The JavaScript in the Code nodes lives in `code/*.js`. `uv run python scripts/export_n8n_workflows.py`
rebuilds the JSON, and `tests/unit/test_n8n_workflows.py` runs that JavaScript against requests
signed by OmniGrowth.

## What n8n can and cannot do

- **Outbound (OmniGrowth → n8n):** four events, each delivered at least once:
  - `content.approval_requested`
  - `publication.failed`
  - `publication.uncertain`
  - `publication.not_published_confirmed` (Phase 13): a person confirmed a post did not go out.
    Carries `will_retry: false`; nothing is retried because of it.

  Publication events carry `data.evidence_class` (Phase 13) and `data.simulated`:
  - `SIMULATED` (`simulated: true`): the mock publisher handled it, so there was no real post and no real audience.
  - `SANDBOX`: a real API call to an account the owner marked as a test account. There was no real audience.
  - `REAL`: a real publication.
  - `null`: unknown (for example a job that failed before it was sent).
- **Inbound (n8n → OmniGrowth):** only what an owner allowed for that integration, and only for its brands. Analytics numbers are never accepted: integration-supplied numbers must not become platform evidence (A17):
  - `source.text_ingest`
  - `source.url_ingest`
  - `campaign.generate`
- **Never:** n8n can never approve, reject, schedule or publish content, reconcile publications, read content, or change accounts, credentials or settings.
- **Informational only:** receiving an event changes nothing in OmniGrowth. People approve and publish in OmniGrowth.

## Set up

### 1. OmniGrowth server
Add these to the server's `.env` (never commit it):

```
OMNIGROWTH_WEBHOOK_KEY=<at least 32 random characters>
OMNIGROWTH_WEBHOOK_TARGET_ALLOWLIST=localhost:5678
OMNIGROWTH_APP_URL=http://127.0.0.1:5173
```

- **`OMNIGROWTH_WEBHOOK_KEY`:** without it, webhooks stay off. Every webhook secret is derived from it, and changing it invalidates all of them at once.
- **`OMNIGROWTH_WEBHOOK_TARGET_ALLOWLIST`:** only needed for a local or self-hosted n8n. Entries are `host:port` (for example `localhost:5678` or `[::1]:5678`); a bare host is rejected. Public targets must be HTTPS on port 443. Link-local and cloud-metadata addresses are never allowed.
- **`OMNIGROWTH_APP_URL`:** the base for the links in notifications.

Restart the API and the scheduler. The scheduler delivers webhooks in its own loop.

### 2. n8n
Start n8n (for example `npx n8n` or its Docker image) with these environment variables:

```
NODE_FUNCTION_ALLOW_BUILTIN=crypto
OMNIGROWTH_WEBHOOK_SECRET=<subscription secret, from step 3>
OMNIGROWTH_API_URL=http://127.0.0.1:8000
OMNIGROWTH_ENDPOINT_KEY=<inbound integration key, from step 4>
OMNIGROWTH_INBOUND_SECRET=<inbound integration secret, from step 4>
OMNIGROWTH_BRAND_ID=<brand id>
```

Import both JSON files (**Workflows → Import from file**). Code nodes read secrets from the
environment, so the secrets never live inside a workflow or its export. If your n8n blocks
environment access in nodes (`N8N_BLOCK_ENV_ACCESS_IN_NODE=true`), move them into n8n
credentials instead.

### 3. Notifications (outbound)
1. Open the events workflow and copy the Webhook node's **production URL**.
2. In OmniGrowth, as an owner, go to **Automations → Add a notification target**. Paste the URL, choose brands and events, and store the secret shown once as `OMNIGROWTH_WEBHOOK_SECRET`.
3. Activate the workflow, then press **Send test** in OmniGrowth. The delivery appears under **Deliveries**.
4. Replace the No-Op "Notify" steps with your channel after reviewing the message text.

### 4. Sources (inbound)
1. In OmniGrowth, go to **Automations → Add an inbound integration**. Choose a key, the brands and the allowed operations, then store the secret shown once as `OMNIGROWTH_INBOUND_SECRET`.
2. Run the "send a text source" workflow manually. The source appears under **Sources**.

## Delivery and security contract

- **Headers:**
  - `X-OmniGrowth-Event-ID`, which is stable across retries; deduplicate on it.
  - `X-OmniGrowth-Delivery-ID`, `X-OmniGrowth-Event-Type`, `X-OmniGrowth-Attempt`, `X-OmniGrowth-Timestamp` (unix seconds).
  - `X-OmniGrowth-Signature: v1=<hex HMAC-SHA256(secret, "<timestamp>.<delivery id>.<raw body>")>`.
    The delivery id is signed, so it cannot be swapped on a captured request. During a secret
    rotation the header holds one `v1=` value per active secret, comma-separated; accept any match.
- **Verification:** reject a timestamp more than 300 s from your clock. Compare signatures in constant time.
- **Retries:**
  - A 2xx answer ends delivery.
  - A 410 stops it at once.
  - Anything else, including timeouts after 10 s, is retried with growing delays up to 8 attempts (about 22 h). After that the delivery is FAILED and an owner can send it again.
  - Redirects are not followed.
- **Inbound requests:** use the same headers. `X-OmniGrowth-Delivery-ID` is required, and a repeated id replays the first answer (`replayed: true`) without running again. The body limit is 1 MiB, with 60 requests per minute per integration and address.
- **Rotation:** rotating a secret keeps the previous one valid for 24 h (`OMNIGROWTH_WEBHOOK_SECRET_GRACE_HOURS`). A second rotation during that window is refused (409); if a secret is compromised, revoke and create a new integration. Revoking stops every secret at once.

## Known limitations

- Publication stays **simulated** (mock publisher) in this build; `publish_mode` is `null` when a job fails before it was dispatched, and the workflow then says "mode unknown".
- A manual "not published" decision (P8) does not emit an event.
- The example workflow answers 200 before its notify step runs. If your notify step fails, OmniGrowth will not retry; check n8n's own execution log.
- The duplicate filter lives in the workflow's static data (7 days). It is reset if you re-import the workflow.
