#!/usr/bin/env python3
"""Render the statespec policy diff for a PR as a markdown comment body.

Usage:
    diff_policy.py --base-ref <sha> --head-ref <sha> [--glob '*.policy.json']
                   [--repo <dir>] [--out <file>]

For every changed policy artifact between base and head, classifies the move
(semantic — approval-invalidating / presentation — wording only / none) from
the artifacts' declared digests, and renders the kernel's plain-English
semantic diff. Exit code is always 0; the classification lands in the body
and in $GITHUB_OUTPUT (change_kind=semantic|presentation|none|added|removed).

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


def _digests(artifact: dict) -> tuple[str | None, str | None]:
    """(binding digest, presentation digest) across envelope v1/v2."""
    if artifact.get("schema_version") == 1:
        return artifact.get("digest"), None
    return artifact.get("semantic_digest"), artifact.get("presentation_digest")


def classify(old: dict | None, new: dict | None) -> str:
    if old is None:
        return "added"
    if new is None:
        return "removed"
    ob, op = _digests(old)
    nb, np_ = _digests(new)
    if ob != nb:
        return "semantic"
    if op != np_ and (op or np_):
        return "presentation"
    return "none"


def render_file_section(path: str, old: dict | None, new: dict | None) -> tuple[str, str]:
    kind = classify(old, new)
    head = {
        "semantic": "🔴 **Semantic change — approval invalidated**",
        "presentation": "🟡 Presentation change (wording only — non-invalidating)",
        "none": "⚪ No policy change (artifact touched, digests identical)",
        "added": "🆕 New policy artifact",
        "removed": "🗑️ Policy artifact removed",
    }[kind]
    lines = [f"### `{path}` — {head}", ""]

    if kind in ("semantic", "presentation", "none") and old and new:
        spec_old, spec_new = old.get("spec", {}), new.get("spec", {})
        try:
            diff_lines = identity.diff(spec_old, spec_new)
        except Exception as e:  # a malformed artifact must not kill the comment
            diff_lines = [f"(diff unavailable: {e})"]
        if diff_lines:
            lines += ["```", *diff_lines, "```", ""]
        ob, _ = _digests(old)
        nb, _ = _digests(new)
        if kind == "semantic":
            lines += [f"binding digest: `{(ob or '?')[:12]}…` → `{(nb or '?')[:12]}…` — "
                      "existing approvals for this policy no longer bind.", ""]
    elif kind == "added" and new:
        spec = new.get("spec", {})
        nb, _ = _digests(new)
        lines += [f"`{spec.get('name', '?')}` v{spec.get('version', '?')} — "
                  f"{len(spec.get('transitions', []))} transitions, "
                  f"{len(spec.get('invariants', []))} invariants; "
                  f"binding digest `{(nb or '?')[:12]}…`.", ""]
    return kind, "\n".join(lines)


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
    sections: list[str] = []
    for path in changed:
        old = _show(args.repo, args.base_ref, path)
        new = _show(args.repo, args.head_ref, path)
        if old is None and new is None:
            continue
        kind, body = render_file_section(path, old, new)
        sections.append(body)
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
            "artifacts. Semantic = the enforced behaviour changed; approvals bind to "
            "the semantic digest.*",
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
