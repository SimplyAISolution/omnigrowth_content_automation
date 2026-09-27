import { useQuery } from "@tanstack/react-query";
import { api, call, type Schemas } from "../api/client";
import { useBrand } from "./brand";

export function useCampaigns() {
  const { brandId } = useBrand();
  return useQuery({
    queryKey: ["campaigns", brandId],
    queryFn: () => call(api.GET("/api/v1/campaigns", { params: { query: { brand_id: brandId ?? undefined } } })),
    enabled: Boolean(brandId),
  });
}

export function useCampaignNames(): Map<string, string> {
  const q = useCampaigns();
  return new Map((q.data?.items ?? []).map((c) => [c.id, c.name]));
}

export function usePlatformCatalog() {
  return useQuery({
    queryKey: ["platforms"],
    queryFn: () => call(api.GET("/api/v1/platforms")),
    staleTime: 5 * 60_000,
  });
}

/** Accounts of a brand (default: the brand picked in the header), optionally one platform. */
export function useAccounts(forBrand?: string | null, platform?: Schemas["Platform"]) {
  const { brandId: selected } = useBrand();
  const brandId = forBrand ?? selected;
  return useQuery({
    queryKey: ["accounts", brandId, platform ?? null],
    queryFn: () =>
      call(api.GET("/api/v1/platform-accounts", { params: { query: { brand_id: brandId ?? undefined, platform: platform ?? undefined } } })),
    enabled: Boolean(brandId),
  });
}

/** How publications are made right now, from the server ("MOCK" = simulated; null = off). */
export function usePublishMode(): Schemas["PublishMode"] | null | undefined {
  const q = usePlatformCatalog();
  return q.data ? (q.data.items[0]?.effective_publisher ?? null) : undefined;
}
