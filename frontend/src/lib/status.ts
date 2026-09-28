/**
 * Plain-language names and explanations for API states. Presentation only: which actions are
 * allowed is always decided by the API (state machines + roles), never here.
 */
import type { Schemas } from "../api/client";

export type Tone = "neutral" | "info" | "warning" | "danger" | "success" | "simulated";

export interface StatusInfo {
  label: string;
  tone: Tone;
  help: string;
}

type AssetStatus = Schemas["ContentAssetStatus"];
type JobStatus = Schemas["PublicationJobStatus"];
type PublicationStatus = Schemas["PublicationStatus"];
type CampaignStatus = Schemas["CampaignStatus"];
type ExperimentStatus = Schemas["ExperimentStatus"];
type BackgroundStatus = Schemas["JobStatus"];

export const ASSET_STATUS: Record<AssetStatus, StatusInfo> = {
  DRAFT: { label: "Being written", tone: "info", help: "A writer is producing this content, or its last attempt failed and it waits for a retry." },
  GENERATED: { label: "Checking quality", tone: "info", help: "Written; automatic quality checks (structure, brand, accuracy, platform rules) have not finished yet." },
  QA_FAILED: { label: "Failed quality checks", tone: "danger", help: "At least one automatic check failed. Edit or regenerate it; it cannot be approved as is." },
  QA_PASSED: { label: "Passed checks", tone: "info", help: "Automatic checks passed; it is on its way to the approval queue." },
  AWAITING_APPROVAL: { label: "Needs your approval", tone: "warning", help: "Nothing is scheduled or published until a person approves it." },
  REVISION_REQUESTED: { label: "Revision requested", tone: "warning", help: "A reviewer asked for changes. Edit or regenerate it." },
  APPROVED: { label: "Approved", tone: "success", help: "Approved by a person; not scheduled yet." },
  REJECTED: { label: "Rejected", tone: "neutral", help: "A reviewer rejected it. It will not be published unless it is edited or regenerated and approved again." },
  SCHEDULED: { label: "Scheduled", tone: "info", help: "A publication job is waiting for its time slot." },
  PUBLISHED: { label: "Published (simulated)", tone: "simulated", help: "Handled by the mock publisher: no platform was contacted and no real audience saw it." },
  ARCHIVED: { label: "Archived", tone: "neutral", help: "Kept for the record; no further actions." },
};

export const JOB_STATUS: Record<JobStatus, StatusInfo> = {
  PENDING: { label: "Waiting for its time", tone: "info", help: "The scheduler will pick it up at the scheduled time." },
  CLAIMED: { label: "Publishing now", tone: "info", help: "The scheduler is handing it to the publisher." },
  SUCCEEDED: { label: "Done (simulated)", tone: "simulated", help: "The mock publisher accepted it. No real platform call was made." },
  RETRY_WAIT: { label: "Will retry", tone: "warning", help: "The last attempt failed; it retries automatically with a delay." },
  FAILED: { label: "Failed", tone: "danger", help: "All attempts failed. Nothing was published by this job." },
  UNCERTAIN: { label: "Outcome unknown", tone: "danger", help: "We cannot tell whether it was published. It must be reconciled before anything else happens on this account." },
  CANCELLED: { label: "Cancelled", tone: "neutral", help: "Cancelled before publishing (for example rescheduled or edited)." },
};

export const PUBLICATION_STATUS: Record<PublicationStatus, StatusInfo> = {
  PUBLISHING: { label: "Publishing", tone: "info", help: "Being handed to the publisher." },
  PUBLISHED: { label: "Published (simulated)", tone: "simulated", help: "Mock publication: no platform call, no real audience." },
  PUBLICATION_UNCERTAIN: { label: "Outcome unknown", tone: "danger", help: "Needs reconciliation by an owner." },
  FAILED: { label: "Failed", tone: "danger", help: "Not published." },
  MANUAL_PENDING: { label: "Waiting for manual posting", tone: "warning", help: "A person has to post it and confirm." },
  DELETED: { label: "Deleted", tone: "neutral", help: "Removed after publication." },
};

export const CAMPAIGN_STATUS: Record<CampaignStatus, StatusInfo> = {
  DRAFT: { label: "Draft", tone: "neutral", help: "Not planned yet." },
  PLANNING: { label: "Planning", tone: "info", help: "Research, strategy, ideas and the 7-day plan are being produced." },
  READY_FOR_REVIEW: { label: "Plan ready for review", tone: "warning", help: "Review the plan, then activate the campaign to generate content." },
  ACTIVE: { label: "Active", tone: "success", help: "Content can be generated, approved and scheduled." },
  PAUSED: { label: "Paused", tone: "neutral", help: "Paused; no new content is generated." },
  COMPLETED: { label: "Completed", tone: "neutral", help: "Finished." },
  ARCHIVED: { label: "Archived", tone: "neutral", help: "Kept for the record." },
  FAILED: { label: "Planning failed", tone: "danger", help: "Planning could not finish. See the job error for the reason." },
};

