"""Static checks of ORM metadata against docs/DATA_MODEL.md (no database needed)."""

from __future__ import annotations

import pytest
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Index, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from omnigrowth.models import Base

# DATA_MODEL §3-§6a (webhook_endpoints / webhook_subscriptions deferred to Phase 12).
EXPECTED_TABLES = frozenset(
    {
        # spec §31
        "users",
        "brands",
        "brand_profiles",
        "audiences",
        "offers",
        "campaigns",
        "source_assets",
        "content_ideas",
        "content_assets",
        "content_versions",
        "publication_jobs",
        "publications",
        "analytics_snapshots",
        "experiments",
        "experiment_variants",
        "agent_runs",
        "model_runs",
        "prompt_versions",
        "platform_accounts",
        "platform_credentials",
        "audit_events",
        # GLOSSARY §2 additions
        "api_tokens",
        "research_packets",
        "research_claims",
        "campaign_strategies",
        "plan_items",
        "qa_results",
        "approval_actions",
        "embeddings",
        "derived_metric_definitions",
        "recommendations",
        # GLOSSARY §4a additions
        "campaign_sources",
        "jobs",
        "idempotency_keys",
        "app_settings",
    }
)

ANALYTICS_METRIC_COLUMNS = (
    "impressions",
    "views",
    "reach",
    "likes",
    "comments",
    "shares",
    "saves",
    "clicks",
    "profile_visits",
    "followers_gained",
    "watch_time_seconds",
    "average_view_duration_seconds",
    "ctr",
    "conversion_count",
    "revenue",
    "revenue_currency",
)

# GLOSSARY §4 (state enums) + §4a status enums.
EXPECTED_STATUS_ENUMS = {
    "campaigns": "campaign_status",
    "content_assets": "content_asset_status",
    "publication_jobs": "publication_job_status",
    "publications": "publication_status",
    "experiments": "experiment_status",
    "jobs": "job_status",
    "source_assets": "source_asset_status",
    "content_ideas": "content_idea_status",
    "agent_runs": "agent_run_status",
    "model_runs": "model_run_status",
    "recommendations": "recommendation_status",
}


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _index(table: str, name: str) -> Index:
    matches = [ix for ix in _table(table).indexes if ix.name == name]
    assert matches, f"index {name} missing on {table}"
    return matches[0]


def test_every_expected_table_is_registered() -> None:
    missing = EXPECTED_TABLES - set(Base.metadata.tables)
    assert not missing, f"missing tables: {sorted(missing)}"


def test_no_unexpected_tables() -> None:
    extra = set(Base.metadata.tables) - EXPECTED_TABLES
    assert not extra, f"unexpected tables: {sorted(extra)}"


@pytest.mark.parametrize("column", ANALYTICS_METRIC_COLUMNS)
def test_analytics_metrics_nullable_without_default(column: str) -> None:
    col = _table("analytics_snapshots").c[column]
    assert col.nullable is True
    assert col.default is None
    assert col.server_default is None


def test_publication_jobs_partial_unique_idempotency_index() -> None:
    ix = _index("publication_jobs", "publication_jobs_idempotency_live_uq")
    assert ix.unique is True
    assert [c.name for c in ix.columns] == ["idempotency_key"]
    where = ix.dialect_options["postgresql"]["where"]
    assert where is not None
    compiled = str(CreateIndex(ix).compile(dialect=postgresql.dialect()))
    assert "WHERE status IN ('PENDING','CLAIMED','RETRY_WAIT','SUCCEEDED','UNCERTAIN')" in compiled
    # FAILED / CANCELLED rows are history and must not block a manual retry.
    assert "'FAILED'" not in compiled
    assert "'CANCELLED'" not in compiled


def test_status_columns_use_named_native_enums() -> None:
    tables_with_status = {t.name for t in Base.metadata.tables.values() if "status" in t.c}
    assert tables_with_status == set(EXPECTED_STATUS_ENUMS)
    for table, enum_name in EXPECTED_STATUS_ENUMS.items():
        col_type = _table(table).c["status"].type
        assert isinstance(col_type, SAEnum), table
        assert col_type.native_enum is True, table
        assert col_type.name == enum_name, table
        assert _table(table).c["status"].nullable is False, table


def test_content_version_pointer_fks_break_cycle_with_use_alter() -> None:
    fks = {fk.name: fk for fk in _table("content_assets").foreign_key_constraints}
    for name in ("fk_content_assets_current_version", "fk_content_assets_approved_version"):
        assert fks[name].use_alter is True
        assert fks[name].referred_table.name == "content_versions"


@pytest.mark.parametrize("table_name", sorted(EXPECTED_TABLES))
def test_create_table_compiles_for_postgresql(table_name: str) -> None:
    dialect = postgresql.dialect()
    table = _table(table_name)
    ddl = str(CreateTable(table).compile(dialect=dialect))
    assert f"CREATE TABLE {table_name}" in ddl
    for ix in table.indexes:
        str(CreateIndex(ix).compile(dialect=dialect))
