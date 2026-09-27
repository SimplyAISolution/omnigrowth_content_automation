import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, call, type Schemas } from "../api/client";
import { AssetCard } from "../components/content";
import { EmptyState, ErrorPanel, Loading, PageHeader } from "../components/ui";
import { useBrand } from "../lib/brand";
import { useCampaignNames, useCampaigns } from "../lib/queries";
import { ASSET_STATUS, PLATFORM_LABEL } from "../lib/status";

const GROUPS: { label: string; statuses: Schemas["ContentAssetStatus"][] }[] = [
  { label: "In progress", statuses: ["DRAFT", "GENERATED", "QA_PASSED"] },
  { label: "Needs attention", statuses: ["QA_FAILED", "REVISION_REQUESTED", "REJECTED"] },
  { label: "Awaiting approval", statuses: ["AWAITING_APPROVAL"] },
  { label: "Approved or scheduled", statuses: ["APPROVED", "SCHEDULED"] },
  { label: "Published (simulated)", statuses: ["PUBLISHED"] },
];

export function ContentPage() {
  const { brandId } = useBrand();
  const names = useCampaignNames();
  const campaigns = useCampaigns();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") as Schemas["ContentAssetStatus"] | null;
  const campaignId = params.get("campaign");
  const platform = params.get("platform") as Schemas["Platform"] | null;

  const q = useQuery({
    queryKey: ["content", brandId, status, campaignId],
    queryFn: () =>
      call(
        api.GET("/api/v1/content", {
          params: { query: { brand_id: brandId ?? undefined, campaign_id: campaignId ?? undefined, status: status ? [status] : undefined } },
        }),
      ),
    enabled: Boolean(brandId),
    refetchInterval: (query) => (query.state.data?.items.some((a) => ["DRAFT", "GENERATED", "QA_PASSED"].includes(a.status)) ? 5000 : false),
  });
  const items = (q.data?.items ?? []).filter((a) => !platform || a.platform === platform);

  function set(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  return (
    <>
      <PageHeader title="Content" description="Everything the writers produced for this brand, in every state. Filter to find what needs attention." />
      <form className="filters" onSubmit={(e) => e.preventDefault()} aria-label="Filter content">
        <label>
          State{" "}
          <select value={status ?? ""} onChange={(e) => set("status", e.target.value)}>
            <option value="">Any</option>
            {Object.entries(ASSET_STATUS).map(([k, v]) => (
              <option key={k} value={k}>
                {v.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Campaign{" "}
          <select value={campaignId ?? ""} onChange={(e) => set("campaign", e.target.value)}>
            <option value="">Any</option>
            {(campaigns.data?.items ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Platform{" "}
          <select value={platform ?? ""} onChange={(e) => set("platform", e.target.value)}>
            <option value="">Any</option>
            {Object.entries(PLATFORM_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
      </form>
      {q.isLoading ? (
        <Loading label="Loading content…" />
      ) : q.error ? (
        <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState title="No content matches">
          {status || campaignId || platform ? "Clear a filter to see more." : "Create a campaign and generate its content to see it here."}
        </EmptyState>
      ) : status ? (
        <ul className="card-grid" aria-label="Content">
          {items.map((a) => (
            <AssetCard key={a.id} asset={a} campaignName={names.get(a.campaign_id)} to={`/content/${a.id}`} />
          ))}
        </ul>
      ) : (
        GROUPS.map((g) => {
          const group = items.filter((a) => g.statuses.includes(a.status));
          if (!group.length) return null;
          return (
            <section key={g.label} aria-labelledby={`g-${g.label}`}>
              <h2 id={`g-${g.label}`}>
                {g.label} <span className="muted small">({group.length})</span>
              </h2>
              <ul className="card-grid">
                {group.map((a) => (
                  <AssetCard key={a.id} asset={a} campaignName={names.get(a.campaign_id)} to={`/content/${a.id}`} />
                ))}
              </ul>
            </section>
          );
        })
      )}
    </>
  );
}
