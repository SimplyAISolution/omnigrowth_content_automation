"""The four domain machines + the jobs helper, transcribed from docs/STATE_MACHINES.md.

Transition ids (C*, CA*, J*, P*, E*) match the document so audit rows and tests can cite them.
Where the document gives one trigger two outcomes chosen by a guard (C10/C11, E4/E5), the
triggers are split so the structural table stays unambiguous; the service picks the trigger
after evaluating the guard.
"""

from __future__ import annotations

from omnigrowth.core.enums import (
    CampaignStatus as C,
)
from omnigrowth.core.enums import (
    ContentAssetStatus as A,
)
from omnigrowth.core.enums import (
    ExperimentStatus as E,
)
from omnigrowth.core.enums import JobStatus, SourceAssetStatus
from omnigrowth.core.enums import (
    PublicationJobStatus as J,
)
from omnigrowth.core.enums import (
    PublicationStatus as P,
)
from omnigrowth.core.state_machines.machine import (
    AGENT,
    OWNER_EDITOR,
    OWNER_ONLY,
    SCHEDULER,
    SYSTEM,
    StateMachine,
    tr,
)

CAMPAIGN = StateMachine(
    name="campaign",
    states=C,
    terminal=frozenset({C.ARCHIVED.value}),
    transitions=(
        tr("C1", None, C.DRAFT, "create", OWNER_EDITOR),
        tr("C2", C.DRAFT, C.PLANNING, "start_planning", OWNER_EDITOR),
        tr("C3", C.PLANNING, C.READY_FOR_REVIEW, "planning_succeeded", SYSTEM),
        tr("C4", C.PLANNING, C.FAILED, "planning_failed", SYSTEM),
        tr("C5", C.FAILED, C.PLANNING, "retry_planning", OWNER_EDITOR),
        tr("C6", C.READY_FOR_REVIEW, C.PLANNING, "replan", OWNER_EDITOR),
        tr("C7", C.READY_FOR_REVIEW, C.ACTIVE, "accept_plan", OWNER_EDITOR),
        tr("C8", C.ACTIVE, C.PAUSED, "pause", OWNER_EDITOR),
        tr("C9", C.PAUSED, C.ACTIVE, "resume", OWNER_EDITOR),
        tr("C10", C.ACTIVE, C.COMPLETED, "complete_by_time", SCHEDULER),
        tr("C11", (C.ACTIVE, C.PAUSED), C.COMPLETED, "complete", OWNER_EDITOR),
        tr(
            "C12",
            (C.DRAFT, C.READY_FOR_REVIEW, C.ACTIVE, C.PAUSED, C.COMPLETED, C.FAILED),
            C.ARCHIVED,
            "archive",
            OWNER_ONLY,
        ),
    ),
)

_EDITABLE = (
    A.GENERATED,
    A.QA_PASSED,
    A.QA_FAILED,
    A.AWAITING_APPROVAL,
    A.REVISION_REQUESTED,
    A.APPROVED,
    A.REJECTED,
)

CONTENT_ASSET = StateMachine(
    name="content_asset",
    states=A,
    terminal=frozenset({A.PUBLISHED.value, A.ARCHIVED.value}),
    transitions=(
        tr("CA1", None, A.DRAFT, "create", OWNER_EDITOR, SYSTEM),
        tr("CA2", A.DRAFT, A.GENERATED, "generated", AGENT, SYSTEM),
        tr("CA3", A.DRAFT, A.GENERATED, "submit", OWNER_EDITOR),
        tr("CA4", A.GENERATED, A.QA_PASSED, "qa_passed", SYSTEM),
        tr("CA5", A.GENERATED, A.QA_FAILED, "qa_failed", SYSTEM),
        tr("CA6", A.QA_PASSED, A.AWAITING_APPROVAL, "submit_for_approval", SYSTEM),
        # SYSTEM only via auto-approve (guard `auto_approve_allowed`; never sufficient for LIVE).
        tr("CA7", A.AWAITING_APPROVAL, A.APPROVED, "APPROVE", OWNER_EDITOR, SYSTEM),
        tr("CA8", A.AWAITING_APPROVAL, A.REJECTED, "REJECT", OWNER_EDITOR),
        tr("CA9", A.AWAITING_APPROVAL, A.REVISION_REQUESTED, "REQUEST_REVISION", OWNER_EDITOR),
        tr("CA10", A.APPROVED, A.SCHEDULED, "SCHEDULE", OWNER_EDITOR, SYSTEM),
        tr("CA11", A.APPROVED, A.SCHEDULED, "PUBLISH_NOW", OWNER_EDITOR),
        tr("CA12", A.SCHEDULED, A.SCHEDULED, "SCHEDULE", OWNER_EDITOR),
        tr("CA13", A.SCHEDULED, A.APPROVED, "unschedule", OWNER_EDITOR, SYSTEM),
        tr("CA14", A.SCHEDULED, A.APPROVED, "publication_failed", SYSTEM),
        tr("CA15", A.SCHEDULED, A.PUBLISHED, "publication_published", SYSTEM),
        tr("CA16", _EDITABLE, A.GENERATED, "EDIT", OWNER_EDITOR),
        tr("CA17", A.SCHEDULED, A.GENERATED, "EDIT", OWNER_EDITOR),
        tr("CA18", _EDITABLE, A.DRAFT, "REGENERATE", OWNER_EDITOR, SYSTEM),
        tr("CA19", A.SCHEDULED, A.DRAFT, "REGENERATE", OWNER_EDITOR),
        tr("CA20", (A.DRAFT, *_EDITABLE), A.ARCHIVED, "archive", OWNER_EDITOR, SYSTEM),
        tr("CA21", A.SCHEDULED, A.ARCHIVED, "archive", OWNER_EDITOR, SYSTEM),
    ),
)

