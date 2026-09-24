"""Unit: brand-domain request validation (spec §5, API_PLAN §3.3, §6.3)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from omnigrowth.schemas.brand import (
    MAX_ITEM_CHARS,
    MAX_LIST_ITEMS,
    AudienceCreate,
    AudienceUpdate,
    BrandCreate,
    BrandProfileCreate,
    BrandProfileUpdate,
    BrandUpdate,
    OfferCreate,
    OfferUpdate,
)


# ---------------------------------------------------------------- voice
def test_voice_accepts_scores_and_labels_and_normalises_keys() -> None:
    p = BrandProfileCreate(voice={" Analytical ": 0.8, "humorous": 0, "concise": "very"})
    assert p.voice == {"analytical": 0.8, "humorous": 0.0, "concise": "very"}


@pytest.mark.parametrize(
    "voice",
    [
        {"sarcastic": 0.5},  # unknown attribute
        {"analytical": 1.5},  # out of range
        {"analytical": -0.1},
        {"analytical": True},  # bool is not a score
        {"analytical": None},
        {"analytical": [0.3]},
        {"analytical": "   "},  # blank label
        {"analytical": "x" * 65},  # label too long
        {"analytical": 0.2, "ANALYTICAL": 0.3},  # duplicate after normalisation
        ["analytical"],
    ],
)
def test_voice_rejects_invalid(voice: Any) -> None:
    with pytest.raises(ValidationError):
        BrandProfileCreate(voice=voice)


def test_voice_null_means_empty() -> None:
    assert BrandProfileCreate.model_validate({"voice": None}).voice == {}


# ---------------------------------------------------------------- lists
def test_lists_are_trimmed_deduped_case_insensitively_and_empties_dropped() -> None:
    p = BrandProfileCreate(
        content_pillars=["  AI ", "ai", "", "   ", "Cybersecurity", "CYBERSECURITY", "local AI"],
        banned_phrases=["Game changer", "game CHANGER"],
    )
    assert p.content_pillars == ["AI", "Cybersecurity", "local AI"]
    assert p.banned_phrases == ["Game changer"]


def test_list_limits() -> None:
    BrandProfileCreate(vocabulary=[f"w{i}" for i in range(MAX_LIST_ITEMS)])
    with pytest.raises(ValidationError, match="at most"):
        BrandProfileCreate(vocabulary=[f"w{i}" for i in range(MAX_LIST_ITEMS + 1)])
    BrandProfileCreate(avoid_list=["x" * MAX_ITEM_CHARS])
    with pytest.raises(ValidationError, match="characters"):
        BrandProfileCreate(avoid_list=["x" * (MAX_ITEM_CHARS + 1)])


def test_profile_text_fields_trimmed_and_blank_becomes_none() -> None:
    p = BrandProfileCreate(niche="  local AI  ", mission="   ")
    assert p.niche == "local AI"
    assert p.mission is None


def test_profile_rejects_unknown_fields_and_bad_expected_version() -> None:
    with pytest.raises(ValidationError):
        BrandProfileCreate.model_validate({"tone": "x"})
    with pytest.raises(ValidationError):
        BrandProfileUpdate(expected_version_no=0)


# ---------------------------------------------------------------- brands
def test_brand_create_defaults_and_timezone() -> None:
    b = BrandCreate(slug="acme-ai", name="  Acme AI ", timezone="Europe/Berlin")
    assert b.name == "Acme AI"
    assert b.timezone == "Europe/Berlin"
    assert b.default_routing_profile == "LOCAL_ONLY"
    assert b.profile is None


@pytest.mark.parametrize("tz", ["Mars/Olympus", "", "../../etc/passwd", "UTC+5"])
def test_brand_rejects_unknown_timezone(tz: str) -> None:
    with pytest.raises(ValidationError):
        BrandCreate(slug="acme", name="Acme", timezone=tz)
    with pytest.raises(ValidationError):
        BrandUpdate(timezone=tz)


@pytest.mark.parametrize("slug", ["Acme", "acme_ai", "-acme", "acme-", "a" * 65, ""])
def test_brand_rejects_bad_slug(slug: str) -> None:
    with pytest.raises(ValidationError):
        BrandCreate(slug=slug, name="Acme")


def test_brand_name_must_not_be_blank() -> None:
    with pytest.raises(ValidationError):
        BrandCreate(slug="acme", name="   ")


def test_brand_update_rejects_explicit_null_for_non_nullable_fields() -> None:
    with pytest.raises(ValidationError):
        BrandUpdate.model_validate({"timezone": None})
    with pytest.raises(ValidationError):
        BrandUpdate.model_validate({"auto_approve_enabled": None})
    assert BrandUpdate().model_dump(exclude_unset=True) == {}


# ---------------------------------------------------------------- audiences
def test_audience_sophistication_range_and_nulls() -> None:
    AudienceCreate(name="CTOs", sophistication_level=5)
    with pytest.raises(ValidationError):
        AudienceCreate(name="CTOs", sophistication_level=6)
    with pytest.raises(ValidationError):
        AudienceUpdate.model_validate({"pains": None})
    assert AudienceUpdate(pains=[" a ", "A"]).pains == ["a"]


# ---------------------------------------------------------------- offers
def test_offer_currency_normalised_and_validated() -> None:
    o = OfferCreate(name="Audit", price=Decimal("12.50"), currency=" usd ")
    assert o.currency == "USD"
    for bad in ("US", "USDX", "U$D", "12A"):
        with pytest.raises(ValidationError):
            OfferCreate(name="Audit", price=Decimal("1"), currency=bad)


def test_offer_price_rules() -> None:
    with pytest.raises(ValidationError):
        OfferCreate(name="Audit", price=Decimal("-1"), currency="USD")
    with pytest.raises(ValidationError):
        OfferCreate(name="Audit", price=Decimal("1.005"), currency="USD")
    with pytest.raises(ValidationError, match="together"):
        OfferCreate(name="Audit", price=Decimal("10"))
    with pytest.raises(ValidationError, match="together"):
        OfferCreate(name="Audit", currency="EUR")
    assert OfferCreate(name="Free", price=Decimal("0"), currency="EUR").price == Decimal("0")


@pytest.mark.parametrize(
    "url", ["ftp://example.com/x", "javascript:alert(1)", "not a url", "file:///etc/passwd"]
)
def test_offer_funnel_url_must_be_http(url: str) -> None:
    with pytest.raises(ValidationError):
        OfferCreate(name="Audit", funnel_destination_url=url)
    with pytest.raises(ValidationError):
        OfferUpdate(funnel_destination_url=url)


def test_offer_funnel_url_accepts_http_and_https() -> None:
    for url in ("https://example.com/buy?x=1", "http://example.com"):
        assert OfferCreate(name="A", funnel_destination_url=url).funnel_destination_url


def test_offer_update_allows_unlinking_audience_but_not_null_lists() -> None:
    assert OfferUpdate.model_validate({"audience_id": None}).model_dump(exclude_unset=True) == {
        "audience_id": None
    }
    with pytest.raises(ValidationError):
        OfferUpdate.model_validate({"proof": None})
