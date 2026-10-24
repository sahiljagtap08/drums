"""The mislabelled-artifact blocker and the transparent reuse of earlier gate evidence."""
import json
import os
import subprocess
import sys

import pytest

from drums_compare import first_check as fc
from drums_compare import host_run as hr
from drums_compare import osworld_matched as om
from drums_compare import stages

sys.path.insert(0, os.path.dirname(__file__))
import test_host_run as th  # noqa: E402


class DrumsProvider:
    pass


class DockerProvider:
    pass


class FakeEnv:
    """What the adapter touches: a provider object, close, and the rollout surface."""

    def __init__(self, provider):
        self.provider, self.closed, self.log = provider(), False, []
        self.controller = self

    def post_execute(self, payload, timeout):
        return 200, {"status": "success", "output": "", "error": "", "returncode": 0}

    def reset(self, task_config=None, **kw):
        self.log.append("reset")
        return {}

    def step(self, a, pause=2):
        return {}, 0, False, {}

    def evaluate(self):
        return 1.0

    def close(self):
        self.closed = True


def drums_factory(worker):
    return FakeEnv(DrumsProvider)


def docker_factory(worker):
    return FakeEnv(DockerProvider)


# ---- the late capture, reproduced and fixed ----
def test_the_old_comprehension_labels_the_drums_factory_with_the_incumbents_name():
    factories = {"drums": drums_factory, "docker_kvm": docker_factory}
    old = {n: (lambda f=f: om.DesktopEnvAdapter(n, f)) for n, f in factories.items()}  # the code that ran on the host: `n` binds late
    assert old["drums"]().name == "docker_kvm" and old["drums"]().factory is drums_factory  # the Drums factory under the incumbent's label: the observed corruption


def test_make_arms_binds_label_factory_and_expected_provider_per_arm():
    arms = stages.make_arms({"drums": drums_factory, "docker_kvm": docker_factory}, "none")
    d, k = arms["drums"](), arms["docker_kvm"]()
    assert (d.name, d.factory, d.expect_provider) == ("drums", drums_factory, "DrumsProvider")
    assert (k.name, k.factory, k.expect_provider) == ("docker_kvm", docker_factory, "DockerProvider")
    v = stages.make_arms({"drums": drums_factory, "virtualbox": docker_factory}, "post_setup")
    assert v["virtualbox"]().expect_provider == "VirtualBoxProvider" and v["drums"]().reuse == "post_setup"


# ---- decisive identity from the constructed environment, before any rollout ----
def test_a_mismatched_environment_is_refused_at_open_before_any_rollout_and_its_envs_are_closed():
    ad = om.DesktopEnvAdapter("drums", docker_factory, expect_provider="DrumsProvider")  # a Docker environment under the Drums label
    with pytest.raises(om.IdentityMismatch, match="expects provider 'DrumsProvider'.*DockerProvider"):
        ad.open(1)
    assert ad.observed_provider == ["DockerProvider"] and ad.envs == []
    built = []

    def spy(worker):
        e = FakeEnv(DockerProvider)
        built.append(e)
        return e
    with pytest.raises(om.IdentityMismatch):
        om.run_arm(om.DesktopEnvAdapter("drums", spy, expect_provider="DrumsProvider"), {"t": {"id": "t", "config": []}}, ["t"], lambda: om.ScriptedAgent({}), 1, om.Prices(1.0))
    assert all(e.closed and "reset" not in e.log for e in built)  # no rollout ran, the environment was closed


def test_a_matching_environment_passes_and_the_summary_records_what_was_observed():
    ad = om.DesktopEnvAdapter("drums", drums_factory, expect_provider="DrumsProvider")
    res = om.run_arm(ad, {"t": {"id": "t", "config": []}}, ["t"], lambda: om.ScriptedAgent({}), 1, om.Prices(1.0), canary=False)
    assert res["summary"]["provider_observed"] == ["DrumsProvider"] and res["summary"]["valid"] == 1
    plain = om.run_arm(om.DesktopEnvAdapter("x", drums_factory), {"t": {"id": "t", "config": []}}, ["t"], lambda: om.ScriptedAgent({}), 1, om.Prices(1.0), canary=False)
    assert plain["summary"]["provider_observed"] is None  # no expectation, no check (unit-test fakes)


