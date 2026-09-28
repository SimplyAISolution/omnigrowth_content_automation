"""live connectors: credentials, sandbox accounts, evidence class, not-published event (Phase 13)

- platform_accounts.sandbox: LIVE posts from a sandbox account are SANDBOX evidence (A17).
- platform_credentials: non-secret fingerprint + who stored / revoked it.
- publications.evidence_class (SIMULATED / SANDBOX / REAL), backfilled from publish_mode:
  MOCK -> SIMULATED; anything else could only have been produced before Phase 13 by a
  person (MANUAL), which is REAL. Fixed at dispatch start from then on.
- publication.not_published_confirmed: emitted when a person confirms an uncertain post did
  not go out (job UNCERTAIN -> FAILED, J13) or a manual export was not posted (publication
  MANUAL_PENDING -> FAILED, P8), instead of a plain publication.failed. Nothing retries.
- Publication events now carry evidence_class.

Downgrade removes publication.not_published_confirmed events and their deliveries, strips the
type from subscriptions, and DELETES subscriptions whose only event type it was (their
configuration is lost; recreate them after upgrading again).

Revision ID: eb1070ccaa98
Revises: c12a0d5e7f31
Create Date: 2026-09-27 22:44:08.889801
"""

# ruff: noqa: S608  (trigger SQL assembled from static fragments only; no input)
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "eb1070ccaa98"
down_revision: str | None = "c12a0d5e7f31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_EVENTS = (
    "'content.approval_requested', 'publication.failed', 'publication.uncertain', "
    "'integration.ping'"
)
NEW_EVENTS = (
    "'content.approval_requested', 'publication.failed', 'publication.uncertain', "
    "'publication.not_published_confirmed', 'integration.ping'"
)


# Shared publication payload (ids, labels, mode and evidence; never text or credentials).
def _job_data(with_evidence: bool) -> str:
    evidence = (
        "'evidence_class', (SELECT p.evidence_class::text FROM publications p "
        "WHERE p.publication_job_id = NEW.id),"
        if with_evidence
        else ""
    )
    return f"""
    jsonb_build_object(
        'publication_job_id', NEW.id,
        'content_asset_id', NEW.content_asset_id,
        'content_version_id', NEW.content_version_id,
        'platform_account_id', NEW.platform_account_id,
        'platform', (SELECT a.platform::text FROM platform_accounts a
                     WHERE a.id = NEW.platform_account_id),
        'status', NEW.status::text,
        'attempt_count', NEW.attempt_count,
        'last_error_code', NEW.last_error_code::text,
        'publish_mode', NEW.publish_mode::text,
        {evidence}
        -- Simulated = the mock publisher handled it; null = mode unknown.
        'simulated', CASE WHEN NEW.publish_mode IS NULL THEN NULL
                          ELSE NEW.publish_mode::text = 'MOCK' END)
"""


_JOB_DATA = _job_data(with_evidence=True)

JOB_TRIGGER_V2 = f"""
CREATE OR REPLACE FUNCTION og_emit_publication_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.status = 'UNCERTAIN' AND NEW.status = 'FAILED' THEN
        -- J13: only a person moves UNCERTAIN -> FAILED (confirmed not published).
        INSERT INTO integration_events (event_type, brand_id, subject_type, subject_id, data)
        VALUES ('publication.not_published_confirmed', NEW.brand_id, 'publication_jobs', NEW.id,
                {_JOB_DATA} || jsonb_build_object('reconciliation', 'UNCERTAIN_CONFIRMED_NOT_PUBLISHED',
                                                  'will_retry', false));
    ELSIF NEW.status IN ('FAILED', 'UNCERTAIN')
       AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM NEW.status) THEN
        INSERT INTO integration_events (event_type, brand_id, subject_type, subject_id, data)
        VALUES (
            CASE NEW.status::text WHEN 'FAILED' THEN 'publication.failed'
                                  ELSE 'publication.uncertain' END,
            NEW.brand_id, 'publication_jobs', NEW.id, {_JOB_DATA});
    END IF;
    RETURN NEW;
END $$;
"""

JOB_TRIGGER_V1 = f"""
CREATE OR REPLACE FUNCTION og_emit_publication_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IN ('FAILED', 'UNCERTAIN')
       AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM NEW.status) THEN
        INSERT INTO integration_events (event_type, brand_id, subject_type, subject_id, data)
        VALUES (
            CASE NEW.status::text WHEN 'FAILED' THEN 'publication.failed'
                                  ELSE 'publication.uncertain' END,
            NEW.brand_id, 'publication_jobs', NEW.id, {_job_data(with_evidence=False)});
    END IF;
    RETURN NEW;
END $$;
"""

