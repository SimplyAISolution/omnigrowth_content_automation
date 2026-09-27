import { ApiError } from "../api/client";

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const dateOnly = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

export function formatDateTime(iso: string | null | undefined, timeZone?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  if (timeZone) {
    try {
      return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone }).format(d);
    } catch {
      // Unknown zone: fall back to the viewer's local time below.
    }
  }
  return dateTime.format(d);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return Number.isNaN(d.getTime()) ? iso : dateOnly.format(d);
}

export function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? "Not captured" : value.toLocaleString();
}

export function formatRate(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;
}

/** Local datetime-input value ("YYYY-MM-DDTHH:mm") → ISO string with the viewer's offset. */
export function localInputToIso(value: string): string {
  return new Date(value).toISOString();
}

export function toLocalInput(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** A sentence a person can act on, for any error thrown by the API layer. */
export function explainError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return error instanceof Error && error.message ? error.message : "Something went wrong. Retry, and if it keeps failing, check the API logs.";
  }
  switch (error.code) {
    case "NETWORK_ERROR":
      return error.message;
    case "UNAUTHORIZED":
      return "Your session has ended. Sign in again.";
    case "FORBIDDEN":
      return "Your role does not allow this action. Ask an owner if you need it.";
    case "NOT_FOUND":
      return "This item no longer exists or was archived.";
    case "INVALID_STATE_TRANSITION":
      return `Not possible in the current state: ${error.message}`;
    case "CONFLICT":
      return `Someone or something changed this in the meantime: ${error.message} Reload and try again.`;
    case "RATE_LIMITED":
      return "Too many requests or the model queue is full. Wait a moment, then retry.";
    case "LIVE_PUBLISH_BLOCKED":
      return "Live publishing is blocked. OmniGrowth only publishes through the mock publisher in this version.";
    case "PUBLICATION_UNCERTAIN":
      return "A publication on this account has an unknown outcome and must be reconciled first.";
    case "VALIDATION_FAILURE": {
      const fields = error.fieldErrors();
      const first = Object.values(fields)[0];
      return first ? `Check the form: ${first}` : error.message;
    }
    default:
      return error.message;
  }
}
