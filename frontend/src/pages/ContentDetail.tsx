import { useQuery } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError, api, call, idem, type Schemas } from "../api/client";
import { AssetCard, QaResults, VersionView } from "../components/content";
import { Dialog, ErrorPanel, Field, KeyValue, Loading, PageHeader, RoleButton, SimulatedNotice, StatusBadge } from "../components/ui";
import { formatDateTime, localInputToIso, toLocalInput, explainError } from "../lib/format";
import { useAction } from "../lib/hooks";
import { useAccounts, useCampaignNames, usePlatformCatalog, usePublishMode } from "../lib/queries";
import { ASSET_STATUS, EVIDENCE, FORMAT_LABEL, JOB_STATUS, PLATFORM_LABEL, humanize, publicationInfo } from "../lib/status";

type Asset = Schemas["ContentAssetRead"];
type Action = Schemas["ApprovalActionType"];

/**
 * Which actions this screen OFFERS in each state. A presentation hint to keep the screen
 * focused; the API's state machine is authoritative and its refusal is shown verbatim.
 */
const OFFERED: Partial<Record<Schemas["ContentAssetStatus"], Action[]>> = {
  AWAITING_APPROVAL: ["APPROVE", "REQUEST_REVISION", "REJECT", "EDIT", "REGENERATE"],
  APPROVED: ["SCHEDULE", "PUBLISH_NOW", "EDIT", "REGENERATE"],
  SCHEDULED: ["SCHEDULE", "EDIT", "REGENERATE"],
  QA_FAILED: ["EDIT", "REGENERATE"],
  REVISION_REQUESTED: ["EDIT", "REGENERATE"],
  REJECTED: ["EDIT", "REGENERATE"],
};

const WHY_NOT: Partial<Record<Schemas["ContentAssetStatus"], string>> = {
  DRAFT: "A writer is working on it. Actions appear when it has been written and checked.",
  GENERATED: "Automatic quality checks are running. Actions appear when they finish.",
  QA_PASSED: "It passed the checks and is moving to the approval queue.",
  QA_FAILED: "It cannot be approved until the failed checks are fixed: edit it or regenerate it.",
  REVISION_REQUESTED: "A reviewer asked for changes: edit or regenerate it, then it is checked again.",
  REJECTED: "It was rejected. Edit or regenerate it to send it back through checks and approval.",
  PUBLISHED: "It has been handled by the mock publisher. Nothing more can be done to this version; you can repurpose it for another platform.",
  ARCHIVED: "Archived content has no actions.",
};

const ACTION_LABEL: Record<Action, string> = {
  APPROVE: "Approve",
  REQUEST_REVISION: "Request revision",
  REJECT: "Reject",
  EDIT: "Edit",
  REGENERATE: "Regenerate",
  SCHEDULE: "Schedule",
  PUBLISH_NOW: "Publish now (simulated)",
};

export function ContentDetailPage() {
  const { assetId = "" } = useParams();
  const [params] = useSearchParams();
  const fromApprovals = params.get("from") === "approvals";
  const names = useCampaignNames();
  const q = useQuery({
    queryKey: ["asset", assetId],
    queryFn: () => call(api.GET("/api/v1/content/{asset_id}", { params: { path: { asset_id: assetId } } })),
    // Poll while a writer or the quality checks are still working; stop after ~10 minutes
    // (a failed generation leaves the asset in DRAFT; the status text explains what to do).
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      const working = s === "DRAFT" || s === "GENERATED" || s === "QA_PASSED";
      return working && query.state.dataUpdateCount < 200 ? 3000 : false;
    },
  });

  if (q.isLoading) return <Loading label="Loading content…" />;
  if (q.error || !q.data) return <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />;
  const asset = q.data;
  const status = ASSET_STATUS[asset.status];

  return (
    <>
      <p className="breadcrumb">
        <Link to={fromApprovals ? "/approvals" : "/content"}>← {fromApprovals ? "Approvals" : "Content"}</Link>
      </p>
      <PageHeader
        title={`${PLATFORM_LABEL[asset.platform]} ${FORMAT_LABEL[asset.format].toLowerCase()}`}
        description={
          <>
            Campaign: <Link to={`/campaigns/${asset.campaign_id}`}>{names.get(asset.campaign_id) ?? "open campaign"}</Link>
          </>
        }
        actions={<StatusBadge info={status} />}
      />
      <div className="notice notice-neutral" role="status" aria-live="polite">
        <strong>{status.label}.</strong> {status.help}
      </div>
      <div className="detail-grid">
        <section aria-labelledby="content-h" className="card">
          <h2 id="content-h">Content</h2>
          {asset.current_version ? <VersionView version={asset.current_version} platform={asset.platform} /> : <p className="muted">No version written yet.</p>}
        </section>
        <div className="stack">
          <ActionsPanel key={asset.id} asset={asset} />
          <section aria-labelledby="qa-h" className="card">
            <h2 id="qa-h">Quality checks</h2>
            <QaResults results={asset.qa ?? []} />
          </section>
        </div>
      </div>
      <PublishingRecord asset={asset} />
      <VersionsAndHistory asset={asset} />
      <Lineage key={asset.id} asset={asset} />
    </>
  );
}

