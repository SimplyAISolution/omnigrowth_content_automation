import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, call, type Schemas } from "../api/client";
import { AssetCard } from "../components/content";
import { EmptyState, ErrorPanel, Loading, PageHeader } from "../components/ui";
import { useBrand } from "../lib/brand";
import { useCampaignNames } from "../lib/queries";
import { PLATFORM_LABEL } from "../lib/status";

export function ApprovalsPage() {
  const { brandId, brand } = useBrand();
  const names = useCampaignNames();
  const [platform, setPlatform] = useState<Schemas["Platform"] | "">("");
  const q = useQuery({
    queryKey: ["approvals", brandId],
    queryFn: () => call(api.GET("/api/v1/approvals", { params: { query: { brand_id: brandId ?? undefined } } })),
    enabled: Boolean(brandId),
    refetchInterval: 15_000,
  });
  const items = (q.data?.items ?? []).filter((a) => !platform || a.platform === platform);
  // Keep a chosen platform selectable even when no item of it is left.
  const platforms = Array.from(new Set([...(q.data?.items ?? []).map((a) => a.platform), ...(platform ? [platform] : [])]));

  return (
    <>
      <PageHeader
        title="Approvals"
        description={
          <>
            Content for {brand?.display_name ?? "this brand"} that passed automatic checks and waits for a person.{" "}
            {brand?.auto_approve_enabled
              ? "This brand has automatic approval turned on: content that passes every check is approved without a person and never appears here."
              : "Nothing is scheduled or published without an approval."}
          </>
        }
      />
      {platforms.length > 1 || platform ? (
        <div className="filters">
          <label>
            Platform{" "}
            <select value={platform} onChange={(e) => setPlatform(e.target.value as Schemas["Platform"] | "")}>
              <option value="">All</option>
              {platforms.map((p) => (
                <option key={p} value={p}>
                  {PLATFORM_LABEL[p]}
                </option>
              ))}
            </select>
          </label>
        </div>
      ) : null}
      {!brandId ? (
        <EmptyState title="No brand selected">Create a brand first, then its content appears here.</EmptyState>
      ) : q.isLoading ? (
        <Loading label="Loading the approval queue…" />
      ) : q.error ? (
        <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />
      ) : items.length === 0 && platform && q.data?.items.length ? (
        <EmptyState title={`Nothing waits for ${PLATFORM_LABEL[platform]}`}>
          {q.data.items.length} item(s) wait on other platforms.{" "}
          <button type="button" className="btn" onClick={() => setPlatform("")}>
            Show all platforms
          </button>
        </EmptyState>
      ) : items.length === 0 ? (
        <EmptyState title="Nothing waits for approval">
          New content appears here after a campaign generates it and it passes the automatic checks. Content that failed the checks is in
          Content, filtered by “Failed quality checks”.
        </EmptyState>
      ) : (
        <>
          <p className="small muted" aria-live="polite">
            {items.length} item{items.length === 1 ? "" : "s"} waiting.
          </p>
          <ul className="card-grid" aria-label="Approval queue">
            {items.map((a) => (
              <AssetCard key={a.id} asset={a} campaignName={names.get(a.campaign_id)} to={`/content/${a.id}?from=approvals`} />
            ))}
          </ul>
        </>
      )}
    </>
  );
}
