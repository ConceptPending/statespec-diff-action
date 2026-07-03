# Vendored from ConceptPending/statespec statespec-v0.4.0 (src/statespec) — DO NOT EDIT.
# Only import statements' `statespec` prefix -> `kernel` is rewritten.
# Sync/verify with scripts/vendor_kernel.py.
"""The generic state-machine engine: data model, enforcement, and analysis.

Nothing in here is invoice-specific. A `StateSpec` is pure data; `apply` is
the only place a transition is ever allowed to happen; `validate` proves a
spec is well-formed before it can mislead anyone. Keep this file small and
domain-free — that is what lets one Hypothesis test (driving `apply` over
random sequences) stand in as a correctness proof for *every* spec.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Mapping

from kernel import expr as _expr
from kernel.expr import ExpressionError  # re-export for callers


# --- Data model -------------------------------------------------------------
#
# Guards and invariants are declarative expressions (see app/statespec/expr.py)
# — pure data, so the whole StateSpec serialises, renders, and diffs. The same
# object evaluates at runtime, so the rendered policy *is* the enforced policy.


@dataclass(frozen=True)
class Transition:
    """A named, role-gated edge from one or more source states to a dest."""

    name: str
    sources: tuple[str, ...]
    dest: str
    # Roles permitted to fire this transition. Empty = no one (a deliberate
    # dead edge is a spec error; `validate` flags it).
    roles: frozenset[str] = frozenset()
    # Optional guard expression that must evaluate True against the context.
    guard: object | None = None  # an expr condition (Compare/All/Any/Not/Opaque)
    # Plain-English description for the human-readable render.
    label: str = ""
    # Stable, namespaced control id for audit/compliance references (e.g.
    # "BATCH-APPROVE"). Defaults to the action name; survives a label reword.
    control_id: str | None = None


@dataclass(frozen=True)
class Invariant:
    name: str
    condition: object  # an expr condition; must hold in every reachable state
    label: str = ""
    control_id: str | None = None  # stable control id (defaults to name)


@dataclass(frozen=True)
class StateSpec:
    """A complete lifecycle: states, the context field schema, transitions,
    and invariants."""

    name: str
    title: str
    # state id -> human description
    states: Mapping[str, str]
    # context field name -> type tag (the contract a service snapshot must
    # satisfy; the basis for validate's field/type checks). See expr.TYPE_TAGS.
    fields: Mapping[str, str]
    initial: str
    terminal: frozenset[str]
    transitions: tuple[Transition, ...]
    invariants: tuple[Invariant, ...] = ()
    # Author-asserted policy version. Bump when the *semantic* digest changes;
    # the (version, semantic_digest) pair is the policy's behavioural identity. A
    # wording-only edit moves presentation_digest, not this. See statespec.identity.
    version: int = 1

    def transition(self, action: str) -> Transition | None:
        for t in self.transitions:
            if t.name == action:
                return t
        return None


# --- Enforcement errors -----------------------------------------------------


class TransitionError(Exception):
    """Base for every reason a transition can be refused."""


class UnknownAction(TransitionError):
    """The action name isn't defined in the spec at all."""


class IllegalTransition(TransitionError):
    """The action exists but not from the entity's current state."""


class PermissionDenied(TransitionError):
    """The actor's roles don't include any role permitted to fire it."""


class GuardRejected(TransitionError):
    """The action's guard predicate returned False for this entity."""


class InvariantViolation(Exception):
    """A transition would produce a state that violates a declared invariant.

    Deliberately NOT a TransitionError: invariants are *consequences* of guards
    (a backstop), so this can only fire from a guard/spec bug or an out-of-band
    mutation. It maps to HTTP 500 (an internal breach of a stated guarantee),
    not a client 4xx. Any client-visible rule must be a guard, never a
    standalone invariant."""

    def __init__(self, dest: str, violated: list[str]):
        self.dest = dest
        self.violated = violated
        super().__init__(
            f"transition into {dest!r} would violate invariant(s): {violated}"
        )


@dataclass(frozen=True)
class Decision:
    """The outcome of a `can_fire` check — allowed, or why not."""

    allowed: bool
    dest: str | None = None
    reason: str = ""
    error: type[TransitionError] | None = None


@dataclass(frozen=True)
class Evaluation:
    """Structured evidence that one condition was evaluated during a transition
    — the audit record's "why was this allowed". `inputs` is just the fields the
    expression referenced (not the whole entity)."""

    control_id: str
    kind: str  # "guard" | "invariant"
    expression: dict  # expr.to_dict — the condition as data
    result: bool
    inputs: Mapping[str, object]