// ------------------------------------------------------------------ actions
function ActionsPanel({ asset }: { asset: Asset }) {
  const offered = OFFERED[asset.status] ?? [];
  const [open, setOpen] = useState<Action | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const action = useAction(
    (body: Schemas["ApprovalActionRequest"], key: string) =>
      call(api.POST("/api/v1/approvals/{asset_id}/actions", { params: { path: { asset_id: asset.id } }, body, ...idem(key) })),
    {
      invalidate: [["asset", asset.id], ["approvals"], ["content"], ["history", asset.id], ["versions", asset.id], ["publications"], ["jobs"], ["calendar"]],
      onSuccess: (result, input) => {
        setOpen(null);
        setNotice(successText(input.action, result));
      },
    },
  );

  const reviewVersion = asset.current_version_id ?? "";
  const approvedVersion = asset.approved_version_id ?? reviewVersion;
  const run = (body: Omit<Schemas["ApprovalActionRequest"], "content_version_id">, versionId = reviewVersion) => {
    setNotice(null);
    action.submit({ ...body, content_version_id: versionId } as Schemas["ApprovalActionRequest"]);
  };

  return (
    <section aria-labelledby="actions-h" className="card">
      <h2 id="actions-h">What you can do</h2>
      {notice ? (
        <div className="notice notice-success" role="status">
          {notice}
        </div>
      ) : null}
      {offered.length ? (
        <div className="action-row">
          {offered.map((a) => (
            <RoleButton
              key={a}
              role="EDITOR"
              className={a === "APPROVE" ? "btn btn-primary" : a === "REJECT" ? "btn btn-danger" : "btn"}
              disabled={action.isPending}
              onClick={() => (a === "APPROVE" ? run({ action: "APPROVE" }) : setOpen(a))}
            >
              {a === "SCHEDULE" && asset.status === "SCHEDULED" ? "Reschedule" : ACTION_LABEL[a]}
            </RoleButton>
          ))}
        </div>
      ) : null}
      {WHY_NOT[asset.status] ? <p className="small muted">{WHY_NOT[asset.status]}</p> : null}
      {action.isPending ? <Loading label="Sending…" /> : null}
      {action.error && !open ? <ErrorPanel title="The action was not applied" error={action.error} onRetry={action.retry} /> : null}

      <CommentDialog
        key={`comment-${open}-${asset.current_version_id}`}
        open={open === "REJECT" || open === "REQUEST_REVISION"}
        action={open === "REJECT" ? "REJECT" : "REQUEST_REVISION"}
        pending={action.isPending}
        error={open ? action.error : null}
        onClose={() => setOpen(null)}
        onSubmit={(comment) => run({ action: open === "REJECT" ? "REJECT" : "REQUEST_REVISION", comment })}
      />
      <EditDialog
        key={`edit-${open === "EDIT"}-${asset.current_version_id}`}
        open={open === "EDIT"}
        asset={asset}
        pending={action.isPending}
        error={open === "EDIT" ? action.error : null}
        onClose={() => setOpen(null)}
        onSubmit={(edit) => run({ action: "EDIT", edit })}
      />
      <RegenerateDialog
        key={`regen-${open === "REGENERATE"}`}
        open={open === "REGENERATE"}
        pending={action.isPending}
        error={open === "REGENERATE" ? action.error : null}
        onClose={() => setOpen(null)}
        onSubmit={(comment) => run({ action: "REGENERATE", comment: comment || null })}
      />
      <ScheduleDialog
        key={`schedule-${open}`}
        open={open === "SCHEDULE" || open === "PUBLISH_NOW"}
        now={open === "PUBLISH_NOW"}
        asset={asset}
        pending={action.isPending}
        error={open === "SCHEDULE" || open === "PUBLISH_NOW" ? action.error : null}
        onClose={() => setOpen(null)}
        onSubmit={(accountId, when) =>
          run(
            open === "PUBLISH_NOW"
              ? { action: "PUBLISH_NOW", platform_account_id: accountId }
              : { action: "SCHEDULE", platform_account_id: accountId, scheduled_at: when },
            approvedVersion,
          )
        }
      />
    </section>
  );
}

