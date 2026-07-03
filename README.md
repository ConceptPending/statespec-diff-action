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

Pin to a commit SHA (or the `v1` tag) rather than `@main`, per standard
supply-chain hygiene. See the open [demo PR](../../pull/1) for the comment
rendered on a real semantic change.

## Kernel vendoring

`kernel/` is vendored from the statespec kernel repo at the pinned tag by
`scripts/vendor_kernel.py` (`sync` / `check`); never hand-edit it. The kernel
is MIT-licensed, stdlib-only, and also visible in the
[baseplate](https://github.com/ConceptPending/baseplate) reference branch. Envelope
`schema_version` 1 and 2 artifacts are both understood (v1 = single digest,
strict: any change classifies as semantic).
