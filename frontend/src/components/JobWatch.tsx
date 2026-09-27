import { useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect } from "react";
import { api, call } from "../api/client";
import { BACKGROUND_STATUS, humanize } from "../lib/status";
import { StatusBadge } from "./ui";

/**
 * Follows a background job (planning, generation, analytics pull, ...) until it finishes,
 * then refreshes the given queries. Model work runs on the local model and can take minutes.
 */
export function JobWatch({ jobId, label, refresh = [] }: { jobId: string; label: string; refresh?: QueryKey[] }) {
  const queryClient = useQueryClient();
  const q = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => call(api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: jobId } } })),
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "SUCCEEDED" || s === "FAILED" || s === "CANCELLED" ? false : 3000;
    },
  });
  const status = q.data?.status;
  useEffect(() => {
    if (status === "SUCCEEDED" || status === "FAILED") {
      refresh.forEach((queryKey) => void queryClient.invalidateQueries({ queryKey }));
    }
    // Only the status change matters; the refresh list is stable per mount.
  }, [status]);
  if (!q.data) return <div className="small muted" role="status">{label}: checking…</div>;
  const job = q.data;
  const result = (job.result ?? {}) as Record<string, unknown>;
  const failed = Array.isArray(result["failed"]) ? result["failed"].length : 0;
  const skipped = Array.isArray(result["skipped"]) ? result["skipped"].length : 0;
  return (
    <div className={`notice ${job.status === "FAILED" ? "notice-danger" : "notice-neutral"}`} role="status" aria-live="polite">
      <strong>{label}:</strong> <StatusBadge info={BACKGROUND_STATUS[job.status]} />
      {job.status === "QUEUED" || job.status === "RUNNING" ? (
        <span className="small"> The local model is working; this can take several minutes. You can leave this page.</span>
      ) : null}
      {job.status === "FAILED" ? (
        <div className="small">
          Reason: {job.error_code ? humanize(job.error_code) : "unknown"}
          {typeof job.error?.["message"] === "string" ? ` — ${job.error["message"]}` : ""}
        </div>
      ) : null}
      {job.status === "SUCCEEDED" && typeof result["no_transition"] === "string" ? (
        <div className="small">
          {result["no_transition"] === "WINDOW_OPEN"
            ? "Nothing was evaluated: the collection period is still open. Results are only read once, at the fixed end (no early looks)."
            : `Nothing changed: ${humanize(String(result["no_transition"]))}.`}
        </div>
      ) : null}
      {job.status === "SUCCEEDED" && (failed || skipped) ? (
        <div className="small">
          {failed ? `${failed} item(s) could not be written and stay in “Being written” for a retry. ` : ""}
          {skipped ? `${skipped} item(s) were skipped.` : ""}
        </div>
      ) : null}
    </div>
  );
}