def test_run_alternating_refuses_a_block_whose_observed_provider_does_not_match_its_label_and_writes_nothing(tmp_path):
    clk = lambda: 0.0
    swapped = {"drums": lambda: om.DesktopEnvAdapter("drums", docker_factory), "docker_kvm": lambda: om.DesktopEnvAdapter("docker_kvm", docker_factory)}

    class Obs(om.DesktopEnvAdapter):
        def open(self, slots):
            super().open(slots)
            self.observed_provider = ["DockerProvider"]  # the adapter reports a Docker environment even under the Drums label
    swapped["drums"] = lambda: Obs("drums", docker_factory)
    with pytest.raises(om.IdentityMismatch, match="block 0 arm 'drums'.*no artifact written"):
        fc.run_alternating(swapped, {"t": {"id": "t", "config": []}}, ["t"], lambda: om.ScriptedAgent({}), 2, {"drums": om.Prices(1.0), "docker_kvm": om.Prices(1.0)}, str(tmp_path), {},
                           mem_reader=None, cpu_reader=None, arm_provider_expect={"drums": "DrumsProvider", "docker_kvm": "DockerProvider"})
    assert not [f for f in os.listdir(tmp_path) if f.startswith("block")]


def test_a_correct_two_arm_run_labels_every_block_with_its_own_arm_and_provider(tmp_path):
    arms = stages.make_arms({"drums": drums_factory, "docker_kvm": docker_factory}, "none")
    s = fc.run_alternating(arms, {"t": {"id": "t", "config": []}}, ["t", "t"], lambda: om.ScriptedAgent({}), 2, {"drums": om.Prices(1.0), "docker_kvm": om.Prices(1.0)}, str(tmp_path), {},
                           mem_reader=None, cpu_reader=None, canary=False, name_prefix=False, arm_provider_expect={"drums": "DrumsProvider", "docker_kvm": "DockerProvider"})
    for blk, arm, prov in (("block0_drums.json", "drums", "DrumsProvider"), ("block0_docker_kvm.json", "docker_kvm", "DockerProvider"), ("block1_docker_kvm.json", "docker_kvm", "DockerProvider"),
                           ("block1_drums.json", "drums", "DrumsProvider")):
        r = json.load(open(tmp_path / blk))
        assert r["summary"]["arm"] == arm and r["summary"]["provider_observed"] == [prov] and {row["arm"] for row in r["rows"]} == {arm}, blk
    assert [tuple(x) for x in s["block_order"]] == [(0, "drums"), (0, "docker_kvm"), (1, "docker_kvm"), (1, "drums")]


# ---- transparent reuse of earlier gate evidence ----
def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a], capture_output=True, text=True, check=True).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "g.py").write_text("def gate(x):\n    return x + 1\n\n\ndef run_only(x):\n    return x\n\n\nK = 1\n")
    (repo / "whole.py").write_text('"""doc"""\n\ndef f():\n    return 1\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "gated")
    return repo, _git(repo, "rev-parse", "HEAD")


GATE_FILES = {"g.py": ["gate", "K"], "whole.py": None}


def _prior(tmp_path, commit, **over):
    cfg, deps = th.setup(tmp_path, incumbent="docker_kvm")
    inputs = hr.compute_inputs(cfg, deps["sh"])
    inputs.update(drums_commit=commit, drums_tree_clean=True)
    g = {"all_ok": True, "config_sha256": hr.config_hash(cfg), "mode": cfg["mode"], "incumbent": "docker_kvm", "inputs": inputs, "parity_decisions": {}, "inputs_sha256": "x"}
    g.update(over)
    p = tmp_path / "prior_gates.json"
    p.write_text(json.dumps(g))
    return cfg, deps, inputs, str(p)


def _reuse(tmp_path, repo, cfg, deps, inputs, p, commit_now, **kw):
    now = dict(inputs, drums_commit=commit_now, drums_tree_clean=True)
    now.update(kw.pop("now_over", {}))
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    runner = lambda cmd, timeout=60: (lambda r: (r.returncode, (r.stdout + r.stderr).strip()))(subprocess.run(cmd, capture_output=True, text=True))
    return hr.reuse_gates(p, cfg, str(out), runner=runner, repo=str(repo), gate_files=GATE_FILES, inputs_now=now), out


def test_gates_are_reused_when_only_non_gate_code_changed_and_the_record_names_what_changed(tmp_path):
    repo, c1 = _repo(tmp_path)
    (repo / "g.py").write_text("def gate(x):\n    return x + 1\n\n\ndef run_only(x):\n    return x * 2  # changed, not gate-bearing\n\n\nK = 1\n")
    (repo / "new.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fix")
    c2 = _git(repo, "rev-parse", "HEAD")
    cfg, deps, inputs, p = _prior(tmp_path, c1)
    (prior, rec), out = _reuse(tmp_path, repo, cfg, deps, inputs, p, c2)
    assert rec["gated_commit"] == c1 and rec["running_commit"] == c2 and sorted(rec["files_changed_since_gated_commit"]) == ["g.py", "new.py"]
    assert rec["gate_bearing_definitions_identical"] == {"g.py": ["K", "gate"], "whole.py": ["<Expr:0>", "f"]} and "not_claimed" in rec
    assert json.load(open(out / "gates_reuse.json"))["gates_json_sha256"] == hr.hashlib.sha256(open(p, "rb").read()).hexdigest()
    assert prior["all_ok"] and json.load(open(p)) == prior  # the prior file is read, never rewritten


@pytest.mark.parametrize("edit,names", [("def gate(x):\n    return x + 2\n\n\ndef run_only(x):\n    return x\n\n\nK = 1\n", "gate"), ("def gate(x):\n    return x + 1\n\n\ndef run_only(x):\n    return x\n\n\nK = 2\n", "K")])
def test_a_changed_gate_bearing_definition_forces_the_gates_to_be_rerun(tmp_path, edit, names):
    repo, c1 = _repo(tmp_path)
    (repo / "g.py").write_text(edit)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "changes a gate")
    cfg, deps, inputs, p = _prior(tmp_path, c1)
    with pytest.raises(SystemExit, match=f"gate-bearing code changed.*{names}"):
        _reuse(tmp_path, repo, cfg, deps, inputs, p, _git(repo, "rev-parse", "HEAD"))


