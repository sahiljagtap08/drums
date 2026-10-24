"""Fail-closed behaviour of the host runner, with fakes only: no VM, no container, no host, no AWS."""
import hashlib
import io
import json
import os
import subprocess

import pytest

from drums_compare import docker_telemetry as dt
from drums_compare import host_run as hr
from drums_compare import vbox

TASK = {"id": hr.FIRST_TASK, "evaluator": {"func": "exact_match", "result": {"type": "vm_command_line"}, "expected": {"type": "rule"}}}
IMAGE = {"source_url": "u", "license_field": "BLANK", "os_release": 'VERSION_ID="22.04"\n', "kernel": "5.15", "desktop": "ubuntu:GNOME", "browser": "Google Chrome 120",
         "libreoffice": "LibreOffice 7.3.7.2", "dpkg": ["a=1"], "composition_known": True, "zip_sha256": "ab"}
DRUMS = {"os_release": {"VERSION_ID": "24.04"}, "kernel": "6.8", "dpkg": {"openbox": "3", "soffice_version_string": "LibreOffice 24.2.7.2 420(Build:2)"},
         "browsers": {"/opt/pw/chrome": "Chromium 141"}}


class Vb:
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


def setup(tmp_path, incumbent="virtualbox", **over):
    src = tmp_path / "osw"
    d = src / "evaluation_examples" / "examples" / "os"
    d.mkdir(parents=True, exist_ok=True)
    (d / (hr.FIRST_TASK + ".json")).write_text(json.dumps(TASK))
    pr = tmp_path / "prices.json"
    pr.write_text(json.dumps({"host_usd_per_hour": 4.6, "label": "owner price sheet 2026-10"}))
    cfg = {"mode": "architecture_measurement", "incumbent": incumbent, "host_kind": "baremetal", "guest_cpus": 2, "guest_memory_mib": 4096, "memory_admission_mib": 1000,
           "osworld_src": str(src), "vm": "Ubuntu0", "tasks": [{"id": hr.FIRST_TASK}], "prices": str(pr), "max_runtime_s": 3600, "external_watcher_confirmed_by": "owner"}
    if incumbent == "docker_kvm":
        q = tmp_path / "Ubuntu.qcow2"
        q.write_bytes(b"qcow")
        cfg.update({"host_kind": "nested_kvm", "guest_cpus": 4, "qcow2": str(q), "qcow2_bytes": 4, "qcow2_sha256": hashlib.sha256(b"qcow").hexdigest(),
                    "docker_image_digest": "sha256:0e64", "verify_sha256": True, "memory_admission_mib": 1000})
    cfg.update(over.pop("cfg", {}))
    st = {"virt": "none" if incumbent == "virtualbox" else "amazon", "head": hr.OSWORLD_PIN, "dirty": "", "vbox": Vb(), "stubs": [], "arch": "x86_64", "euid": 0, "kvm": True, "clean": True,
          "avail_kib": 8 * 1024 * 1024, "docker_dig": "happysixd/osworld-docker@sha256:0e64", "docker_env": {"DISK_SIZE": "32G", "RAM_SIZE": "4G", "CPU_CORES": "4"}}
    st.update(over)

    def sh(cmd, timeout=60):
        if cmd[0] == "systemd-detect-virt":
            return 0, st["virt"]
        if cmd[0] == "git" and cmd[3] == "rev-parse":
            return 0, st["head"]
        if cmd[0] == "git" and cmd[3] == "status":
            return 0, st["dirty"]
        if cmd[0] == "VBoxManage":
            return 0, "7.0.20r163906"
        if cmd[:3] == ["docker", "version", "--format"]:
            return 0, "27.3.1"
        if cmd[:3] == ["docker", "image", "inspect"]:
            return 0, st["docker_dig"]
        return 1, ""

    def imp(allow=()):
        if st.get("import_error"):
            raise ModuleNotFoundError("No module named 'librosa'", name="librosa")
        return object(), st["stubs"]

    def read(p):
        if p == "/proc/cpuinfo":
            return " vmx "
        return f"MemTotal: {16 * 1024 * 1024} kB\nMemAvailable: {st['avail_kib']} kB\n"
    existing = {"/dev/kvm"} if st["kvm"] else set()
    deps = {"sh": sh, "import": imp, "read": read, "machine": lambda: st["arch"], "vbox_runner": st["vbox"], "euid": lambda: st["euid"],
            "exists": lambda p: p in existing or os.path.exists(p), "docker_env": lambda src: st["docker_env"],
            "source_identity": lambda: {"drums_commit": "c" * 40, "drums_tree_clean": st["clean"]},
            "preflight": lambda cfg: st.get("pf", {"go_drums": True, "go_drums_restore": True, "no_go_reasons": []}), "kit_ok": lambda: True}
    return cfg, deps


