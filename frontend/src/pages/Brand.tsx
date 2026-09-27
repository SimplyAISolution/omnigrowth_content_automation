import { useQuery } from "@tanstack/react-query";
import { ApiError, api, call } from "../api/client";
import { EmptyState, ErrorPanel, KeyValue, Loading, PageHeader, Unavailable } from "../components/ui";
import { useBrand } from "../lib/brand";

function list(xs: unknown[] | undefined): string {
  return xs && xs.length ? xs.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(", ") : "—";
}

export function BrandPage() {
  const { brand, brandId } = useBrand();
  const profile = useQuery({
    queryKey: ["profile", brandId],
    queryFn: () => call(api.GET("/api/v1/brands/{brand_id}/profile", { params: { path: { brand_id: brandId ?? "" } } })),
    enabled: Boolean(brandId),
  });
  const audiences = useQuery({
    queryKey: ["audiences", brandId],
    queryFn: () => call(api.GET("/api/v1/brands/{brand_id}/audiences", { params: { path: { brand_id: brandId ?? "" } } })),
    enabled: Boolean(brandId),
  });
  if (!brand) return <EmptyState title="No brand selected" />;
  const p = profile.data;
  return (
    <>
      <PageHeader title="Brand profile" description="What writers are told about the brand: voice, pillars and phrases never to use. Banned phrases are also enforced by the quality checks." />
      <KeyValue
        items={[
          ["Name", brand.display_name],
          ["Time zone", brand.timezone ?? "—"],
          ["Automatic approval", brand.auto_approve_enabled ? "On — content that passes every check is approved without a person" : "Off — a person approves every item"],
        ]}
      />
      {profile.isLoading ? <Loading /> : null}
      {profile.error && !(profile.error instanceof ApiError && profile.error.status === 404) ? <ErrorPanel error={profile.error} onRetry={() => void profile.refetch()} /> : null}
      {p ? (
        <section className="card" aria-labelledby="bp-h">
          <h2 id="bp-h">Profile (version {p.version_no})</h2>
          <KeyValue
            items={[
              ["Niche", p.niche ?? "—"],
              ["Mission", p.mission ?? "—"],
              ["Description", p.company_description ?? "—"],
              ["Content pillars", list(p.content_pillars)],
              ["Differentiators", list(p.differentiators)],
              ["Never say", list(p.banned_phrases)],
              ["Avoid", list(p.avoid_list)],
              ["Vocabulary", list(p.vocabulary)],
            ]}
          />
        </section>
      ) : null}
      <section className="card" aria-labelledby="aud-h">
        <h2 id="aud-h">Audiences</h2>
        {audiences.data?.items.length ? (
          <ul>
            {audiences.data.items.map((a) => (
              <li key={a.id}>
                <strong>{a.name}</strong>
                {a.is_primary ? " (primary)" : ""} — pains: {list(a.pains)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No audiences yet.</p>
        )}
      </section>
      <Unavailable>Editing the profile and audiences from this screen comes later; the API supports it (PUT /brands/&#123;id&#125;/profile).</Unavailable>
    </>
  );
}
