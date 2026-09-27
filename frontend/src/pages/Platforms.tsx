import { useState, type FormEvent } from "react";
import { ApiError, api, call, idem, type Schemas } from "../api/client";
import { Badge, ErrorPanel, Field, Loading, PageHeader, RoleButton, SimulatedNotice } from "../components/ui";
import { useBrand } from "../lib/brand";
import { useAction } from "../lib/hooks";
import { useAccounts, usePlatformCatalog } from "../lib/queries";
import { FORMAT_LABEL, PLATFORM_LABEL, humanize } from "../lib/status";

const ACCOUNT_TYPES: Schemas["PlatformAccountType"][] = ["PERSONAL", "ORGANIZATION", "PAGE", "CHANNEL", "SUBREDDIT_POSTER", "INSTANCE_USER"];

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
  if (typeof s["max_characters"] === "number") return `Instance limit: ${s["max_characters"]} characters`;
  return null;
}

export function PlatformsPage() {
  const catalog = usePlatformCatalog();
  const accounts = useAccounts();
  const { brandId } = useBrand();
  const [platform, setPlatform] = useState<Schemas["Platform"]>("LINKEDIN");
  const [type, setType] = useState<Schemas["PlatformAccountType"]>("ORGANIZATION");
  const [handle, setHandle] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const create = useAction(
    (body: Schemas["PlatformAccountCreate"], key: string) => call(api.POST("/api/v1/platform-accounts", { body, ...idem(key) })),
    { invalidate: [["accounts", brandId]], onSuccess: () => setHandle("") },
  );
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!handle.trim()) {
      setProblem("Enter the account handle.");
      return;
    }
    setProblem(null);
    create.submit({ brand_id: brandId ?? "", platform, account_type: type, handle: handle.trim().replace(/^@/, "") });
  }
  const fieldErrors = create.error instanceof ApiError ? create.error.fieldErrors() : {};

  return (
    <>
      <PageHeader title="Platforms" description="What OmniGrowth can write for each platform, the limits it checks, and your accounts." />
      <SimulatedNotice>
        Publishing to every platform is simulated by the mock publisher. No platform's real API has been verified yet, so live publishing is not
        available, and the limits below are commonly cited values that have not been checked against official documentation.
      </SimulatedNotice>
      {catalog.isLoading ? <Loading /> : catalog.error ? <ErrorPanel error={catalog.error} onRetry={() => void catalog.refetch()} /> : null}
      {catalog.data ? (
        <table className="table">
          <caption>Supported formats per platform</caption>
          <thead>
            <tr>
              <th scope="col">Platform</th>
              <th scope="col">Formats written</th>
              <th scope="col">Limits checked (unverified)</th>
              <th scope="col">Publishing</th>
            </tr>
          </thead>
          <tbody>
            {catalog.data.items.map((p) => (
              <tr key={p.platform}>
                <td>{PLATFORM_LABEL[p.platform]}</td>
                <td>{p.has_writer ? p.formats.map((f) => FORMAT_LABEL[f]).join(", ") : "Not supported"}</td>
                <td className="small">{limitText(p.limits)}</td>
                <td>{p.effective_publisher === "MOCK" ? <Badge tone="simulated">Simulated</Badge> : <Badge tone="danger">Off</Badge>}</td>
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
              </tr>
            </thead>
            <tbody>
              {accounts.data.items.map((a) => (
                <tr key={a.id}>
                  <td>{PLATFORM_LABEL[a.platform]}</td>
                  <td>@{a.handle}</td>
                  <td>{humanize(a.account_type)}</td>
                  <td>
                    {a.live_enabled ? (
                      <Badge tone="warning" title="Live publishing is enabled on the account, but no live publisher exists yet; posts are still simulated or refused.">
                        Live-enabled (no live publisher)
                      </Badge>
                    ) : (
                      <Badge tone="simulated">Simulated</Badge>
                    )}
                  </td>
                  <td className="small">
                    {a.has_open_uncertain ? "Has a publication with an unknown outcome. " : ""}
                    {settingsSummary(a) ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No accounts yet. Scheduling needs an account on the content's platform.</p>
        )}
        <form className="card form" onSubmit={submit} noValidate aria-labelledby="new-acct-h">
          <h3 id="new-acct-h">Add an account</h3>
          <p className="small muted">Adds the account for simulated publishing only. No credentials are asked for and nothing connects to the platform.</p>
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
          {create.error && !fieldErrors["handle"] ? <ErrorPanel title="Not added" error={create.error} onRetry={create.retry} /> : null}
          <RoleButton role="OWNER" type="submit" className="btn btn-primary" disabled={create.isPending} reason="Only an owner can add platform accounts.">
            Add account
          </RoleButton>
        </form>
      </section>
    </>
  );
}