def failing(res):
    return {c["name"] for c in res["checks"] if not c["ok"]}


# ---- shared checks ----
def test_virtualbox_baremetal_architecture_mode_passes_and_labels_itself(tmp_path):
    cfg, deps = setup(tmp_path)
    r = hr.check_all(cfg, deps)
    assert r["all_ok"], failing(r)
    assert r["inputs_sha256"] and r["inputs"]["config"] == hr.config_hash(cfg) and "image_licence" in r["known_risks"]
    assert any(c["name"] == "architecture_measurement_labelled" and "NOT_A_COMPARISON" in c["detail"] and "UNKNOWN" in c["detail"] for c in r["checks"])


@pytest.mark.parametrize("over,name", [
    ({"virt": "kvm"}, "host_is_not_virtualized"), ({"arch": "aarch64"}, "host_arch_x86_64"), ({"head": "deadbeef"}, "osworld_pinned"), ({"dirty": " M x.py"}, "osworld_unmodified"),
    ({"vbox": Vb(cpus=4)}, "vm_settings_readback"), ({"vbox": Vb(mem=2048)}, "vm_settings_readback"), ({"vbox": Vb(cap="50")}, "vm_settings_readback"),
    ({"vbox": Vb(snaps=())}, "vm_has_init_state_snapshot"), ({"pf": {"go_drums": False, "go_drums_restore": True, "no_go_reasons": ["ip"]}}, "drums_preflight_go"),
    ({"import_error": True}, "desktop_env_imports"), ({"euid": 1000}, "running_as_root"), ({"clean": False}, "drums_source_clean_and_recorded"),
    ({"avail_kib": 1024}, "memory_admission"),
])
def test_each_missing_or_wrong_thing_refuses(tmp_path, over, name):
    cfg, deps = setup(tmp_path, **over)
    r = hr.check_all(cfg, deps)
    assert not r["all_ok"] and name in failing(r)


def test_memory_admission_threshold_must_be_set(tmp_path):
    cfg, deps = setup(tmp_path)
    del cfg["memory_admission_mib"]
    assert "memory_admission" in failing(hr.check_all(cfg, deps))


def test_mode_and_incumbent_must_be_declared_and_reuse_must_match(tmp_path):
    cfg, deps = setup(tmp_path, cfg={"mode": "benchmark"})
    assert "mode_and_incumbent_declared" in failing(hr.check_all(cfg, deps))
    cfg, deps = setup(tmp_path, cfg={"reuse": "none"})
    assert "reuse_matches_the_incumbent" in failing(hr.check_all(cfg, deps))
    cfg, deps = setup(tmp_path, incumbent="docker_kvm", cfg={"reuse": "post_setup"})
    assert "reuse_matches_the_incumbent" in failing(hr.check_all(cfg, deps))  # the Docker provider cannot snapshot


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


# ---- stubs ----
def _vlc_tree(tmp_path):
    root = tmp_path / "osw" / "desktop_env" / "evaluators" / "metrics"
    root.mkdir(parents=True)
    (root / "vlc.py").write_text("import librosa\ndef compare_audios(a, b):\n    return librosa.load(a)\n")
    (root / "general.py").write_text("def exact_match(r, e):\n    return r == e\n")