function successText(action: Action, result: Schemas["ApprovalActionResultRead"]): string {
  switch (action) {
    case "APPROVE":
      return "Approved. Next, schedule it or publish it now (both simulated).";
    case "REJECT":
      return "Rejected. It will not be published unless it is edited or regenerated and approved again.";
    case "REQUEST_REVISION":
      return "Revision requested. The comment is kept in the history.";
    case "EDIT":
      return "Your edit was saved as a new version. Automatic checks run again before it returns to the approval queue.";
    case "REGENERATE":
      return "Regeneration started. A writer produces a new version, then the checks run again.";
    case "SCHEDULE":
      return `Scheduled for ${formatDateTime(result.publication_job?.due_at)}. Publication will be simulated by the mock publisher.`;
    case "PUBLISH_NOW":
      return "Handed to the mock publisher. It is simulated: no platform is contacted.";
  }
}

function DialogError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="notice notice-danger" role="alert">
      {explainError(error)}
      {error instanceof ApiError && error.correlationId ? <div className="small muted">Reference: {error.correlationId}</div> : null}
    </div>
  );
}

function CommentDialog(props: { open: boolean; action: "REJECT" | "REQUEST_REVISION"; pending: boolean; error: unknown; onClose: () => void; onSubmit: (comment: string) => void }) {
  const [comment, setComment] = useState("");
  const [missing, setMissing] = useState(false);
  const id = useId();
  const reject = props.action === "REJECT";
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!comment.trim()) {
      setMissing(true);
      return;
    }
    props.onSubmit(comment.trim());
  }
  return (
    <Dialog open={props.open} title={reject ? "Reject this content" : "Request a revision"} onClose={props.onClose}>
      <form onSubmit={submit} noValidate>
        <Field label="Reason (required)" htmlFor={id} error={missing ? "Please say why, so the next version can address it." : undefined}>
          <textarea id={id} rows={4} value={comment} onChange={(e) => { setComment(e.target.value); setMissing(false); }} />
        </Field>
        <DialogError error={props.error} />
        <div className="action-row">
          <button type="submit" className={reject ? "btn btn-danger" : "btn btn-primary"} disabled={props.pending}>
            {reject ? "Reject" : "Request revision"}
          </button>
          <button type="button" className="btn" onClick={props.onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

const TITLED = new Set<Schemas["Platform"]>(["YOUTUBE", "RUMBLE", "REDDIT", "SUBSTACK"]);
const THREADED = new Set<Schemas["Platform"]>(["X", "MASTODON"]);

function EditDialog(props: { open: boolean; asset: Asset; pending: boolean; error: unknown; onClose: () => void; onSubmit: (edit: Schemas["ApprovalEdit"]) => void }) {
  const v = props.asset.current_version;
  const [title, setTitle] = useState(v?.title ?? "");
  const [hook, setHook] = useState(v?.hook ?? "");
  const [body, setBody] = useState(v?.body ?? "");
  const [cta, setCta] = useState(v?.cta ?? "");
  const tags = (v?.platform_payload?.["hashtags"] as string[] | undefined) ?? [];
  const [hashtags, setHashtags] = useState(tags.join(" "));
  const [invalid, setInvalid] = useState(false);
  const threaded = THREADED.has(props.asset.platform);
  const fieldErrors = props.error instanceof ApiError ? props.error.fieldErrors() : {};
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!body.trim()) {
      setInvalid(true);
      return;
    }
    props.onSubmit({
      body,
      // An empty string clears the field; null would keep the previous value.
      hook: threaded ? null : hook,
      cta,
      title: TITLED.has(props.asset.platform) ? title : null,
      hashtags: hashtags.split(/[\s,]+/).filter(Boolean),
    });
  }
  return (
    <Dialog open={props.open} title="Edit content" onClose={props.onClose}>
      <form onSubmit={submit} noValidate>
        <p className="small muted">Saving creates a new version. The previous one is kept, and the new one goes through the automatic checks and approval again.</p>
        {TITLED.has(props.asset.platform) ? (
          <Field label="Title" htmlFor="edit-title" error={fieldErrors["title"]}>
            <input id="edit-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} />
          </Field>
        ) : null}
        {!threaded ? (
          <Field label="Hook (first line people see)" htmlFor="edit-hook" error={fieldErrors["hook"]}>
            <input id="edit-hook" value={hook} onChange={(e) => setHook(e.target.value)} maxLength={600} />
          </Field>
        ) : null}
        <Field
          label={threaded ? "Posts" : "Text"}
          htmlFor="edit-body"
          error={invalid ? "The text cannot be empty." : fieldErrors["body"]}
          hint={threaded ? "Separate posts with a line containing only ---" : undefined}
        >
          <textarea id="edit-body" rows={12} value={body} onChange={(e) => { setBody(e.target.value); setInvalid(false); }} maxLength={20000} />
        </Field>
        <Field label="Call to action" htmlFor="edit-cta" error={fieldErrors["cta"]}>
          <input id="edit-cta" value={cta} onChange={(e) => setCta(e.target.value)} maxLength={400} />
        </Field>
        <Field label="Hashtags" htmlFor="edit-tags" hint="Separated by spaces or commas." error={fieldErrors["hashtags"]}>
          <input id="edit-tags" value={hashtags} onChange={(e) => setHashtags(e.target.value)} />
        </Field>
        <p className="small muted">Scenes, script sections and other production notes are kept as they are; editing them here is not supported yet.</p>
        <DialogError error={props.error} />
        <div className="action-row">
          <button type="submit" className="btn btn-primary" disabled={props.pending}>
            Save new version
          </button>
          <button type="button" className="btn" onClick={props.onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function RegenerateDialog(props: { open: boolean; pending: boolean; error: unknown; onClose: () => void; onSubmit: (instructions: string) => void }) {
  const [text, setText] = useState("");
  return (
    <Dialog open={props.open} title="Regenerate with the writer" onClose={props.onClose}>
      <form onSubmit={(e) => { e.preventDefault(); props.onSubmit(text.trim()); }}>
        <p className="small muted">The platform's writer produces a new version using the local model. This can take a few minutes. Any approval is cleared.</p>
        <Field label="Instructions (optional)" htmlFor="regen-notes">
          <textarea id="regen-notes" rows={4} value={text} onChange={(e) => setText(e.target.value)} maxLength={4000} />
        </Field>
        <DialogError error={props.error} />
        <div className="action-row">
          <button type="submit" className="btn btn-primary" disabled={props.pending}>
            Regenerate
          </button>
          <button type="button" className="btn" onClick={props.onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function ScheduleDialog(props: { open: boolean; now: boolean; asset: Asset; pending: boolean; error: unknown; onClose: () => void; onSubmit: (accountId: string, whenIso: string) => void }) {
  const accounts = useAccounts(props.asset.brand_id, props.asset.platform);
  const options = (accounts.data?.items ?? []).filter((a) => a.platform === props.asset.platform && a.brand_id === props.asset.brand_id);
  const mode = usePublishMode();
  const jobs = useQuery({
    queryKey: ["jobs", props.asset.campaign_id],
    queryFn: () => call(api.GET("/api/v1/publications/jobs", { params: { query: { campaign_id: props.asset.campaign_id } } })),
    enabled: props.open,
  });
  const pending = (jobs.data?.items ?? []).find((j) => j.content_asset_id === props.asset.id && j.status === "PENDING");
  const [accountId, setAccountId] = useState("");
  const [when, setWhen] = useState(() => toLocalInput(new Date(Date.now() + 60 * 60 * 1000)));
  const [problem, setProblem] = useState<string | null>(null);
  // Rescheduling keeps the account of the pending job unless the person picks another.
  const chosen = accountId || pending?.platform_account_id || options[0]?.id || "";
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!chosen) {
      setProblem("Choose an account.");
      return;
    }
    const at = new Date(when).getTime();
    if (!props.now && !Number.isFinite(at)) {
      setProblem("Enter a date and time.");
      return;
    }
    if (!props.now && at <= Date.now()) {
      setProblem("Pick a time in the future.");
      return;
    }
    setProblem(null);
    props.onSubmit(chosen, props.now ? new Date().toISOString() : localInputToIso(when));
  }
  return (
    <Dialog open={props.open} title={props.now ? "Publish now (simulated)" : "Schedule publication"} onClose={props.onClose}>
      <form onSubmit={submit} noValidate>
        {mode === "MOCK" ? (
          <SimulatedNotice />
        ) : mode === null ? (
          <div className="notice notice-danger" role="alert">
            Publishing is switched off on this server: no publisher is available, so a scheduled job would fail.
          </div>
        ) : null}
        {accounts.isLoading ? (
          <Loading label="Loading accounts…" />
        ) : options.length === 0 ? (
          <div className="notice notice-warning" role="alert">
            This brand has no {PLATFORM_LABEL[props.asset.platform]} account yet. An owner can add one under <Link to="/platforms">Platforms</Link>.
          </div>
        ) : (
          <Field label={`${PLATFORM_LABEL[props.asset.platform]} account`} htmlFor="sched-account">
            <select id="sched-account" value={chosen} onChange={(e) => setAccountId(e.target.value)}>
              {options.map((a) => (
                <option key={a.id} value={a.id}>
                  @{a.handle}
                  {a.has_open_uncertain ? " (has an unresolved publication)" : ""}
                </option>
              ))}
            </select>
          </Field>
        )}
        {!props.now ? (
          <Field label="Date and time" htmlFor="sched-when" hint={`Your local time (${Intl.DateTimeFormat().resolvedOptions().timeZone}).`}>
            <input id="sched-when" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
          </Field>
        ) : null}
        {problem ? (
          <div className="small error-text" role="alert">
            {problem}
          </div>
        ) : null}
        <DialogError error={props.error} />
        <div className="action-row">
          <button type="submit" className="btn btn-primary" disabled={props.pending || options.length === 0}>
            {props.now ? "Publish now (simulated)" : "Schedule"}
          </button>
          <button type="button" className="btn" onClick={props.onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

// ------------------------------------------------------------------ publishing record
function PublishingRecord({ asset }: { asset: Asset }) {
  const jobs = useQuery({
    queryKey: ["jobs", asset.campaign_id],
    queryFn: () => call(api.GET("/api/v1/publications/jobs", { params: { query: { campaign_id: asset.campaign_id } } })),
  });
  const pubs = useQuery({
    queryKey: ["publications", asset.campaign_id],
    queryFn: () => call(api.GET("/api/v1/publications", { params: { query: { campaign_id: asset.campaign_id } } })),
  });
  const myJobs = (jobs.data?.items ?? []).filter((j) => j.content_asset_id === asset.id);
  const myPubs = (pubs.data?.items ?? []).filter((p) => p.content_asset_id === asset.id);
  if (!myJobs.length && !myPubs.length) return null;
  return (
    <section aria-labelledby="pub-h" className="card">
      <h2 id="pub-h">Publishing</h2>
      {myPubs.some((p) => p.evidence_class === "SIMULATED") ? <SimulatedNotice /> : null}
      <table className="table">
        <caption className="sr-only">Publication jobs and outcomes for this content</caption>
        <thead>
          <tr>
            <th scope="col">What</th>
            <th scope="col">When</th>
            <th scope="col">State</th>
            <th scope="col">Details</th>
          </tr>
        </thead>
        <tbody>
          {myJobs.map((j) => (
            <tr key={j.id}>
              <td>Publication job</td>
              <td>{formatDateTime(j.due_at, j.schedule_timezone)}</td>
              <td>
                <StatusBadge info={JOB_STATUS[j.status]} />
              </td>
              <td className="small">
                Attempt {j.attempt_count} of {j.max_attempts}
                {j.last_error_code ? ` · last error: ${humanize(j.last_error_code)}` : ""}
              </td>
            </tr>
          ))}
          {myPubs.map((p) => (
            <tr key={p.id}>
              <td>
                Publication <StatusBadge info={EVIDENCE[p.evidence_class]} />
              </td>
              <td>{formatDateTime(p.published_at ?? p.scheduled_at)}</td>
              <td>
                <StatusBadge info={publicationInfo(p)} />
              </td>
              <td className="small">
                {p.canonical_url && p.evidence_class !== "SIMULATED" ? (
                  <a href={p.canonical_url} target="_blank" rel="noreferrer noopener">
                    View post
                  </a>
                ) : p.external_post_id ? (
                  `${p.evidence_class === "SIMULATED" ? "Mock post id" : "Post id"} ${p.external_post_id}`
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

// ------------------------------------------------------------------ versions + history
function VersionsAndHistory({ asset }: { asset: Asset }) {
  const versions = useQuery({
    queryKey: ["versions", asset.id],
    queryFn: () => call(api.GET("/api/v1/content/{asset_id}/versions", { params: { path: { asset_id: asset.id } } })),
  });
  const history = useQuery({
    queryKey: ["history", asset.id],
    queryFn: () => call(api.GET("/api/v1/approvals/{asset_id}/history", { params: { path: { asset_id: asset.id } } })),
  });
  const many = (versions.data?.items.length ?? 0) > 1;
  const diff = useQuery({
    queryKey: ["diff", asset.id, asset.current_version_id],
    queryFn: () => call(api.GET("/api/v1/content/{asset_id}/diff", { params: { path: { asset_id: asset.id } } })),
    enabled: many,
  });
  return (
    <section aria-labelledby="hist-h" className="card">
      <h2 id="hist-h">Versions and decisions</h2>
      {versions.error ? <ErrorPanel error={versions.error} onRetry={() => void versions.refetch()} /> : null}
      <p className="small muted">{versions.data ? `${versions.data.items.length} version(s). Versions are never overwritten.` : null}</p>
      {many && diff.data ? (
        <details>
          <summary>
            What changed from version {diff.data.from_version_no} to {diff.data.to_version_no}
          </summary>
          <pre className="diff">{diff.data.unified || "No text changes."}</pre>
        </details>
      ) : null}
      {history.isLoading ? <Loading /> : null}
      {history.data && history.data.items.length ? (
        <ol className="timeline">
          {history.data.items.map((h) => (
            <li key={h.id}>
              <strong>{humanize(h.action)}</strong> by {h.actor_type === "HUMAN" ? "a person" : humanize(h.actor_type).toLowerCase()} ·{" "}
              {ASSET_STATUS[h.from_status].label} → {ASSET_STATUS[h.to_status].label} · <span className="small muted">{formatDateTime(h.created_at)}</span>
              {h.comment ? <div className="small">“{h.comment}”</div> : null}
            </li>
          ))}
        </ol>
      ) : history.data ? (
        <p className="muted">No decisions yet.</p>
      ) : null}
    </section>
  );
}

// ------------------------------------------------------------------ lineage + repurposing
function Lineage({ asset }: { asset: Asset }) {
  const catalog = usePlatformCatalog();
  const names = useCampaignNames();
  const derivatives = useQuery({
    queryKey: ["derivatives", asset.id],
    queryFn: () => call(api.GET("/api/v1/content/{asset_id}/derivatives", { params: { path: { asset_id: asset.id } } })),
    refetchInterval: (query) =>
      query.state.data?.items.some((d) => ["DRAFT", "GENERATED", "QA_PASSED"].includes(d.status)) ? 3000 : false,
  });
  const writable = (catalog.data?.items ?? []).filter((p) => p.has_writer && p.formats.length);
  const [platform, setPlatform] = useState<Schemas["Platform"] | "">("");
  const [format, setFormat] = useState<Schemas["ContentFormat"] | "">("");
  const [instructions, setInstructions] = useState("");
  const [done, setDone] = useState(false);
  const formats = writable.find((p) => p.platform === platform)?.formats ?? [];
  const repurpose = useAction(
    (body: Schemas["RepurposeRequest"], key: string) =>
      call(api.POST("/api/v1/content/{asset_id}/repurpose", { params: { path: { asset_id: asset.id } }, body, ...idem(key) })),
    { invalidate: [["derivatives", asset.id], ["content"]], onSuccess: () => setDone(true) },
  );
  const eligible = ["QA_PASSED", "AWAITING_APPROVAL", "APPROVED", "SCHEDULED", "PUBLISHED"].includes(asset.status);

  return (
    <section aria-labelledby="lin-h" className="card">
      <h2 id="lin-h">Repurposing</h2>
      <KeyValue
        items={[
          ["Made from", asset.parent_asset_id ? <Link to={`/content/${asset.parent_asset_id}`}>the source asset</Link> : "Original (from the campaign plan)"],
          ["Depth in its family", `${asset.lineage_depth} of 5`],
          ...(asset.repurpose_instructions ? ([["Instructions", asset.repurpose_instructions]] as [string, string][]) : []),
        ]}
      />
      {derivatives.data?.items.length ? (
        <>
          <h3>Made from this</h3>
          <ul className="card-grid">
            {derivatives.data.items.map((d) => (
              <AssetCard key={d.id} asset={d} campaignName={names.get(d.campaign_id)} to={`/content/${d.id}`} />
            ))}
          </ul>
        </>
      ) : derivatives.isLoading ? (
        <Loading />
      ) : (
        <p className="muted">Nothing has been repurposed from this yet.</p>
      )}
      <h3>Repurpose for another platform</h3>
      {!eligible ? (
        <p className="small muted">Only content that passed the quality checks can be repurposed.</p>
      ) : (
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (platform && format) repurpose.submit({ target_platform: platform, target_format: format, instructions: instructions.trim() || null });
          }}
        >
          <Field label="Platform" htmlFor="rp-platform">
            <select id="rp-platform" value={platform} onChange={(e) => { setPlatform(e.target.value as Schemas["Platform"]); setFormat(""); setDone(false); }}>
              <option value="">Choose…</option>
              {writable.map((p) => (
                <option key={p.platform} value={p.platform}>
                  {PLATFORM_LABEL[p.platform]}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Format" htmlFor="rp-format" hint={platform ? "Only formats this product can write for the platform are listed." : undefined}>
            <select id="rp-format" value={format} onChange={(e) => setFormat(e.target.value as Schemas["ContentFormat"])} disabled={!platform}>
              <option value="">Choose…</option>
              {formats.map((f) => (
                <option key={f} value={f}>
                  {FORMAT_LABEL[f]}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Instructions (optional)" htmlFor="rp-notes">
            <input id="rp-notes" value={instructions} onChange={(e) => setInstructions(e.target.value)} maxLength={4000} />
          </Field>
          <RoleButton role="EDITOR" type="submit" className="btn btn-primary" disabled={!platform || !format || repurpose.isPending}>
            Repurpose
          </RoleButton>
          <p className="small muted">
            Refused when the family already has this platform and format, or when the chain would go deeper than 5. The new content is
            written from this exact version, then checked and approved like any other.
          </p>
          {repurpose.error ? <ErrorPanel title="Not repurposed" error={repurpose.error} onRetry={repurpose.retry} /> : null}
          {done && !repurpose.error ? (
            <div className="notice notice-success" role="status">
              Repurposing started. The new item appears above while it is being written.
            </div>
          ) : null}
        </form>
      )}
    </section>
  );
}
