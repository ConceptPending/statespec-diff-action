# Vendored from ConceptPending/statespec statespec-v0.4.0 (src/statespec) — DO NOT EDIT.
# Only import statements' `statespec` prefix -> `kernel` is rewritten.
# Sync/verify with scripts/vendor_kernel.py.
"""Policy identity: canonical serialisation, content digest, and semantic diff.

The whole point is to answer, for a governed workflow: *which exact policy is
this, and what changed?* `canonical()` produces a deterministic, content-only
representation, projected into two disjoint, independently-hashed views:
`semantic_digest()` over executable behaviour (states, transitions, roles,
guard/invariant expressions, and a hash of any `Opaque` body — the
tamper-evidence the registry alone can't give), and `presentation_digest()`
over the human-facing wording (title, descriptions, labels). Approval binds to
the *semantic* digest, so a copy-edit no longer invalidates it; the
presentation digest still moves, so a reword is tracked, not lost. `diff()`
turns two canonical forms into a plain-English list of what changed — the seam
a policy review (e.g. a GitHub check) renders for a human approver.
"""

from __future__ import annotations

import hashlib
import inspect
import json

from kernel import expr as _expr
from kernel.core import StateSpec


def _walk_opaque(node) -> list:
    if isinstance(node, _expr.Opaque):
        return [node]
    if isinstance(node, (_expr.All, _expr.Any)):
        out = []
        for t in node.terms:
            out += _walk_opaque(t)
        return out
    if isinstance(node, _expr.Not):
        return _walk_opaque(node.term)
    return []


def _opaque_hashes(node) -> dict:
    """For each Opaque in the expression, hash its registered source so a body
    change is reflected in the digest even without a version bump."""
    out = {}
    if node is None:
        return out
    for op in _walk_opaque(node):
        fn = _expr._OPAQUE_REGISTRY.get((op.name, op.version))
        src = inspect.getsource(fn) if fn is not None else "<unregistered>"
        out[f"{op.name}:{op.version}"] = hashlib.sha256(src.encode()).hexdigest()[:16]
    return out


def _guard_dict(node):
    return _expr.to_dict(node) if node is not None else None


def canonical(spec: StateSpec) -> dict:
    """Deterministic, content-only representation. Title, labels and
    descriptions are included (a reworded policy is a changed policy), but
    rendered text is not (it's derivable). Keyed and sorted so the form is
    stable. `title` was added in statespec-v0.4.0 so the presentation digest
    is independently verifiable from the artifact alone; it never enters the
    semantic projection, so digest bindings are unaffected."""
    return {
        "name": spec.name,
        "title": spec.title,
        "version": spec.version,
        "states": {s: d for s, d in sorted(spec.states.items())},
        "initial": spec.initial,
        "terminal": sorted(spec.terminal),
        "fields": dict(sorted(spec.fields.items())),
        "transitions": sorted(
            (
                {
                    "id": t.control_id or t.name,
                    "name": t.name,
                    "from": sorted(t.sources),
                    "to": t.dest,
                    "roles": sorted(t.roles),
                    "label": t.label,
                    "guard": _guard_dict(t.guard),
                    "opaque": _opaque_hashes(t.guard),
                }
                for t in spec.transitions
            ),
            key=lambda d: d["id"],
        ),
        "invariants": sorted(
            (
                {
                    "id": i.control_id or i.name,
                    "name": i.name,
                    "label": i.label,
                    "condition": _expr.to_dict(i.condition),
                    "opaque": _opaque_hashes(i.condition),
                }
                for i in spec.invariants
            ),
            key=lambda d: d["id"],
        ),
    }


# --- Two-part identity: behaviour vs wording --------------------------------
#
# `canonical()` is the full, human-readable form embedded in the artifact. It is
# projected into two disjoint views, each hashed on its own:
#
#   semantic_digest      — the graph and its rules (states, transitions, roles,
#                          guards, invariants, opaque-body hashes). What approval
#                          binds to; a change here bumps spec_version.
#   presentation_digest  — the human-facing strings (title, state descriptions,
#                          transition/invariant labels). Tracked and surfaced by
#                          diff(), but non-invalidating to approval by default.
#
# Splitting the digest is what lets a copy-edit move presentation_digest without
# moving semantic_digest — so the behavioural approval survives a reword while
# the reword is still recorded. See docs/policy-artifact-contract.md.


def _sha256(obj) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def semantic_canonical(spec: StateSpec) -> dict:
    """Behaviour-only projection of `canonical()`: the graph and its rules with
    every human-facing string dropped. Hashing this gives the policy's
    behavioural identity — the thing approval is about."""
    c = canonical(spec)
    return {
        "name": c["name"],
        "version": c["version"],
        "states": sorted(c["states"]),  # node ids only; descriptions are prose
        "initial": c["initial"],
        "terminal": c["terminal"],
        "fields": c["fields"],
        "transitions": [
            {k: v for k, v in t.items() if k != "label"} for t in c["transitions"]
        ],
        "invariants": [
            {k: v for k, v in i.items() if k != "label"} for i in c["invariants"]
        ],
    }