def test_comparison_mode_refuses_any_stub_and_any_allowance_architecture_mode_records_it(tmp_path):
    cfg, deps = setup(tmp_path, stubs=["librosa"], cfg={"mode": "comparison", "allow_stub": ["librosa"]})
    _vlc_tree(tmp_path)
    r = hr.check_all(cfg, deps)
    assert "comparison_mode_has_no_stub" in failing(r) and r["stubbed_modules"] == ["librosa"] and r["allow_stub"] == ["librosa"]
    cfg2, deps2 = setup(tmp_path, stubs=["librosa"], cfg={"allow_stub": ["librosa"]})
    r2 = hr.check_all(cfg2, deps2)
    assert r2["all_ok"] and r2["stubbed_modules"] == ["librosa"]
    assert any("ANALYSIS ONLY" in c["detail"] for c in r2["checks"] if c["name"].startswith("stub_not_directly"))
    assert r2["known_risks"]["stub_analysis"].startswith("stub_reach inspects DIRECT imports only")


def test_stub_reach_sees_a_direct_import_and_documents_that_a_transitive_one_is_missed(tmp_path):
    _vlc_tree(tmp_path)
    root = tmp_path / "osw" / "desktop_env" / "evaluators" / "metrics"
    (root / "wrapper.py").write_text("from . import vlc\ndef sneaky(a):\n    return vlc.compare_audios(a, a)\n")
    src = str(tmp_path / "osw")
    assert hr.stub_reach(src, [{"id": "x", "task": {"evaluator": {"func": "compare_audios"}}}], ["librosa"]) == [("x", "compare_audios", "metrics/vlc.py")]
    assert hr.stub_reach(src, [{"id": "x", "task": {"evaluator": {"func": "sneaky"}}}], ["librosa"]) == []  # transitive: NOT detected, which is why comparison mode forbids stubs


# ---- image manifest, differences, equivalence ----
def test_image_manifest_validation_and_difference_detection():
    assert hr.validate_image_manifest(IMAGE) == []
    assert hr.validate_image_manifest({**IMAGE, "composition_known": False, "dpkg": []})
    diffs = {d["aspect"] for d in hr.detect_differences(IMAGE, DRUMS)}
    assert diffs == {"os_release", "kernel", "desktop", "browser", "libreoffice"}


def test_comparison_mode_needs_the_image_manifest_and_measured_digest_bound_evidence_for_every_difference(tmp_path):
    cfg, deps = setup(tmp_path, cfg={"mode": "comparison"})
    r = hr.check_all(cfg, deps)
    assert "comparison_equivalence_evidenced" in failing(r) and "no image manifest" in [c for c in r["checks"] if c["name"] == "comparison_equivalence_evidenced"][0]["detail"]
    im, dm = tmp_path / "image.json", tmp_path / "drums.json"
    im.write_text(json.dumps(IMAGE))
    dm.write_text(json.dumps(DRUMS))
    cfg["image_manifest"], cfg["drums_manifest"] = str(im), str(dm)
    r = hr.check_all(cfg, deps)
    gaps = [c for c in r["checks"] if c["name"] == "comparison_equivalence_evidenced"][0]["detail"]
    assert "desktop: no accepted equivalence entry" in gaps and "browser" in gaps and not r["all_ok"]
    acc = []
    for d in r["differences"]:
        ev = tmp_path / f"ev_{d['aspect']}.json"
        ev.write_text(json.dumps({"measured": d["aspect"]}))
        acc.append({"aspect": d["aspect"], "scope": "task e0df059f rename only", "measured": True, "evidence": str(ev), "sha256": hr.file_sha256(str(ev))})
    cfg["equivalence"] = {"accepted": acc}
    assert hr.check_all(cfg, deps)["all_ok"]
    acc[0]["measured"] = False  # a bare boolean or an unmeasured entry does not close a gap
    assert "comparison_equivalence_evidenced" in failing(hr.check_all(cfg, deps))
    acc[0]["measured"] = True
    open(acc[0]["evidence"], "w").write("tampered")  # the evidence bytes no longer match the recorded digest
    assert "comparison_equivalence_evidenced" in failing(hr.check_all(cfg, deps))


# ---- digest binding ----
def test_run_and_gates_refuse_when_any_bound_input_changes_after_check(tmp_path):
    cfg, deps = setup(tmp_path)
    out = tmp_path / "run"
    out.mkdir()
    with pytest.raises(SystemExit):
        hr.need_checks(str(out), cfg, deps['sh'])
    r = hr.check_all(cfg, deps)
    json.dump(r, open(out / "checks.json", "w"))
    assert hr.need_checks(str(out), cfg, deps['sh'])
    with pytest.raises(SystemExit, match="different config"):
        hr.need_checks(str(out), dict(cfg, blocks=9), deps['sh'])
    open(cfg["prices"], "w").write(json.dumps({"host_usd_per_hour": 0.01, "label": "cheaper"}))  # same config, different price bytes
    with pytest.raises(SystemExit, match="inputs changed.*prices"):
        hr.need_checks(str(out), cfg, deps['sh'])


