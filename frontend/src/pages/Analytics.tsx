import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { ApiError, api, call, idem, type Schemas } from "../api/client";
import { JobWatch } from "../components/JobWatch";
import { Badge, EmptyState, ErrorPanel, Loading, PageHeader, RoleButton } from "../components/ui";
import { explainError, formatNumber, formatRate } from "../lib/format";
import { useAction } from "../lib/hooks";
import { useCampaigns } from "../lib/queries";
import { FORMAT_LABEL, PLATFORM_LABEL, humanize } from "../lib/status";

type Derived = { value: number | null; null_reason?: string | null; missing_inputs?: string[] };

const METRIC_LABEL: Record<string, string> = {
  engagement_rate: "Engagement rate",
  click_rate: "Click rate",
  comment_rate: "Comment rate",
  share_rate: "Share rate",
  save_rate: "Save rate",
  follow_conversion: "Profile visit → follow",
  content_efficiency: "Content efficiency",
};

const REAL_SOURCES = new Set(["API", "MANUAL"]);

/**
 * A number is real evidence only when it describes a post that was NOT mock-published and
 * came from a platform API or manual entry (the rule experiments use, A14). Manual numbers
 * typed in for a mock-published post are still simulated.
 */
export function isRealRow(row: Record<string, unknown>, publishMode: Map<string, Schemas["PublishMode"]>): boolean {
  const mode = publishMode.get(String(row["publication_id"]));
  return Boolean(row["snapshot_id"]) && REAL_SOURCES.has(String(row["source"])) && mode !== undefined && mode !== "MOCK";
}

function ProvenanceBanner({ rows, publishMode }: { rows: Record<string, unknown>[]; publishMode: Map<string, Schemas["PublishMode"]> }) {
  const measured = rows.filter((r) => r["snapshot_id"]);
  const real = measured.filter((r) => isRealRow(r, publishMode)).length;
  const simulated = measured.length - real;
  if (!measured.length)
    return (
      <div className="notice notice-neutral" role="note">
        <strong>No metrics yet.</strong> Nothing has been measured for this campaign, so there is nothing to analyse.
      </div>
    );
  if (!real)
    return (
      <div className="notice notice-simulated" role="note">
        <strong>Simulated data.</strong> Every number below describes mock-published posts ({simulated} post{simulated === 1 ? "" : "s"}), so no real
        audience is behind it — even numbers entered by hand. It shows how the analysis works; do not base decisions on it.
      </div>
    );
  if (simulated)
    return (
      <div className="notice notice-warning" role="note">
        <strong>Mixed data.</strong> {real} post(s) have real measurements and {simulated} are simulated. Real and simulated evidence are
        kept apart in experiments; treat totals here with care.
      </div>
    );
  return (
    <div className="notice notice-neutral" role="note">
      <strong>Real data</strong> from {real} post(s) published outside the mock publisher.
    </div>
  );
}

