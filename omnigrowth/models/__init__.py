"""OmniGrowth ORM models.

Importing this package registers every table on ``Base.metadata`` (used by Alembic).
Phase 12 integration tables live in ``models/integrations.py``.
"""

from omnigrowth.models.ai import AgentRun, Embedding, ModelRun, PromptVersion
from omnigrowth.models.analytics import AnalyticsSnapshot, DerivedMetricDefinition, Recommendation
from omnigrowth.models.base import Base
from omnigrowth.models.brand import (
    AppSetting,
    Audience,
    Brand,
    BrandProfile,
    Offer,
    PlatformAccount,
    PlatformCredential,
)
from omnigrowth.models.campaign import Campaign, CampaignSource, CampaignStrategy, PlanItem
from omnigrowth.models.content import (
    ApprovalAction,
    ContentAsset,
    ContentIdea,
    ContentVersion,
    QaResult,
)
from omnigrowth.models.experiments import Experiment, ExperimentUnit, ExperimentVariant
from omnigrowth.models.identity import ApiToken, AuditEvent, User
from omnigrowth.models.integrations import (
    IntegrationEvent,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookSubscription,
)
from omnigrowth.models.jobs import IdempotencyKey, Job
from omnigrowth.models.publishing import Publication, PublicationJob
from omnigrowth.models.research import ResearchClaim, ResearchPacket, SourceAsset

__all__ = [
    "AgentRun",
    "AnalyticsSnapshot",
    "ApiToken",
    "AppSetting",
    "ApprovalAction",
    "Audience",
    "AuditEvent",
    "Base",
    "Brand",
    "BrandProfile",
    "Campaign",
    "CampaignSource",
    "CampaignStrategy",
    "ContentAsset",
    "ContentIdea",
    "ContentVersion",
    "DerivedMetricDefinition",
    "Embedding",
    "Experiment",
    "ExperimentUnit",
    "ExperimentVariant",
    "IdempotencyKey",
    "IntegrationEvent",
    "Job",
    "ModelRun",
    "Offer",
    "PlanItem",
    "PlatformAccount",
    "PlatformCredential",
    "PromptVersion",
    "Publication",
    "PublicationJob",
    "QaResult",
    "Recommendation",
    "ResearchClaim",
    "ResearchPacket",
    "SourceAsset",
    "User",
    "WebhookDelivery",
    "WebhookEndpoint",
    "WebhookSubscription",
]