def test_a_changed_task_json_or_action_sequence_invalidates_the_stamp(tmp_path):
    cfg, deps = setup(tmp_path)
    out = tmp_path / "run"
    out.mkdir()
    json.dump(hr.check_all(cfg, deps), open(out / "checks.json", "w"))
    p = tmp_path / "osw" / "evaluation_examples" / "examples" / "os" / (hr.FIRST_TASK + ".json")
    p.write_text(json.dumps({**TASK, "evaluator": {"func": "check_include_exclude"}}))
    with pytest.raises(SystemExit, match="task:"):
        hr.need_checks(str(out), cfg, deps['sh'])


def test_gates_stamp_must_pass_and_match_too(tmp_path):
    cfg, deps = setup(tmp_path)
    out = tmp_path / "run"
    out.mkdir()
    r = hr.check_all(cfg, deps)
    with pytest.raises(SystemExit):
        hr.need_gates(str(out), cfg, deps['sh'])
    json.dump({**r, "all_ok": False}, open(out / "gates.json", "w"))
    with pytest.raises(SystemExit, match="did not pass"):
        hr.need_gates(str(out), cfg, deps['sh'])
    json.dump(r, open(out / "gates.json", "w"))
    assert hr.need_gates(str(out), cfg, deps['sh'])


# ---- docker_kvm incumbent ----
def test_docker_kvm_stage_passes_with_kvm_pinned_image_verified_qcow2_and_the_stock_budget(tmp_path):
    cfg, deps = setup(tmp_path, incumbent="docker_kvm")
    r = hr.check_all(cfg, deps)
    assert r["all_ok"], failing(r)
    names = {c["name"] for c in r["checks"]}
    assert {"host_kind_nested_kvm", "docker_image_pinned_by_digest", "qcow2_sha256_matches_record", "docker_guest_budget_matches", "reuse_matches_the_incumbent"} <= names


@pytest.mark.parametrize("over,name", [
    ({"kvm": False}, "host_kind_nested_kvm"), ({"virt": "none"}, "host_kind_nested_kvm"), ({"docker_dig": "happysixd/osworld-docker@sha256:beef"}, "docker_image_pinned_by_digest"),
    ({"docker_env": {"DISK_SIZE": "32G", "RAM_SIZE": "8G", "CPU_CORES": "4"}}, "docker_guest_budget_matches"), ({"docker_env": {"RAM_SIZE": "4G", "CPU_CORES": "2"}}, "docker_guest_budget_matches"),
])
def test_docker_kvm_refusals(tmp_path, over, name):
    cfg, deps = setup(tmp_path, incumbent="docker_kvm", **over)
    assert name in failing(hr.check_all(cfg, deps))


def test_docker_qcow2_size_and_checksum_must_match_the_record(tmp_path):
    cfg, deps = setup(tmp_path, incumbent="docker_kvm")
    cfg["qcow2_sha256"] = "0" * 64
    assert "qcow2_sha256_matches_record" in failing(hr.check_all(cfg, deps))
    cfg, deps = setup(tmp_path, incumbent="docker_kvm", cfg={"qcow2_bytes": 5})
    assert "qcow2_size_matches_record" in failing(hr.check_all(cfg, deps))


def test_docker_provider_budget_is_read_from_the_stock_source(tmp_path):
    p = tmp_path / "desktop_env" / "providers" / "docker"
    p.mkdir(parents=True)
    (p / "provider.py").write_text('class P:\n    def __init__(self):\n        self.environment = {"DISK_SIZE": "32G", "RAM_SIZE": "4G", "CPU_CORES": "4"}  # Modify if needed\n')
    assert hr.docker_provider_env(str(tmp_path)) == {"DISK_SIZE": "32G", "RAM_SIZE": "4G", "CPU_CORES": "4"}


