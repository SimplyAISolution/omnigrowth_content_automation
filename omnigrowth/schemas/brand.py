"""Brand domain API schemas (API_PLAN §2.3/§3.3, DATA_MODEL §3.3-§3.6, spec §5).

Request models forbid unknown fields. Free-text lists are normalised (trimmed, empties
dropped, case-insensitively de-duplicated) and bounded; violations surface as
``422 VALIDATION_FAILURE`` through the API validation handler.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    UrlConstraints,
    field_validator,
    model_validator,
)

from omnigrowth.core.enums import ActorType, RoutingProfile

# ---------------------------------------------------------------- limits
MAX_LIST_ITEMS: Final = 100
MAX_ITEM_CHARS: Final = 500
MAX_NAME_CHARS: Final = 200
MAX_SHORT_TEXT_CHARS: Final = 500
MAX_LONG_TEXT_CHARS: Final = 4000
MAX_VOICE_CATEGORY_CHARS: Final = 64
SLUG_PATTERN: Final = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

VOICE_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {
        "authoritative",
        "analytical",
        "conversational",
        "technical",
        "provocative",
        "educational",
        "humorous",
        "concise",
        "storytelling",
    }
)


# ---------------------------------------------------------------- normalisers
def normalize_str_list(values: list[str]) -> list[str]:
    """Trim, drop empties, dedupe case-insensitively (first spelling wins), enforce bounds."""
    if len(values) > MAX_LIST_ITEMS:
        raise ValueError(f"at most {MAX_LIST_ITEMS} items allowed")
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        item = raw.strip()
        if not item:
            continue
        if len(item) > MAX_ITEM_CHARS:
            raise ValueError(f"items must be at most {MAX_ITEM_CHARS} characters")
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _none_to_empty(value: Any) -> Any:
    return [] if value is None else value


def _trim_optional(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _trim_required(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("must not be blank")
    return trimmed


def validate_timezone(value: str) -> str:
    name = value.strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown IANA timezone: {name!r}") from exc
    return name


def _optional_timezone(value: str | None) -> str | None:
    return None if value is None else validate_timezone(value)


def normalize_voice(value: Any) -> dict[str, float | str]:
    """Voice = attribute -> score in [0, 1] or a short categorical label (spec §5)."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("voice must be an object")
    out: dict[str, float | str] = {}
    for raw_key, raw_val in value.items():
        if not isinstance(raw_key, str):
            raise ValueError("voice keys must be strings")
        key = raw_key.strip().lower()
        if key not in VOICE_ATTRIBUTES:
            allowed = ", ".join(sorted(VOICE_ATTRIBUTES))
            raise ValueError(f"unknown voice attribute {raw_key!r}; allowed: {allowed}")
        if key in out:
            raise ValueError(f"duplicate voice attribute {key!r}")
        if isinstance(raw_val, bool):
            raise ValueError(f"voice.{key} must be a number in [0, 1] or a string")
        if isinstance(raw_val, int | float):
            score = float(raw_val)
            if not 0.0 <= score <= 1.0:
                raise ValueError(f"voice.{key} must be between 0 and 1")
            out[key] = score
        elif isinstance(raw_val, str):
            label = raw_val.strip()
            if not label or len(label) > MAX_VOICE_CATEGORY_CHARS:
                raise ValueError(
                    f"voice.{key} label must be 1..{MAX_VOICE_CATEGORY_CHARS} characters"
                )
            out[key] = label
        else:
            raise ValueError(f"voice.{key} must be a number in [0, 1] or a string")
    return out