def test_a_changed_whole_module_gate_file_also_refuses(tmp_path):
    repo, c1 = _repo(tmp_path)
    (repo / "whole.py").write_text('"""doc"""\n\ndef f():\n    return 2\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "x")
    cfg, deps, inputs, p = _prior(tmp_path, c1)
    with pytest.raises(SystemExit, match="whole.py"):
        _reuse(tmp_path, repo, cfg, deps, inputs, p, _git(repo, "rev-parse", "HEAD"))


def test_any_other_changed_input_a_failed_prior_or_a_different_config_refuses(tmp_path):
    repo, c1 = _repo(tmp_path)
    cfg, deps, inputs, p = _prior(tmp_path, c1)
    with pytest.raises(SystemExit, match="bound inputs other than the Drums commit.*prices"):
        _reuse(tmp_path, repo, cfg, deps, inputs, p, c1, now_over={"prices": "0" * 64})
    with pytest.raises(SystemExit, match="not clean"):
        _reuse(tmp_path, repo, cfg, deps, inputs, p, c1, now_over={"drums_tree_clean": False})
    cfg2, deps2, inputs2, p2 = _prior(tmp_path, c1, all_ok=False)
    with pytest.raises(SystemExit, match="did not pass"):
        _reuse(tmp_path, repo, cfg2, deps2, inputs2, p2, c1)
    cfg3, deps3, inputs3, p3 = _prior(tmp_path, c1)
    with pytest.raises(SystemExit, match="different config"):
        hr.reuse_gates(p3, dict(cfg3, blocks=9), str(tmp_path), gate_files=GATE_FILES, inputs_now=inputs3)


def test_the_same_commit_reuses_trivially_and_check_can_skip_the_hash_without_changing_the_config(tmp_path):
    repo, c1 = _repo(tmp_path)
    cfg, deps, inputs, p = _prior(tmp_path, c1)
    (prior, rec), _ = _reuse(tmp_path, repo, cfg, deps, inputs, p, c1)
    assert rec["files_changed_since_gated_commit"] == [] and rec["gated_commit"] == rec["running_commit"]
    before = hr.config_hash(cfg)
    r = hr.check_all(cfg, deps, options={"skip_qcow2_hash": "sha256 verified at 14:12 and 14:31"})
    c = [x for x in r["checks"] if x["name"] == "qcow2_sha256_matches_record"][0]
    assert c["ok"] and "NOT RECOMPUTED" in c["detail"] and r["options"] == {"skip_qcow2_hash": "sha256 verified at 14:12 and 14:31"} and hr.config_hash(cfg) == before
    (tmp_path / "cfg.json").write_text(json.dumps(cfg))
    with pytest.raises(SystemExit, match="needs --earlier-verified"):
        hr.main(["check", "--config", str(tmp_path / "cfg.json"), "--out", str(tmp_path / "o"), "--skip-qcow2-hash"])


def test_the_real_gate_bearing_definitions_exist_in_the_repo_so_the_rule_cannot_silently_pass_on_missing_names():
    root = hr.repo_root()
    for path, names in hr.GATE_BEARING.items():
        parts = hr._ast_parts(open(os.path.join(root, path)).read(), names)
        if names is not None:
            assert set(names) <= set(parts), (path, set(names) - set(parts))
        else:
            assert parts