export function AnalyticsPage() {
  const campaigns = useCampaigns();
  const location = useLocation();
  const [campaignId, setCampaignId] = useState<string>((location.state as { campaignId?: string } | null)?.campaignId ?? "");
  const [jobs, setJobs] = useState<{ id: string; label: string; campaignId: string }[]>([]);
  useEffect(() => {
    if (!campaignId && campaigns.data?.items[0]) setCampaignId(campaigns.data.items[0].id);
  }, [campaigns.data, campaignId]);

  const report = useQuery({
    queryKey: ["report", campaignId],
    queryFn: () => call(api.GET("/api/v1/analytics/campaigns/{campaign_id}/report", { params: { path: { campaign_id: campaignId } } })),
    enabled: Boolean(campaignId),
  });
  const recs = useQuery({
    queryKey: ["recommendations", campaignId],
    queryFn: () => call(api.GET("/api/v1/recommendations", { params: { query: { campaign_id: campaignId } } })),
    enabled: Boolean(campaignId),
  });
  // The campaign is the action's input, so a retry reuses the key only for the same campaign.
  const pull = useAction((id: string, key: string) => call(api.POST("/api/v1/analytics/pull", { body: { campaign_id: id }, ...idem(key) })), {
    onSuccess: (job, id) => setJobs((j) => [{ id: job.id, label: "Collecting metrics", campaignId: id }, ...j]),
  });
  const suggest = useAction(
    (id: string, key: string) => call(api.POST("/api/v1/recommendations/generate", { body: { campaign_id: id }, ...idem(key) })),
    { onSuccess: (job, id) => setJobs((j) => [{ id: job.id, label: "Writing recommendations", campaignId: id }, ...j]) },
  );
  const publications = useQuery({
    queryKey: ["publications", campaignId],
    queryFn: () => call(api.GET("/api/v1/publications", { params: { query: { campaign_id: campaignId } } })),
    enabled: Boolean(campaignId),
  });
  const publishMode = new Map((publications.data?.items ?? []).map((p) => [p.id, p.publish_mode]));

  const r = report.data;
  const derived = (r?.derived ?? {}) as Record<string, Derived>;
  const pubs = (r?.publications ?? []) as Record<string, unknown>[];
  const sufficiency = (r?.sufficiency ?? {}) as Record<string, unknown>;

  return (
    <>
      <PageHeader title="Analytics" description="How published content performed, where each number came from, and what the assistant suggests." />
      <div className="filters">
        <label>
          Campaign{" "}
          <select value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
            {(campaigns.data?.items ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <RoleButton role="EDITOR" disabled={!campaignId || pull.isPending} onClick={() => pull.submit(campaignId)}>
          Collect metrics now
        </RoleButton>
        <RoleButton role="EDITOR" disabled={!campaignId || suggest.isPending} onClick={() => suggest.submit(campaignId)}>
          Suggest improvements
        </RoleButton>
      </div>
      {pull.error || suggest.error ? <ErrorPanel title="Not started" error={pull.error ?? suggest.error} /> : null}
      {jobs.map((j) => (
        <JobWatch key={j.id} jobId={j.id} label={j.label} refresh={[["report", j.campaignId], ["recommendations", j.campaignId]]} />
      ))}
      {!campaigns.isLoading && !campaigns.data?.items.length ? <EmptyState title="No campaigns yet" /> : null}
      {report.isLoading ? <Loading label="Loading the report…" /> : null}
      {report.error ? <ErrorPanel error={report.error} onRetry={() => void report.refetch()} /> : null}
      {r ? (
        <>
          <ProvenanceBanner rows={pubs} publishMode={publishMode} />
          {r.coverage["published"] === 0 ? (
            <EmptyState title="Nothing published in this campaign yet">Metrics appear after content is published (simulated) and metrics are collected.</EmptyState>
          ) : (
            <>
              <p className="small muted">
                {r.coverage["with_snapshot"]} of {r.coverage["published"]} published posts have metrics.{" "}
                {sufficiency["satisfied"]
                  ? `Enough posts (at least ${String(sufficiency["min_sample"])}) to show the best and weakest.`
                  : `Fewer than ${String(sufficiency["min_sample"])} posts have metrics: the best/weakest ranking is withheld because it would not mean much.`}
              </p>
              <section aria-labelledby="rates-h">
                <h2 id="rates-h">Rates across the campaign</h2>
                <div className="tiles">
                  {Object.entries(derived).map(([key, d]) => (
                    <div key={key} className="card tile static">
                      <span className="tile-value">{d.value === null ? "—" : formatRate(d.value)}</span>
                      <span className="tile-label">{METRIC_LABEL[key] ?? humanize(key)}</span>
                      {d.value === null ? (
                        <span className="small muted">Not calculated: {d.null_reason ?? "inputs missing"}</span>
                      ) : d.missing_inputs?.length ? (
                        <span className="small muted">Without {d.missing_inputs.join(", ")} (not captured)</span>
                      ) : null}
                    </div>
                  ))}
                </div>
              </section>
              <section aria-labelledby="tot-h">
                <h2 id="tot-h">Totals</h2>
                <p className="small muted">“Not captured” means the source does not report it; it is never counted as zero.</p>
                <dl className="kv kv-inline">
                  {Object.entries(r.totals).map(([k, v]) => (
                    <div key={k}>
                      <dt>{humanize(k)}</dt>
                      <dd>{formatNumber(v)}</dd>
                    </div>
                  ))}
                </dl>
              </section>
              <section aria-labelledby="posts-h">
                <h2 id="posts-h">Per post</h2>
                <table className="table">
                  <caption className="sr-only">Metrics per published post</caption>
                  <thead>
                    <tr>
                      <th scope="col">Post</th>
                      <th scope="col">Platform</th>
                      <th scope="col">Data</th>
                      <th scope="col">Impressions</th>
                      <th scope="col">Engagement</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pubs.map((p) => {
                      const d = (p["derived"] ?? {}) as Record<string, Derived>;
                      const m = (p["metrics"] ?? {}) as Record<string, number | null>;
                      return (
                        <tr key={String(p["publication_id"])}>
                          <td>
                            <Link to={`/content/${String(p["content_asset_id"])}`}>{String(p["hook"] ?? "Open")}</Link>
                          </td>
                          <td>
                            {PLATFORM_LABEL[p["platform"] as Schemas["Platform"]]} · {FORMAT_LABEL[p["format"] as Schemas["ContentFormat"]]}
                          </td>
                          <td>
                            {!p["snapshot_id"] ? (
                              "No metrics yet"
                            ) : isRealRow(p, publishMode) ? (
                              <Badge tone="neutral">Real ({humanize(String(p["source"]))})</Badge>
                            ) : (
                              <Badge tone="simulated">Simulated</Badge>
                            )}
                          </td>
                          <td>{formatNumber(m["impressions"])}</td>
                          <td>{formatRate(d["engagement_rate"]?.value)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </section>
            </>
          )}
        </>
      ) : null}
      <Recommendations query={recs} campaignId={campaignId} anyReal={pubs.some((p) => isRealRow(p, publishMode))} />
    </>
  );
}

function Recommendations({
  query,
  campaignId,
  anyReal,
}: {
  query: ReturnType<typeof useQuery<Schemas["Page_RecommendationRead_"]>>;
  campaignId: string;
  anyReal: boolean;
}) {
  const decide = useAction(
    ({ id, verdict }: { id: string; verdict: "accept" | "dismiss" }, key: string) =>
      verdict === "accept"
        ? call(api.POST("/api/v1/recommendations/{recommendation_id}/accept", { params: { path: { recommendation_id: id } }, ...idem(key) }))
        : call(api.POST("/api/v1/recommendations/{recommendation_id}/dismiss", { params: { path: { recommendation_id: id } }, ...idem(key) })),
    { invalidate: [["recommendations", campaignId]] },
  );
  const items = query.data?.items ?? [];
  return (
    <section aria-labelledby="rec-h">
      <h2 id="rec-h">Suggestions</h2>
      <p className="small muted">
        Suggestions are written by the assistant from the report rows it cites. Accepting one records your decision only; nothing changes in
        your campaigns automatically.
      </p>
      {query.error ? <ErrorPanel error={query.error} onRetry={() => void query.refetch()} /> : null}
      {decide.error ? (
        <div className="notice notice-danger" role="alert">
          {decide.error instanceof ApiError ? explainError(decide.error) : "The decision was not saved."}
        </div>
      ) : null}
      {!items.length && !query.isLoading ? <p className="muted">No suggestions yet.</p> : null}
      <ul className="card-grid">
        {items.map((rec) => {
          const flag = (rec.evidence_query as Record<string, unknown>)["simulated_evidence"];
          // Real only if the API says so AND the campaign has real (non-mock) measurements.
          const provenance = flag === true || !anyReal ? "simulated" : flag === false ? "real" : "unknown";
          const labels = ((rec.evidence_query as Record<string, unknown>)["labels"] ?? []) as string[];
          return (
            <li key={rec.id} className="card">
              <p>
                <strong>{rec.statement}</strong>
              </p>
              {rec.rationale ? <p className="small">{rec.rationale}</p> : null}
              <p className="small muted">
                Evidence: {rec.evidence_snapshot_ids.length} metric snapshot(s){labels.length ? ` (${labels.join(", ")})` : ""}.
              </p>
              <div className="cal-badges">
                {provenance === "simulated" ? (
                  <Badge tone="simulated">Based on simulated data</Badge>
                ) : provenance === "real" ? (
                  <Badge tone="neutral">Based on real data</Badge>
                ) : (
                  <Badge tone="warning">Evidence source unknown</Badge>
                )}
                <Badge tone={rec.status === "ACCEPTED" ? "success" : rec.status === "DISMISSED" ? "neutral" : "warning"}>{humanize(rec.status)}</Badge>
              </div>
              {rec.status === "PROPOSED" ? (
                <div className="action-row">
                  <RoleButton role="EDITOR" disabled={decide.isPending} onClick={() => decide.submit({ id: rec.id, verdict: "accept" })}>
                    Accept
                  </RoleButton>
                  <RoleButton role="EDITOR" disabled={decide.isPending} onClick={() => decide.submit({ id: rec.id, verdict: "dismiss" })}>
                    Dismiss
                  </RoleButton>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
