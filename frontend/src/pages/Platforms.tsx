import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ApiError, api, call, idem, type Schemas } from "../api/client";
import { useAuth } from "../auth/auth";
import { Badge, Dialog, ErrorPanel, Field, Loading, PageHeader, RoleButton, SimulatedNotice } from "../components/ui";
import { useBrand } from "../lib/brand";
import { explainError, formatDateTime } from "../lib/format";
import { useAction } from "../lib/hooks";
import { useAccounts, usePlatformCatalog } from "../lib/queries";
import { FORMAT_LABEL, PLATFORM_LABEL, humanize } from "../lib/status";

const ACCOUNT_TYPES: Schemas["PlatformAccountType"][] = ["PERSONAL", "ORGANIZATION", "PAGE", "CHANNEL", "SUBREDDIT_POSTER", "INSTANCE_USER"];
const VISIBILITY = ["public", "unlisted", "private"] as const;
// Least privilege for the Mastodon connector (docs/PLATFORM_CAPABILITIES.md §4.9).
const MASTODON_SCOPES = ["write:statuses", "read:statuses", "read:accounts"];

/** Why a live post would be refused right now, in plain words (the server re-checks). */
const BLOCKER_TEXT: Record<string, string> = {
  NO_VERIFIED_ADAPTER: "This platform has no verified connector.",
  ENV_TEST: "The server runs in test mode, where live publishing is impossible.",
  PUBLISH_MODE_NOT_LIVE: "The server is not in live publishing mode (PUBLISH_MODE is not LIVE).",
  ACCOUNT_NOT_LIVE_ENABLED: "Live publishing is switched off for this account.",
  ACCOUNT_NOT_CONFIGURED: "The instance address is missing from the account settings.",
  ACCOUNT_SETTINGS_INVALID: "The account settings are invalid.",
  NO_CREDENTIAL: "No access token is stored.",
  CREDENTIAL_EXPIRED: "The stored access token has expired.",
  OPEN_UNCERTAIN: "A publication with an unknown outcome must be reconciled first.",
};

