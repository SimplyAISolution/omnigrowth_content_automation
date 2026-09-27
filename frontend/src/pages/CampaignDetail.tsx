import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { ApiError, api, call, idem, type Schemas } from "../api/client";
import { JobWatch } from "../components/JobWatch";
import { Badge, ErrorPanel, KeyValue, Loading, PageHeader, RoleButton, StatusBadge, Unavailable } from "../components/ui";
import { formatDate, formatDateTime } from "../lib/format";
import { useAction } from "../lib/hooks";
import { CAMPAIGN_STATUS, FORMAT_LABEL, PLATFORM_LABEL, humanize } from "../lib/status";

const TABS = ["Plan", "Research", "Strategy", "Ideas", "Source"] as const;
type Tab = (typeof TABS)[number];

const CLAIM_LABEL: Record<Schemas["ClaimLabel"], { label: string; tone: "success" | "warning" | "neutral" }> = {
  SOURCE_STATED: { label: "Stated in the source", tone: "success" },
  NEEDS_VERIFICATION: { label: "Needs verification", tone: "warning" },
  OPINION: { label: "Opinion", tone: "neutral" },
};

export function CampaignDetailPage() {
  const { campaignId = "" } = useParams();
  const location = useLocation();
  const [jobs, setJobs] = useState<{ id: string; label: string }[]>(() => {
    const planning = (location.state as { planningJob?: string } | null)?.planningJob;
    return planning ? [{ id: planning, label: "Planning" }] : [];
  });
  const [tab, setTab] = useState<Tab>("Plan");
  const q = useQuery({
    queryKey: ["campaign", campaignId],
    queryFn: () => call(api.GET("/api/v1/campaigns/{campaign_id}", { params: { path: { campaign_id: campaignId } } })),
    refetchInterval: (query) => (query.state.data?.status === "PLANNING" ? 4000 : false),
  });
  const path = { params: { path: { campaign_id: campaignId } } };
  const activate = useAction((_: null, key: string) => call(api.POST("/api/v1/campaigns/{campaign_id}/activate", { ...path, ...idem(key) })), {
    invalidate: [["campaign", campaignId], ["campaigns"]],
  });
  const generate = useAction((_: null, key: string) => call(api.POST("/api/v1/campaigns/{campaign_id}/generate-assets", { ...path, body: null, ...idem(key) })), {
    invalidate: [["content"]],
    onSuccess: (job) => setJobs((j) => [{ id: job.id, label: "Writing content" }, ...j]),
  });
  const replan = useAction((_: null, key: string) => call(api.POST("/api/v1/campaigns/{campaign_id}/generate", { ...path, ...idem(key) })), {
    invalidate: [["campaign", campaignId]],
    onSuccess: (job) => setJobs((j) => [{ id: job.id, label: "Planning" }, ...j]),
  });

  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />;
  const c = q.data;
  const actionError = activate.error ?? generate.error ?? replan.error;

  return (
    <>
      <p className="breadcrumb">
        <Link to="/campaigns">← Campaigns</Link>
      </p>
      <PageHeader title={c.name} description={c.topic} actions={<StatusBadge info={CAMPAIGN_STATUS[c.status]} />} />
      <div className="notice notice-neutral" role="status">
        <strong>{CAMPAIGN_STATUS[c.status].label}.</strong> {CAMPAIGN_STATUS[c.status].help}
      </div>
      {jobs.map((j) => (
        <JobWatch key={j.id} jobId={j.id} label={j.label} refresh={[["campaign", campaignId], ["plan", campaignId], ["content"], ["approvals"]]} />
      ))}
      <section className="card" aria-labelledby="cact-h">
        <h2 id="cact-h">Next step</h2>
        <div className="action-row">
          {c.status === "READY_FOR_REVIEW" ? (
            <RoleButton role="EDITOR" className="btn btn-primary" disabled={activate.isPending} onClick={() => activate.submit(null)}>
              Accept the plan and activate
            </RoleButton>
          ) : null}
          {c.status === "ACTIVE" || c.status === "PAUSED" ? (
            <RoleButton role="EDITOR" className="btn btn-primary" disabled={generate.isPending} onClick={() => generate.submit(null)}>
              Write the planned content
            </RoleButton>
          ) : null}
          {c.status === "DRAFT" || c.status === "FAILED" ? (
            <RoleButton role="EDITOR" className="btn btn-primary" disabled={replan.isPending} onClick={() => replan.submit(null)}>
              {c.status === "FAILED" ? "Retry planning" : "Start planning"}
            </RoleButton>
          ) : null}
          <Link className="btn" to={`/content?campaign=${c.id}`}>
            See its content
          </Link>
          <Link className="btn" to="/analytics" state={{ campaignId: c.id }}>
            Analytics
          </Link>
        </div>
        {c.status === "ACTIVE" ? (
          <p className="small muted">Writing again only fills plan items that have no content yet or whose last attempt failed.</p>
        ) : null}
        {actionError ? <ErrorPanel title="That did not work" error={actionError} /> : null}
        <Unavailable>Pausing, duplicating and archiving a campaign have no API yet.</Unavailable>
      </section>
      <KeyValue
        items={[
          ["Goal", humanize(c.objective)],
          ["Platforms", [c.primary_platform, ...c.secondary_platforms].map((p) => PLATFORM_LABEL[p]).join(", ")],
          ["Starts", formatDate(c.start_date)],
          ["Time zone", c.timezone ?? "Brand default"],
          ["Model routing", c.routing_profile === "LOCAL_ONLY" ? "Local model only" : humanize(c.routing_profile)],
        ]}
      />
      <div className="tabs" role="tablist" aria-label="Campaign details">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            id={`tab-${t}`}
            aria-selected={tab === t}
            aria-controls={`panel-${t}`}
            tabIndex={tab === t ? 0 : -1}
            className={`tab${tab === t ? " active" : ""}`}
            onClick={() => setTab(t)}
            onKeyDown={(e) => {
              // ARIA tabs: arrow keys move between tabs, Home/End jump to the ends.
              const i = TABS.indexOf(t);
              const next =
                e.key === "ArrowRight" ? TABS[(i + 1) % TABS.length] : e.key === "ArrowLeft" ? TABS[(i + TABS.length - 1) % TABS.length] : e.key === "Home" ? TABS[0] : e.key === "End" ? TABS[TABS.length - 1] : undefined;
              if (next) {
                e.preventDefault();
                setTab(next);
                document.getElementById(`tab-${next}`)?.focus();
              }
            }}
          >
            {t}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="card">
        {tab === "Plan" ? <PlanTab campaign={c} /> : null}
        {tab === "Research" ? <ResearchTab campaignId={c.id} /> : null}
        {tab === "Strategy" ? <StrategyTab campaignId={c.id} /> : null}
        {tab === "Ideas" ? <IdeasTab campaignId={c.id} /> : null}
        {tab === "Source" ? <SourceTab campaignId={c.id} /> : null}
      </div>
    </>
  );
}