def test_docker_telemetry_finds_live_container_cgroups_and_kvm_facts():
    runner = lambda cmd: (0, "abc123\ndef456") if cmd[:2] == ["docker", "ps"] and "--no-trunc" in cmd else (0, "abc123")
    present = {"/sys/fs/cgroup/system.slice/docker-abc123.scope", "/sys/fs/cgroup/docker/def456"}
    assert dt.docker_cgroup_dirs(runner, exists=lambda p: p in present) == ["/sys/fs/cgroup/system.slice/docker-abc123.scope", "/sys/fs/cgroup/docker/def456"]
    assert dt.docker_cgroup_dirs(lambda c: (1, ""), exists=lambda p: True) == []
    f = dt.container_facts(lambda c: (0, "abc123") if c[:2] == ["docker", "ps"] else (0, '[{"PathOnHost":"/dev/kvm"}]|["KVM=N","CPU_CORES=4"]|sha256:aaaa'))
    assert f[0]["kvm_disabled"] is True and f[0]["has_kvm_device"] is True


# ---- gates (pure functions with fakes) ----
class FakeLease:
    def __init__(self):
        self.state, self.saved = {}, {}

    def reset(self, task):
        self.state = {"dir": "Jan_1"}

    def save_branch(self, name):
        self.saved[name] = dict(self.state)

    def restore_branch(self, name, task):
        self.state = dict(self.saved[name])

    def step(self, a):
        if a == "correct":
            self.state["dir"] = "Jan_2"

    def evaluate(self):
        return 1.0 if self.state["dir"] == "Jan_2" else 0.0


def test_snapshot_score_gate_passes_and_fails_when_revert_does_not_reset():
    assert hr.score_gate(FakeLease(), {}, "correct", "wrong", "n")["pass"]

    class NoRevert(FakeLease):
        def restore_branch(self, name, task):
            pass
    assert not hr.score_gate(NoRevert(), {}, "correct", "wrong", "n")["pass"]


def test_cold_score_gate_needs_zero_before_one_after_and_a_fresh_reset_each_time():
    assert hr.cold_score_gate(FakeLease(), {}, "correct", "wrong")["pass"]

    class Sticky(FakeLease):
        def reset(self, task):
            self.state = getattr(self, "state", None) or {"dir": "Jan_1"}  # a "reset" that keeps old state
    assert not hr.cold_score_gate(Sticky(), {}, "correct", "wrong")["pass"]


def _screen(blocks):
    return json.dumps(blocks).encode()


def _area_diff(a, b, **kw):
    """Fake pixel diff over a fake screen: the area of the blocks present in one screen and not the other."""
    sa, sb = {tuple(x[:4]) + (tuple(x[4]),) for x in json.loads(a)}, {tuple(x[:4]) + (tuple(x[4]),) for x in json.loads(b)}
    return sum((x[2] - x[0]) * (x[3] - x[1]) for x in sa ^ sb)


class GuiLease(FakeLease):
    """A fake desktop whose 'screen' is a list of blocks; live snapshots restore it unless restore_ok is False."""

    def __init__(self, restore_ok=True):
        super().__init__()
        self.screen, self.restore_ok, self.snap = [], restore_ok, None

    def save_branch(self, name):
        self.snap = list(self.screen)

    def restore_branch(self, name, task):
        if self.restore_ok:
            self.screen = list(self.snap)


def _gui_run(lease, pids=None, writer=True):
    calls = []
    it = iter(pids) if pids else None

    def step(code):
        calls.append(code)
        if "write(" in code and "zzzz" in code:
            lease.screen = [(100, 200, 900, 260, (0, 0, 0))]
        elif "write(" in code:
            lease.screen = [(100, 200, 500, 240, (30, 30, 30))]
        elif "libreoffice" in code and writer:
            lease.screen = [(0, 100, 1280, 800, (240, 240, 240))]

    def sh_out(s):
        if "pgrep" in s:
            return (next(it) if it else "123") if writer else ""
        if "wmctrl" in s:
            return "1" if writer else "0"
        return "0" if "find" in s else ""
    return hr.gui_state_gate(lease, {}, sh_out, step, lambda: _screen(lease.screen), sleep=lambda s: None, diff=_area_diff), calls