def presentation_canonical(spec: StateSpec) -> dict:
    """Wording-only projection: the human-facing strings keyed by stable control
    id, so a reword is detectable and attributable without touching behaviour."""
    c = canonical(spec)
    return {
        "title": c["title"],  # from the canonical form: verifiable from the artifact
        "states": c["states"],  # id -> description
        "transitions": [{"id": t["id"], "label": t["label"]} for t in c["transitions"]],
        "invariants": [{"id": i["id"], "label": i["label"]} for i in c["invariants"]],
    }


def semantic_digest(spec: StateSpec) -> str:
    """sha256 of the behavioural projection. The digest approval binds to."""
    return _sha256(semantic_canonical(spec))


def presentation_digest(spec: StateSpec) -> str:
    """sha256 of the wording projection. Tracked; non-invalidating by default."""
    return _sha256(presentation_canonical(spec))


# Version of the policy-artifact FORMAT itself (the .policy.json envelope), so
# a consumer (e.g. an external control plane) can evolve with it. Bump only when
# the artifact shape changes — independent of any individual policy's version.
# v2: split the single `digest` into `semantic_digest` + `presentation_digest`.
POLICY_ARTIFACT_SCHEMA = 2


def policy_record(spec: StateSpec) -> dict:
    """The committed policy artifact: a versioned envelope around the canonical
    spec. This is the stable contract an external consumer reads (see
    docs/policy-artifact-contract.md). It is a *committed* baseline, not
    an *approved* one — approval lives in the control plane, tied to the
    *semantic* digest."""
    return {
        "schema_version": POLICY_ARTIFACT_SCHEMA,
        "spec_version": spec.version,
        "semantic_digest": semantic_digest(spec),
        "presentation_digest": presentation_digest(spec),
        "spec": canonical(spec),
    }


def change_kind(old: dict, new: dict) -> str:
    """Classify the move between two policy records: 'semantic' (behaviour
    changed — approval invalidated), 'presentation' (wording only —
    non-invalidating), or 'none'. The mechanical routing primitive the approval
    layer uses to decide who, if anyone, must re-approve."""
    if old.get("semantic_digest") != new.get("semantic_digest"):
        return "semantic"
    if old.get("presentation_digest") != new.get("presentation_digest"):
        return "presentation"
    return "none"


# --- Semantic diff ----------------------------------------------------------


def _render_guard(d) -> str:
    return _expr.render(_expr.from_dict(d)) if d else "—"


def diff(old: dict, new: dict) -> list[str]:
    """Plain-English changes from one canonical spec to another. `old`/`new`
    are `canonical()` dicts (or the `spec` field of a policy record)."""
    out: list[str] = []

    if old.get("version") != new.get("version"):
        out.append(f"version: {old.get('version')} → {new.get('version')}")

    # .get(): canonical dicts from kernels < v0.4.0 carry no title key.
    if old.get("title") != new.get("title"):
        out.append(f"title: {old.get('title')!r} → {new.get('title')!r}")

    old_states, new_states = set(old["states"]), set(new["states"])
    for s in sorted(new_states - old_states):
        out.append(f"+ state {s}")
    for s in sorted(old_states - new_states):
        out.append(f"- state {s}")

    of, nf = old["fields"], new["fields"]
    for k in sorted(set(nf) - set(of)):
        out.append(f"+ field {k}: {nf[k]}")
    for k in sorted(set(of) - set(nf)):
        out.append(f"- field {k}")
    for k in sorted(set(of) & set(nf)):
        if of[k] != nf[k]:
            out.append(f"field {k}: type {of[k]} → {nf[k]}")

    out += _diff_items(old["transitions"], new["transitions"], "transition", _diff_transition)
    out += _diff_items(old["invariants"], new["invariants"], "invariant", _diff_invariant)
    return out


def _diff_items(old_list, new_list, kind, differ) -> list[str]:
    out = []
    old_by = {d["id"]: d for d in old_list}
    new_by = {d["id"]: d for d in new_list}
    for i in sorted(set(new_by) - set(old_by)):
        out.append(f"+ {kind} {i}")
    for i in sorted(set(old_by) - set(new_by)):
        out.append(f"- {kind} {i}")
    for i in sorted(set(old_by) & set(new_by)):
        out += differ(i, old_by[i], new_by[i])
    return out


def _diff_transition(tid, o, n) -> list[str]:
    out = []
    if o["from"] != n["from"] or o["to"] != n["to"]:
        out.append(f"transition {tid}: {o['from']}→{o['to']} ⇒ {n['from']}→{n['to']}")
    if o["roles"] != n["roles"]:
        out.append(f"transition {tid}: roles {o['roles']} → {n['roles']}")
    if o["guard"] != n["guard"]:
        out.append(
            f"transition {tid}: guard {_render_guard(o['guard'])} → "
            f"{_render_guard(n['guard'])}"
        )
    if o.get("opaque") != n.get("opaque"):
        out.append(f"transition {tid}: opaque body/version changed")
    if o["label"] != n["label"]:
        out.append(f"transition {tid}: label changed")
    return out


def _diff_invariant(iid, o, n) -> list[str]:
    out = []
    if o["condition"] != n["condition"]:
        out.append(
            f"invariant {iid}: {_render_guard(o['condition'])} → "
            f"{_render_guard(n['condition'])}"
        )
    if o.get("opaque") != n.get("opaque"):
        out.append(f"invariant {iid}: opaque body/version changed")
    if o["label"] != n["label"]:
        out.append(f"invariant {iid}: label changed")
    return out
