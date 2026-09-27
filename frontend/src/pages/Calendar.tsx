import { useQuery } from "@tanstack/react-query";
import { useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { api, call, idem, type Schemas } from "../api/client";
import { Badge, Dialog, EmptyState, ErrorPanel, Loading, PageHeader, SimulatedNotice, StatusBadge } from "../components/ui";
import { useAuth } from "../auth/auth";
import { useBrand } from "../lib/brand";
import { addDays, addMonths, dayKey, daysBetween, timeOfDay, todayIn, weekdayName, zonedToIso } from "../lib/dates";
import { explainError, formatDateTime } from "../lib/format";
import { useAction } from "../lib/hooks";
import { useCampaigns } from "../lib/queries";
import { ASSET_STATUS, FORMAT_LABEL, JOB_STATUS, PLATFORM_LABEL } from "../lib/status";

type Item = Schemas["CalendarItemRead"];
type View = "day" | "week" | "month";

function when(item: Item): string | null {
  return item.due_at ?? item.window_start;
}

export function CalendarPage() {
  const { brand, brandId } = useBrand();
  const { can } = useAuth();
  const campaigns = useCampaigns();
  const tz = brand?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  const [view, setView] = useState<View>("week");
  const [day, setDay] = useState(() => todayIn(tz));
  const [platform, setPlatform] = useState<Schemas["Platform"] | "">("");
  const [campaignId, setCampaignId] = useState("");
  const [showPlanned, setShowPlanned] = useState(true);
  const [moving, setMoving] = useState<{ item: Item; to: string } | null>(null);

  const q = useQuery({
    queryKey: ["calendar", brandId, view, day, tz, platform, campaignId, showPlanned],
    queryFn: () =>
      call(
        api.GET("/api/v1/calendar", {
          params: {
            query: {
              date: day,
              tz,
              view,
              brand_id: brandId ?? undefined,
              platform: platform ? [platform] : undefined,
              campaign_id: campaignId ? [campaignId] : undefined,
              include_unscheduled_plan_items: showPlanned,
            },
          },
        }),
      ),
    enabled: Boolean(brandId),
  });

  const step = (n: number) => setDay(view === "month" ? addMonths(day, n) : addDays(day, view === "week" ? 7 * n : n));
  const days = q.data ? daysBetween(q.data.range_start, q.data.range_end, tz) : [];
  const byDay = new Map<string, Item[]>();
  for (const item of q.data?.items ?? []) {
    const at = when(item);
    if (!at) continue;
    const key = dayKey(at, tz);
    byDay.set(key, [...(byDay.get(key) ?? []), item]);
  }
  const canMove = can("EDITOR");

  function onDrop(e: DragEvent, target: string) {
    e.preventDefault();
    const id = e.dataTransfer.getData("text/plain");
    const item = q.data?.items.find((i) => i.id === id);
    if (item && dayKey(when(item) ?? "", tz) !== target) setMoving({ item, to: target });
  }

  return (
    <>
      <PageHeader title="Calendar" description={`Scheduled publications and planned items, shown in ${tz}.`} />
      <SimulatedNotice>Scheduled posts are published by the mock publisher: simulated, no real audience.</SimulatedNotice>
      <div className="filters" role="group" aria-label="Calendar controls">
        <div className="segmented" role="radiogroup" aria-label="View">
          {(["day", "week", "month"] as View[]).map((v) => (
            <button key={v} type="button" role="radio" aria-checked={view === v} className={`btn${view === v ? " btn-primary" : ""}`} onClick={() => setView(v)}>
              {v[0]?.toUpperCase() + v.slice(1)}
            </button>
          ))}
        </div>
        <button type="button" className="btn" onClick={() => step(-1)} aria-label={`Previous ${view}`}>
          ←
        </button>
        <button type="button" className="btn" onClick={() => setDay(todayIn(tz))}>
          Today
        </button>
        <button type="button" className="btn" onClick={() => step(1)} aria-label={`Next ${view}`}>
          →
        </button>
        <label>
          Platform{" "}
          <select value={platform} onChange={(e) => setPlatform(e.target.value as Schemas["Platform"] | "")}>
            <option value="">All</option>
            {Object.entries(PLATFORM_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label>
          Campaign{" "}
          <select value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
            <option value="">All</option>
            {(campaigns.data?.items ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={showPlanned} onChange={(e) => setShowPlanned(e.target.checked)} /> Show planned items without a schedule
        </label>
      </div>
      {canMove ? (
        <p className="small muted">Drag a scheduled post to another day to move it (same time of day). Keyboard users: open the post and choose Reschedule.</p>
      ) : null}
      {q.isLoading ? (
        <Loading label="Loading the calendar…" />
      ) : q.error ? (
        <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />
      ) : !q.data?.items.length ? (
        <EmptyState title="Nothing in this period">Schedule approved content, or plan a campaign, to fill the calendar.</EmptyState>
      ) : null}
      {q.data ? (
        <div className={`calendar calendar-${view}`}>
          {days.map((d) => (
            <section
              key={d}
              className={`cal-day${d === todayIn(tz) ? " today" : ""}`}
              aria-label={weekdayName(d)}
              onDragOver={(e) => canMove && e.preventDefault()}
              onDrop={(e) => canMove && onDrop(e, d)}
            >
              <h2 className="cal-day-title">{weekdayName(d)}</h2>
              <ul>
                {(byDay.get(d) ?? []).map((item) => (
                  <CalendarCard key={item.id} item={item} tz={tz} draggable={canMove && item.kind === "PUBLICATION_JOB" && item.publication_job_status === "PENDING"} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      ) : null}
      {moving ? <MoveDialog move={moving} tz={tz} onClose={() => setMoving(null)} /> : null}
    </>
  );
}

function CalendarCard({ item, tz, draggable }: { item: Item; tz: string; draggable: boolean }) {
  const at = when(item);
  const planned = item.kind === "PLAN_ITEM";
  return (
    <li
      className={`cal-card${planned ? " planned" : ""}`}
      draggable={draggable}
      onDragStart={(e) => e.dataTransfer.setData("text/plain", item.id)}
    >
      <div className="small">
        <strong>{at ? timeOfDay(at, tz) : "—"}</strong> · {PLATFORM_LABEL[item.platform]} · {FORMAT_LABEL[item.format]}
      </div>
      <div className="cal-title">{item.content_asset_id ? <Link to={`/content/${item.content_asset_id}`}>{item.title}</Link> : item.title}</div>
      <div className="small muted">{item.campaign_name}</div>
      <div className="cal-badges">
        {planned ? <Badge tone="neutral" title="From the campaign plan; no publication is scheduled yet.">Planned window</Badge> : null}
        {item.content_asset_status ? <StatusBadge info={ASSET_STATUS[item.content_asset_status]} /> : planned ? <Badge tone="neutral">Not written yet</Badge> : null}
        {item.publication_job_status ? <StatusBadge info={JOB_STATUS[item.publication_job_status]} /> : null}
      </div>
    </li>
  );
}

function MoveDialog({ move, tz, onClose }: { move: { item: Item; to: string }; tz: string; onClose: () => void }) {
  const at = when(move.item) ?? "";
  const target = zonedToIso(move.to, timeOfDay(at, tz), tz);
  const assetId = move.item.content_asset_id ?? "";
  const action = useAction(
    async (_: null, key: string) => {
      const asset = await call(api.GET("/api/v1/content/{asset_id}", { params: { path: { asset_id: assetId } } }));
      const jobs = await call(api.GET("/api/v1/publications/jobs", { params: { query: { campaign_id: asset.campaign_id } } }));
      const job = jobs.items.find((j) => j.content_asset_id === assetId && j.status === "PENDING");
      if (!job || !asset.approved_version_id) throw new Error("This post is no longer waiting for its time slot. Reload the calendar.");
      return call(
        api.POST("/api/v1/approvals/{asset_id}/actions", {
          params: { path: { asset_id: assetId } },
          body: { action: "SCHEDULE", content_version_id: asset.approved_version_id, platform_account_id: job.platform_account_id, scheduled_at: target },
          ...idem(key),
        }),
      );
    },
    { invalidate: [["calendar"], ["asset", assetId], ["jobs"]], onSuccess: onClose },
  );
  return (
    <Dialog open title="Move this scheduled post?" onClose={onClose}>
      <p>
        “{move.item.title}” moves from {formatDateTime(at, tz)} to <strong>{formatDateTime(target, tz)}</strong>. The pending job is cancelled and a
        new one is created; publication stays simulated.
      </p>
      {action.error ? (
        <div className="notice notice-danger" role="alert">
          {explainError(action.error)}
        </div>
      ) : null}
      <div className="action-row">
        <button type="button" className="btn btn-primary" disabled={action.isPending} onClick={() => action.submit(null)}>
          Move
        </button>
        <button type="button" className="btn" onClick={onClose}>
          Keep it
        </button>
      </div>
    </Dialog>
  );
}