def test_gui_gate_passes_when_the_typed_pixels_come_back_after_revert_and_fails_when_they_do_not():
    ok, calls = _gui_run(GuiLease())
    assert ok["pass"] and ok["d12_typed_to_mutated_px"] >= 300 and ok["d13_typed_to_restored_px"] == 0 and ok["same_soffice_pid"] and ok["no_document_saved"]
    assert any("libreoffice" in c for c in calls) and not any("registrymodifications" in c or ".xcu" in c for c in calls)  # no settings or target file is touched
    bad, _ = _gui_run(GuiLease(restore_ok=False))
    assert not bad["pass"] and bad["d13_typed_to_restored_px"] > 0.25 * bad["d12_typed_to_mutated_px"]


def test_gui_gate_fails_closed_without_a_writer_process_or_when_the_process_was_replaced():
    none, _ = _gui_run(GuiLease(), writer=False)
    assert not none["pass"] and not none["writer_process"]
    # pids seen: wait loop (1), then the check after restore (different): the restored state is not the same application instance
    replaced, _ = _gui_run(GuiLease(), pids=["111", "222"])
    assert replaced["same_soffice_pid"] is False and not replaced["pass"]


def test_changed_pixels_with_real_images_when_pillow_is_available():
    pytest.importorskip("PIL")
    import io
    from PIL import Image

    def png(boxes):
        im = Image.new("RGB", (1280, 800), (255, 255, 255))
        for b in boxes:
            im.paste(b[4], b[:4])
        o = io.BytesIO()
        im.save(o, "PNG")
        return o.getvalue()
    a, b = png([(100, 200, 500, 240, (30, 30, 30))]), png([])
    assert hr.changed_pixels(a, a) == 0 and hr.changed_pixels(a, b) == 400 * 40


def test_cold_isolation_gate_passes_only_when_all_state_is_gone_after_reset():
    state = {"alive": False, "marker": None, "writer": False}

    class L(FakeLease):
        def reset(self, task):
            state.update({"alive": False, "marker": None, "writer": False})

    def step(code):
        if "libreoffice" in code:
            state["writer"] = True
        elif "gate_marker" in code:
            state["marker"] = code.split("write('")[1].split("'")[0]
        else:
            state["alive"] = True

    def sh_out(s):
        if "ASK" in s or "socket" in s:
            return "tok" if state["alive"] else ""
        if "cat /tmp/gate_marker" in s:
            return state["marker"] or ""
        if "pgrep" in s:
            return "9" if state["writer"] else ""
        return ""
    import time as _t
    orig = _t.sleep
    _t.sleep = lambda s: None
    try:
        assert hr.cold_isolation_gate(L(), {}, sh_out, step)["pass"]

        class Leaky(L):
            def reset(self, task):
                pass
        assert not hr.cold_isolation_gate(Leaky(), {}, sh_out, step)["pass"]
    finally:
        _t.sleep = orig


def test_parity_decision_is_a_readback_record_not_a_claim():
    ok = hr.parity_decision("vm", 4, 4096, limits_readback={"cpus": 4, "memory_mib": 4096}, guest={"nproc": 4, "mem_total_kib": 4000 * 1024})
    assert ok["readbacks_equal_target"] and "no resource-parity claim" in ok["note"]
    assert not hr.parity_decision("vm", 4, 4096, limits_readback={"cpus": 2, "memory_mib": 4096})["readbacks_equal_target"]
    assert not hr.parity_decision("docker", 4, 4096, guest={"nproc": 4, "mem_total_kib": 2048 * 1024})["readbacks_equal_target"]
    assert not hr.parity_decision("x", 4, 4096)["readbacks_equal_target"]


def _run_dir(tmp_path):
    out = tmp_path / "run"
    (out / "work" / "tpl" / "home" / "drums" / "profile" / "Default").mkdir(parents=True)
    (out / "work" / "tpl" / "home" / "drums" / "profile" / "Default" / "Cookies").write_bytes(b"SQLite format 3\0 private bytes")
    (out / "work" / "state").mkdir()
    (out / "work" / "state" / "session.json").write_text("{}")
    for n, body in (("checks.json", "{}"), ("gates.json", "{}"), ("summary.json", "{}"), ("pins.json", "{}"), ("run_status.json", "{}"), ("block0_drums.json", "{}"), ("block1_docker_kvm.json", "{}"),
                    ("smoke.log", "log line")):
        (out / n).write_text(body)
    (out / "secret.txt").write_text("an unrelated secret file")
    (out / "id_rsa").write_text("not exported")
    (out / ".aws_credentials").write_text("not exported")
    (out / "notes.json").write_text("{}")  # an unlisted top-level name
    os.symlink("/etc/passwd", out / "block9_link.json")
    return out


