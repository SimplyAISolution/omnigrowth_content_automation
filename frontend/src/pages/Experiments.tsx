import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api, call, idem } from "../api/client";
import { Badge, EmptyState, ErrorPanel, Field, KeyValue, Loading, PageHeader, RoleButton, StatusBadge, Unavailable } from "../components/ui";
import { JobWatch } from "../components/JobWatch";
import { formatDateTime } from "../lib/format";
import { useAction } from "../lib/hooks";
import { useBrand } from "../lib/brand";
import { EXPERIMENT_STATUS, humanize } from "../lib/status";

/** What each verdict means, in plain words (SPEC_AMENDMENTS A14). */
const VERDICT: Record<string, { title: string; text: string; tone: "success" | "warning" | "neutral" | "simulated" }> = {
  WINNER: { title: "A winner is supported", tone: "success", text: "Randomised design, real data, every variant reached the required sample, and the difference is significant after adjustment for multiple comparisons." },
  NO_DETECTABLE_DIFFERENCE: { title: "No detectable difference", tone: "neutral", text: "With the evidence collected, the variants cannot be told apart at the chosen confidence. This is not proof that they perform the same." },
  INSUFFICIENT_EVIDENCE: { title: "Not enough evidence", tone: "warning", text: "Too few measured posts per variant (or the metric could not be computed), so no conclusion is drawn." },
  NO_REAL_DATA: { title: "No real data", tone: "simulated", text: "Only simulated (mock) metrics exist. Simulated evidence is never used to pick a winner." },
  TREATMENTS_BEAT_CONTROL_UNRANKED: { title: "Several variants beat the control", tone: "warning", text: "More than one variant beat the control, but they could not be ranked against each other." },
  OBSERVATIONAL_ASSOCIATION: { title: "Association only", tone: "warning", text: "Posts were not randomly assigned, so any difference may have other causes. No winner is declared." },
};

