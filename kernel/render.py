# Vendored from ConceptPending/statespec statespec-v0.4.0 (src/statespec) — DO NOT EDIT.
# Only import statements' `statespec` prefix -> `kernel` is rewritten.
# Sync/verify with scripts/vendor_kernel.py.
"""Turn a `StateSpec` into artifacts humans read.

These outputs are the half of the value proposition that isn't tests: a
diagram and a plain-English table that a reviewer, auditor, or domain owner
can read to confirm the workflow is *fit for purpose* — without reading
Python. `to_dict` is the same data as JSON, the seed for a future
layperson-facing viewer (a separate tool/repo).
"""

from __future__ import annotations

from kernel import expr as _expr
from kernel import identity as _identity
from kernel.core import StateSpec


def to_mermaid(spec: StateSpec) -> str:
    """A Mermaid `stateDiagram-v2`. Renders on GitHub, in docs, and in most
    Markdown viewers — so the diagram lives next to the code and never goes
    stale (it is generated, not drawn)."""
    lines = ["stateDiagram-v2", f"    %% {spec.title}", f"    [*] --> {spec.initial}"]
    for t in spec.transitions:
        roles = "/".join(sorted(t.roles)) or "—"
        guard = f" [{_expr.render(t.guard)}]" if t.guard else ""
        edge = f"{t.name} ({roles}){guard}"
        for src in t.sources:
            lines.append(f"    {src} --> {t.dest}: {edge}")
    for term in sorted(spec.terminal):
        lines.append(f"    {term} --> [*]")
    return "\n".join(lines)


def to_table(spec: StateSpec) -> str:
    """A plain-English Markdown transition table — the auditor's checklist."""
    rows = [
        "| Action | From | To | Who may do it | Condition |",
        "| --- | --- | --- | --- | --- |",
    ]
    for t in spec.transitions:
        roles = ", ".join(sorted(t.roles)) or "—"
        guard = _expr.render(t.guard) if t.guard else "—"
        frm = ", ".join(t.sources)
        label = f" — {t.label}" if t.label else ""
        rows.append(f"| **{t.name}**{label} | {frm} | {t.dest} | {roles} | {guard} |")
    return "\n".join(rows)


def to_dict(spec: StateSpec) -> dict:
    """Serialisable form — the machine-readable spec, also what an API can
    hand to a front-end lifecycle viewer."""
    return {
        "name": spec.name,
        "title": spec.title,
        # Policy identity — which exact policy this is. Two digests: behaviour
        # (what approval binds to) and wording (tracked, non-invalidating).
        "version": spec.version,
        "semantic_digest": _identity.semantic_digest(spec),
        "presentation_digest": _identity.presentation_digest(spec),
        "initial": spec.initial,
        "terminal": sorted(spec.terminal),
        "states": [{"id": s, "description": d} for s, d in spec.states.items()],
        "fields": [{"name": n, "type": t} for n, t in spec.fields.items()],
        "transitions": [
            {
                "name": t.name,
                "label": t.label,
                "from": list(t.sources),
                "to": t.dest,
                "roles": sorted(t.roles),
                # The condition as both a structured tree (for tooling/diff)
                # and rendered text (for humans).
                "guard": _expr.to_dict(t.guard) if t.guard else None,
                "guard_text": _expr.render(t.guard) if t.guard else None,
            }
            for t in spec.transitions
        ],
        "invariants": [
            {
                "name": i.name,
                "label": i.label,
                "condition": _expr.to_dict(i.condition),
                "text": _expr.render(i.condition),
            }
            for i in spec.invariants
        ],
    }


def to_markdown_doc(spec: StateSpec) -> str:
    """The full generated artifact: title, diagram, table, invariants."""
    parts = [
        f"# {spec.title}",
        "",
        "> **Generated file — do not edit by hand.** Regenerate with "
        "`make spec-doc`. The source of truth is the spec in "
        f"`app/statespec/{spec.name}_spec.py`; this document is rendered from "
        "it so the picture can never drift from the enforced behaviour.",
        "",
        "## Lifecycle",
        "",
        "```mermaid",
        to_mermaid(spec),
        "```",
        "",
        "## States",
        "",
        "| State | Meaning |",
        "| --- | --- |",
    ]
    for s, d in spec.states.items():
        tag = " _(start)_" if s == spec.initial else ""
        tag += " _(final)_" if s in spec.terminal else ""
        parts.append(f"| `{s}`{tag} | {d} |")
    parts += ["", "## Transitions", "", to_table(spec)]
    if spec.invariants:
        parts += [
            "",
            "## Invariants",
            "",
            "Properties that must hold in every reachable state. The engine "
            "evaluates them against the proposed post-state on every transition "
            "and refuses the transition if any fails; the property-based suite "
            "(Hypothesis) also checks them across random action sequences. (A "
            "mutation made entirely outside a transition is still beyond the "
            "engine's reach — that is the database-constraint domain.)",
            "",
        ]
        for inv in spec.invariants:
            cond = _expr.render(inv.condition)
            parts.append(f"- **{inv.name}** (`{cond}`) — {inv.label or '(see spec)'}")
    parts.append("")
    return "\n".join(parts)
