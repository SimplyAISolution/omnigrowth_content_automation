"""Generic, data-driven state machine (docs/STATE_MACHINES.md §0 and "Enforcement").

The machine answers "is trigger T allowed from state S for actor A, and where does it go?".
Domain guards that need database reads (e.g. ``campaign_active``) are evaluated by services
*before* calling :func:`apply_transition`; this module owns the structural rules and the
only code path that writes a ``status`` column.

Rejection order (STATE_MACHINES §0): unknown transition -> INVALID_STATE_TRANSITION,
then actor/role -> FORBIDDEN, then (in services) guards, then the compare-and-set write
losing a race -> CONFLICT.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from omnigrowth.core.enums import ActorType, UserRole
from omnigrowth.core.errors import ErrorCode, InvalidStateTransitionError, OmniGrowthError


@dataclass(frozen=True, slots=True)
class Actor:
    type: ActorType
    id: str
    role: UserRole | None = None

    def __post_init__(self) -> None:
        if self.type is ActorType.HUMAN and self.role is None:
            raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "HUMAN actor requires a role")


@dataclass(frozen=True, slots=True)
class ActorRule:
    """An actor type, optionally restricted to human roles (``H:OWNER,EDITOR``)."""

    type: ActorType
    roles: frozenset[UserRole] | None = None

    def allows(self, actor: Actor) -> bool:
        if actor.type is not self.type:
            return False
        return self.roles is None or actor.role in self.roles


def human(*roles: UserRole) -> ActorRule:
    return ActorRule(ActorType.HUMAN, frozenset(roles))


SYSTEM = ActorRule(ActorType.SYSTEM)
SCHEDULER = ActorRule(ActorType.SCHEDULER)
AGENT = ActorRule(ActorType.AGENT)
INTEGRATION = ActorRule(ActorType.INTEGRATION)  # Phase 12 least-privilege n8n principal
OWNER_EDITOR = human(UserRole.OWNER, UserRole.EDITOR)
OWNER_ONLY = human(UserRole.OWNER)


@dataclass(frozen=True, slots=True)
class Transition:
    id: str
    sources: frozenset[str]  # empty = creation transition (no prior state)
    target: str
    trigger: str
    actors: tuple[ActorRule, ...]

    def allows(self, actor: Actor) -> bool:
        return any(rule.allows(actor) for rule in self.actors)


@dataclass(frozen=True)
class StateMachine:
    name: str
    states: type[StrEnum]
    transitions: tuple[Transition, ...]
    terminal: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        valid = {s.value for s in self.states}
        seen: set[tuple[str, str]] = set()
        for t in self.transitions:
            unknown = ({t.target} | set(t.sources)) - valid
            if unknown:
                raise ValueError(f"{self.name}.{t.id}: unknown states {sorted(unknown)}")
            for source in t.sources or {""}:
                key = (source, t.trigger)
                if key in seen:
                    raise ValueError(
                        f"{self.name}: ambiguous trigger {t.trigger!r} from {source!r}"
                    )
                seen.add(key)
        if not self.terminal <= valid:
            raise ValueError(f"{self.name}: unknown terminal states")

    def initial(self, trigger: str, actor: Actor) -> Transition:
        return self.resolve(None, trigger, actor)

    def resolve(self, from_state: str | None, trigger: str, actor: Actor) -> Transition:
        """Return the transition for ``trigger`` from ``from_state`` or raise."""
        source = "" if from_state is None else str(from_state)
        for t in self.transitions:
            if t.trigger != trigger:
                continue
            if (source == "" and not t.sources) or source in t.sources:
                if not t.allows(actor):
                    raise OmniGrowthError(
                        ErrorCode.FORBIDDEN,
                        f"{self.name}: {actor.type}"
                        f"{'/' + actor.role if actor.role else ''} may not {trigger!r}",
                        details={"transition": t.id, "trigger": trigger},
                    )
                return t
        raise InvalidStateTransitionError(
            self.name, source or "<new>", "?", f"trigger {trigger!r} not allowed"
        )

    def triggers_from(self, state: str) -> set[str]:
        return {t.trigger for t in self.transitions if state in t.sources}

    def reachable(self) -> set[str]:
        reached = {t.target for t in self.transitions if not t.sources}
        frontier = set(reached)
        while frontier:
            nxt = {t.target for t in self.transitions if t.sources & frontier} - reached
            reached |= nxt
            frontier = nxt
        return reached


def tr(
    tid: str,
    sources: Iterable[StrEnum] | StrEnum | None,
    target: StrEnum,
    trigger: str,
    *actors: ActorRule,
) -> Transition:
    if sources is None:
        src: frozenset[str] = frozenset()
    elif isinstance(sources, StrEnum):
        src = frozenset({sources.value})
    else:
        src = frozenset(s.value for s in sources)
    return Transition(tid, src, target.value, trigger, tuple(actors))


def audit_payload(
    transition: Transition, from_state: str | None, reason: str | None
) -> Mapping[str, Any]:
    """``audit_events.metadata`` for a status transition (STATE_MACHINES AE)."""
    return {
        "transition": transition.id,
        "from": from_state,
        "to": transition.target,
        "trigger": transition.trigger,
        "reason": reason,
    }