def test_export_is_an_explicit_allowlist_and_never_archives_work_trees_profiles_keys_or_symlinks(tmp_path):
    import tarfile
    out = _run_dir(tmp_path)
    tgz, sums = hr.export(str(out))
    names = set(tarfile.open(tgz).getnames())
    assert {"checks.json", "gates.json", "summary.json", "pins.json", "run_status.json", "block0_drums.json", "block1_docker_kvm.json", "smoke.log", "export_manifest.json", "SHA256SUMS"} == names
    joined = " ".join(names)
    for forbidden in ("work", "Cookies", "profile", "secret.txt", "id_rsa", "aws_credentials", "notes.json", "block9_link", "passwd"):
        assert forbidden not in joined, forbidden
    man = json.load(open(out / "export_manifest.json"))
    assert man["excluded_count"] == 6 and "secret" not in json.dumps(man["included"]) and man["withheld_by_secret_scan"] == []
    assert "work" not in open(out / "SHA256SUMS").read()
    hr.export(str(out))  # idempotent: a second export does not archive the first one's outputs
    assert "run_export.tar.gz" not in open(out / "SHA256SUMS").read()


def test_a_listed_file_that_contains_a_credential_is_withheld_and_named(tmp_path):
    import tarfile
    out = _run_dir(tmp_path)
    (out / "pins.json").write_text('{"x": "AKIA' + "A" * 16 + '"}')
    (out / "smoke.log").write_text("-----BEGIN RSA PRIVATE KEY-----\nabc")
    tgz, _ = hr.export(str(out))
    names = set(tarfile.open(tgz).getnames())
    assert "pins.json" not in names and "smoke.log" not in names and "checks.json" in names
    assert sorted(json.load(open(out / "export_manifest.json"))["withheld_by_secret_scan"]) == ["pins.json", "smoke.log"]


def test_evidence_directories_take_only_exportable_extensions_and_staged_files_are_archived(tmp_path):
    import tarfile
    cfg, deps = setup(tmp_path)
    im = tmp_path / "image.json"
    im.write_text(json.dumps(IMAGE))
    cfg["image_manifest"] = str(im)
    out = _run_dir(tmp_path)
    staged = hr.stage_evidence(cfg, str(out))
    assert "manifests/image_manifest.json" in staged and "manifests/prices.json" in staged and f"fixtures/{hr.FIRST_TASK}.json" in staged
    (out / "manifests" / "disk.img").write_bytes(b"x" * 10)  # an unlisted extension inside an evidence directory
    (out / "fixtures" / "nested").mkdir()
    (out / "fixtures" / "nested" / "x.json").write_text("{}")
    names = set(tarfile.open(hr.export(str(out))[0]).getnames())
    assert {"manifests/image_manifest.json", "manifests/prices.json", f"fixtures/{hr.FIRST_TASK}.json"} <= names
    assert not any("disk.img" in n or "nested" in n for n in names)


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
    assert out["cpu_s_lower_bound"] == 3.0 and out["vm_processes_max"] == 1 and out["empty_samples"] == 0


def test_snapshot_cleanup_refuses_the_base_snapshot():
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    r = vbox.delete_snapshots("vm", ["init_state", "b0-post_setup-x-w0"], runner)
    assert r["init_state"].startswith("refused") and r["b0-post_setup-x-w0"] is None and len(calls) == 1


def test_a_cold_reset_stage_does_not_need_criu_restore_but_a_snapshot_stage_does(tmp_path):
    nore = {"go_drums": True, "go_drums_restore": False, "no_go_reasons": ["ip: not found"]}
    cfg, deps = setup(tmp_path, incumbent="docker_kvm", pf=nore)
    assert "drums_preflight_go" not in failing(hr.check_all(cfg, deps))
    cfg2, deps2 = setup(tmp_path, pf=nore)
    assert "drums_preflight_go" in failing(hr.check_all(cfg2, deps2))
