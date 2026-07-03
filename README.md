# statespec-diff-action

Comments the rendered **statespec policy diff** on pull requests that touch
`.policy.json` artifacts: was the change **semantic** (the enforced behaviour
moved — existing approvals no longer bind) or **presentation** (wording only —
non-invalidating)? Classification comes from the artifacts' *declared* digests;
the plain-English diff comes from the statespec kernel, vendored byte-identical
at tag `statespec-v0.4.0` (stdlib-only — nothing is installed on the runner).

A rule change should be visible in English in the PR, not archaeology in a
JSON blob. This is the demo-able unit of "governed lifecycle policy".

## Usage

```yaml
name: policy-diff
on:
  pull_request:
    paths: ["**/*.policy.json"]

permissions:
  pull-requests: write

jobs:
  policy-diff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ConceptPending/statespec-diff-action@main
        # inputs (all optional):
        # with:
        #   artifact-glob: "*.policy.json"
        #   comment: "true"
```

Outputs: `change_kind` (`semantic|added|removed|presentation|none`, worst
across files) and `has_changes` — usable as a gate:

```yaml
      - id: policy
        uses: ConceptPending/statespec-diff-action@main
      - if: steps.policy.outputs.change_kind == 'semantic'
        run: echo "::warning::semantic policy change — re-approval required"
```

While this repo is private, enable *Settings → Actions → Access → accessible
from repositories in the ConceptPending organization* so sibling repos can use it.

## Kernel vendoring

`kernel/` is vendored from `ConceptPending/statespec` at the pinned tag by
`scripts/vendor_kernel.py` (`sync` / `check`); never hand-edit it. Envelope
`schema_version` 1 and 2 artifacts are both understood (v1 = single digest,
strict: any change classifies as semantic).