function limitText(l: Schemas["PlatformLimitsRead"]): string {
  const parts = [
    l.max_chars ? `${l.max_chars.toLocaleString()} characters` : null,
    l.max_chars_per_post ? `${l.max_chars_per_post} characters per post` : null,
    l.max_title_chars ? `title ${l.max_title_chars}` : null,
    l.max_thread_posts ? `${l.max_thread_posts} posts per thread` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "No length limit checked";
}

function settingsSummary(a: Schemas["PlatformAccountRead"]): string | null {
  const s = a.settings ?? {};
  const subs = Array.isArray(s["subreddits"]) ? (s["subreddits"] as { subreddit?: string }[]) : [];
  if (subs.length) return `Subreddits: ${subs.map((x) => `r/${x.subreddit}`).join(", ")}`;
  const bits = [
    typeof s["instance_base_url"] === "string" ? `Instance ${s["instance_base_url"]}` : null,
    typeof s["visibility"] === "string" ? `posts ${s["visibility"]}` : null,
    typeof s["max_characters"] === "number" ? `limit ${s["max_characters"]} characters` : null,
  ].filter(Boolean);
  return bits.length ? bits.join(" · ") : null;
}

function CatalogPublishing({ p }: { p: Schemas["PlatformCatalogRead"] }) {
  if (p.effective_publisher === "MOCK") return <Badge tone="simulated">Simulated</Badge>;
  if (p.effective_publisher === "LIVE") return <Badge tone="warning">Live (verified connector)</Badge>;
  return <Badge tone="danger">Off</Badge>;
}

function AccountPublishing({ a }: { a: Schemas["PlatformAccountRead"] }) {
  if (a.effective_publisher === "LIVE")
    return <Badge tone="warning">{a.sandbox ? "Live · sandbox account" : "Live · real account"}</Badge>;
  if (a.effective_publisher === "MOCK")
    return (
      <>
        <Badge tone="simulated">Simulated</Badge>
        {a.live_enabled ? (
          <span className="small muted"> (live switched on; used only when the server runs in live mode)</span>
        ) : null}
      </>
    );
  return <Badge tone="danger">Not publishing</Badge>;
}

export function PlatformsPage() {
  const catalog = usePlatformCatalog();
  const accounts = useAccounts();
  const { brandId } = useBrand();
  const [platform, setPlatform] = useState<Schemas["Platform"]>("LINKEDIN");
  const [type, setType] = useState<Schemas["PlatformAccountType"]>("ORGANIZATION");
  const [handle, setHandle] = useState("");
  const [instance, setInstance] = useState("");
  const [visibility, setVisibility] = useState<(typeof VISIBILITY)[number]>("unlisted");
  const [problem, setProblem] = useState<string | null>(null);
  const [managing, setManaging] = useState<string | null>(null);
  const create = useAction(
    (body: Schemas["PlatformAccountCreate"], key: string) => call(api.POST("/api/v1/platform-accounts", { body, ...idem(key) })),
    { invalidate: [["accounts", brandId]], onSuccess: () => setHandle("") },
  );
  const mastodon = platform === "MASTODON";
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!handle.trim()) return setProblem("Enter the account handle.");
    if (mastodon && instance.trim() && !/^https?:\/\/[^/\s]+\/?$/.test(instance.trim()))
      return setProblem("The instance address is only the server, for example https://mastodon.social");
    setProblem(null);
    create.submit({
      brand_id: brandId ?? "",
      platform,
      account_type: type,
      handle: handle.trim().replace(/^@/, ""),
      settings: mastodon && instance.trim() ? { instance_base_url: instance.trim().replace(/\/$/, ""), visibility } : null,
    });
  }
  const fieldErrors = create.error instanceof ApiError ? create.error.fieldErrors() : {};
  const managed = accounts.data?.items.find((a) => a.id === managing) ?? null;

  return (
    <>
      <PageHeader title="Platforms" description="What OmniGrowth can write for each platform, the limits it checks, and your accounts." />
      <SimulatedNotice>
        Publishing is simulated by the mock publisher unless the server runs in live mode. Only platforms marked “Verified” have a real
        connector (checked against official documentation); every other platform is simulated or off, and its limits below are commonly
        cited values that have not been verified.
      </SimulatedNotice>
      {catalog.isLoading ? <Loading /> : catalog.error ? <ErrorPanel error={catalog.error} onRetry={() => void catalog.refetch()} /> : null}
      {catalog.data ? (
        <table className="table">
          <caption>Supported formats per platform</caption>
          <thead>
            <tr>
              <th scope="col">Platform</th>
              <th scope="col">Formats written</th>
              <th scope="col">Limits checked</th>
              <th scope="col">API capabilities</th>
              <th scope="col">Publishing</th>
            </tr>
          </thead>
          <tbody>
            {catalog.data.items.map((p) => (
              <tr key={p.platform}>
                <td>{PLATFORM_LABEL[p.platform]}</td>
                <td>{p.has_writer ? p.formats.map((f) => FORMAT_LABEL[f]).join(", ") : "Not supported"}</td>
                <td className="small">{limitText(p.limits)}</td>
                <td>
                  {p.capability_status === "VERIFIED" ? <Badge tone="success">Verified</Badge> : <Badge tone="neutral">Unverified</Badge>}
                </td>
                <td>
                  <CatalogPublishing p={p} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <p className="small muted">Other formats (for example comment drafts, Facebook reels and stories, Mastodon polls) are not written yet.</p>

      <section aria-labelledby="acct-h">
        <h2 id="acct-h">Accounts for this brand</h2>
        {accounts.isLoading ? (
          <Loading />
        ) : accounts.error ? (
          <ErrorPanel error={accounts.error} onRetry={() => void accounts.refetch()} />
        ) : accounts.data?.items.length ? (
          <table className="table">
            <caption className="sr-only">Platform accounts</caption>
            <thead>
              <tr>
                <th scope="col">Platform</th>
                <th scope="col">Handle</th>
                <th scope="col">Type</th>
                <th scope="col">Publishing</th>
                <th scope="col">Notes</th>
                <th scope="col">Connection</th>
              </tr>
            </thead>
            <tbody>
              {accounts.data.items.map((a) => (
                <tr key={a.id}>
                  <td>{PLATFORM_LABEL[a.platform]}</td>
                  <td>@{a.handle}</td>
                  <td>
                    {humanize(a.account_type)}
                    {a.sandbox ? (
                      <>
                        {" "}
                        <Badge tone="warning" title="Live posts from this account are sandbox evidence, never real.">
                          Sandbox
                        </Badge>
                      </>
                    ) : null}
                  </td>
                  <td>
                    <AccountPublishing a={a} />
                  </td>
                  <td className="small">
                    {a.has_open_uncertain ? "Has a publication with an unknown outcome. " : ""}
                    {settingsSummary(a) ?? "—"}
                  </td>
                  <td>
                    {a.live_connector_available ? (
                      <RoleButton role="OWNER" reason="Only an owner manages connections." onClick={() => setManaging(a.id)}>
                        Manage connection
                      </RoleButton>
                    ) : (
                      <span className="small muted">Simulated only</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No accounts yet. Scheduling needs an account on the content's platform.</p>
        )}
        {managed ? <ConnectionDialog account={managed} onClose={() => setManaging(null)} /> : null}
        <form className="card form" onSubmit={submit} noValidate aria-labelledby="new-acct-h">
          <h3 id="new-acct-h">Add an account</h3>
          <p className="small muted">
            New accounts publish in simulation only. For a platform with a verified connector, an owner can later store an access token and
            switch the account to live publishing.
          </p>
          <Field label="Platform" htmlFor="a-platform">
            <select id="a-platform" value={platform} onChange={(e) => setPlatform(e.target.value as Schemas["Platform"])}>
              {Object.entries(PLATFORM_LABEL).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Account type" htmlFor="a-type">
            <select id="a-type" value={type} onChange={(e) => setType(e.target.value as Schemas["PlatformAccountType"])}>
              {ACCOUNT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {humanize(t)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Handle" htmlFor="a-handle" error={problem ?? fieldErrors["handle"]}>
            <input id="a-handle" value={handle} onChange={(e) => setHandle(e.target.value)} maxLength={200} />
          </Field>
          {mastodon ? (
            <>
              <Field label="Instance address (for live publishing)" htmlFor="a-instance" hint="Only the server, for example https://mastodon.social">
                <input id="a-instance" type="url" value={instance} onChange={(e) => setInstance(e.target.value)} maxLength={300} />
              </Field>
              <Field label="Who sees live posts" htmlFor="a-visibility">
                <select id="a-visibility" value={visibility} onChange={(e) => setVisibility(e.target.value as (typeof VISIBILITY)[number])}>
                  <option value="public">Public</option>
                  <option value="unlisted">Unlisted (not in public timelines)</option>
                  <option value="private">Followers only</option>
                </select>
              </Field>
            </>
          ) : null}
          {create.error && !fieldErrors["handle"] ? <ErrorPanel title="Not added" error={create.error} onRetry={create.retry} /> : null}
          <RoleButton role="OWNER" type="submit" className="btn btn-primary" disabled={create.isPending} reason="Only an owner can add platform accounts.">
            Add account
          </RoleButton>
        </form>
      </section>
    </>
  );
}

function ConnectionDialog({ account, onClose }: { account: Schemas["PlatformAccountRead"]; onClose: () => void }) {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const { brandId } = useBrand();
  const phrase = `ENABLE LIVE PUBLISHING FOR ${account.platform}`;
  const [token, setToken] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const credentials = useQuery({
    queryKey: ["credentials", account.id],
    queryFn: () => call(api.GET("/api/v1/platform-accounts/{account_id}/credentials", { params: { path: { account_id: account.id } } })),
    enabled: can("OWNER"),
  });
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["credentials", account.id] });
    await queryClient.invalidateQueries({ queryKey: ["accounts", brandId] });
  };
  // Plain mutations (no retry buffer): the token is never kept after the request.
  const store = useMutation({
    mutationFn: (secret: string) =>
      call(
        api.POST("/api/v1/platform-accounts/{account_id}/credentials", {
          params: { path: { account_id: account.id } },
          body: { kind: "OAUTH2", secret, scopes: MASTODON_SCOPES },
        }),
      ),
    onSettled: () => {
      setToken("");
      // Drop the mutation's variables (the plaintext token) from the query cache at once.
      queueMicrotask(() => store.reset());
    },
    onSuccess: refresh,
  });
  const revoke = useMutation({
    mutationFn: (id: string) =>
      call(api.DELETE("/api/v1/platform-accounts/{account_id}/credentials/{credential_id}", { params: { path: { account_id: account.id, credential_id: id } } })),
    onSuccess: refresh,
  });
  const live = useMutation({
    mutationFn: (body: Schemas["PlatformAccountLiveRequest"]) =>
      call(api.POST("/api/v1/platform-accounts/{account_id}/live", { params: { path: { account_id: account.id } }, body })),
    onSuccess: async () => {
      setConfirmation("");
      await refresh();
    },
  });
  const sandbox = useMutation({
    mutationFn: (value: boolean) =>
      call(api.PATCH("/api/v1/platform-accounts/{account_id}", { params: { path: { account_id: account.id } }, body: { sandbox: value } })),
    onSuccess: refresh,
  });
  function saveToken(e: FormEvent) {
    e.preventDefault();
    if (!token.trim()) return setProblem("Paste the access token.");
    setProblem(null);
    store.mutate(token.trim());
  }
  const error = store.error ?? revoke.error ?? live.error ?? sandbox.error;
  const blockers = account.live_blockers ?? [];
  return (
    <Dialog open title={`Connection: ${PLATFORM_LABEL[account.platform]} @${account.handle}`} onClose={onClose}>
      <p className="small">
        The access token is encrypted before it is stored and is never shown again. Create it in the account's own settings with only these
        permissions: {MASTODON_SCOPES.join(", ")}.
      </p>
      <h3>What would stop a live post now</h3>
      {blockers.length ? (
        <ul className="small">
          {blockers.map((b) => (
            <li key={b}>{BLOCKER_TEXT[b] ?? humanize(b)}</li>
          ))}
        </ul>
      ) : (
        <p className="small">Nothing: approved posts scheduled to this account will be published for real (after a final check at send time).</p>
      )}

      <h3>Access token</h3>
      {credentials.isLoading ? <Loading /> : null}
      {credentials.data?.items.length ? (
        <table className="table">
          <caption className="sr-only">Stored tokens</caption>
          <thead>
            <tr>
              <th scope="col">Token</th>
              <th scope="col">Permissions</th>
              <th scope="col">Stored</th>
              <th scope="col">State</th>
            </tr>
          </thead>
          <tbody>
            {credentials.data.items.map((c) => (
              <tr key={c.id}>
                <td className="small">Fingerprint {c.fingerprint}</td>
                <td className="small">{c.scopes.join(", ")}</td>
                <td className="small">{formatDateTime(c.created_at)}</td>
                <td>
                  {c.revoked_at ? (
                    <Badge tone="neutral">Revoked</Badge>
                  ) : (
                    <button type="button" className="btn btn-danger" disabled={revoke.isPending} onClick={() => revoke.mutate(c.id)}>
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : credentials.data ? (
        <p className="small muted">No token stored.</p>
      ) : null}
      <form onSubmit={saveToken} noValidate>
        <Field label={account.has_credential ? "Replace the token" : "Access token"} htmlFor={`tok-${account.id}`} error={problem ?? undefined}>
          <input
            id={`tok-${account.id}`}
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            maxLength={4096}
          />
        </Field>
        <button type="submit" className="btn btn-primary" disabled={store.isPending}>
          Store token
        </button>
      </form>

      <h3>Evidence</h3>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={account.sandbox}
          disabled={account.live_enabled || sandbox.isPending}
          onChange={(e) => sandbox.mutate(e.target.checked)}
        />{" "}
        This is a sandbox (test) account: its live posts and their numbers are never treated as real evidence.
      </label>
      {account.live_enabled ? <p className="small muted">Switch live publishing off to change this.</p> : null}

      <h3>Live publishing</h3>
      {account.live_enabled ? (
        <button type="button" className="btn btn-danger" disabled={live.isPending} onClick={() => live.mutate({ enabled: false })}>
          Switch live publishing off
        </button>
      ) : (
        <>
          <p className="small">
            To allow real posts from this account, type <code>{phrase}</code>. Each post is still checked at send time: human approval of the exact
            version, a stored token, no unresolved uncertain post, and the server in live mode.
          </p>
          <Field label="Confirmation" htmlFor={`live-${account.id}`}>
            <input id={`live-${account.id}`} value={confirmation} onChange={(e) => setConfirmation(e.target.value)} autoComplete="off" />
          </Field>
          <button
            type="button"
            className="btn btn-primary"
            disabled={live.isPending || confirmation !== phrase}
            onClick={() => live.mutate({ enabled: true, confirmation })}
          >
            Enable live publishing
          </button>
        </>
      )}
      {error ? (
        <div className="notice notice-danger" role="alert">
          {explainError(error)}
        </div>
      ) : null}
    </Dialog>
  );
}
