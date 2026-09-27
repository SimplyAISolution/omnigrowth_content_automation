import { Link } from "react-router-dom";
import type { Schemas } from "../api/client";
import { ASSET_STATUS, FORMAT_LABEL, PLATFORM_LABEL, QA_STAGE_LABEL, humanize } from "../lib/status";
import { Badge, StatusBadge } from "./ui";

type Version = Schemas["ContentVersionRead"];
type Asset = Schemas["ContentAssetRead"];
type Qa = Schemas["QaResultRead"];

/** Thread platforms store posts in one body separated by lines containing only "---". */
export function splitPosts(body: string): string[] {
  return body
    .split(/^\s*---\s*$/m)
    .map((p) => p.trim())
    .filter(Boolean);
}

const THREADED = new Set<Schemas["Platform"]>(["X", "MASTODON"]);

function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v : null;
}

function list(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function obj(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

export function PlatformFormat({ asset }: { asset: Pick<Asset, "platform" | "format"> }) {
  return (
    <span className="platform-format">
      <strong>{PLATFORM_LABEL[asset.platform]}</strong> · {FORMAT_LABEL[asset.format]}
    </span>
  );
}

/** The version's text, laid out the way the platform will show it, plus production notes. */
export function VersionView({ version, platform }: { version: Version; platform: Schemas["Platform"] }) {
  const p = version.platform_payload ?? {};
  const hashtags = list(p["hashtags"]).map(String);
  const posts = THREADED.has(platform) ? splitPosts(version.body) : [version.body];
  const frames = list(p["frames"]).map(obj);
  const sections = list(p["sections"]).map(obj);
  const chapters = list(p["chapters"]).map(obj);
  const candidates = list(p["title_candidates"]).map(String);
  const thumbs = list(p["thumbnail_concepts"]).map(String);
  const cw = str(p["content_warning"]);
  return (
    <article className="version" aria-label={`Version ${version.version_no}`}>
      {version.title ? (
        <p className="version-title">
          <span className="label">Title</span> {version.title}
        </p>
      ) : null}
      {str(p["subtitle"]) ? (
        <p>
          <span className="label">Subtitle</span> {str(p["subtitle"])}
        </p>
      ) : null}
      {str(p["subreddit"]) ? (
        <p>
          <span className="label">Subreddit</span> r/{str(p["subreddit"])}
          {str(p["flair"]) ? ` · flair “${str(p["flair"])}”` : ""}
        </p>
      ) : null}
      {cw ? (
        <p>
          <span className="label">Content warning</span> {cw}
        </p>
      ) : null}
      {version.hook && !THREADED.has(platform) ? (
        <p className="hook">
          <span className="label">Hook</span> {version.hook}
        </p>
      ) : null}
      <div className="label">{THREADED.has(platform) ? `Posts (${posts.length})` : platform === "YOUTUBE" || platform === "RUMBLE" ? "Description" : "Text"}</div>
      {posts.map((post, i) => (
        <div className="post-text" key={i}>
          {THREADED.has(platform) ? <span className="post-no">{i + 1}</span> : null}
          <p>{post}</p>
        </div>
      ))}
      {version.cta ? (
        <p>
          <span className="label">Call to action</span> {version.cta}
        </p>
      ) : null}
      {hashtags.length ? (
        <p>
          <span className="label">Hashtags</span> {hashtags.join(" ")}
        </p>
      ) : null}
      {sections.length ? (
        <details open>
          <summary>Script / outline ({sections.length} sections)</summary>
          <ol className="sections">
            {sections.map((s, i) => (
              <li key={i}>
                <strong>{String(s["heading"] ?? "")}</strong>
                <p>{String(s["text"] ?? "")}</p>
              </li>
            ))}
          </ol>
        </details>
      ) : null}
      {frames.length ? (
        <details open>
          <summary>Scenes / slides ({frames.length})</summary>
          <ol className="sections">
            {frames.map((f, i) => (
              <li key={i}>
                <span className="small muted">Visual:</span> {String(f["visual"] ?? "")}
                {f["text"] ? (
                  <>
                    <br />
                    <span className="small muted">Words:</span> {String(f["text"])}
                  </>
                ) : null}
              </li>
            ))}
          </ol>
        </details>
      ) : null}
      {chapters.length ? (
        <details>
          <summary>Chapters ({chapters.length})</summary>
          <ul>
            {chapters.map((c, i) => (
              <li key={i}>
                {String(c["at"] ?? "")} {String(c["title"] ?? "")}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {candidates.length || thumbs.length || str(p["pinned_comment"]) || str(p["visual_direction"]) || str(p["lead_magnet"]) ? (
        <details>
          <summary>Production notes</summary>
          <ul>
            {candidates.length ? <li>Title candidates: {candidates.join(" · ")}</li> : null}
            {thumbs.map((t, i) => (
              <li key={`t${i}`}>Thumbnail idea: {t}</li>
            ))}
            {str(p["pinned_comment"]) ? <li>Pinned comment: {str(p["pinned_comment"])}</li> : null}
            {str(p["visual_direction"]) ? <li>Visual direction: {str(p["visual_direction"])}</li> : null}
            {str(p["lead_magnet"]) ? <li>Lead magnet idea: {str(p["lead_magnet"])}</li> : null}
          </ul>
        </details>
      ) : null}
      {version.media_requirements.length ? (
        <p className="small muted">Media to produce: {version.media_requirements.map(String).join("; ")}</p>
      ) : null}
      <p className="small muted">
        Version {version.version_no} · {humanize(version.origin)} · {version.word_count} words · {version.char_count} characters
      </p>
    </article>
  );
}

const OUTCOME_TONE = { PASS: "success", WARN: "warning", FAIL: "danger" } as const;

export function QaResults({ results }: { results: Qa[] }) {
  if (!results.length) return <p className="muted">No quality-check results for this version yet.</p>;
  return (
    <ul className="qa-list">
      {results.map((r) => {
        const findings = (r.findings ?? []).map(obj);
        return (
          <li key={r.id}>
            <div className="qa-head">
              <span>{QA_STAGE_LABEL[r.stage]}</span>
              <Badge tone={OUTCOME_TONE[r.outcome as keyof typeof OUTCOME_TONE] ?? "neutral"}>{humanize(r.outcome)}</Badge>
            </div>
            {findings.length ? (
              <ul className="findings">
                {findings.map((f, i) => (
                  <li key={i} className={f["severity"] === "FAIL" ? "error-text" : undefined}>
                    <span className="small">{f["severity"] === "FAIL" ? "Must fix" : "Warning"}:</span> {String(f["message"] ?? f["code"] ?? "")}
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export function QaSummary({ results }: { results: Qa[] }) {
  if (!results.length) return <span className="small muted">Checks pending</span>;
  const fails = results.filter((r) => r.outcome === "FAIL").length;
  const warns = results.filter((r) => r.outcome === "WARN").length;
  if (fails) return <Badge tone="danger">{fails} check{fails > 1 ? "s" : ""} failed</Badge>;
  if (warns) return <Badge tone="warning">Passed with {warns} warning{warns > 1 ? "s" : ""}</Badge>;
  return <Badge tone="success">All checks passed</Badge>;
}

export function AssetCard({ asset, campaignName, to }: { asset: Asset; campaignName?: string | undefined; to: string }) {
  const v = asset.current_version;
  const preview = v?.title ?? v?.hook ?? v?.body ?? "";
  return (
    <li className="card asset-card">
      <div className="asset-card-head">
        <PlatformFormat asset={asset} />
        <StatusBadge info={ASSET_STATUS[asset.status]} />
      </div>
      <p className="asset-preview">{preview.length > 220 ? `${preview.slice(0, 220)}…` : preview || <span className="muted">No text yet</span>}</p>
      <div className="asset-card-foot">
        <span className="small muted">{campaignName ?? "Campaign"}</span>
        <QaSummary results={asset.qa ?? []} />
        <Link className="btn" to={to} aria-label={`Review ${PLATFORM_LABEL[asset.platform]} ${FORMAT_LABEL[asset.format]}: ${preview.slice(0, 60)}`}>
          Review
        </Link>
      </div>
      {asset.parent_asset_id ? <p className="small muted">Repurposed from another asset (depth {asset.lineage_depth}).</p> : null}
    </li>
  );
}