# --- The single enforcement path -------------------------------------------


def can_fire(
    spec: StateSpec,
    action: str,
    current_state: str,
    actor_roles: frozenset[str],
    entity: Mapping[str, object] | None = None,
) -> Decision:
    """Pure decision function. Never mutates anything, and never raises
    TransitionError — refusals come back as a Decision. It can raise
    ExpressionError, for a malformed guard or a missing context field:
    that's a contract/spec bug, not a refusal.

    Checks, in order: action exists -> reachable from current state ->
    actor permitted -> guard holds. The ordering is deliberate and matches
    the error precedence callers (and tests) rely on.
    """
    t = spec.transition(action)
    if t is None:
        return Decision(False, reason=f"unknown action {action!r}", error=UnknownAction)
    if current_state not in t.sources:
        return Decision(
            False,
            reason=f"{action!r} not allowed from state {current_state!r}",
            error=IllegalTransition,
        )
    if not (actor_roles & t.roles):
        return Decision(
            False,
            reason=f"none of roles {set(actor_roles)} may fire {action!r}",
            error=PermissionDenied,
        )
    if t.guard is not None:
        # The guard is an expr condition. A malformed guard / missing context
        # field raises ExpressionError (a contract bug) rather than a refusal.
        if not t.guard.evaluate(entity or {}):
            return Decision(
                False,
                reason=f"guard {_expr.render(t.guard)} rejected {action!r}",
                error=GuardRejected,
            )
    return Decision(True, dest=t.dest, reason="ok")


def _require_fields(spec: StateSpec, ctx: Mapping[str, object]) -> None:
    missing = set(spec.fields) - set(ctx)
    if missing:
        raise ExpressionError(
            f"context for {spec.name!r} is missing field(s): {sorted(missing)}"
        )


def fire(
    spec: StateSpec,
    action: str,
    current_state: str,
    actor_roles: frozenset[str],
    entity: Mapping[str, object] | None = None,
    *,
    post_overrides: Mapping[str, object] | None = None,
) -> tuple[str, list[Evaluation]]:
    """Enforce a transition and return `(dest, evaluations)` — the destination
    state plus structured evidence of every condition evaluated (the guard and
    each invariant, with the inputs it read). The single mutation-decision path;
    `apply` is the dest-only convenience wrapper.

    After the transition is judged legal (action / source / role / guard), the
    invariants are evaluated against the proposed post-state and the transition
    is refused (InvariantViolation) if any fails. Proposed post-state is
    `{**entity, status: dest, **post_overrides}`.
    """
    decision = can_fire(spec, action, current_state, actor_roles, entity)
    if not decision.allowed:
        assert decision.error is not None
        raise decision.error(decision.reason)
    assert decision.dest is not None
    dest = decision.dest
    ctx = entity or {}
    t = spec.transition(action)
    evaluations: list[Evaluation] = []

    if t.guard is not None:
        evaluations.append(
            Evaluation(
                control_id=t.control_id or t.name,
                kind="guard",
                expression=_expr.to_dict(t.guard),
                result=True,  # can_fire allowed => the guard held
                inputs={f: ctx[f] for f in t.guard.fields() if f in ctx},
            )
        )

    post = {**ctx, "status": dest, **(post_overrides or {})}
    _require_fields(spec, post)
    _expr.validate_context(spec.fields, post)  # runtime values match declared types
    violated: list[str] = []
    for inv in spec.invariants:
        ok = bool(inv.condition.evaluate(post))
        evaluations.append(
            Evaluation(
                control_id=inv.control_id or inv.name,
                kind="invariant",
                expression=_expr.to_dict(inv.condition),
                result=ok,
                inputs={f: post[f] for f in inv.condition.fields() if f in post},
            )
        )
        if not ok:
            violated.append(inv.name)
    if violated:
        raise InvariantViolation(dest, violated)
    return dest, evaluations


def apply(
    spec: StateSpec,
    action: str,
    current_state: str,
    actor_roles: frozenset[str],
    entity: Mapping[str, object] | None = None,
    *,
    post_overrides: Mapping[str, object] | None = None,
) -> str:
    """Enforce a transition and return the destination state, or raise. The
    dest-only wrapper around `fire` for callers that don't need the evidence."""
    dest, _ = fire(
        spec, action, current_state, actor_roles, entity, post_overrides=post_overrides
    )
    return dest


