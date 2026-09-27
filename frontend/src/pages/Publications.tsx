import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/auth";
import { Link } from "react-router-dom";
import { api, call, idem, type Schemas } from "../api/client";
import { Dialog, EmptyState, ErrorPanel, Field, Loading, PageHeader, RoleButton, SimulatedNotice, StatusBadge } from "../components/ui";
import { useBrand } from "../lib/brand";
import { explainError, formatDateTime } from "../lib/format";
import { useAction } from "../lib/hooks";
import { useCampaigns } from "../lib/queries";
import { JOB_STATUS, PLATFORM_LABEL, humanize, publicationInfo } from "../lib/status";

type Job = Schemas["PublicationJobRead"];

export function PublicationsPage() {
  const { brandId } = useBrand();
  const campaigns = useCampaigns();
  const [campaignId, setCampaignId] = useState("");
  const jobs = useQuery({
    queryKey: ["jobs", campaignId || "all"],
    queryFn: () => call(api.GET("/api/v1/publications/jobs", { params: { query: { campaign_id: campaignId || undefined } } })),
    refetchInterval: 15_000,
  });
  const pubs = useQuery({
    queryKey: ["publications", campaignId || "all"],
    queryFn: () => call(api.GET("/api/v1/publications", { params: { query: { campaign_id: campaignId || undefined } } })),
  });
  const mine = <T extends { brand_id: string }>(xs: T[] | undefined) => (xs ?? []).filter((x) => x.brand_id === brandId);
  const allJobs = mine(jobs.data?.items);
  const upcoming = allJobs.filter((j) => ["PENDING", "CLAIMED", "RETRY_WAIT"].includes(j.status));
  const problems = allJobs.filter((j) => ["FAILED", "UNCERTAIN"].includes(j.status));
  const outcomes = mine(pubs.data?.items);

  return (
    <>
      <PageHeader title="Publishing" description="Scheduled jobs, failures that need a person, and what was published." />
      <SimulatedNotice />
      <div className="filters">
        <label>
          Campaign{" "}
          <select value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
            <option value="">All campaigns</option>
            {(campaigns.data?.items ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {jobs.isLoading || pubs.isLoading ? <Loading /> : null}
      {jobs.error ? <ErrorPanel error={jobs.error} onRetry={() => void jobs.refetch()} /> : null}
      {pubs.error ? <ErrorPanel error={pubs.error} onRetry={() => void pubs.refetch()} /> : null}

      <section aria-labelledby="up-h">
        <h2 id="up-h">Scheduled and in progress</h2>
        {upcoming.length ? <JobTable jobs={upcoming} /> : <p className="muted">Nothing is waiting to be published.</p>}
      </section>
      <section aria-labelledby="pr-h">
        <h2 id="pr-h">Needs attention</h2>
        {problems.length ? (
          <>
            <p className="small muted">
              “Failed” means every attempt failed and nothing was published by the job; open the content and schedule it again once
              the cause is fixed (there is no one-click retry yet). “Outcome unknown” means we cannot tell whether the post went out: an
              editor can confirm it was published, only an owner can record that it was not, and until then the account publishes
              nothing else.
            </p>
            <JobTable jobs={problems} />
          </>
        ) : (
          <p className="muted">No failed or uncertain publications.</p>
        )}
      </section>
      <section aria-labelledby="out-h">
        <h2 id="out-h">Outcomes</h2>
        {outcomes.length ? (
          <table className="table">
            <caption className="sr-only">Publication outcomes</caption>
            <thead>
              <tr>
                <th scope="col">Content</th>
                <th scope="col">Platform</th>
                <th scope="col">When</th>
                <th scope="col">Outcome</th>
                <th scope="col">Mode</th>
                <th scope="col">Resolve</th>
              </tr>
            </thead>
            <tbody>
              {outcomes.map((p) => (
                <tr key={p.id}>
                  <td>
                    <Link to={`/content/${p.content_asset_id}`}>Open</Link>
                  </td>
                  <td>{PLATFORM_LABEL[p.platform]}</td>
                  <td>{formatDateTime(p.published_at ?? p.scheduled_at)}</td>
                  <td>
                    <StatusBadge info={publicationInfo(p)} />
                  </td>
                  <td>{p.publish_mode === "MOCK" ? "Simulated (mock publisher)" : humanize(p.publish_mode)}</td>
                  <td>{p.status === "PUBLICATION_UNCERTAIN" ? <Reconcile publication={p} /> : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState title="Nothing published yet">Approved content appears here after its scheduled time passes.</EmptyState>
        )}
      </section>
    </>
  );
}

function JobTable({ jobs }: { jobs: Job[] }) {
  return (
    <table className="table">
      <caption className="sr-only">Publication jobs</caption>
      <thead>
        <tr>
          <th scope="col">Content</th>
          <th scope="col">Due</th>
          <th scope="col">State</th>
          <th scope="col">Attempts</th>
          <th scope="col">Last error</th>
          <th scope="col">Action</th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((j) => (
          <tr key={j.id}>
            <td>
              <Link to={`/content/${j.content_asset_id}`}>Open</Link>
            </td>
            <td>{formatDateTime(j.due_at, j.schedule_timezone)}</td>
            <td>
              <StatusBadge info={JOB_STATUS[j.status]} />
            </td>
            <td>
              {j.attempt_count} / {j.max_attempts}
            </td>
            <td className="small">{j.last_error_code ? humanize(j.last_error_code) : "—"}</td>
            <td>{j.status === "PENDING" ? <CancelJob job={j} /> : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CancelJob({ job }: { job: Job }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const cancel = useAction(
    (body: Schemas["omnigrowth__schemas__publishing__CancelRequest"], key: string) =>
      call(api.POST("/api/v1/publications/jobs/{job_id}/cancel", { params: { path: { job_id: job.id } }, body, ...idem(key) })),
    { invalidate: [["jobs"], ["calendar"], ["asset", job.content_asset_id]], onSuccess: () => setOpen(false) },
  );
  return (
    <>
      <RoleButton role="EDITOR" onClick={() => setOpen(true)}>
        Cancel
      </RoleButton>
      <Dialog open={open} title="Cancel this scheduled publication?" onClose={() => setOpen(false)}>
        <p>The content stays approved; you can schedule it again later.</p>
        <Field label="Reason (optional)" htmlFor={`cancel-${job.id}`}>
          <input id={`cancel-${job.id}`} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={2000} />
        </Field>
        {cancel.error ? (
          <div className="notice notice-danger" role="alert">
            {explainError(cancel.error)}
          </div>
        ) : null}
        <div className="action-row">
          <button type="button" className="btn btn-danger" disabled={cancel.isPending} onClick={() => cancel.submit({ reason: reason || null })}>
            Cancel publication
          </button>
          <button type="button" className="btn" onClick={() => setOpen(false)}>
            Keep it
          </button>
        </div>
      </Dialog>
    </>
  );
}

function Reconcile({ publication }: { publication: Schemas["PublicationRead"] }) {
  const { can } = useAuth();
  const [open, setOpen] = useState(false);
  const [method, setMethod] = useState<Schemas["ReconcileMethod"]>("MANUAL_CONFIRM_PUBLISHED");
  const [url, setUrl] = useState("");
  const [externalId, setExternalId] = useState("");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const act = useAction(
    (body: Schemas["PublicationReconcileRequest"], key: string) =>
      call(api.POST("/api/v1/publications/{publication_id}/reconcile", { params: { path: { publication_id: publication.id } }, body, ...idem(key) })),
    { invalidate: [["publications"], ["jobs"]], onSuccess: () => setOpen(false) },
  );
  const published = method === "MANUAL_CONFIRM_PUBLISHED";
  function submit(e: FormEvent) {
    e.preventDefault();
    if (published && !url.trim()) return setProblem("Paste the link to the post you found.");
    if (!published && !note.trim()) return setProblem("Say how you checked that it was not published.");
    setProblem(null);
    act.submit(
      published
        ? { method, canonical_url: url.trim(), external_post_id: externalId.trim() || null, note: note.trim() || null }
        : { method, note: note.trim() },
    );
  }
  return (
    <>
      <RoleButton role="EDITOR" onClick={() => setOpen(true)}>
        Resolve
      </RoleButton>
      <Dialog open={open} title="Resolve an uncertain publication" onClose={() => setOpen(false)}>
        <form onSubmit={submit} noValidate>
          <p>Check the platform account yourself, then record what you found. Until then this account publishes nothing else.</p>
          {publication.publish_mode === "MOCK" ? (
            <p className="small muted">This was a mock publication: there is no real platform post to look for.</p>
          ) : null}
          <fieldset className="field">
            <legend>What did you find?</legend>
            <label className="checkbox">
              <input type="radio" name={`m-${publication.id}`} checked={published} onChange={() => setMethod("MANUAL_CONFIRM_PUBLISHED")} /> It was published
            </label>
            <label className="checkbox">
              <input
                type="radio"
                name={`m-${publication.id}`}
                checked={!published}
                disabled={!can("OWNER")}
                onChange={() => setMethod("MANUAL_CONFIRM_NOT_PUBLISHED")}
              />{" "}
              It was not published {can("OWNER") ? "" : "(only an owner can record this)"}
            </label>
          </fieldset>
          {published ? (
            <>
              <Field label="Link to the post (required)" htmlFor={`recu-${publication.id}`}>
                <input id={`recu-${publication.id}`} type="url" value={url} onChange={(e) => setUrl(e.target.value)} maxLength={2048} />
              </Field>
              <Field label="Post id on the platform (optional)" htmlFor={`reci-${publication.id}`}>
                <input id={`reci-${publication.id}`} value={externalId} onChange={(e) => setExternalId(e.target.value)} maxLength={500} />
              </Field>
            </>
          ) : null}
          <Field label={published ? "Note (optional)" : "How you checked (required)"} htmlFor={`recn-${publication.id}`}>
            <input id={`recn-${publication.id}`} value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
          </Field>
          {problem ? (
            <div className="small error-text" role="alert">
              {problem}
            </div>
          ) : null}
          {act.error ? (
            <div className="notice notice-danger" role="alert">
              {explainError(act.error)}
            </div>
          ) : null}
          <div className="action-row">
            <button type="submit" className="btn btn-primary" disabled={act.isPending}>
              Record
            </button>
          </div>
        </form>
      </Dialog>
    </>
  );
}
