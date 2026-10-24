"""Fail-closed behaviour of the host runner, with fakes only: no VM, no host, no AWS."""
import hashlib
import json
import os
import subprocess

import pytest

from drums_compare import host_run as hr
from drums_compare import vbox

TASK = {"id": hr.FIRST_TASK, "evaluator": {"func": "exact_match", "result": {"type": "vm_command_line"}, "expected": {"type": "rule"}}}


class Vb:
    """A fake VBoxManage."""

    def __init__(self, cpus=2, mem=4096, snaps=("init_state",), cap="100"):
        self.cpus, self.mem, self.snaps, self.cap = cpus, mem, snaps, cap

    def __call__(self, cmd, **kw):
        a = cmd[1:]
        out = ""
        if a[0] == "showvminfo":
            out = f'name="Ubuntu0"\nUUID="u-1"\nmemory={self.mem}\ncpus={self.cpus}\nVMState="poweroff"\nnic1="hostonly"\ncpuexecutioncap="{self.cap}"\n'
        elif a[0] == "snapshot" and a[2] == "list":
            out = "".join(f'SnapshotName{"" if i == 0 else "-" + str(i)}="{n}"\n' for i, n in enumerate(self.snaps))
        return subprocess.CompletedProcess(cmd, 0, out, "")


def setup(tmp_path, **over):
    src = tmp_path / "osw"
    d = src / "evaluation_examples" / "examples" / "os"
    d.mkdir(parents=True)
    (d / (hr.FIRST_TASK + ".json")).write_text(json.dumps(TASK))
    pr = tmp_path / "prices.json"
    pr.write_text(json.dumps({"host_usd_per_hour": 4.6, "label": "owner price sheet 2026-10"}))
    cfg = {"osworld_src": str(src), "vm": "Ubuntu0", "tasks": [{"id": hr.FIRST_TASK}], "prices": str(pr), "max_runtime_s": 3600, "external_watcher_confirmed_by": "owner"}
    cfg.update(over.pop("cfg", {}))
    state = {"virt": "none", "head": hr.OSWORLD_PIN, "dirty": "", "vbox": Vb(), "stubs": [], "arch": "x86_64"}
    state.update(over)

    def sh(cmd, timeout=60):
        if cmd[0] == "systemd-detect-virt":
            return 0, state["virt"]
        if cmd[0] == "git" and cmd[3] == "rev-parse":
            return 0, state["head"]
        if cmd[0] == "git" and cmd[3] == "status":
            return 0, state["dirty"]
        if cmd[0] == "VBoxManage":
            return 0, "7.0.20r163906"
        return 1, ""
    deps = {"sh": sh, "import": lambda allow=(): (object(), state["stubs"]) if not state.get("import_error") else (_ for _ in ()).throw(ModuleNotFoundError("No module named 'librosa'", name="librosa")),
            "read": lambda p: " vmx ", "machine": lambda: state["arch"], "vbox_runner": state["vbox"], "preflight": lambda cfg: state.get("pf", {"go_drums": True, "go_drums_restore": True, "no_go_reasons": []}),
            "kit_ok": lambda: True}
    return cfg, deps


def failing(res):
    return {c["name"] for c in res["checks"] if not c["ok"]}


def test_everything_present_passes_and_is_hashed(tmp_path):
    cfg, deps = setup(tmp_path)
    r = hr.check_all(cfg, deps)
    assert r["all_ok"], failing(r)
    assert r["config_sha256"] == hr.config_hash(cfg) and "image_licence" in r["known_risks"]


@pytest.mark.parametrize("over,name", [
    ({"virt": "kvm"}, "host_is_not_virtualized"), ({"arch": "aarch64"}, "host_arch_x86_64"), ({"head": "deadbeef"}, "osworld_pinned"),
    ({"dirty": " M desktop_env/x.py"}, "osworld_unmodified"), ({"vbox": Vb(cpus=4)}, "vm_settings_readback"), ({"vbox": Vb(mem=2048)}, "vm_settings_readback"),
    ({"vbox": Vb(cap="50")}, "vm_settings_readback"), ({"vbox": Vb(snaps=())}, "vm_has_init_state_snapshot"),
    ({"pf": {"go_drums": False, "go_drums_restore": True, "no_go_reasons": ["ip: not found"]}}, "drums_preflight_go"),
    ({"import_error": True}, "desktop_env_imports"),
])
def test_each_missing_or_wrong_thing_refuses(tmp_path, over, name):
    cfg, deps = setup(tmp_path, **over)
    r = hr.check_all(cfg, deps)
    assert not r["all_ok"] and name in failing(r)


def test_placeholder_prices_missing_ceiling_and_watcher_refuse(tmp_path):
    cfg, deps = setup(tmp_path)
    json.dump({"host_usd_per_hour": 4.6, "label": "harness placeholder, not a price"}, open(cfg["prices"], "w"))
    cfg["max_runtime_s"] = 999999
    cfg["external_watcher_confirmed_by"] = ""
    assert {"prices_are_owner_inputs", "runtime_ceiling_set", "external_watcher_asserted"} <= failing(hr.check_all(cfg, deps))


