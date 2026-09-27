import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { api, call, type Schemas } from "../api/client";
import { ErrorPanel, Loading } from "../components/ui";

interface BrandState {
  brands: Schemas["BrandRead"][];
  brand: Schemas["BrandRead"] | null;
  brandId: string | null;
  setBrandId: (id: string) => void;
  isLoading: boolean;
  error: unknown;
  refetch: () => void;
}

const BrandContext = createContext<BrandState | null>(null);
const KEY = "og.brand";

function remembered(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function BrandProvider({ children }: { children: ReactNode }) {
  const query = useQuery({
    queryKey: ["brands"],
    queryFn: () => call(api.GET("/api/v1/brands")),
  });
  const [chosen, setBrandIdState] = useState<string | null>(remembered);
  const brands = useMemo(() => query.data?.items ?? [], [query.data]);
  // The remembered brand if it still exists, else the first one (no empty intermediate render).
  const brandId = brands.some((b) => b.id === chosen) ? chosen : (brands[0]?.id ?? null);

  const value = useMemo<BrandState>(
    () => ({
      brands,
      brand: brands.find((b) => b.id === brandId) ?? null,
      brandId,
      setBrandId: (id: string) => {
        setBrandIdState(id);
        try {
          window.localStorage.setItem(KEY, id);
        } catch {
          // per-viewer convenience only
        }
      },
      isLoading: query.isLoading,
      error: query.error,
      refetch: () => void query.refetch(),
    }),
    [brands, brandId, query],
  );
  // Every screen is brand-scoped: wait for the brand list instead of flashing "no brand".
  if (query.isLoading) return <Loading label="Loading brands…" />;
  if (query.error) return <ErrorPanel error={query.error} onRetry={() => void query.refetch()} />;
  return <BrandContext.Provider value={value}>{children}</BrandContext.Provider>;
}

export function useBrand(): BrandState {
  const ctx = useContext(BrandContext);
  if (!ctx) throw new Error("useBrand outside BrandProvider");
  return ctx;
}
