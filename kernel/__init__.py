# Vendored from ConceptPending/statespec statespec-v0.4.1 (src/statespec) — DO NOT EDIT.
# Only import statements' `statespec` prefix -> `kernel` is rewritten.
# Sync/verify with scripts/vendor_kernel.py.
"""Declarative state-machine specs with a generic enforcement engine.

A *spec* is a plain-data description of an entity's lifecycle: its states,
the named transitions between them, who (which roles) may fire each
transition, and the guard predicates that must hold. The same artifact is:

- **human-readable** — `render.to_mermaid` / `render.to_table` turn it into a
  diagram and a plain-English table a non-engineer can sign off on;
- **machine-checkable** — `core.validate` proves the graph is coherent
  (no unreachable or stuck states) and `core.apply` is the single, generic
  interpreter every service-layer transition goes through, so the code
  *cannot* drift from the spec without a test noticing.

The engine here is deliberately domain-agnostic: it knows nothing about
any domain. Domain specs live in the consuming application, next to the
slice they govern.
"""

from kernel.core import (
    Decision,
    Evaluation,
    ExpressionError,
    GuardRejected,
    IllegalTransition,
    Invariant,
    InvariantViolation,
    PermissionDenied,
    StateSpec,
    Transition,
    TransitionError,
    UnknownAction,
    apply,
    can_fire,
    enabled_transitions,
    fire,
    validate,
)

__all__ = [
    "Decision",
    "Evaluation",
    "ExpressionError",
    "GuardRejected",
    "IllegalTransition",
    "Invariant",
    "InvariantViolation",
    "PermissionDenied",
    "StateSpec",
    "Transition",
    "TransitionError",
    "UnknownAction",
    "apply",
    "can_fire",
    "enabled_transitions",
    "fire",
    "validate",
]