def test_another_task_needs_actions_and_a_validated_record_and_custom_json_needs_a_variant_label(tmp_path):
    cfg, deps = setup(tmp_path)
    other = tmp_path / "osw" / "evaluation_examples" / "examples" / "os" / "2cd43775.json"
    other.write_text(json.dumps({"id": "2cd43775", "evaluator": {"func": "check_auto_saving_time"}}))
    cfg["tasks"].append({"id": "2cd43775"})
    assert "tasks_allowed" in failing(hr.check_all(cfg, deps))
    cfg["tasks"][1] = {"id": "2cd43775", "actions": ["pyautogui.click(1,1)"], "actions_validated": {"host": "h", "date": "d"}}
    assert hr.check_all(cfg, deps)["all_ok"]
    cfg["tasks"][1]["task_json"] = str(other)
    assert "tasks_allowed" in failing(hr.check_all(cfg, deps))
    cfg["tasks"][1]["variant"] = {"label": "VARIANT: home-relative path"}
    assert hr.check_all(cfg, deps)["all_ok"]


def test_a_stub_is_refused_unless_allowed_and_never_for_a_function_that_imports_it(tmp_path):
    cfg, deps = setup(tmp_path, stubs=["librosa"])
    root = tmp_path / "osw" / "desktop_env" / "evaluators" / "metrics"
    root.mkdir(parents=True)
    (root / "vlc.py").write_text("import librosa\ndef compare_audios(a, b):\n    return librosa.load(a)\n")
    (root / "general.py").write_text("def exact_match(r, e):\n    return r == e\n")
    r = hr.check_all(cfg, deps)
    assert r["all_ok"] and r["stubbed_modules"] == ["librosa"]  # exact_match does not live in vlc.py
    t = json.loads(open(next((tmp_path / "osw" / "evaluation_examples" / "examples" / "os").glob("*.json"))).read())
    assert hr.stub_reach(str(tmp_path / "osw"), [{"id": "x", "task": {"evaluator": {"func": "compare_audios"}}}], ["librosa"]) == [("x", "compare_audios", "metrics/vlc.py")]
    assert hr.stub_reach(str(tmp_path / "osw"), [{"id": "x", "task": t}], ["librosa"]) == []


def test_run_and_gates_refuse_without_matching_stamps(tmp_path):
    cfg, deps = setup(tmp_path)
    out = tmp_path / "run"
    out.mkdir()
    with pytest.raises(SystemExit):
        hr.need_checks(str(out), cfg)
    r = hr.check_all(cfg, deps)
    json.dump(r, open(out / "checks.json", "w"))
    assert hr.need_checks(str(out), cfg)
    with pytest.raises(SystemExit):
        hr.need_checks(str(out), dict(cfg, blocks=9))  # a changed config invalidates the stamp
    with pytest.raises(SystemExit):
        hr.need_gates(str(out), cfg)
    json.dump({"all_ok": False, "config_sha256": hr.config_hash(cfg)}, open(out / "gates.json", "w"))
    with pytest.raises(SystemExit):
        hr.need_gates(str(out), cfg)


class FakeLease:
    """Models a provider with LIVE snapshots: state is a dict; restore puts back the saved dict (processes included)."""

    def __init__(self, live=True):
        self.live, self.state, self.saved = live, {}, {}
        self.holder = None

    def reset(self, task):
        self.state = {"dir": "Jan_1", "files": {}}

    def save_branch(self, name):
        import copy
        self.saved[name] = copy.deepcopy(self.state)

    def restore_branch(self, name, task):
        import copy
        self.state = copy.deepcopy(self.saved[name])

    def step(self, a):
        if a == "correct":
            self.state["dir"] = "Jan_2"

    def evaluate(self):
        return 1.0 if self.state["dir"] == "Jan_2" else 0.0


def test_score_gate_passes_for_a_correct_provider_and_fails_when_revert_does_not_reset_state():
    assert hr.score_gate(FakeLease(), {}, "correct", "wrong", "n")["pass"]

    class NoRevert(FakeLease):
        def restore_branch(self, name, task):
            pass
    assert not hr.score_gate(NoRevert(), {}, "correct", "wrong", "n")["pass"]


def test_export_hashes_every_file_and_excludes_itself(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.log").write_text("x")
    tgz, sums = hr.export(str(tmp_path))
    assert os.path.exists(tgz) and set(sums) == {"a.json", os.path.join("sub", "b.log")}
    assert sums["a.json"] == hashlib.sha256(b"{}").hexdigest()
    hr.export(str(tmp_path))  # idempotent: a second export does not hash the first export's outputs
    assert "run_export.tar.gz" not in open(tmp_path / "SHA256SUMS").read()


def test_vbox_probe_reads_vm_processes_from_a_fake_proc(tmp_path):
    for pid, cmd in ((101, ["/usr/lib/virtualbox/VBoxHeadless", "--comment", "Ubuntu0", "--startvm", "u-1"]), (102, ["/bin/sleep", "1"])):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes("\0".join(cmd).encode() + b"\0")
        (d / "stat").write_text(f"{pid} (VBoxHeadless) S " + " ".join(["0"] * 10) + " 250 50 " + " ".join(["0"] * 30))
        (d / "statm").write_text("1000 500 0 0 0 0 0")
    assert vbox.vm_pids("u-1", "Ubuntu0", str(tmp_path)) == [101]
    p = vbox.VBoxProbe("u-1", "Ubuntu0", period=0.05, proc_root=str(tmp_path), hz=100)
    p.begin()
    out = p.end()
    assert out["cpu_s_lower_bound"] == 3.0 and out["vm_processes_max"] == 1 and out["empty_samples"] == 0 and out["rss_peak_mib_sampled"] > 0


def test_snapshot_cleanup_refuses_the_base_snapshot(tmp_path):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    r = vbox.delete_snapshots("vm", ["init_state", "b0-post_setup-x-w0"], runner)
    assert r["init_state"].startswith("refused") and r["b0-post_setup-x-w0"] is None and len(calls) == 1
