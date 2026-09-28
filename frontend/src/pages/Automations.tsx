import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ApiError, api, call, idem, type Schemas } from "../api/client";
import { useAuth } from "../auth/auth";
import { Badge, Dialog, EmptyState, ErrorPanel, Field, Loading, PageHeader, RoleButton } from "../components/ui";
import { useBrand } from "../lib/brand";
import { explainError, formatDateTime } from "../lib/format";
import { useAction } from "../lib/hooks";

type EventType = Schemas["IntegrationEventType"];
type Operation = Schemas["IntegrationOperation"];

const EVENTS: { value: EventType; label: string; help: string }[] = [
  { value: "content.approval_requested", label: "Content needs approval", help: "A piece of content passed its checks and waits for a person." },
  { value: "publication.failed", label: "Publication failed", help: "Every attempt failed; nothing was published by that job." },
  { value: "publication.uncertain", label: "Publication outcome uncertain", help: "Someone must reconcile it before the account publishes again." },
  {
    value: "publication.not_published_confirmed",
    label: "Confirmed not published",
    help: "A person recorded that a post did not go out. Nothing is retried automatically.",
  },
];

const OPERATIONS: { value: Operation; label: string }[] = [
  { value: "source.text_ingest", label: "Add a text source" },
  { value: "source.url_ingest", label: "Add a source from a URL (fetched with the SSRF guard)" },
  { value: "campaign.generate", label: "Start planning a campaign (uses the local model)" },
];

const DELIVERY_TONE = { PENDING: "info", DELIVERING: "info", SUCCEEDED: "success", FAILED: "danger" } as const;

/** Shows a new secret once, with what to do with it. */
function SecretDialog({ secret, onClose }: { secret: { value: string; what: string } | null; onClose: () => void }) {
  return (
    <Dialog open={secret !== null} title="Copy this secret now" onClose={onClose}>
      <p>
        This is the only time the secret is shown. Store it in n8n as an environment variable or credential ({secret?.what}), never inside a
        workflow. If it is lost, rotate it.
      </p>
      <Field label="Secret" htmlFor="new-secret">
        <input id="new-secret" readOnly value={secret?.value ?? ""} onFocus={(e) => e.currentTarget.select()} />
      </Field>
      <div className="action-row">
        <button type="button" className="btn btn-primary" onClick={onClose}>
          I have stored it
        </button>
      </div>
    </Dialog>
  );
}

