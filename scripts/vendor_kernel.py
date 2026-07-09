#!/usr/bin/env python3
"""Vendor (or verify) the StateSpec kernel at its frozen tag.

The control plane reuses the OSS kernel's canonicalisation, digest, diff and
expression code — never reimplements it. A canonicalisation fork between OSS
and Cloud is a silent catastrophic bug, so the vendored copy is byte-identical
to the pinned tag except for one mechanical rewrite: import statements'
`statespec` package prefix -> `kernel`.

    python scripts/vendor_kernel.py sync     # copy from the pinned tag
    python scripts/vendor_kernel.py check    # verify parity (CI gate); exit 1 on drift

Source resolution: $STATESPEC_REPO (path to a ConceptPending/statespec clone)
or ../statespec. The tag must exist in that clone (`git fetch --tags` if
missing). Before statespec-v0.4.0 the kernel lived in the baseplate repo; it
now has its own home.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

KERNEL_TAG = "statespec-v0.4.1"
KERNEL_FILES = ["__init__.py", "core.py", "expr.py", "identity.py", "render.py"]
SRC_PREFIX = "src/statespec"
DEST = Path(__file__).resolve().parent.parent / "kernel"
OLD_IMPORT, NEW_IMPORT = "statespec", "kernel"
# Anchored to import statements: the bare token `statespec` also appears in
# prose and docstrings, which must stay untouched.
IMPORT_RE = re.compile(rf"^(\s*from|\s*import) {OLD_IMPORT}\b", flags=re.M)

HEADER = (
    f"# Vendored from ConceptPending/statespec {KERNEL_TAG} ({SRC_PREFIX}) — DO NOT EDIT.\n"
    f"# Only import statements' `{OLD_IMPORT}` prefix -> `{NEW_IMPORT}` is rewritten.\n"
    f"# Sync/verify with scripts/vendor_kernel.py.\n"
)


def repo_path() -> Path:
    p = Path(os.environ.get("STATESPEC_REPO", Path(__file__).resolve().parents[2] / "statespec"))
    if not (p / ".git").exists():
        sys.exit(f"statespec clone not found at {p}; set STATESPEC_REPO")
    return p


def upstream(repo: Path, name: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "show", f"{KERNEL_TAG}:{SRC_PREFIX}/{name}"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        sys.exit(f"cannot read {name} at tag {KERNEL_TAG}: {out.stderr.strip()}")
    return out.stdout


def vendored_form(source: str) -> str:
    return HEADER + IMPORT_RE.sub(rf"\1 {NEW_IMPORT}", source)


def sync() -> None:
    repo = repo_path()
    DEST.mkdir(parents=True, exist_ok=True)
    for name in KERNEL_FILES:
        (DEST / name).write_text(vendored_form(upstream(repo, name)))
        print(f"vendored {name}")
    print(f"kernel synced at {KERNEL_TAG}")


def check() -> None:
    repo = repo_path()
    drift = []
    for name in KERNEL_FILES:
        expected = vendored_form(upstream(repo, name))
        actual = (DEST / name).read_text() if (DEST / name).exists() else None
        if actual != expected:
            drift.append(name)
    if drift:
        sys.exit(f"kernel drift vs {KERNEL_TAG}: {', '.join(drift)} — run vendor_kernel.py sync")
    print(f"kernel parity OK at {KERNEL_TAG}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    {"sync": sync, "check": check}.get(mode, lambda: sys.exit(f"unknown mode {mode}"))()
