"""Structural tests for the state machines (docs/STATE_MACHINES.md)."""

from __future__ import annotations

import ast
import re
import uuid
from pathlib import Path

import pytest

from omnigrowth.core.enums import (
    ActorType,
    CampaignStatus,
    ContentAssetStatus,
    ExperimentStatus,
    PublicationJobStatus,
    PublicationStatus,
    UserRole,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.state_machines import (
    ALL_MACHINES,
    CAMPAIGN,
    CONTENT_ASSET,
    EXPERIMENT,
    PUBLICATION,
    PUBLICATION_JOB,
    Actor,
)
from omnigrowth.core.state_machines.apply import build_audit_row

OWNER = Actor(ActorType.HUMAN, "u-owner", UserRole.OWNER)
EDITOR = Actor(ActorType.HUMAN, "u-editor", UserRole.EDITOR)
VIEWER = Actor(ActorType.HUMAN, "u-viewer", UserRole.VIEWER)
SYSTEM = Actor(ActorType.SYSTEM, "system")
SCHED = Actor(ActorType.SCHEDULER, "scheduler-1")
AGENT = Actor(ActorType.AGENT, "linkedin_writer")

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("machine", ALL_MACHINES, ids=lambda m: m.name)
def test_every_state_is_reachable(machine: object) -> None:
    from omnigrowth.core.state_machines.machine import StateMachine

    assert isinstance(machine, StateMachine)
    assert {s.value for s in machine.states} == machine.reachable()


def test_section_11_default_chain_is_walkable() -> None:
    steps = [
        ("GENERATED", "generated", AGENT),
        ("QA_PASSED", "qa_passed", SYSTEM),
        ("AWAITING_APPROVAL", "submit_for_approval", SYSTEM),
        ("APPROVED", "APPROVE", EDITOR),
        ("SCHEDULED", "SCHEDULE", EDITOR),
        ("PUBLISHED", "publication_published", SYSTEM),
    ]
    state = CONTENT_ASSET.initial("create", EDITOR).target
    assert state == ContentAssetStatus.DRAFT
    for expected, trigger, actor in steps:
        state = CONTENT_ASSET.resolve(state, trigger, actor).target
        assert state == expected


def test_approval_cannot_be_skipped() -> None:
    for state in ("DRAFT", "GENERATED", "QA_PASSED", "QA_FAILED"):
        assert "SCHEDULE" not in CONTENT_ASSET.triggers_from(state)
        assert "PUBLISH_NOW" not in CONTENT_ASSET.triggers_from(state)
        assert "APPROVE" not in CONTENT_ASSET.triggers_from(state)


def test_viewer_can_never_transition() -> None:
    for machine in ALL_MACHINES:
        for t in machine.transitions:
            assert not t.allows(VIEWER), f"{machine.name}.{t.id} allows VIEWER"


def test_agent_only_records_its_own_output() -> None:
    agent_triggers = {
        (m.name, t.trigger) for m in ALL_MACHINES for t in m.transitions if t.allows(AGENT)
    }
    assert agent_triggers <= {("content_asset", "generated"), ("job", "enqueue")}


def test_unknown_transition_is_invalid_state_transition() -> None:
    with pytest.raises(OmniGrowthError) as exc:
        CONTENT_ASSET.resolve("DRAFT", "APPROVE", EDITOR)
    assert exc.value.code is ErrorCode.INVALID_STATE_TRANSITION


def test_wrong_role_is_forbidden() -> None:
    with pytest.raises(OmniGrowthError) as exc:
        CAMPAIGN.resolve("ACTIVE", "archive", EDITOR)  # C12 is OWNER-only
    assert exc.value.code is ErrorCode.FORBIDDEN
    assert CAMPAIGN.resolve("ACTIVE", "archive", OWNER).target == CampaignStatus.ARCHIVED


def test_uncertain_publication_is_never_auto_retried() -> None:
    triggers = PUBLICATION_JOB.triggers_from("UNCERTAIN")
    assert "claim" not in triggers
    targets = {t.target for t in PUBLICATION_JOB.transitions if "UNCERTAIN" in t.sources}
    assert targets == {PublicationJobStatus.SUCCEEDED, PublicationJobStatus.FAILED}
    # Only an OWNER may confirm "not published" (J13).
    with pytest.raises(OmniGrowthError):
        PUBLICATION_JOB.resolve("UNCERTAIN", "human_confirmed_not_published", EDITOR)
    with pytest.raises(OmniGrowthError):
        PUBLICATION_JOB.resolve("UNCERTAIN", "human_confirmed_not_published", SYSTEM)
    assert (
        PUBLICATION_JOB.resolve("UNCERTAIN", "human_confirmed_not_published", OWNER).target
        == PublicationJobStatus.FAILED
    )


def test_failed_and_cancelled_jobs_are_terminal() -> None:
    for state in ("FAILED", "CANCELLED", "SUCCEEDED"):
        assert PUBLICATION_JOB.triggers_from(state) == set()


def test_lease_expiry_depends_on_dispatch() -> None:
    assert (
        PUBLICATION_JOB.resolve("CLAIMED", "lease_expired_not_dispatched", SCHED).target
        == PublicationJobStatus.PENDING
    )
    assert (
        PUBLICATION_JOB.resolve("CLAIMED", "lease_expired_dispatched", SCHED).target
        == PublicationJobStatus.UNCERTAIN
    )


def test_publication_manual_path() -> None:
    assert (
        PUBLICATION.resolve("PUBLISHING", "manual_export_ready", SCHED).target
        == PublicationStatus.MANUAL_PENDING
    )
    assert (
        PUBLICATION.resolve("MANUAL_PENDING", "mark_published", EDITOR).target
        == PublicationStatus.PUBLISHED
    )


def test_experiment_has_no_path_to_concluded_except_evaluation() -> None:
    into_concluded = [t for t in EXPERIMENT.transitions if t.target == ExperimentStatus.CONCLUDED]
    assert [t.trigger for t in into_concluded] == ["evaluate_concluded"]
    assert into_concluded[0].sources == {"COLLECTING"}


def test_human_actor_requires_role() -> None:
    with pytest.raises(OmniGrowthError):
        Actor(ActorType.HUMAN, "u1")


def test_audit_row_masks_reason_secrets() -> None:
    t = CAMPAIGN.resolve("DRAFT", "start_planning", OWNER)
    row = build_audit_row(
        table_name="campaigns",
        entity_id=uuid.uuid4(),
        transition=t,
        from_state="DRAFT",
        actor=OWNER,
        correlation_id="c1",
        brand_id=None,
        reason="retry with api_key=sk-abcdefghijklmnopqrstuvwxyz",
    )
    assert row["action"] == "status.transition"
    assert row["metadata"]["from"] == "DRAFT"
    assert row["metadata"]["to"] == "PLANNING"
    assert "sk-abcdefghijklmnop" not in str(row["metadata"])


# ---------------------------------------------------------------- AGENTS.md §4 enforcement
def _orm_class_names() -> set[str]:
    from omnigrowth.models import Base

    return {mapper.class_.__name__ for mapper in Base.registry.mappers}


_RAW_SQL_STATUS = re.compile(r"\bSET\s+(?:[a-z_]+\s*=\s*[^,]+,\s*)*status\s*=", re.IGNORECASE)


def _is_status_key(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == "status"


def _status_writes(path: Path, orm_classes: set[str]) -> list[int]:
    """Lines that write a ``status`` column outside the state-machine module:
    ``x.status = …``, ``setattr(x, "status", …)``, ``status=`` / ``{"status": …}`` /
    ``**{"status": …}`` passed to update/values/insert, ORM constructors with ``status=``,
    and raw SQL strings containing ``SET … status =``. DTOs such as CheckResult are fine."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: set[int] = set()
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        hits.update(
            node.lineno
            for target in targets
            if isinstance(target, ast.Attribute) and target.attr == "status"
        )
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _RAW_SQL_STATUS.search(node.value)
        ):
            hits.add(node.lineno)
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id == "setattr"
            and len(node.args) >= 2
            and _is_status_key(node.args[1])
        ):
            hits.add(node.lineno)
        kw_status = any(kw.arg == "status" for kw in node.keywords)
        dict_status = any(
            isinstance(arg, ast.Dict) and any(_is_status_key(k) for k in arg.keys)
            for arg in [*node.args, *(kw.value for kw in node.keywords if kw.arg is None)]
        )
        # `**row` with a variable cannot be verified statically: treat as a possible write.
        opaque_splat = any(
            kw.arg is None and not isinstance(kw.value, ast.Dict) for kw in node.keywords
        )
        is_write_call = isinstance(func, ast.Attribute) and func.attr in {
            "values",
            "update",
            "insert",
        }
        if is_write_call and (kw_status or dict_status or opaque_splat):
            hits.add(node.lineno)
        if isinstance(func, ast.Name) and func.id in orm_classes and (kw_status or dict_status):
            hits.add(node.lineno)
    return sorted(hits)


def test_no_status_writes_outside_state_machines() -> None:
    allowed = REPO / "omnigrowth" / "core" / "state_machines"
    # Documented exceptions (GLOSSARY section 4a): append-only outcome logs written once.
    allowlist = {REPO / "omnigrowth" / "storage" / "model_runs.py"}
    orm_classes = _orm_class_names()
    offenders: dict[str, list[int]] = {}
    for path in (REPO / "omnigrowth").rglob("*.py"):
        if allowed in path.parents or path in allowlist:
            continue
        lines = _status_writes(path, orm_classes)
        if lines:
            offenders[str(path.relative_to(REPO))] = lines
    assert offenders == {}, f"direct status writes (AGENTS.md §4): {offenders}"


def test_status_write_detector_catches_violations(tmp_path: Path) -> None:
    sample = tmp_path / "bad.py"
    sample.write_text(
        "asset.status = 'APPROVED'\n"
        "stmt = update(t).values(status='X')\n"
        "obj = Campaign(status='DRAFT')\n"
        "setattr(asset, 'status', 'X')\n"
        "stmt = update(t).values({'status': 'X'})\n"
        "stmt = update(t).values(**{'status': 'X'})\n"
        "stmt = insert(t).values(**row)\n"
        "sql = 'UPDATE campaigns SET name = 1, status = 2'\n"
        "ok = CheckResult(status='OK')\n"
        "fine = 'SELECT status FROM t'\n",
        encoding="utf-8",
    )
    assert _status_writes(sample, {"Campaign"}) == [1, 2, 3, 4, 5, 6, 7, 8]
