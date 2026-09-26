"""Canonical enums (docs/GLOSSARY.md §3, §4, §4a).

Postgres enum type names are the snake_case form of the class name (GLOSSARY §4a rule),
exposed via ``pg_enum_name``.
"""

from __future__ import annotations

import re
from enum import StrEnum


def pg_enum_name(enum_cls: type[StrEnum]) -> str:
    """``ContentAssetStatus`` -> ``content_asset_status``."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", enum_cls.__name__).lower()


# ------------------------------------------------------------------ GLOSSARY §3
class Platform(StrEnum):
    LINKEDIN = "LINKEDIN"
    X = "X"
    INSTAGRAM = "INSTAGRAM"
    FACEBOOK = "FACEBOOK"
    YOUTUBE = "YOUTUBE"
    REDDIT = "REDDIT"
    SUBSTACK = "SUBSTACK"
    RUMBLE = "RUMBLE"
    MASTODON = "MASTODON"


class ContentFormat(StrEnum):
    TEXT_POST = "TEXT_POST"
    THREAD = "THREAD"
    ARTICLE = "ARTICLE"
    CAROUSEL = "CAROUSEL"
    DOCUMENT_CAROUSEL = "DOCUMENT_CAROUSEL"
    POLL = "POLL"
    REEL_SCRIPT = "REEL_SCRIPT"
    STORY = "STORY"
    IMAGE_POST = "IMAGE_POST"
    LONG_VIDEO_OUTLINE = "LONG_VIDEO_OUTLINE"
    LONG_VIDEO_SCRIPT = "LONG_VIDEO_SCRIPT"
    SHORT = "SHORT"
    NEWSLETTER = "NEWSLETTER"
    NOTE = "NOTE"
    DISCUSSION = "DISCUSSION"
    COMMENT_DRAFT = "COMMENT_DRAFT"
    QUOTE_POST = "QUOTE_POST"


class SourceType(StrEnum):
    TEXT = "TEXT"
    URL = "URL"
    MARKDOWN = "MARKDOWN"
    PDF = "PDF"
    DOCX = "DOCX"
    TRANSCRIPT = "TRANSCRIPT"
    SUBTITLE = "SUBTITLE"
    AUDIO = "AUDIO"
    VIDEO = "VIDEO"
    YOUTUBE_URL = "YOUTUBE_URL"
    NOTE = "NOTE"


class CampaignObjective(StrEnum):
    REACH = "REACH"
    AUTHORITY = "AUTHORITY"
    FOLLOWERS = "FOLLOWERS"
    EMAIL_SUBSCRIBERS = "EMAIL_SUBSCRIBERS"
    LEADS = "LEADS"
    PRODUCT_AWARENESS = "PRODUCT_AWARENESS"
    SALES = "SALES"


class FunnelStage(StrEnum):
    AWARENESS = "AWARENESS"
    CONSIDERATION = "CONSIDERATION"
    CONVERSION = "CONVERSION"
    RETENTION = "RETENTION"


class AngleType(StrEnum):
    EDUCATIONAL = "EDUCATIONAL"
    CONTRARIAN = "CONTRARIAN"
    STORY = "STORY"
    TACTICAL = "TACTICAL"
    COMPARISON = "COMPARISON"
    OPINION = "OPINION"
    DATA_DRIVEN = "DATA_DRIVEN"
    CASE_STUDY = "CASE_STUDY"


class ClaimLabel(StrEnum):
    SOURCE_STATED = "SOURCE_STATED"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
    OPINION = "OPINION"


class QaStage(StrEnum):
    STRUCTURAL = "STRUCTURAL"
    BRAND = "BRAND"
    ACCURACY = "ACCURACY"
    PLATFORM = "PLATFORM"


class QaOutcome(StrEnum):
    PASS = "PASS"  # noqa: S105  # nosec B105 - QA outcome label, not a password
    WARN = "WARN"
    FAIL = "FAIL"


class ApprovalActionType(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_REVISION = "REQUEST_REVISION"
    EDIT = "EDIT"
    REGENERATE = "REGENERATE"
    SCHEDULE = "SCHEDULE"
    PUBLISH_NOW = "PUBLISH_NOW"


class ActorType(StrEnum):
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"
    SCHEDULER = "SCHEDULER"
    AGENT = "AGENT"


class UserRole(StrEnum):
    OWNER = "OWNER"
    EDITOR = "EDITOR"
    VIEWER = "VIEWER"


class RoutingProfile(StrEnum):
    LOCAL_ONLY = "LOCAL_ONLY"
    BALANCED = "BALANCED"
    QUALITY_FIRST = "QUALITY_FIRST"


class ModelRole(StrEnum):
    CHEAP_LOCAL = "CHEAP_LOCAL"
    REASONING_LOCAL = "REASONING_LOCAL"
    CODING_LOCAL = "CODING_LOCAL"
    EMBEDDING_LOCAL = "EMBEDDING_LOCAL"
    FRONTIER_FAST = "FRONTIER_FAST"
    FRONTIER_REASONING = "FRONTIER_REASONING"
    FRONTIER_CREATIVE = "FRONTIER_CREATIVE"


class PrivacyClass(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    PRIVATE = "PRIVATE"


class ModelLocality(StrEnum):
    LOCAL = "LOCAL"
    REMOTE = "REMOTE"


class PublishMode(StrEnum):
    MOCK = "MOCK"
    MANUAL = "MANUAL"
    LIVE = "LIVE"


# ------------------------------------------------------------------ GLOSSARY §4 (states)
class CampaignStatus(StrEnum):
    DRAFT = "DRAFT"
    PLANNING = "PLANNING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"
    FAILED = "FAILED"


class ContentAssetStatus(StrEnum):
    DRAFT = "DRAFT"
    GENERATED = "GENERATED"
    QA_FAILED = "QA_FAILED"
    QA_PASSED = "QA_PASSED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class PublicationJobStatus(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    SUCCEEDED = "SUCCEEDED"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    CANCELLED = "CANCELLED"


class PublicationStatus(StrEnum):
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    PUBLICATION_UNCERTAIN = "PUBLICATION_UNCERTAIN"
    FAILED = "FAILED"
    MANUAL_PENDING = "MANUAL_PENDING"
    DELETED = "DELETED"


class ExperimentStatus(StrEnum):
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    COLLECTING = "COLLECTING"
    CONCLUDED = "CONCLUDED"
    INCONCLUSIVE = "INCONCLUSIVE"
    CANCELLED = "CANCELLED"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# ------------------------------------------------------------------ GLOSSARY §4a
class MetricSource(StrEnum):
    MOCK = "MOCK"
    FIXTURE = "FIXTURE"
    API = "API"
    MANUAL = "MANUAL"


class MetricNullReason(StrEnum):
    MISSING_INPUT = "MISSING_INPUT"
    ZERO_DENOMINATOR = "ZERO_DENOMINATOR"
    BELOW_MIN_DENOMINATOR = "BELOW_MIN_DENOMINATOR"


class CalendarItemKind(StrEnum):
    PUBLICATION_JOB = "PUBLICATION_JOB"
    PLAN_ITEM = "PLAN_ITEM"


class ContentVersionOrigin(StrEnum):
    GENERATED = "GENERATED"
    HUMAN_EDIT = "HUMAN_EDIT"
    REGENERATION = "REGENERATION"


class ContentIdeaStatus(StrEnum):
    PROPOSED = "PROPOSED"
    SELECTED = "SELECTED"
    DISCARDED = "DISCARDED"


class SourceAssetStatus(StrEnum):
    PENDING = "PENDING"
    NORMALIZED = "NORMALIZED"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"


class AgentRunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ModelRunStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class RecommendationStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    DISMISSED = "DISMISSED"


class ExperimentVariable(StrEnum):
    HOOK = "HOOK"
    TITLE = "TITLE"
    IMAGE = "IMAGE"
    CTA = "CTA"
    POST_LENGTH = "POST_LENGTH"
    POSTING_TIME = "POSTING_TIME"
    CAROUSEL_LENGTH = "CAROUSEL_LENGTH"
    VIDEO_OPENING = "VIDEO_OPENING"
    THUMBNAIL = "THUMBNAIL"
    TOPIC = "TOPIC"


class CredentialKind(StrEnum):
    OAUTH2 = "OAUTH2"
    API_KEY = "API_KEY"
    APP_PASSWORD = "APP_PASSWORD"  # noqa: S105  # nosec B105 - enum label, not a secret


class EmbeddingOwnerType(StrEnum):
    CONTENT_VERSION = "CONTENT_VERSION"
    SOURCE_ASSET = "SOURCE_ASSET"
    RESEARCH_CLAIM = "RESEARCH_CLAIM"
    BRAND_PROFILE = "BRAND_PROFILE"
    AUDIENCE = "AUDIENCE"
    OFFER = "OFFER"
    CAMPAIGN_STRATEGY = "CAMPAIGN_STRATEGY"
    CONTENT_IDEA = "CONTENT_IDEA"


class EmbeddingField(StrEnum):
    FULL = "FULL"
    HOOK = "HOOK"
    CTA = "CTA"
    CHUNK = "CHUNK"


class PlatformAccountType(StrEnum):
    PERSONAL = "PERSONAL"
    ORGANIZATION = "ORGANIZATION"
    PAGE = "PAGE"
    CHANNEL = "CHANNEL"
    SUBREDDIT_POSTER = "SUBREDDIT_POSTER"
    INSTANCE_USER = "INSTANCE_USER"


class JobKind(StrEnum):
    SOURCE_INGEST = "SOURCE_INGEST"
    CAMPAIGN_PLANNING = "CAMPAIGN_PLANNING"
    ASSET_GENERATION = "ASSET_GENERATION"
    CONTENT_REGENERATION = "CONTENT_REGENERATION"
    QA_RUN = "QA_RUN"
    EMBEDDING = "EMBEDDING"
    REEMBED = "REEMBED"
    ANALYTICS_PULL = "ANALYTICS_PULL"
    EXPERIMENT_EVALUATION = "EXPERIMENT_EVALUATION"
    RECOMMENDATION_GENERATION = "RECOMMENDATION_GENERATION"
    PLATFORM_VERIFY = "PLATFORM_VERIFY"
    PUBLICATION_RECONCILE = "PUBLICATION_RECONCILE"
    WEBHOOK_DISPATCH = "WEBHOOK_DISPATCH"


class HealthStatus(StrEnum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class Environment(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"