export function ExperimentsPage() {
  const { brandId } = useBrand();
  const q = useQuery({
    queryKey: ["experiments", brandId],
    queryFn: () => call(api.GET("/api/v1/experiments", { params: { query: { brand_id: brandId ?? undefined } } })),
    enabled: Boolean(brandId),
  });
  return (
    <>
      <PageHeader
        title="Experiments"
        description="Controlled tests of one change at a time (for example the hook or the call to action). Results follow a fixed, pre-registered plan; inconclusive is a normal outcome."
      />
      <Unavailable>Designing a new experiment from this screen is not available yet; experiments are created through the API (POST /api/v1/experiments).</Unavailable>
      {q.isLoading ? (
        <Loading />
      ) : q.error ? (
        <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />
      ) : !q.data?.items.length ? (
        <EmptyState title="No experiments yet" />
      ) : (
        <table className="table">
          <caption className="sr-only">Experiments</caption>
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Testing</th>
              <th scope="col">State</th>
              <th scope="col">Result</th>
            </tr>
          </thead>
          <tbody>
            {q.data.items.map((e) => (
              <tr key={e.id}>
                <td>
                  <Link to={`/experiments/${e.id}`}>{e.name}</Link>
                </td>
                <td>{humanize(e.variable)}</td>
                <td>
                  <StatusBadge info={EXPERIMENT_STATUS[e.status]} />
                </td>
                <td>{e.verdict ? VERDICT[e.verdict]?.title ?? humanize(e.verdict) : "Not evaluated yet"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

type Comparison = Record<string, unknown>;

export function ExperimentDetailPage() {
  const { experimentId = "" } = useParams();
  const path = { params: { path: { experiment_id: experimentId } } };
  const q = useQuery({ queryKey: ["experiment", experimentId], queryFn: () => call(api.GET("/api/v1/experiments/{experiment_id}", path)) });
  const result = useQuery({
    queryKey: ["experiment-result", experimentId],
    queryFn: () => call(api.GET("/api/v1/experiments/{experiment_id}/results", path)),
    retry: false,
  });
  const invalidate = [["experiment", experimentId], ["experiment-result", experimentId], ["experiments"]];
  const start = useAction((_: null, key: string) => call(api.POST("/api/v1/experiments/{experiment_id}/start", { ...path, ...idem(key) })), { invalidate });
  const [evaluationJob, setEvaluationJob] = useState<string | null>(null);
  const evaluate = useAction((_: null, key: string) => call(api.POST("/api/v1/experiments/{experiment_id}/evaluate", { ...path, ...idem(key) })), {
    invalidate,
    onSuccess: (job) => setEvaluationJob(job.id),
  });
  const [cancelReason, setCancelReason] = useState<string | null>(null);
  const cancel = useAction((reason: string, key: string) => call(api.POST("/api/v1/experiments/{experiment_id}/cancel", { ...path, body: { reason }, ...idem(key) })), {
    invalidate,
    onSuccess: () => setCancelReason(null),
  });

  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />;
  const e = q.data;
  const r = result.data;
  const verdict = r?.verdict ? VERDICT[r.verdict] : undefined;
  const real = (r?.real ?? null) as { per_variant?: Record<string, unknown>[]; comparisons?: Comparison[] } | null;
  const simulated = r?.simulated as Record<string, unknown> | null | undefined;
  const actionError = start.error ?? evaluate.error ?? cancel.error;
  const noResult = result.error instanceof ApiError && result.error.status === 404;

  return (
    <>
      <p className="breadcrumb">
        <Link to="/experiments">← Experiments</Link>
      </p>
      <PageHeader title={e.name} description={e.hypothesis} actions={<StatusBadge info={EXPERIMENT_STATUS[e.status]} />} />
      <div className="notice notice-neutral" role="status">
        <strong>{EXPERIMENT_STATUS[e.status].label}.</strong> {EXPERIMENT_STATUS[e.status].help}
      </div>
      <div className="action-row">
        {e.status === "DRAFT" ? (
          <RoleButton role="EDITOR" className="btn btn-primary" onClick={() => start.submit(null)} disabled={start.isPending}>
            Start
          </RoleButton>
        ) : null}
        {e.status === "COLLECTING" ? (
          <RoleButton role="EDITOR" onClick={() => evaluate.submit(null)} disabled={evaluate.isPending}>
            Run the final evaluation
          </RoleButton>
        ) : null}
        {e.status === "DRAFT" || e.status === "RUNNING" || e.status === "COLLECTING" ? (
          <RoleButton role="EDITOR" className="btn btn-danger" onClick={() => setCancelReason("")} disabled={cancel.isPending}>
            Cancel experiment
          </RoleButton>
        ) : null}
      </div>
      {cancelReason !== null ? (
        <form
          className="card form"
          onSubmit={(ev) => {
            ev.preventDefault();
            if (cancelReason.trim()) cancel.submit(cancelReason.trim());
          }}
        >
          <Field label="Why are you cancelling? (required)" htmlFor="x-cancel" error={cancelReason.trim() ? undefined : "A reason is required."}>
            <input id="x-cancel" value={cancelReason} onChange={(ev) => setCancelReason(ev.target.value)} maxLength={500} />
          </Field>
          <div className="action-row">
            <button type="submit" className="btn btn-danger" disabled={!cancelReason.trim() || cancel.isPending}>
              Cancel the experiment
            </button>
            <button type="button" className="btn" onClick={() => setCancelReason(null)}>
              Keep it running
            </button>
          </div>
        </form>
      ) : null}
      {e.status === "RUNNING" || e.status === "COLLECTING" ? (
        <p className="small muted">
          Results are read once, after the collection period ends ({formatDateTime(e.end_at)}). There are no early looks: evaluating
          before then changes nothing. The scheduler evaluates automatically at the end.
        </p>
      ) : null}
      {evaluationJob ? (
        <JobWatch jobId={evaluationJob} label="Evaluation" refresh={[["experiment", experimentId], ["experiment-result", experimentId]]} />
      ) : null}
      {actionError ? <ErrorPanel title="That did not work" error={actionError} /> : null}
      <section className="card" aria-labelledby="design-h">
        <h2 id="design-h">Plan (fixed before the experiment starts)</h2>
        <KeyValue
          items={[
            ["What changes", humanize(e.variable)],
            ["Measured by", String(e.metric["key"] ?? e.metric["name"] ?? JSON.stringify(e.metric))],
            ["Assignment", e.assignment === "RANDOMIZED" ? "Random (can show cause and effect)" : "Not random (association only)"],
            ["Posts needed per variant", String(e.min_units_per_variant)],
            ["Confidence level", `${Math.round(e.confidence_level * 100)}%`],
            ["Starts", formatDateTime(e.start_at)],
            ["Results collected until", formatDateTime(e.end_at)],
          ]}
        />
        <ul>
          {e.variants.map((v) => (
            <li key={v.id}>
              <strong>{v.label}</strong>
              {v.is_control ? " (control)" : ""}: {v.description} — {v.units} post(s) assigned
            </li>
          ))}
        </ul>
      </section>
      <section className="card" aria-labelledby="res-h">
        <h2 id="res-h">Result</h2>
        {result.isLoading ? <Loading /> : null}
        {noResult ? <p className="muted">Not evaluated yet. The single evaluation happens after the collection period ends.</p> : null}
        {result.error && !noResult ? <ErrorPanel error={result.error} onRetry={() => void result.refetch()} /> : null}
        {r ? (
          <>
            <div className={`notice notice-${verdict?.tone === "success" ? "success" : verdict?.tone === "simulated" ? "simulated" : "warning"}`} role="note">
              <strong>{verdict?.title ?? humanize(r.verdict ?? "No verdict")}.</strong> {verdict?.text}
              {r.inconclusive_reason ? <div className="small">Reason: {humanize(r.inconclusive_reason)}</div> : null}
            </div>
            {real?.per_variant?.length ? (
              <table className="table">
                <caption>Real data per variant</caption>
                <thead>
                  <tr>
                    <th scope="col">Variant</th>
                    <th scope="col">Measured posts</th>
                    <th scope="col">Average</th>
                    <th scope="col">Enough posts?</th>
                  </tr>
                </thead>
                <tbody>
                  {real.per_variant.map((v) => (
                    <tr key={String(v["variant_id"])}>
                      <td>{String(v["label"])}</td>
                      <td>{String(v["n"])}</td>
                      <td>{typeof v["mean"] === "number" ? v["mean"].toFixed(4) : "—"}</td>
                      <td>{v["meets_requirement"] ? "Yes" : `No (needs ${e.min_units_per_variant})`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted">No real measurements yet.</p>
            )}
            {real?.comparisons?.map((c) => (
              <div key={String(c["variant_id"])} className="card">
                <strong>
                  {String(c["label"])} vs {String(c["versus"])}
                </strong>
                {c["not_estimable"] ? (
                  <p className="small">Cannot be estimated: {String(c["not_estimable"])}.</p>
                ) : (
                  <p className="small">
                    Difference {Number(c["difference"]).toFixed(4)} (range {Number(c["ci_low"]).toFixed(4)} to {Number(c["ci_high"]).toFixed(4)} at{" "}
                    {Math.round(Number(c["ci_level"]) * 100)}% confidence). Adjusted p-value {Number(c["p_value_adjusted"]).toFixed(3)} —{" "}
                    {c["significant"] ? "significant" : "not significant"}.
                  </p>
                )}
              </div>
            ))}
            {simulated ? (
              <div className="notice notice-simulated" role="note">
                <strong>Simulated data exists</strong> for this experiment. It is analysed separately and never used for the result above.
              </div>
            ) : null}
            <p className="small muted">{r.interpretation}</p>
            <Badge tone="neutral">{r.evidence_snapshot_ids.length} evidence snapshot(s)</Badge>
          </>
        ) : null}
      </section>
    </>
  );
}