PUBLICATION_JOB = StateMachine(
    name="publication_job",
    states=J,
    terminal=frozenset({J.SUCCEEDED.value, J.FAILED.value, J.CANCELLED.value}),
    transitions=(
        # J1 inherits the CA10/CA11 actor.
        tr("J1", None, J.PENDING, "enqueue", OWNER_EDITOR, SYSTEM),
        tr("J2", J.PENDING, J.CLAIMED, "claim", SCHEDULER),
        tr("J3", J.RETRY_WAIT, J.CLAIMED, "claim", SCHEDULER),
        tr("J4", J.CLAIMED, J.CLAIMED, "dispatch_start", SCHEDULER),
        tr("J5", J.CLAIMED, J.SUCCEEDED, "publish_succeeded", SCHEDULER),
        tr("J6", J.CLAIMED, J.FAILED, "publish_failed", SCHEDULER),
        tr("J7", J.CLAIMED, J.RETRY_WAIT, "publish_retryable", SCHEDULER),
        tr("J7b", J.CLAIMED, J.RETRY_WAIT, "blocked_by_uncertain", SCHEDULER),
        tr("J8", J.CLAIMED, J.UNCERTAIN, "publish_uncertain", SCHEDULER),
        tr("J9", (J.PENDING, J.RETRY_WAIT), J.CANCELLED, "cancel", OWNER_EDITOR, SYSTEM),
        tr("J10", J.CLAIMED, J.PENDING, "lease_expired_not_dispatched", SCHEDULER),
        tr("J11", J.CLAIMED, J.UNCERTAIN, "lease_expired_dispatched", SCHEDULER),
        tr("J12", J.UNCERTAIN, J.SUCCEEDED, "reconciled_found", SYSTEM),
        tr("J12b", J.UNCERTAIN, J.SUCCEEDED, "human_confirmed_published", OWNER_EDITOR),
        tr("J13", J.UNCERTAIN, J.FAILED, "human_confirmed_not_published", OWNER_ONLY),
    ),
)

PUBLICATION = StateMachine(
    name="publication",
    states=P,
    terminal=frozenset({P.FAILED.value, P.DELETED.value}),
    transitions=(
        tr("P1", None, P.PUBLISHING, "dispatch_start", SCHEDULER),
        tr("P2", P.PUBLISHING, P.PUBLISHED, "publish_succeeded", SCHEDULER),
        tr("P3", P.PUBLISHING, P.FAILED, "publish_failed", SCHEDULER),
        tr("P3b", P.PUBLISHING, P.FAILED, "cancel", OWNER_EDITOR, SYSTEM),
        tr("P4", P.PUBLISHING, P.MANUAL_PENDING, "manual_export_ready", SCHEDULER),
        tr("P5", P.PUBLICATION_UNCERTAIN, P.PUBLISHED, "reconciled_found", SYSTEM),
        tr(
            "P5b",
            P.PUBLICATION_UNCERTAIN,
            P.PUBLISHED,
            "human_confirmed_published",
            OWNER_EDITOR,
        ),
        tr("P6", P.PUBLISHING, P.PUBLICATION_UNCERTAIN, "publish_uncertain", SCHEDULER),
        tr(
            "P6b",
            P.PUBLISHING,
            P.PUBLICATION_UNCERTAIN,
            "lease_expired_dispatched",
            SCHEDULER,
        ),
        tr("P7", P.MANUAL_PENDING, P.PUBLISHED, "mark_published", OWNER_EDITOR),
        tr(
            "P7b",
            P.PUBLICATION_UNCERTAIN,
            P.FAILED,
            "human_confirmed_not_published",
            OWNER_ONLY,
        ),
        tr("P8", P.MANUAL_PENDING, P.FAILED, "mark_not_published", OWNER_EDITOR),
        tr("P9", P.PUBLISHED, P.DELETED, "deleted", OWNER_ONLY, SYSTEM),
    ),
)

