import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, call } from "../api/client";
import { useAuth } from "../auth/auth";
import { EmptyState, ErrorPanel, Loading, PageHeader, SimulatedNotice } from "../components/ui";
import { useBrand } from "../lib/brand";
import { useCampaigns } from "../lib/queries";

function Tile({ label, value, to, hint }: { label: string; value: number | string; to: string; hint: string }) {
  return (
    <Link to={to} className="card tile">
      <span className="tile-value">{value}</span>
      <span className="tile-label">{label}</span>
      <span className="small muted">{hint}</span>
    </Link>
  );
}

export function DashboardPage() {
  const { me } = useAuth();
  const { brand, brandId, brands, isLoading, error, refetch } = useBrand();
  const campaigns = useCampaigns();
  const content = useQuery({
    queryKey: ["content", brandId, null, null],
    queryFn: () => call(api.GET("/api/v1/content", { params: { query: { brand_id: brandId ?? undefined } } })),
    enabled: Boolean(brandId),
  });

  if (isLoading) return <Loading />;
  if (error) return <ErrorPanel error={error} onRetry={refetch} />;
  if (!brands.length)
    return (
      <>
        <PageHeader title={`Welcome${me ? `, ${me.email}` : ""}`} />
        <EmptyState title="No brands yet">
          An owner creates brands through the API today (<code>POST /api/v1/brands</code>); creating them from this screen is not available yet.
        </EmptyState>
      </>
    );

  const items = content.data?.items ?? [];
  // While loading (or after an error) show "…", never a misleading 0.
  const count = (...s: string[]) => (content.data ? items.filter((a) => s.includes(a.status)).length : "…");
  const truncated = items.length >= 200;
  return (
    <>
      <PageHeader title={brand?.display_name ?? "Overview"} description="What needs you now, and where everything stands." />
      <SimulatedNotice />
      {content.error ? <ErrorPanel error={content.error} onRetry={() => void content.refetch()} /> : null}
      <div className="tiles">
        <Tile label="Need your approval" value={count("AWAITING_APPROVAL")} to="/approvals" hint="Reviewed by a person before anything is scheduled." />
        <Tile label="Need attention" value={count("QA_FAILED", "REVISION_REQUESTED")} to="/content" hint="Failed checks or revision requested (see “Needs attention” under Content)." />
        <Tile label="Being written or checked" value={count("DRAFT", "GENERATED", "QA_PASSED")} to="/content" hint="Writers and automatic checks at work." />
        <Tile label="Scheduled" value={count("SCHEDULED")} to="/calendar" hint="Waiting for their time slot." />
        <Tile label="Published (simulated)" value={count("PUBLISHED")} to="/publications" hint="Mock publisher only — no real audience." />
        <Tile label="Campaigns" value={campaigns.data?.items.length ?? "…"} to="/campaigns" hint="Plans, research and generated content." />
      </div>
      {truncated ? <p className="small muted">Counts cover the first 200 content items only.</p> : null}
      <section className="card">
        <h2>Start something</h2>
        <ul className="link-list">
          <li>
            <Link to="/campaigns/new">Plan a new campaign from a source text</Link>
          </li>
          <li>
            <Link to="/approvals">Review the approval queue</Link>
          </li>
          <li>
            <Link to="/analytics">See how published content performed (simulated data)</Link>
          </li>
        </ul>
      </section>
    </>
  );
}
