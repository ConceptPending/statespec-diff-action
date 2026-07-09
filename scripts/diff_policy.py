#!/usr/bin/env python3
"""Render the statespec policy diff for a PR as a markdown comment body.

Usage:
    diff_policy.py --base-ref <sha> --head-ref <sha> [--glob '*.policy.json']
                   [--repo <dir>] [--out <file>]

For every changed policy artifact between base and head, classifies the move
(semantic — approval-invalidating / presentation — wording only / none) from
digests RECOMPUTED from each artifact's spec body (declared digests are
cross-checked; mismatches are called out in the comment — a stale or
hand-edited declaration must not steer the classification), and renders the
kernel's plain-English semantic diff. Exit code is always 0; the
classification lands in the body and in $GITHUB_OUTPUT
(change_kind=semantic|presentation|none|added|removed,
digest_verified=true|false).

Uses only the vendored kernel (stdlib-only) — no pip installs on the runner.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel import identity  # noqa: E402

MARKER = "<!-- statespec-diff -->"
_SEVERITY = ["none", "presentation", "removed", "added", "semantic"]


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True).stdout


def _show(repo: str, ref: str, path: str) -> dict | None:
    out = subprocess.run(["git", "-C", repo, "show", f"{ref}:{path}"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


_REQUIRED_SPEC_KEYS = {"name", "version", "states", "initial", "terminal",
                       "fields", "transitions", "invariants"}


def _semantic_projection(spec: dict) -> dict:
    """Dict-level mirror of the kernel's `semantic_canonical`: the behaviour-only
    projection of a canonical spec with every human-facing string dropped. Must
    stay byte-equivalent to the kernel's — pinned by tests against
    kernel-emitted artifacts."""
    return {
        "name": spec["name"],
        "version": spec["version"],
        "states": sorted(spec["states"]),
        "initial": spec["initial"],
        "terminal": spec["terminal"],
        "fields": spec["fields"],
        "transitions": [{k: v for k, v in t.items() if k != "label"} for t in spec["transitions"]],
        "invariants": [{k: v for k, v in i.items() if k != "label"} for i in spec["invariants"]],
    }


def _presentation_projection(spec: dict) -> dict:
    """Dict-level mirror of the kernel's `presentation_canonical`. Only
    computable from artifacts whose spec carries `title` (kernel >= v0.4.0)."""
    return {
        "title": spec["title"],
        "states": spec["states"],
        "transitions": [{"id": t["id"], "label": t["label"]} for t in spec["transitions"]],
        "invariants": [{"id": i["id"], "label": i["label"]} for i in spec["invariants"]],
    }


def _short(digest: str | None) -> str:
    return f"`{(digest or '?')[:12]}…`"


def _digests(artifact: dict) -> tuple[str | None, str | None, list[str]]:
    """(binding digest, presentation digest, problems) across envelope v1/v2.

    Digests are RECOMPUTED from the spec body — the body, not the declaration,
    is authoritative. A PR can edit the spec and leave the declared digests
    stale (or hand-edit them), which would otherwise let a behavioural change
    render as cosmetic. Declared values are cross-checked and every mismatch
    is reported so the comment can say so out loud."""
    problems: list[str] = []
    spec = artifact.get("spec")
    recomputable = isinstance(spec, dict) and _REQUIRED_SPEC_KEYS.issubset(spec)

    if artifact.get("schema_version") == 1:
        declared = artifact.get("digest")
        if not recomputable:
            problems.append("spec body missing or malformed — trusting the declared digest")
            return declared, None, problems
        recomputed = identity._sha256(spec)
        if declared != recomputed:
            problems.append(f"digest: declared {_short(declared)} ≠ recomputed {_short(recomputed)}")
        return recomputed, None, problems

    declared_sem = artifact.get("semantic_digest")
    declared_pres = artifact.get("presentation_digest")
    if not recomputable:
        problems.append("spec body missing or malformed — trusting the declared digests")
        return declared_sem, declared_pres, problems
    sem = identity._sha256(_semantic_projection(spec))
    if declared_sem != sem:
        problems.append(f"semantic_digest: declared {_short(declared_sem)} ≠ recomputed {_short(sem)}")
    pres = declared_pres
    if "title" in spec:  # kernel >= v0.4.0: presentation independently verifiable
        pres = identity._sha256(_presentation_projection(spec))
        if declared_pres != pres:
            problems.append(f"presentation_digest: declared {_short(declared_pres)} "
                            f"≠ recomputed {_short(pres)}")
    return sem, pres, problems


def classify(old: dict | None, new: dict | None) -> str:
    if old is None:
        return "added"
    if new is None:
        return "removed"
    ob, op, _ = _digests(old)
    nb, np_, _ = _digests(new)
    if ob != nb:
        return "semantic"
    if op != np_ and (op or np_):
        return "presentation"
    return "none"


def render_file_section(path: str, old: dict | None, new: dict | None) -> tuple[str, str, bool]:
    kind = classify(old, new)
    head = {
        "semantic": "🔴 **Semantic change — approval invalidated**",
        "presentation": "🟡 Presentation change (wording only — non-invalidating)",
        "none": "⚪ No policy change (artifact touched, digests identical)",
        "added": "🆕 New policy artifact",
        "removed": "🗑️ Policy artifact removed",
    }[kind]
    lines = [f"### `{path}` — {head}", ""]

    problems = [p for art in (old, new) if art for p in _digests(art)[2]]
    if problems:
        lines += ["> ⚠️ **Declared digests do not match the spec body** (stale or "
                  "hand-edited declaration). The classification above is computed "
                  "from the spec body, which is authoritative.",
                  *(f"> - {p}" for p in problems), ""]

    if kind in ("semantic", "presentation", "none") and old and new:
        spec_old, spec_new = old.get("spec", {}), new.get("spec", {})
        try:
            diff_lines = identity.diff(spec_old, spec_new)
        except Exception as e:  # a malformed artifact must not kill the comment
            diff_lines = [f"(diff unavailable: {e})"]
        if diff_lines:
            lines += ["```", *diff_lines, "```", ""]
        ob = _digests(old)[0]
        nb = _digests(new)[0]
        if kind == "semantic":
            lines += [f"binding digest: {_short(ob)} → {_short(nb)} — "
                      "existing approvals for this policy no longer bind.", ""]
    elif kind == "added" and new:
        spec = new.get("spec", {})
        nb = _digests(new)[0]
        lines += [f"`{spec.get('name', '?')}` v{spec.get('version', '?')} — "
                  f"{len(spec.get('transitions', []))} transitions, "
                  f"{len(spec.get('invariants', []))} invariants; "
                  f"binding digest {_short(nb)}.", ""]
    return kind, "\n".join(lines), not problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-ref", required=True)
    ap.add_argument("--head-ref", required=True)
    ap.add_argument("--glob", default="*.policy.json")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    changed = [
        f for f in _git(args.repo, "diff", "--name-only",
                        args.base_ref, args.head_ref).splitlines()
        if f and fnmatch.fnmatch(Path(f).name, args.glob.split("/")[-1])
    ]

    worst = "none"
    verified = True
    sections: list[str] = []
    for path in changed:
        old = _show(args.repo, args.base_ref, path)
        new = _show(args.repo, args.head_ref, path)
        if old is None and new is None:
            continue
        kind, body, digest_ok = render_file_section(path, old, new)
        sections.append(body)
        verified = verified and digest_ok
        if _SEVERITY.index(kind) > _SEVERITY.index(worst):
            worst = kind

    if sections:
        body = "\n".join([
            MARKER,
            "## Statespec policy diff",
            "",
            *sections,
            "---",
            "*Rendered by [statespec-diff-action] from the committed `.policy.json` "
            "artifacts — digests recomputed from each spec body and cross-checked "
            "against the declared values. Semantic = the enforced behaviour changed; "
            "approvals bind to the semantic digest.*",
        ])
    else:
        body = ""

    if args.out:
        Path(args.out).write_text(body)
    else:
        print(body)

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write(f"change_kind={worst}\n")
            fh.write(f"has_changes={'true' if sections else 'false'}\n")
            fh.write(f"digest_verified={'true' if verified else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