EXPERIMENT = StateMachine(
    name="experiment",
    states=E,
    terminal=frozenset({E.CONCLUDED.value, E.INCONCLUSIVE.value, E.CANCELLED.value}),
    transitions=(
        tr("E1", None, E.DRAFT, "create", OWNER_EDITOR),
        tr("E2", E.DRAFT, E.RUNNING, "start", OWNER_EDITOR),
        tr("E3", E.RUNNING, E.COLLECTING, "all_variants_published", SYSTEM),
        # E4/E5: the evaluator decides; a winner requires min sample + confidence guards.
        tr(
            "E4",
            E.COLLECTING,
            E.CONCLUDED,
            "evaluate_concluded",
            SYSTEM,
            SCHEDULER,
            OWNER_EDITOR,
        ),
        tr(
            "E5",
            E.COLLECTING,
            E.INCONCLUSIVE,
            "evaluate_inconclusive",
            SYSTEM,
            SCHEDULER,
            OWNER_EDITOR,
        ),
        tr("E6", E.RUNNING, E.INCONCLUSIVE, "window_closed", SCHEDULER),
        tr("E7", (E.DRAFT, E.RUNNING, E.COLLECTING), E.CANCELLED, "cancel", OWNER_EDITOR, SYSTEM),
    ),
)

# Small linear machine for `jobs.status` (DATA_MODEL §6a.1, GLOSSARY §4a).
JOB = StateMachine(
    name="job",
    states=JobStatus,
    terminal=frozenset(
        {JobStatus.SUCCEEDED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value}
    ),
    transitions=(
        tr("JB1", None, JobStatus.QUEUED, "enqueue", OWNER_EDITOR, SYSTEM, SCHEDULER, AGENT),
        tr("JB2", JobStatus.QUEUED, JobStatus.RUNNING, "start", SYSTEM),
        tr("JB3", JobStatus.RUNNING, JobStatus.SUCCEEDED, "succeed", SYSTEM),
        tr("JB4", JobStatus.RUNNING, JobStatus.FAILED, "fail", SYSTEM),
        tr("JB5", JobStatus.QUEUED, JobStatus.CANCELLED, "cancel", OWNER_EDITOR, SYSTEM),
        # A worker that died or was killed (RQ job_timeout) leaves RUNNING behind; the
        # scheduler's reaper fails it so nothing stays RUNNING forever.
        tr("JB6", JobStatus.RUNNING, JobStatus.FAILED, "reap", SCHEDULER),
    ),
)


# `source_assets.status` (DATA_MODEL §4.1; GLOSSARY §4a SourceAssetStatus). Ingestion writes it
# only through this machine: created PENDING, then exactly one terminal outcome.
SOURCE_ASSET = StateMachine(
    name="source_asset",
    states=SourceAssetStatus,
    terminal=frozenset(
        {
            SourceAssetStatus.NORMALIZED.value,
            SourceAssetStatus.FAILED.value,
            SourceAssetStatus.UNSUPPORTED.value,
        }
    ),
    transitions=(
        tr("SA1", None, SourceAssetStatus.PENDING, "create", OWNER_EDITOR, SYSTEM),
        tr("SA2", SourceAssetStatus.PENDING, SourceAssetStatus.NORMALIZED, "normalized", SYSTEM),
        tr("SA3", SourceAssetStatus.PENDING, SourceAssetStatus.FAILED, "failed", SYSTEM),
        tr("SA4", SourceAssetStatus.PENDING, SourceAssetStatus.UNSUPPORTED, "unsupported", SYSTEM),
        # Transient failures (network, queue down) must be retryable: (brand, checksum) is
        # unique, so the same content can never be re-inserted as a new row.
        tr("SA5", SourceAssetStatus.FAILED, SourceAssetStatus.PENDING, "retry", OWNER_EDITOR),
    ),
)

ALL_MACHINES = (
    CAMPAIGN,
    CONTENT_ASSET,
    PUBLICATION_JOB,
    PUBLICATION,
    EXPERIMENT,
    JOB,
    SOURCE_ASSET,
)
