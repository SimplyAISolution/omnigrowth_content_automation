import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api, call, idem, type Schemas } from "../api/client";
import { ErrorPanel, Field, Loading, PageHeader, RoleButton } from "../components/ui";
import { useBrand } from "../lib/brand";
import { useAction } from "../lib/hooks";
import { usePlatformCatalog } from "../lib/queries";
import { PLATFORM_LABEL, humanize } from "../lib/status";

const OBJECTIVES: Schemas["CampaignObjective"][] = ["REACH", "AUTHORITY", "FOLLOWERS", "EMAIL_SUBSCRIBERS", "LEADS", "PRODUCT_AWARENESS", "SALES"];

export function CampaignNewPage() {
  const { brand, brandId } = useBrand();
  const navigate = useNavigate();
  const catalog = usePlatformCatalog();
  const audiences = useQuery({
    queryKey: ["audiences", brandId],
    queryFn: () => call(api.GET("/api/v1/brands/{brand_id}/audiences", { params: { path: { brand_id: brandId ?? "" } } })),
    enabled: Boolean(brandId),
  });
  const sources = useQuery({
    queryKey: ["sources", brandId],
    queryFn: () => call(api.GET("/api/v1/sources", { params: { query: { brand_id: brandId ?? undefined } } })),
    enabled: Boolean(brandId),
  });
  const writable = (catalog.data?.items ?? []).filter((p) => p.has_writer);
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [objective, setObjective] = useState<Schemas["CampaignObjective"]>("AUTHORITY");
  const [primary, setPrimary] = useState<Schemas["Platform"]>("LINKEDIN");
  const [secondary, setSecondary] = useState<Schemas["Platform"][]>([]);
  const [audienceId, setAudienceId] = useState("");
  const [sourceMode, setSourceMode] = useState<"paste" | "existing">("paste");
  const [sourceTitle, setSourceTitle] = useState("");
  const [sourceText, setSourceText] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [startDate, setStartDate] = useState(() => new Date(Date.now() + 86400000).toISOString().slice(0, 10));
  const [errors, setErrors] = useState<Record<string, string>>({});

  const create = useAction(
    (body: Schemas["CampaignWizardRequest"], key: string) => call(api.POST("/api/v1/campaigns/wizard", { body, ...idem(key) })),
    {
      invalidate: [["campaigns"]],
      onSuccess: (result) => navigate(`/campaigns/${result.campaign.id}`, { state: { planningJob: result.job?.id } }),
    },
  );
  const serverErrors = create.error instanceof ApiError ? create.error.fieldErrors() : {};

  function submit(e: FormEvent) {
    e.preventDefault();
    const problems: Record<string, string> = {};
    if (!name.trim()) problems["name"] = "Give the campaign a name.";
    if (!topic.trim()) problems["topic"] = "Say what the campaign is about.";
    if (sourceMode === "paste" && sourceText.trim().length < 50) problems["source_text"] = "Paste the source material (at least a paragraph).";
    if (sourceMode === "existing" && !sourceId) problems["source_asset_ids"] = "Choose a source.";
    setErrors(problems);
    if (Object.keys(problems).length || !brandId) return;
    create.submit({
      brand_id: brandId,
      name: name.trim(),
      topic: topic.trim(),
      objective,
      primary_platform: primary,
      secondary_platforms: secondary.filter((p) => p !== primary),
      audience_id: audienceId || null,
      start_date: startDate,
      timezone: brand?.timezone ?? null,
      source_text: sourceMode === "paste" ? sourceText : null,
      source_title: sourceMode === "paste" ? sourceTitle || null : null,
      source_asset_ids: sourceMode === "existing" ? [sourceId] : [],
      generate: true,
      priority: 3,
    });
  }

  const err = (k: string) => errors[k] ?? serverErrors[k];
  if (catalog.isLoading) return <Loading />;
  return (
    <>
      <PageHeader
        title="New campaign"
        description="Planning runs on the local model: research, strategy, ideas and a 7-day plan. It takes a few minutes. Nothing is written or published until you review the plan and activate the campaign."
      />
      <form className="card form" onSubmit={submit} noValidate>
        <Field label="Campaign name" htmlFor="c-name" error={err("name")}>
          <input id="c-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />
        </Field>
        <Field label="What is it about?" htmlFor="c-topic" error={err("topic")} hint="One or two sentences, in plain words.">
          <textarea id="c-topic" rows={2} value={topic} onChange={(e) => setTopic(e.target.value)} />
        </Field>
        <Field label="Goal" htmlFor="c-objective">
          <select id="c-objective" value={objective} onChange={(e) => setObjective(e.target.value as Schemas["CampaignObjective"])}>
            {OBJECTIVES.map((o) => (
              <option key={o} value={o}>
                {humanize(o)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Main platform" htmlFor="c-primary">
          <select id="c-primary" value={primary} onChange={(e) => setPrimary(e.target.value as Schemas["Platform"])}>
            {writable.map((p) => (
              <option key={p.platform} value={p.platform}>
                {PLATFORM_LABEL[p.platform]}
              </option>
            ))}
          </select>
        </Field>
        <fieldset className="field">
          <legend>Other platforms</legend>
          <div className="checks">
            {writable
              .filter((p) => p.platform !== primary)
              .map((p) => (
                <label key={p.platform} className="checkbox">
                  <input
                    type="checkbox"
                    checked={secondary.includes(p.platform)}
                    onChange={(e) => setSecondary((s) => (e.target.checked ? [...s, p.platform] : s.filter((x) => x !== p.platform)))}
                  />{" "}
                  {PLATFORM_LABEL[p.platform]}
                </label>
              ))}
          </div>
        </fieldset>
        <Field label="Audience" htmlFor="c-aud" hint="Optional. Audiences are managed on the brand.">
          <select id="c-aud" value={audienceId} onChange={(e) => setAudienceId(e.target.value)}>
            <option value="">The brand's primary audience</option>
            {(audiences.data?.items ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Start date" htmlFor="c-start" hint={`Times are planned in ${brand?.timezone ?? "the brand's time zone"}.`}>
          <input id="c-start" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </Field>
        <fieldset className="field">
          <legend>Source material</legend>
          <div className="checks">
            <label className="checkbox">
              <input type="radio" name="src" checked={sourceMode === "paste"} onChange={() => setSourceMode("paste")} /> Paste text
            </label>
            <label className="checkbox">
              <input type="radio" name="src" checked={sourceMode === "existing"} onChange={() => setSourceMode("existing")} disabled={!sources.data?.items.length} /> Use an existing source
            </label>
          </div>
        </fieldset>
        {sourceMode === "paste" ? (
          <>
            <Field label="Source title" htmlFor="c-stitle">
              <input id="c-stitle" value={sourceTitle} onChange={(e) => setSourceTitle(e.target.value)} />
            </Field>
            <Field label="Source text" htmlFor="c-stext" error={err("source_text")} hint="Writers only use facts from this text and the research drawn from it.">
              <textarea id="c-stext" rows={10} value={sourceText} onChange={(e) => setSourceText(e.target.value)} />
            </Field>
          </>
        ) : (
          <Field label="Source" htmlFor="c-src" error={err("source_asset_ids")}>
            <select id="c-src" value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
              <option value="">Choose…</option>
              {(sources.data?.items ?? [])
                .filter((s) => s.status === "NORMALIZED")
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title ?? s.source_url ?? s.id}
                  </option>
                ))}
            </select>
          </Field>
        )}
        {create.error ? <ErrorPanel title="The campaign was not created" error={create.error} onRetry={create.retry} /> : null}
        <RoleButton role="EDITOR" type="submit" className="btn btn-primary" disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Create and start planning"}
        </RoleButton>
      </form>
    </>
  );
}