export const EXPERIMENT_STATUS: Record<ExperimentStatus, StatusInfo> = {
  DRAFT: { label: "Draft", tone: "neutral", help: "Designed but not started; the design can still change." },
  RUNNING: { label: "Allocating posts", tone: "info", help: "New posts are assigned to variants by the pre-registered randomisation." },
  COLLECTING: { label: "Collecting results", tone: "info", help: "No new posts join; waiting until every post reaches the fixed measurement age." },
  CONCLUDED: { label: "Concluded", tone: "success", help: "A winner was supported by enough real, randomised evidence." },
  INCONCLUSIVE: { label: "Inconclusive", tone: "warning", help: "The evidence does not support a winner. This is a valid result, not an error." },
  CANCELLED: { label: "Cancelled", tone: "neutral", help: "Stopped before a result." },
};

export const BACKGROUND_STATUS: Record<BackgroundStatus, StatusInfo> = {
  QUEUED: { label: "Queued", tone: "info", help: "Waiting for a worker." },
  RUNNING: { label: "Running", tone: "info", help: "A worker is on it." },
  SUCCEEDED: { label: "Finished", tone: "success", help: "Completed." },
  FAILED: { label: "Failed", tone: "danger", help: "The job failed; see the error." },
  CANCELLED: { label: "Cancelled", tone: "neutral", help: "Cancelled." },
};

export const PLATFORM_LABEL: Record<Schemas["Platform"], string> = {
  LINKEDIN: "LinkedIn",
  X: "X",
  INSTAGRAM: "Instagram",
  FACEBOOK: "Facebook",
  YOUTUBE: "YouTube",
  REDDIT: "Reddit",
  SUBSTACK: "Substack",
  RUMBLE: "Rumble",
  MASTODON: "Mastodon",
};

export const FORMAT_LABEL: Record<Schemas["ContentFormat"], string> = {
  TEXT_POST: "Text post",
  THREAD: "Thread",
  ARTICLE: "Article",
  CAROUSEL: "Carousel",
  DOCUMENT_CAROUSEL: "Document carousel",
  POLL: "Poll",
  REEL_SCRIPT: "Reel script",
  STORY: "Story",
  IMAGE_POST: "Image post",
  LONG_VIDEO_OUTLINE: "Long video outline",
  LONG_VIDEO_SCRIPT: "Long video script",
  SHORT: "Short",
  NEWSLETTER: "Newsletter",
  NOTE: "Note",
  DISCUSSION: "Discussion",
  COMMENT_DRAFT: "Comment draft",
  QUOTE_POST: "Quote post",
};

export const QA_STAGE_LABEL: Record<Schemas["QaStage"], string> = {
  STRUCTURAL: "Structure",
  BRAND: "Brand voice",
  ACCURACY: "Accuracy",
  PLATFORM: "Platform rules",
};

export function humanize(code: string): string {
  const s = code.replace(/_/g, " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * What a publication's numbers can prove (A17), fixed when it was sent: SIMULATED (mock
 * publisher, no audience), SANDBOX (a real API call to a test account, not a real audience)
 * or REAL. Only REAL publications count as real evidence anywhere in the UI.
 */
export const EVIDENCE: Record<Schemas["EvidenceClass"], StatusInfo> = {
  SIMULATED: { label: "Simulated", tone: "simulated", help: "Mock publisher: nothing was posted and no real audience saw it." },
  SANDBOX: {
    label: "Sandbox",
    tone: "warning",
    help: "Posted through the platform's API to an account marked as a sandbox (test) account: not a real audience.",
  },
  REAL: { label: "Real", tone: "success", help: "Posted to a real account and audience." },
};

/** A publication's state; "simulated" / "sandbox" follow its evidence class. */
export function publicationInfo(p: { status: PublicationStatus; evidence_class: Schemas["EvidenceClass"] }): StatusInfo {
  if (p.status === "PUBLISHED" && p.evidence_class === "REAL") return { label: "Published", tone: "success", help: EVIDENCE.REAL.help };
  if (p.status === "PUBLISHED" && p.evidence_class === "SANDBOX")
    return { label: "Published (sandbox)", tone: "warning", help: EVIDENCE.SANDBOX.help };
  return PUBLICATION_STATUS[p.status];
}
