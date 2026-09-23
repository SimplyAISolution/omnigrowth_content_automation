"""The code's transition tables must match docs/STATE_MACHINES.md row for row.

Rows are parsed from the document (id | from | to | trigger | guard | actors | ...), so an
edit to either side without the other fails this test. Also: an exhaustive
(state x trigger) matrix and per-row allow/deny checks for every actor kind.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from omnigrowth.core.enums import ActorType, UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.state_machines import (
    ALL_MACHINES,
    CAMPAIGN,
    CONTENT_ASSET,
    EXPERIMENT,
    PUBLICATION,
    PUBLICATION_JOB,
    Actor,
    StateMachine,
)
from omnigrowth.core.state_machines.machine import ActorRule

DOC = Path(__file__).resolve().parents[2] / "docs" / "STATE_MACHINES.md"
ROW = re.compile(r"^\|\s*(?P<id>(?:CA|C|J|P|E)\d+b?)\s*\|(?P<rest>.*)$")

MACHINE_BY_PREFIX: dict[str, StateMachine] = {
    "C": CAMPAIGN,
    "CA": CONTENT_ASSET,
    "J": PUBLICATION_JOB,
    "P": PUBLICATION,
    "E": EXPERIMENT,
}

# Doc rows implemented as several code transitions (one trigger each, see definitions.py).
SPLITS: dict[str, tuple[str, ...]] = {
    "J7": ("J7", "J7b"),
    "J12": ("J12", "J12b"),
    "P3": ("P3", "P3b"),
    "P5": ("P5", "P5b"),
    "P6": ("P6", "P6b"),
}
# Rows whose actor cell is not a list of actor kinds.
ACTORS_NOT_LISTED = {"J1", "P3"}

ACTOR_KINDS = {
    "OWNER": Actor(ActorType.HUMAN, "u", UserRole.OWNER),
    "EDITOR": Actor(ActorType.HUMAN, "u", UserRole.EDITOR),
    "VIEWER": Actor(ActorType.HUMAN, "u", UserRole.VIEWER),
    "SYSTEM": Actor(ActorType.SYSTEM, "s"),
    "SCHEDULER": Actor(ActorType.SCHEDULER, "s"),
    "AGENT": Actor(ActorType.AGENT, "a"),
}


def _prefix(tid: str) -> str:
    return "CA" if tid.startswith("CA") else tid[0]


def _states(cell: str) -> frozenset[str]:
    cell = cell.strip()
    if cell in {"—", "-", ""}:
        return frozenset()
    return frozenset(s.strip() for s in cell.split(",") if s.strip())


def _actor_kinds(cell: str) -> set[str]:
    cell = re.sub(r"\([^)]*\)", "", cell)  # drop parenthetical notes
    kinds: set[str] = set()
    for roles in re.findall(r"H:([A-Z,]+)", cell):
        kinds |= {r for r in roles.split(",") if r}
    cell = re.sub(r"H:[A-Z,]+", "", cell)
    for kind in ("SYSTEM", "SCHEDULER", "AGENT"):
        if re.search(rf"\b{kind}\b", cell):
            kinds.add(kind)
    return kinds


def _doc_rows() -> dict[str, tuple[frozenset[str], str, set[str]]]:
    rows: dict[str, tuple[frozenset[str], str, set[str]]] = {}
    for line in DOC.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if not match:
            continue
        cells = [c.strip() for c in match["rest"].split("|")]
        sources, target, actors = _states(cells[0]), cells[1].strip(), _actor_kinds(cells[4])
        rows[match["id"]] = (sources, target, actors)
    return rows


DOC_ROWS = _doc_rows()


def _code_kinds(rules: tuple[ActorRule, ...]) -> set[str]:
    return {name for name, actor in ACTOR_KINDS.items() if any(r.allows(actor) for r in rules)}


def test_doc_was_parsed() -> None:
    assert len(DOC_ROWS) >= 60, "STATE_MACHINES.md transition tables not found"


@pytest.mark.parametrize("doc_id", sorted(DOC_ROWS))
def test_code_matches_doc_row(doc_id: str) -> None:
    doc_sources, doc_target, doc_actors = DOC_ROWS[doc_id]
    machine = MACHINE_BY_PREFIX[_prefix(doc_id)]
    code = {t.id: t for t in machine.transitions}
    code_ids = SPLITS.get(doc_id, (doc_id,))
    missing = [cid for cid in code_ids if cid not in code]
    assert not missing, f"{doc_id}: not implemented as {missing}"
    for cid in code_ids:
        t = code[cid]
        assert t.sources == doc_sources, f"{cid}: sources {t.sources} != doc {doc_sources}"
        assert t.target == doc_target, f"{cid}: target {t.target} != doc {doc_target}"
    if doc_id not in ACTORS_NOT_LISTED:
        code_actors: set[str] = set()
        for cid in code_ids:
            code_actors |= _code_kinds(code[cid].actors)
        assert code_actors == doc_actors, f"{doc_id}: actors {code_actors} != doc {doc_actors}"


def test_no_code_transition_without_doc_row() -> None:
    documented = {cid for doc_id in DOC_ROWS for cid in SPLITS.get(doc_id, (doc_id,))}
    for machine in (CAMPAIGN, CONTENT_ASSET, PUBLICATION_JOB, PUBLICATION, EXPERIMENT):
        undocumented = {t.id for t in machine.transitions} - documented
        assert not undocumented, f"{machine.name}: {sorted(undocumented)} not in STATE_MACHINES.md"


@pytest.mark.parametrize("machine", ALL_MACHINES, ids=lambda m: m.name)
def test_exhaustive_state_trigger_matrix(machine: StateMachine) -> None:
    triggers = {t.trigger for t in machine.transitions}
    allowed = {(s, t.trigger) for t in machine.transitions for s in t.sources}
    superuser_actors = list(ACTOR_KINDS.values())
    for state in machine.states:
        for trigger in triggers:
            if (state.value, trigger) in allowed:
                continue
            for actor in superuser_actors:
                with pytest.raises(OmniGrowthError) as exc:
                    machine.resolve(state.value, trigger, actor)
                assert exc.value.code is ErrorCode.INVALID_STATE_TRANSITION


@pytest.mark.parametrize("machine", ALL_MACHINES, ids=lambda m: m.name)
def test_every_row_allows_exactly_its_actors(machine: StateMachine) -> None:
    for t in machine.transitions:
        source = next(iter(t.sources)) if t.sources else None
        permitted = _code_kinds(t.actors)
        for name, actor in ACTOR_KINDS.items():
            if name in permitted:
                assert machine.resolve(source, t.trigger, actor).id == t.id
            else:
                with pytest.raises(OmniGrowthError) as exc:
                    machine.resolve(source, t.trigger, actor)
                assert exc.value.code is ErrorCode.FORBIDDEN