StrList = Annotated[list[str], BeforeValidator(_none_to_empty), AfterValidator(normalize_str_list)]
RequiredName = Annotated[str, Field(max_length=MAX_NAME_CHARS), AfterValidator(_trim_required)]
ShortText = Annotated[
    str | None, Field(default=None, max_length=MAX_SHORT_TEXT_CHARS), AfterValidator(_trim_optional)
]
LongText = Annotated[
    str | None, Field(default=None, max_length=MAX_LONG_TEXT_CHARS), AfterValidator(_trim_optional)
]
Timezone = Annotated[str, Field(max_length=64), AfterValidator(validate_timezone)]
Voice = Annotated[dict[str, float | str], BeforeValidator(normalize_voice)]
Slug = Annotated[str, Field(min_length=1, max_length=64, pattern=SLUG_PATTERN)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Read(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _reject_explicit_nulls(model: BaseModel, non_nullable: tuple[str, ...]) -> None:
    for name in non_nullable:
        if name in model.model_fields_set and getattr(model, name) is None:
            raise ValueError(f"{name} may be omitted but not null")


# ---------------------------------------------------------------- pagination


# ---------------------------------------------------------------- brand profile
class BrandProfileCreate(_Request):
    """Content of one profile version (spec §5 identity, voice, avoid list, pillars)."""

    company_description: LongText = None
    niche: ShortText = None
    mission: LongText = None
    products: StrList = Field(default_factory=list)
    services: StrList = Field(default_factory=list)
    differentiators: StrList = Field(default_factory=list)
    voice: Voice = Field(default_factory=dict)
    avoid_list: StrList = Field(default_factory=list)
    banned_phrases: StrList = Field(default_factory=list)
    vocabulary: StrList = Field(default_factory=list)
    content_pillars: StrList = Field(default_factory=list)


class BrandProfileUpdate(BrandProfileCreate):
    """``PUT /brands/{id}/profile`` body: the full content of the NEW version.

    ``expected_version_no`` (or an ``If-Match`` header) enables optimistic concurrency:
    a stale value yields ``409 CONFLICT``.
    """

    expected_version_no: int | None = Field(default=None, gt=0)


PROFILE_CONTENT_FIELDS: Final[tuple[str, ...]] = tuple(BrandProfileCreate.model_fields)


class BrandProfileRead(_Read):
    id: uuid.UUID
    brand_id: uuid.UUID
    version_no: int
    is_active: bool
    company_description: str | None
    niche: str | None
    mission: str | None
    products: list[Any]
    services: list[Any]
    differentiators: list[Any]
    voice: dict[str, Any]
    avoid_list: list[str]
    banned_phrases: list[str]
    vocabulary: list[str]
    content_pillars: list[str]
    actor_type: ActorType
    actor_id: str
    created_at: datetime


# ---------------------------------------------------------------- brands
class BrandCreate(_Request):
    slug: Slug
    name: RequiredName
    display_name: Annotated[
        str | None, Field(default=None, max_length=MAX_NAME_CHARS), AfterValidator(_trim_optional)
    ] = None
    timezone: Timezone = "UTC"
    default_routing_profile: RoutingProfile = RoutingProfile.LOCAL_ONLY
    profile: BrandProfileCreate | None = None


class BrandUpdate(_Request):
    display_name: Annotated[
        str | None, Field(default=None, max_length=MAX_NAME_CHARS), AfterValidator(_trim_optional)
    ] = None
    timezone: Annotated[
        str | None, Field(default=None, max_length=64), AfterValidator(_optional_timezone)
    ] = None
    default_routing_profile: RoutingProfile | None = None
    auto_approve_enabled: bool | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> BrandUpdate:
        _reject_explicit_nulls(
            self,
            ("display_name", "timezone", "default_routing_profile", "auto_approve_enabled"),
        )
        return self


class BrandRead(_Read):
    id: uuid.UUID
    slug: str
    name: str
    display_name: str
    timezone: str
    default_routing_profile: RoutingProfile
    auto_approve_enabled: bool
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


# ---------------------------------------------------------------- audiences
SophisticationLevel = Annotated[int, Field(ge=1, le=5)]


class AudienceCreate(_Request):
    name: RequiredName
    is_primary: bool = False
    description: LongText = None
    pains: StrList = Field(default_factory=list)
    goals: StrList = Field(default_factory=list)
    objections: StrList = Field(default_factory=list)
    common_questions: StrList = Field(default_factory=list)
    vocabulary: StrList = Field(default_factory=list)
    sophistication_level: SophisticationLevel | None = None


class AudienceUpdate(_Request):
    name: Annotated[
        str | None, Field(default=None, max_length=MAX_NAME_CHARS), AfterValidator(_trim_optional)
    ] = None
    is_primary: bool | None = None
    description: LongText = None
    pains: StrList | None = None
    goals: StrList | None = None
    objections: StrList | None = None
    common_questions: StrList | None = None
    vocabulary: StrList | None = None
    sophistication_level: SophisticationLevel | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> AudienceUpdate:
        _reject_explicit_nulls(
            self,
            (
                "name",
                "is_primary",
                "pains",
                "goals",
                "objections",
                "common_questions",
                "vocabulary",
            ),
        )
        return self


class AudienceRead(_Read):
    id: uuid.UUID
    brand_id: uuid.UUID
    name: str
    is_primary: bool
    description: str | None
    pains: list[str]
    goals: list[str]
    objections: list[str]
    common_questions: list[str]
    vocabulary: list[str]
    sophistication_level: int | None
    observed_interests: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


# ---------------------------------------------------------------- offers
Price = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
FunnelUrl = Annotated[AnyHttpUrl, UrlConstraints(max_length=2048)]


def _normalize_currency(value: str | None) -> str | None:
    if value is None:
        return None
    code = value.strip().upper()
    if len(code) != 3 or not code.isascii() or not code.isalpha():
        raise ValueError("currency must be a 3-letter ISO 4217 code")
    return code


Currency = Annotated[str | None, AfterValidator(_normalize_currency)]


class OfferCreate(_Request):
    name: RequiredName
    audience_id: uuid.UUID | None = None
    price: Price | None = None
    currency: Currency = None
    cta: ShortText = None
    funnel_destination_url: FunnelUrl | None = None
    proof: StrList = Field(default_factory=list)
    objections: StrList = Field(default_factory=list)

    @model_validator(mode="after")
    def _price_currency_pair(self) -> OfferCreate:
        if (self.price is None) != (self.currency is None):
            raise ValueError("price and currency must be given together")
        return self


class OfferUpdate(_Request):
    """PATCH semantics. The price/currency pairing is re-checked on the merged row."""

    name: Annotated[
        str | None, Field(default=None, max_length=MAX_NAME_CHARS), AfterValidator(_trim_optional)
    ] = None
    audience_id: uuid.UUID | None = None
    price: Price | None = None
    currency: Currency = None
    cta: ShortText = None
    funnel_destination_url: FunnelUrl | None = None
    proof: StrList | None = None
    objections: StrList | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> OfferUpdate:
        _reject_explicit_nulls(self, ("name", "proof", "objections"))
        return self


class OfferRead(_Read):
    id: uuid.UUID
    brand_id: uuid.UUID
    audience_id: uuid.UUID | None
    name: str
    price: Decimal | None
    currency: str | None
    cta: str | None
    funnel_destination_url: str | None
    proof: list[Any]
    objections: list[str]
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None

    @field_validator("currency", mode="before")
    @classmethod
    def _strip_char_padding(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value