MANUAL_TRIGGER = """
CREATE FUNCTION og_emit_manual_not_published() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'MANUAL_PENDING' AND NEW.status = 'FAILED' THEN
        -- P8: a person says the manual export was not posted.
        INSERT INTO integration_events (event_type, brand_id, subject_type, subject_id, data)
        VALUES ('publication.not_published_confirmed', NEW.brand_id, 'publications', NEW.id,
            jsonb_build_object(
                'publication_id', NEW.id,
                'publication_job_id', NEW.publication_job_id,
                'content_asset_id', NEW.content_asset_id,
                'content_version_id', NEW.content_version_id,
                'platform_account_id', NEW.platform_account_id,
                'platform', NEW.platform::text,
                'status', NEW.status::text,
                'publish_mode', NEW.publish_mode::text,
                'evidence_class', NEW.evidence_class::text,
                'simulated', NEW.publish_mode::text = 'MOCK',
                'reconciliation', 'MANUAL_EXPORT_NOT_PUBLISHED',
                'will_retry', false));
    END IF;
    RETURN NEW;
END $$;
"""


def _replace_checks(events: str) -> None:
    op.drop_constraint(
        op.f("ck_integration_events_event_type_known"), "integration_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_integration_events_event_type_known"),
        "integration_events",
        f"event_type IN ({events})",
    )
    op.drop_constraint(
        op.f("ck_webhook_subscriptions_event_types_known"), "webhook_subscriptions", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_webhook_subscriptions_event_types_known"),
        "webhook_subscriptions",
        f"event_types <@ ARRAY[{events}]::text[]",
    )


def upgrade() -> None:
    evidence = postgresql.ENUM("SIMULATED", "SANDBOX", "REAL", name="evidence_class")
    evidence.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "platform_accounts",
        sa.Column("sandbox", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("platform_credentials", sa.Column("fingerprint", sa.Text(), nullable=True))
    op.add_column("platform_credentials", sa.Column("created_by", sa.Text(), nullable=True))
    op.add_column("platform_credentials", sa.Column("revoked_by", sa.Text(), nullable=True))
    op.add_column(
        "publications",
        sa.Column(
            "evidence_class",
            postgresql.ENUM(name="evidence_class", create_type=False),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE publications SET evidence_class = CASE WHEN publish_mode = 'MOCK' "
        "THEN 'SIMULATED'::evidence_class ELSE 'REAL'::evidence_class END"
    )
    op.alter_column("publications", "evidence_class", nullable=False)

    _replace_checks(NEW_EVENTS)
    op.execute(JOB_TRIGGER_V2)
    op.execute(MANUAL_TRIGGER)
    op.execute(
        "CREATE TRIGGER publications_emit_not_published AFTER UPDATE OF status "
        "ON publications FOR EACH ROW EXECUTE FUNCTION og_emit_manual_not_published()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS publications_emit_not_published ON publications")
    op.execute("DROP FUNCTION IF EXISTS og_emit_manual_not_published()")
    op.execute(JOB_TRIGGER_V1)
    # Events of the new type cannot satisfy the old CHECK: they are removed with their
    # deliveries (operational rows; the audit log keeps the human decision itself).
    op.execute(
        "DELETE FROM webhook_deliveries WHERE event_id IN (SELECT id FROM integration_events "
        "WHERE event_type = 'publication.not_published_confirmed')"
    )
    op.execute(
        "DELETE FROM integration_events WHERE event_type = 'publication.not_published_confirmed'"
    )
    op.execute(
        "UPDATE webhook_subscriptions SET event_types = "
        "array_remove(event_types, 'publication.not_published_confirmed')"
    )
    op.execute(
        "DELETE FROM webhook_deliveries WHERE subscription_id IN (SELECT id FROM "
        "webhook_subscriptions WHERE cardinality(event_types) = 0)"
    )
    op.execute("DELETE FROM webhook_subscriptions WHERE cardinality(event_types) = 0")
    _replace_checks(OLD_EVENTS)
    op.drop_column("publications", "evidence_class")
    op.drop_column("platform_credentials", "revoked_by")
    op.drop_column("platform_credentials", "created_by")
    op.drop_column("platform_credentials", "fingerprint")
    op.drop_column("platform_accounts", "sandbox")
    postgresql.ENUM(name="evidence_class").drop(op.get_bind(), checkfirst=True)
