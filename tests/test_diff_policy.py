"""End-to-end test: temp git repo with policy artifacts across two commits;
diff_policy.py classifies and renders correctly."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel import identity  # noqa: E402
from kernel.core import StateSpec, Transition  # noqa: E402
from kernel.expr import field  # noqa: E402
from scripts.diff_policy import classify, render_file_section  # noqa: E402


def _spec(threshold=0, label="Submit for review", version=1):
    return StateSpec(
        name="demo", title="Demo workflow",
        states={"draft": "", "done": ""},
        fields={"errors": "int"},
        initial="draft", terminal=frozenset({"done"}),
        transitions=(Transition("submit", ("draft",), "done",
                                roles=frozenset({"author"}),
                                guard=field("errors").le(threshold),
                                label=label, control_id="DEMO-1"),),
        invariants=(), version=version,
    )


def _record(**kw) -> dict:
    return identity.policy_record(_spec(**kw))


def test_classify_semantic_presentation_none():
    base = _record()
    assert classify(base, _record(threshold=5, version=2)) == "semantic"
    assert classify(base, _record(label="Reworded label")) == "presentation"
    assert classify(base, json.loads(json.dumps(base))) == "none"
    assert classify(None, base) == "added"
    assert classify(base, None) == "removed"


def test_classify_v1():
    spec = identity.canonical(_spec())
    v1 = {"schema_version": 1, "spec_version": 1, "digest": identity._sha256(spec), "spec": spec}
    spec2 = identity.canonical(_spec(threshold=5, version=2))
    v1_moved = {"schema_version": 1, "spec_version": 2,
                "digest": identity._sha256(spec2), "spec": spec2}
    assert classify(v1, v1_moved) == "semantic"
    assert classify(v1, dict(v1)) == "none"


def test_stale_declared_digests_cannot_hide_a_semantic_change():
    """The attack the recompute exists for: edit the spec body, keep the old
    declared digests. Classification must come from the body."""
    base = _record()
    tampered = _record(threshold=5, version=2)
    tampered["semantic_digest"] = base["semantic_digest"]
    tampered["presentation_digest"] = base["presentation_digest"]
    assert classify(base, tampered) == "semantic"
    kind, body, digest_ok = render_file_section("p.json", base, tampered)
    assert kind == "semantic"
    assert not digest_ok
    assert "do not match the spec body" in body
    assert "semantic_digest: declared" in body


def test_declaration_only_corruption_is_none_with_warning():
    """Spec body unchanged, declared digest corrupted: no behavioural change,
    but the mismatch is reported and digest_verified is false."""
    base = _record()
    corrupt = dict(base, semantic_digest="not-a-real-digest")
    assert classify(base, corrupt) == "none"
    kind, body, digest_ok = render_file_section("p.json", base, corrupt)
    assert kind == "none"
    assert not digest_ok
    assert "do not match the spec body" in body


def test_recomputed_digests_match_kernel_emitted_artifacts():
    """The dict-level projections must stay byte-equivalent to the kernel's."""
    from scripts.diff_policy import _digests
    rec = _record()
    binding, pres, problems = _digests(rec)
    assert problems == []
    assert binding == rec["semantic_digest"] == identity.semantic_digest(_spec())
    assert pres == rec["presentation_digest"] == identity.presentation_digest(_spec())


def test_render_semantic_section_names_the_change():
    kind, body, digest_ok = render_file_section("docs/specs/demo.policy.json",
                                                _record(), _record(threshold=5, version=2))
    assert kind == "semantic"
    assert digest_ok
    assert "Semantic change" in body
    assert "errors ≤ 0" in body and "errors ≤ 5" in body  # kernel's plain-English diff
    assert "no longer bind" in body
    assert "do not match" not in body  # clean artifacts get no warning block


def test_render_presentation_section():
    kind, body, digest_ok = render_file_section("p.json", _record(), _record(label="New words"))
    assert kind == "presentation"
    assert digest_ok
    assert "wording only" in body
    assert "label changed" in body


def test_cli_end_to_end(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", repo, "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True)

    art = repo / "docs" / "demo.policy.json"
    art.parent.mkdir(parents=True)
    art.write_text(json.dumps(_record(), indent=2))
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "base"], check=True)
    base = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()

    art.write_text(json.dumps(_record(threshold=5, version=2), indent=2))
    subprocess.run(["git", "-C", repo, "commit", "-aqm", "change"], check=True)
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()

    out_file = tmp_path / "body.md"
    gh_out = tmp_path / "gh_output"
    script = Path(__file__).resolve().parent.parent / "scripts" / "diff_policy.py"
    subprocess.run(
        [sys.executable, str(script), "--base-ref", base, "--head-ref", head,
         "--repo", str(repo), "--out", str(out_file)],
        check=True, env={"GITHUB_OUTPUT": str(gh_out), "PATH": "/usr/bin:/bin"},
    )
    body = out_file.read_text()
    assert body.startswith("<!-- statespec-diff -->")
    assert "docs/demo.policy.json" in body
    assert "change_kind=semantic" in gh_out.read_text()
    assert "has_changes=true" in gh_out.read_text()
    assert "digest_verified=true" in gh_out.read_text()