function BrandChecks({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const { brands } = useBrand();
  return (
    <fieldset className="field">
      <legend>Brands it may see or act for</legend>
      <div className="checks">
        {brands.map((b) => (
          <label key={b.id} className="checkbox">
            <input
              type="checkbox"
              checked={selected.includes(b.id)}
              onChange={(e) => onChange(e.target.checked ? [...selected, b.id] : selected.filter((x) => x !== b.id))}
            />{" "}
            {b.display_name}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function AutomationsPage() {
  const { can } = useAuth();
  const [secret, setSecret] = useState<{ value: string; what: string } | null>(null);
  if (!can("OWNER"))
    return (
      <>
        <PageHeader title="Automations" />
        <div className="notice notice-neutral" role="note">
          <strong>Owners only.</strong> Connecting OmniGrowth to n8n (notifications out, sources in) is managed by an owner.
        </div>
      </>
    );
  return (
    <>
      <PageHeader
        title="Automations"
        description="Send events to n8n and let n8n add sources. n8n informs people; approving and publishing always happen here, and nothing in OmniGrowth waits for n8n."
      />
      <div className="notice notice-neutral" role="note">
        Events are delivered at least once: the same event can arrive twice, always with the same event id, so your workflow should ignore
        repeats (the example workflow in <code>n8n/</code> does). Publication events say whether the publication was simulated.
      </div>
      <Subscriptions onSecret={(value) => setSecret({ value, what: "OMNIGROWTH_WEBHOOK_SECRET" })} />
      <Deliveries />
      <Endpoints onSecret={(value) => setSecret({ value, what: "OMNIGROWTH_INBOUND_SECRET" })} />
      <SecretDialog secret={secret} onClose={() => setSecret(null)} />
    </>
  );
}

// ------------------------------------------------------------------ outbound
function Subscriptions({ onSecret }: { onSecret: (secret: string) => void }) {
  const { brandId } = useBrand();
  const q = useQuery({ queryKey: ["webhook-subscriptions"], queryFn: () => call(api.GET("/api/v1/webhooks/subscriptions")) });
  const [url, setUrl] = useState("");
  const [brands, setBrands] = useState<string[]>(brandId ? [brandId] : []);
  const [events, setEvents] = useState<EventType[]>(EVENTS.map((e) => e.value));
  const [problem, setProblem] = useState<string | null>(null);
  const invalidate = [["webhook-subscriptions"], ["webhook-deliveries"]];
  const create = useAction(
    (body: Schemas["WebhookSubscriptionCreate"], key: string) => call(api.POST("/api/v1/webhooks/subscriptions", { body, ...idem(key) })),
    { invalidate, onSuccess: (r) => { setUrl(""); onSecret(r.secret); } },
  );
  const rotate = useAction(
    (id: string, key: string) => call(api.POST("/api/v1/webhooks/subscriptions/{subscription_id}/rotate-secret", { params: { path: { subscription_id: id } }, ...idem(key) })),
    { invalidate, onSuccess: (r) => onSecret(r.secret) },
  );
  const test = useAction(
    (id: string, key: string) => call(api.POST("/api/v1/webhooks/subscriptions/{subscription_id}/test", { params: { path: { subscription_id: id } }, ...idem(key) })),
    { invalidate },
  );
  const [revoking, setRevoking] = useState<string | null>(null);
  const revoke = useAction(
    (id: string) => call(api.DELETE("/api/v1/webhooks/subscriptions/{subscription_id}", { params: { path: { subscription_id: id } } })),
    { invalidate, onSuccess: () => setRevoking(null) },
  );
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!url.trim()) return setProblem("Paste the n8n Webhook node's production URL.");
    if (!brands.length) return setProblem("Choose at least one brand.");
    if (!events.length) return setProblem("Choose at least one event.");
    setProblem(null);
    create.submit({ target_url: url.trim(), brand_ids: brands, event_types: events });
  }
  const fieldErrors = create.error instanceof ApiError ? create.error.fieldErrors() : {};
  const actionError = rotate.error ?? test.error ?? revoke.error;
  return (
    <section aria-labelledby="subs-h">
      <h2 id="subs-h">Notifications to n8n</h2>
      {q.isLoading ? <Loading /> : q.error ? <ErrorPanel error={q.error} onRetry={() => void q.refetch()} /> : null}
      {actionError ? <ErrorPanel title="That did not work" error={actionError} /> : null}
      {test.isSuccess ? (
        <div className="notice notice-success" role="status">
          Test event queued. The scheduler sends it within a few seconds; see Deliveries below.
        </div>
      ) : null}
      {q.data?.items.length ? (
        <table className="table">
          <caption className="sr-only">Outbound subscriptions</caption>
          <thead>
            <tr>
              <th scope="col">Target</th>
              <th scope="col">Events</th>
              <th scope="col">Secret</th>
              <th scope="col">Last result</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {q.data.items.map((s) => (
              <tr key={s.id}>
                <td className="small">{s.target_url}</td>
                <td className="small">{s.event_types.join(", ")}</td>
                <td className="small">
                  v{s.secret_version} · {s.secret_fingerprint}
                </td>
                <td className="small">
                  {s.revoked_at ? <Badge tone="neutral">Revoked</Badge> : s.last_failure_at && (!s.last_success_at || s.last_failure_at > s.last_success_at) ? <Badge tone="danger">Failing</Badge> : s.last_success_at ? <Badge tone="success">Delivering</Badge> : "No deliveries yet"}
                </td>
                <td>
                  {s.revoked_at ? (
                    "—"
                  ) : (
                    <div className="action-row">
                      <button type="button" className="btn" onClick={() => test.submit(s.id)}>
                        Send test
                      </button>
                      <button type="button" className="btn" onClick={() => rotate.submit(s.id)}>
                        Rotate secret
                      </button>
                      <button type="button" className="btn btn-danger" onClick={() => setRevoking(s.id)}>
                        Revoke
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : q.data ? (
        <EmptyState title="No notifications configured" />
      ) : null}
      <Dialog open={revoking !== null} title="Revoke this subscription?" onClose={() => setRevoking(null)}>
        <p>Deliveries stop immediately; queued ones are marked failed. This cannot be undone (create a new subscription instead).</p>
        <div className="action-row">
          <button type="button" className="btn btn-danger" disabled={revoke.isPending} onClick={() => revoking && revoke.submit(revoking)}>
            Revoke
          </button>
        </div>
      </Dialog>
      <form className="card form" onSubmit={submit} noValidate aria-labelledby="new-sub-h">
        <h3 id="new-sub-h">Add a notification target</h3>
        <Field
          label="n8n Webhook URL"
          htmlFor="sub-url"
          error={problem ?? fieldErrors["target_url"] ?? (create.error instanceof ApiError && create.error.code === "SSRF_BLOCKED" ? explainError(create.error) : undefined)}
          hint="Public targets must be HTTPS. A local or self-hosted n8n must be listed in OMNIGROWTH_WEBHOOK_TARGET_ALLOWLIST on the server."
        >
          <input id="sub-url" type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://n8n.example.com/webhook/omnigrowth-events" />
        </Field>
        <BrandChecks selected={brands} onChange={setBrands} />
        <fieldset className="field">
          <legend>Events</legend>
          {EVENTS.map((ev) => (
            <label key={ev.value} className="checkbox">
              <input
                type="checkbox"
                checked={events.includes(ev.value)}
                onChange={(e) => setEvents((xs) => (e.target.checked ? [...xs, ev.value] : xs.filter((x) => x !== ev.value)))}
              />{" "}
              {ev.label} <span className="small muted">— {ev.help}</span>
            </label>
          ))}
        </fieldset>
        {create.error && !fieldErrors["target_url"] && !(create.error instanceof ApiError && create.error.code === "SSRF_BLOCKED") ? (
          <ErrorPanel title="Not added" error={create.error} />
        ) : null}
        <RoleButton role="OWNER" type="submit" className="btn btn-primary" disabled={create.isPending}>
          Add target
        </RoleButton>
      </form>
    </section>
  );
}

function Deliveries() {
  const [failedOnly, setFailedOnly] = useState(false);
  const q = useQuery({
    queryKey: ["webhook-deliveries", failedOnly],
    queryFn: () => call(api.GET("/api/v1/webhooks/deliveries", { params: { query: { status: failedOnly ? "FAILED" : undefined } } })),
    refetchInterval: 10_000,
  });
  const redeliver = useAction(
    (id: string, key: string) => call(api.POST("/api/v1/webhooks/deliveries/{delivery_id}/redeliver", { params: { path: { delivery_id: id } }, ...idem(key) })),
    { invalidate: [["webhook-deliveries"]] },
  );
  return (
    <section aria-labelledby="dlv-h">
      <h2 id="dlv-h">Deliveries</h2>
      <p className="small muted">
        Failed attempts are retried with growing delays (about a day in total) before a delivery is marked failed. Failed deliveries can be sent
        again once the receiver works.
      </p>
      <label className="checkbox">
        <input type="checkbox" checked={failedOnly} onChange={(e) => setFailedOnly(e.target.checked)} /> Show failed only
      </label>
      {redeliver.error ? <ErrorPanel title="Not redelivered" error={redeliver.error} /> : null}
      {q.isLoading ? (
        <Loading />
      ) : q.error ? (
        <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />
      ) : !q.data?.items.length ? (
        <p className="muted">No deliveries{failedOnly ? " have failed" : " yet"}.</p>
      ) : (
        <table className="table">
          <caption className="sr-only">Webhook deliveries</caption>
          <thead>
            <tr>
              <th scope="col">Event</th>
              <th scope="col">Happened</th>
              <th scope="col">State</th>
              <th scope="col">Attempts</th>
              <th scope="col">Last result</th>
              <th scope="col">Action</th>
            </tr>
          </thead>
          <tbody>
            {q.data.items.map((d) => (
              <tr key={d.id}>
                <td className="small">
                  {d.event_type}
                  <br />
                  <span className="muted">{d.event_id}</span>
                </td>
                <td className="small">{formatDateTime(d.occurred_at)}</td>
                <td>
                  <Badge tone={DELIVERY_TONE[d.status]}>{d.status === "PENDING" && d.attempt_count > 0 ? "Retrying" : d.status[0] + d.status.slice(1).toLowerCase()}</Badge>
                </td>
                <td>
                  {d.attempt_count} / {d.max_attempts}
                </td>
                <td className="small">{d.last_status_code ? `HTTP ${d.last_status_code}` : d.last_error ?? "—"}</td>
                <td>
                  {d.status === "FAILED" ? (
                    <button type="button" className="btn" onClick={() => redeliver.submit(d.id)} disabled={redeliver.isPending}>
                      Send again
                    </button>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ inbound
function Endpoints({ onSecret }: { onSecret: (secret: string) => void }) {
  const { brandId } = useBrand();
  const q = useQuery({ queryKey: ["webhook-endpoints"], queryFn: () => call(api.GET("/api/v1/webhooks/endpoints")) });
  const [key, setKey] = useState("");
  const [brands, setBrands] = useState<string[]>(brandId ? [brandId] : []);
  const [ops, setOps] = useState<Operation[]>(["source.text_ingest"]);
  const [problem, setProblem] = useState<string | null>(null);
  const invalidate = [["webhook-endpoints"]];
  const create = useAction(
    (body: Schemas["WebhookEndpointCreate"], k: string) => call(api.POST("/api/v1/webhooks/endpoints", { body, ...idem(k) })),
    { invalidate, onSuccess: (r) => { setKey(""); onSecret(r.secret); } },
  );
  const rotate = useAction(
    (id: string, k: string) => call(api.POST("/api/v1/webhooks/endpoints/{endpoint_id}/rotate-secret", { params: { path: { endpoint_id: id } }, ...idem(k) })),
    { invalidate, onSuccess: (r) => onSecret(r.secret) },
  );
  const [revoking, setRevoking] = useState<string | null>(null);
  const revoke = useAction(
    (id: string) => call(api.DELETE("/api/v1/webhooks/endpoints/{endpoint_id}", { params: { path: { endpoint_id: id } } })),
    { invalidate, onSuccess: () => setRevoking(null) },
  );
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!/^[a-z0-9][a-z0-9-]{2,63}$/.test(key)) return setProblem("Use 3–64 lowercase letters, digits or dashes.");
    if (!brands.length) return setProblem("Choose at least one brand.");
    if (!ops.length) return setProblem("Choose at least one operation.");
    setProblem(null);
    create.submit({ key, brand_ids: brands, allowed_operations: ops });
  }
  const actionError = rotate.error ?? revoke.error;
  return (
    <section aria-labelledby="eps-h">
      <h2 id="eps-h">What n8n may do here</h2>
      <p className="small muted">
        Each inbound integration may only run the operations and brands you choose. It can never approve, reject, schedule or publish content,
        change accounts, or read content.
      </p>
      {q.isLoading ? <Loading /> : q.error ? <ErrorPanel error={q.error} onRetry={() => void q.refetch()} /> : null}
      {actionError ? <ErrorPanel title="That did not work" error={actionError} /> : null}
      {q.data?.items.length ? (
        <table className="table">
          <caption className="sr-only">Inbound integrations</caption>
          <thead>
            <tr>
              <th scope="col">Key</th>
              <th scope="col">May</th>
              <th scope="col">Secret</th>
              <th scope="col">Last used</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {q.data.items.map((ep) => (
              <tr key={ep.id}>
                <td className="small">
                  {ep.key}
                  <br />
                  <span className="muted">{ep.inbound_path}</span>
                </td>
                <td className="small">{ep.allowed_operations.join(", ")}</td>
                <td className="small">
                  v{ep.secret_version} · {ep.secret_fingerprint}
                  {ep.previous_secret_valid_until ? <div className="muted">previous valid until {formatDateTime(ep.previous_secret_valid_until)}</div> : null}
                </td>
                <td className="small">{ep.revoked_at ? <Badge tone="neutral">Revoked</Badge> : formatDateTime(ep.last_used_at)}</td>
                <td>
                  {ep.revoked_at ? (
                    "—"
                  ) : (
                    <div className="action-row">
                      <button type="button" className="btn" onClick={() => rotate.submit(ep.id)}>
                        Rotate secret
                      </button>
                      <button type="button" className="btn btn-danger" onClick={() => setRevoking(ep.id)}>
                        Revoke
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : q.data ? (
        <EmptyState title="No inbound integrations" />
      ) : null}
      <Dialog open={revoking !== null} title="Revoke this integration?" onClose={() => setRevoking(null)}>
        <p>Every request signed with its secrets is refused from now on. The record stays for the audit trail.</p>
        <div className="action-row">
          <button type="button" className="btn btn-danger" disabled={revoke.isPending} onClick={() => revoking && revoke.submit(revoking)}>
            Revoke
          </button>
        </div>
      </Dialog>
      <form className="card form" onSubmit={submit} noValidate aria-labelledby="new-ep-h">
        <h3 id="new-ep-h">Add an inbound integration</h3>
        <Field label="Key (part of the URL)" htmlFor="ep-key" error={problem ?? undefined}>
          <input id="ep-key" value={key} onChange={(e) => setKey(e.target.value.toLowerCase())} placeholder="n8n-sources" />
        </Field>
        <BrandChecks selected={brands} onChange={setBrands} />
        <fieldset className="field">
          <legend>Allowed operations</legend>
          {OPERATIONS.map((op) => (
            <label key={op.value} className="checkbox">
              <input
                type="checkbox"
                checked={ops.includes(op.value)}
                onChange={(e) => setOps((xs) => (e.target.checked ? [...xs, op.value] : xs.filter((x) => x !== op.value)))}
              />{" "}
              {op.label}
            </label>
          ))}
        </fieldset>
        {create.error ? <ErrorPanel title="Not added" error={create.error} /> : null}
        <RoleButton role="OWNER" type="submit" className="btn btn-primary" disabled={create.isPending}>
          Add integration
        </RoleButton>
      </form>
    </section>
  );
}
