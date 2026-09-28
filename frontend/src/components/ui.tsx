import { useEffect, useId, useRef, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, api, call } from "../api/client";
import { useAuth } from "../auth/auth";
import { explainError } from "../lib/format";
import type { StatusInfo, Tone } from "../lib/status";
import type { Schemas } from "../api/client";

export function Badge({ tone, children, title }: { tone: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function StatusBadge({ info }: { info: StatusInfo }) {
  return (
    <Badge tone={info.tone} title={info.help}>
      {info.label}
    </Badge>
  );
}

/** Shown wherever results come from the mock publisher or simulated analytics. */
export function SimulatedNotice({ children }: { children?: ReactNode }) {
  // Follows the server's publish mode per platform (GET /platforms) instead of assuming it.
  const catalog = useQuery({ queryKey: ["platforms"], queryFn: () => call(api.GET("/api/v1/platforms")), staleTime: 5 * 60_000 });
  const items = catalog.data?.items ?? [];
  const live = items.filter((p) => p.effective_publisher === "LIVE").map((p) => p.platform);
  if (catalog.data && live.length)
    return (
      <div className="notice notice-warning" role="note">
        <strong>Live publishing mode.</strong> Only accounts switched to live on a verified connector ({live.join(", ")}) can publish, and each
        post is checked again before it is sent. Every other platform is off in this mode: its scheduled posts are refused, not simulated.
      </div>
    );
  if (catalog.data && items.every((p) => p.effective_publisher === null))
    return (
      <div className="notice notice-danger" role="note">
        <strong>Publishing is off.</strong> No publisher is available on this server, so scheduled posts fail instead of being published.
      </div>
    );
  return (
    <div className="notice notice-simulated" role="note">
      <strong>Simulated.</strong>{" "}
      {children ??
        "Publishing in this version goes through a mock publisher: no platform is contacted and no real audience sees the content. Metrics derived from it are synthetic."}
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {description ? <p className="muted">{description}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" /> {label}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h2>{title}</h2>
      {children ? <div className="muted">{children}</div> : null}
    </div>
  );
}

export function ErrorPanel({ error, onRetry, title = "This could not be loaded" }: { error: unknown; onRetry?: () => void; title?: string }) {
  const ref = error instanceof ApiError ? error.correlationId : undefined;
  return (
    <div className="notice notice-danger" role="alert">
      <strong>{title}.</strong> {explainError(error)}
      {ref ? <div className="small muted">Reference for support: {ref}</div> : null}
      {onRetry ? (
        <div>
          <button type="button" className="btn" onClick={onRetry}>
            Retry
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function Unavailable({ children }: { children: ReactNode }) {
  return (
    <div className="notice notice-neutral" role="note">
      <strong>Not available yet.</strong> {children}
    </div>
  );
}

/**
 * A button that stays visible but disabled (with the reason) when the signed-in role is too
 * low. This is a hint for people; the API still checks the role on every request.
 */
export function RoleButton({
  role,
  children,
  reason,
  className = "btn",
  ...rest
}: { role: Schemas["UserRole"]; reason?: string } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const { can } = useAuth();
  const allowed = can(role);
  const id = useId();
  return (
    <span className="role-button">
      <button {...rest} className={className} disabled={!allowed || rest.disabled} aria-describedby={!allowed ? id : rest["aria-describedby"]}>
        {children}
      </button>
      {!allowed ? (
        <span id={id} className="small muted">
          {reason ?? `Requires the ${role.toLowerCase()} role.`}
        </span>
      ) : null}
    </span>
  );
}

/** Modal dialog on the native <dialog> element: focus trap, Escape to close, focus restore. */
export function Dialog({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) {
      if (typeof el.showModal === "function") el.showModal();
      else el.setAttribute("open", "");
    } else if (!open && el.open) {
      if (typeof el.close === "function") el.close();
      else el.removeAttribute("open");
    }
  }, [open]);
  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      {open ? (
        <>
          <div className="dialog-head">
            <h2 id={titleId}>{title}</h2>
            <button type="button" className="btn btn-ghost" onClick={onClose} aria-label="Close dialog">
              ✕
            </button>
          </div>
          {children}
        </>
      ) : null}
    </dialog>
  );
}

export function Field({ label, error, hint, children, htmlFor }: { label: string; error?: string | undefined; hint?: ReactNode; children: ReactNode; htmlFor: string }) {
  return (
    <div className={`field${error ? " field-error" : ""}`}>
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {hint ? <div className="small muted">{hint}</div> : null}
      {error ? (
        <div className="small error-text" role="alert">
          {error}
        </div>
      ) : null}
    </div>
  );
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}