function notReady(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

function PlanTab({ campaign }: { campaign: Schemas["CampaignRead"] }) {
  const plan = useQuery({
    queryKey: ["plan", campaign.id],
    queryFn: () => call(api.GET("/api/v1/campaigns/{campaign_id}/plan", { params: { path: { campaign_id: campaign.id } } })),
  });
  if (plan.isLoading) return <Loading />;
  if (notReady(plan.error) || (plan.data && !plan.data.items.length)) return <p className="muted">No plan yet. It appears when planning finishes.</p>;
  if (plan.error) return <ErrorPanel error={plan.error} onRetry={() => void plan.refetch()} />;
  return (
    <table className="table">
      <caption>7-day plan ({plan.data?.items.length} items)</caption>
      <thead>
        <tr>
          <th scope="col">#</th>
          <th scope="col">Window</th>
          <th scope="col">Platform</th>
          <th scope="col">Format</th>
          <th scope="col">Angle</th>
          <th scope="col">Call to action</th>
        </tr>
      </thead>
      <tbody>
        {plan.data?.items.map((p) => (
          <tr key={p.id}>
            <td>{p.ordinal}</td>
            <td>{formatDateTime(p.window_start, campaign.timezone)}</td>
            <td>{PLATFORM_LABEL[p.platform]}</td>
            <td>{FORMAT_LABEL[p.format]}</td>
            <td>{humanize(p.angle)}</td>
            <td>{p.cta ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ResearchTab({ campaignId }: { campaignId: string }) {
  const q = useQuery({
    queryKey: ["research", campaignId],
    queryFn: () => call(api.GET("/api/v1/campaigns/{campaign_id}/research-packet", { params: { path: { campaign_id: campaignId } } })),
  });
  if (q.isLoading) return <Loading />;
  if (notReady(q.error)) return <p className="muted">No research yet.</p>;
  if (q.error || !q.data) return <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />;
  return (
    <>
      <p>{q.data.summary}</p>
      <p className="small muted">
        Research is drawn from the source material only (provider: {q.data.provider}); no web search is used. Writers may only state facts
        from these claims or the source, and content is checked against them.
      </p>
      <ol className="claims">
        {q.data.claims.map((cl) => (
          <li key={cl.id}>
            <Badge tone={CLAIM_LABEL[cl.label].tone}>{CLAIM_LABEL[cl.label].label}</Badge> {cl.claim_text}
            {cl.source_excerpt ? <div className="small muted">From the source: “{cl.source_excerpt}”</div> : null}
            {cl.citation_url ? <div className="small">Citation: {cl.citation_url}</div> : null}
          </li>
        ))}
      </ol>
    </>
  );
}

function StrategyTab({ campaignId }: { campaignId: string }) {
  const q = useQuery({
    queryKey: ["strategy", campaignId],
    queryFn: () => call(api.GET("/api/v1/campaigns/{campaign_id}/strategy", { params: { path: { campaign_id: campaignId } } })),
  });
  if (q.isLoading) return <Loading />;
  if (notReady(q.error)) return <p className="muted">No strategy yet.</p>;
  if (q.error || !q.data) return <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />;
  const s = q.data;
  return (
    <>
      <KeyValue
        items={[
          ["Positioning", s.positioning],
          ["Funnel stage", humanize(s.funnel_stage)],
          ["Main call to action", s.primary_cta ?? "—"],
          ["Version", String(s.version_no)],
        ]}
      />
      <details>
        <summary>Full strategy</summary>
        <pre className="json">{JSON.stringify(s.strategy, null, 2)}</pre>
      </details>
    </>
  );
}

function IdeasTab({ campaignId }: { campaignId: string }) {
  const q = useQuery({
    queryKey: ["ideas", campaignId],
    queryFn: () => call(api.GET("/api/v1/campaigns/{campaign_id}/ideas", { params: { path: { campaign_id: campaignId } } })),
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />;
  if (!q.data?.items.length) return <p className="muted">No ideas yet.</p>;
  return (
    <ul className="ideas">
      {q.data.items.map((i) => (
        <li key={i.id}>
          <Badge tone={i.status === "SELECTED" ? "success" : "neutral"}>{i.status === "SELECTED" ? "In the plan" : humanize(i.status)}</Badge> <strong>{humanize(i.angle)}:</strong> {i.hook}
        </li>
      ))}
    </ul>
  );
}

function SourceTab({ campaignId }: { campaignId: string }) {
  const plan = useQuery({
    queryKey: ["plan", campaignId],
    queryFn: () => call(api.GET("/api/v1/campaigns/{campaign_id}/plan", { params: { path: { campaign_id: campaignId } } })),
  });
  const sourceId = plan.data?.items.find((p) => p.source_asset_id)?.source_asset_id ?? null;
  const content = useQuery({
    queryKey: ["source-content", sourceId],
    queryFn: () => call(api.GET("/api/v1/sources/{source_id}/content", { params: { path: { source_id: sourceId ?? "" } } })),
    enabled: Boolean(sourceId),
  });
  if (plan.isLoading || content.isLoading) return <Loading />;
  if (!sourceId) return <p className="muted">The source is shown once the plan exists.</p>;
  if (content.error || !content.data) return <ErrorPanel error={content.error} onRetry={() => void content.refetch()} />;
  const text = content.data.content ?? "(The source has no text content.)";
  return <pre className="source-text">{text}</pre>;
}