def enabled_transitions(
    spec: StateSpec,
    current_state: str,
    actor_roles: frozenset[str],
    entity: Mapping[str, object] | None = None,
) -> list[Transition]:
    """Every transition the given actor could fire right now."""
    return [
        t
        for t in spec.transitions
        if can_fire(spec, t.name, current_state, actor_roles, entity).allowed
    ]


# --- Static analysis (well-formedness) -------------------------------------


def reachable_states(spec: StateSpec) -> set[str]:
    """States reachable from `initial` by following transitions (roles/guards
    ignored — this is structural reachability)."""
    seen = {spec.initial}
    queue: deque[str] = deque([spec.initial])
    while queue:
        s = queue.popleft()
        for t in spec.transitions:
            if s in t.sources and t.dest not in seen:
                seen.add(t.dest)
                queue.append(t.dest)
    return seen


def _can_reach_terminal(spec: StateSpec, start: str) -> bool:
    if not spec.terminal:
        return True  # nothing to reach; treated as vacuously fine
    seen = {start}
    queue: deque[str] = deque([start])
    while queue:
        s = queue.popleft()
        if s in spec.terminal:
            return True
        for t in spec.transitions:
            if s in t.sources and t.dest not in seen:
                seen.add(t.dest)
                queue.append(t.dest)
    return False


def validate(
    spec: StateSpec, known_roles: frozenset[str] | None = None
) -> list[str]:
    """Return a list of human-readable problems. Empty list == well-formed.

    A well-formed spec has: a declared initial state; declared, terminal-only
    sink states; every state reachable from initial; every non-terminal state
    able to reach some terminal (no traps); unique transition + invariant
    names; valid field-schema type tags; and guard/invariant expressions that
    type-check against the field schema (every referenced field declared, every
    comparison type-compatible, every opaque registered).

    If `known_roles` is supplied (the application's role catalogue), every
    transition's roles must be drawn from it — this catches a misspelled role
    (e.g. `aprover`) that no user could ever hold, which would otherwise make
    the transition silently un-fireable.
    """
    problems: list[str] = []
    states = set(spec.states)

    if spec.initial not in states:
        problems.append(f"initial state {spec.initial!r} is not declared")
    for term in spec.terminal:
        if term not in states:
            problems.append(f"terminal state {term!r} is not declared")

    for fname, tag in spec.fields.items():
        if tag not in _expr.TYPE_TAGS:
            problems.append(f"field {fname!r} has unknown type tag {tag!r}")

    seen_actions: set[str] = set()
    for t in spec.transitions:
        if t.name in seen_actions:
            problems.append(f"duplicate transition name {t.name!r}")
        seen_actions.add(t.name)
        for s in t.sources:
            if s not in states:
                problems.append(f"transition {t.name!r} has undeclared source {s!r}")
        if t.dest not in states:
            problems.append(f"transition {t.name!r} has undeclared dest {t.dest!r}")
        if any(src in spec.terminal for src in t.sources):
            problems.append(
                f"transition {t.name!r} leaves terminal state(s) "
                f"{set(t.sources) & spec.terminal} — terminals must be sinks"
            )
        if not t.roles:
            problems.append(f"transition {t.name!r} has no permitted roles (dead edge)")
        if known_roles is not None and (t.roles - known_roles):
            problems.append(
                f"transition {t.name!r} references role(s) not in the catalogue: "
                f"{sorted(t.roles - known_roles)}"
            )
        if t.guard is not None:
            problems += [
                f"transition {t.name!r} guard: {p}"
                for p in _expr.typecheck(t.guard, spec.fields)
            ]

    seen_invariants: set[str] = set()
    for inv in spec.invariants:
        if inv.name in seen_invariants:
            problems.append(f"duplicate invariant name {inv.name!r}")
        seen_invariants.add(inv.name)
        problems += [
            f"invariant {inv.name!r}: {p}"
            for p in _expr.typecheck(inv.condition, spec.fields)
        ]

    reachable = reachable_states(spec)
    for s in states - reachable:
        problems.append(f"state {s!r} is unreachable from initial {spec.initial!r}")

    for s in states - spec.terminal:
        if not _can_reach_terminal(spec, s):
            problems.append(f"state {s!r} cannot reach any terminal state (trap)")

    return problems
