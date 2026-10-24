import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "envd-linux"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "envd-linux", "tests"))
from drums_compare import fullroot_gates as fg  # noqa: E402
from drums_compare import host_run as hr  # noqa: E402
from drums_compare import stages  # noqa: E402

ID_A, ID_B = "a" * 32, "b" * 32
ACT_OK = '   array [\n      string "org.freedesktop.DBus"\n   ]'


def cap(dev=62, ino=7, bid=ID_A, mnt="mnt:[1]", act=ACT_OK, rc_id=0):
    base = {k: {"rc": 0, "out": "x", "err": ""} for k in fg.PARITY_PROBES}
    base.update(system_bus_socket={"rc": 0, "out": f"{dev} {ino}", "err": ""}, mnt_ns={"rc": 0, "out": mnt, "err": ""},
                bus_id={"rc": rc_id, "out": f'method return\n   string "{bid}"' if rc_id == 0 else "Failed", "err": ""}, bus_activatable={"rc": 0, "out": act, "err": ""})
    return base


def test_equal_inodes_on_different_tmpfs_devices_are_not_a_collision_but_equal_device_and_inode_are():
    a, b = cap(62, 7, ID_A, "mnt:[1]"), cap(74, 7, ID_B, "mnt:[2]")
    assert fg.bus_problems([a, b]) == []  # the real run: inode 7 twice, devices 0:62 and 0:74
    assert any("same device and inode" in p for p in fg.bus_problems([a, cap(62, 7, ID_B, "mnt:[2]")]))
    assert any("same ID" in p for p in fg.bus_problems([a, cap(74, 7, ID_A, "mnt:[2]")]))
    assert any("one mount namespace" in p for p in fg.bus_problems([a, cap(74, 7, ID_B, "mnt:[1]")]))


def test_the_bus_parser_needs_exactly_two_valid_captures_and_rejects_malformed_or_empty_facts():
    a, b = cap(62, 7, ID_A, "mnt:[1]"), cap(74, 7, ID_B, "mnt:[2]")
    assert any("exactly two" in p for p in fg.bus_problems([a])) and any("exactly two" in p for p in fg.bus_problems([a, b, b]))
    for bad, why in ((cap(74, 7, ID_B, "mnt:[2]", rc_id=1), "bus_id failed"), (cap(74, 7, "short", "mnt:[2]"), "valid 32-hex"), (cap(74, 7, ID_B, ""), "malformed mount namespace"),
                     (cap(74, 7, ID_B, "pid:[2]"), "malformed mount namespace"), (cap(74, 7, ID_B, "mnt:[2]", act=""), "activation listing"),
                     (cap(74, 7, ID_B, "mnt:[2]", act='string "org.freedesktop.UPower"'), "activation listing")):
        assert any(why in p for p in fg.bus_problems([a, bad])), why
    mal = cap(74, 7, ID_B, "mnt:[2]")
    mal["system_bus_socket"]["out"] = "74"
    assert any("malformed socket stat" in p for p in fg.bus_problems([a, mal]))
    leak = cap(74, 7, ID_B, "mnt:[2]", act='   array [\n      string "org.freedesktop.DBus"\n      string "org.freedesktop.UPower"\n   ]')
    assert any("activatable names besides" in p for p in fg.bus_problems([a, leak]))
    empty = {k: {"rc": None, "out": "", "err": "probe failed"} for k in fg.PARITY_PROBES}
    assert len(fg.bus_problems([empty, empty])) >= 8 and fg.bus_problems([{}, {}])


def test_parity_capture_keeps_raw_results_and_records_probe_errors_not_blank_success():
    def run(script):
        if "gnome-shell" in script:
            raise RuntimeError("endpoint down")
        return {"stdout": "out " + script[:5], "stderr": "", "returncode": 127 if "soffice" in script else 0}
    c = fg.capture_parity(run)
    assert c["gnome_shell"]["rc"] is None and "probe failed" in c["gnome_shell"]["err"] and c["office"]["rc"] == 127 and c["kernel"]["rc"] == 0


def test_the_parity_report_is_never_fair_lists_differences_and_marks_unknowns():
    d = {k: {"rc": 0, "out": "d", "err": ""} for k in fg.PARITY_PROBES}
    v = {k: {"rc": 0, "out": "d", "err": ""} for k in fg.PARITY_PROBES}
    v["kernel"]["out"] = "6.5.0-15"
    r = fg.parity_report(v, d)
    assert r["different"] == ["kernel"] and r["fair_comparison"] is False and r["label"] == "ARCHITECTURAL_NOT_FAIR_COMPARISON"
    none = fg.parity_report(None, d)
    assert none["vm_captured"] is False and len(none["not_comparable"]) == len(fg.PARITY_PROBES)
    assert {"xvfb_vs_xorg", "no_display_manager", "private_bus_non_activating", "kernel", "screen_lock_and_clock"} <= {g["id"] for g in fg.PARITY_GAPS}


# ---- the canary with REAL local processes (no namespace needed)
class LocalGuest:
    def __init__(self, tmp):
        self.tmp, self.resets = tmp, 0

    def run(self, script):  # a private "/tmp" per guest: the gate's /tmp paths are rewritten into this guest's own directory
        r = subprocess.run(["sh", "-c", script.replace("/tmp/", self.tmp + "/")], capture_output=True, text=True, cwd=self.tmp)
        return {"stdout": r.stdout, "stderr": r.stderr, "returncode": r.returncode}

    def step(self, code):
        subprocess.run([sys.executable, "-c", code.replace("/tmp/", self.tmp + "/")], cwd=self.tmp, timeout=20)

    def reset(self):
        self.resets += 1


def test_the_old_comment_marker_vanishes_after_exec_and_the_exact_argv_marker_is_durable_and_never_matches_the_probe():
    tok = "tok" + str(os.getpid())
    old = subprocess.Popen(["sh", "-c", f"exec sleep 30 # canary_{tok}"])
    new = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", f"canary_{tok}"])
    try:
        time.sleep(0.5)
        assert f"canary_{tok}".encode() not in open(f"/proc/{old.pid}/cmdline", "rb").read()  # the earlier design: the marker is gone after exec
        out = subprocess.run(["sh", "-c", fg.scan_script(tok)], capture_output=True, text=True).stdout
        rows = fg.parse_scan(out)
        assert [r[0] for r in rows] == [new.pid]  # found, once; the probe's own sh/python did not match although its command line holds the token
        assert subprocess.run(["sh", "-c", fg.scan_script(tok + "x")], capture_output=True, text=True).stdout.strip() == ""  # an absent canary is absent
    finally:
        for p in (old, new):
            p.kill()
            p.wait()
    assert fg.parse_scan(subprocess.run(["sh", "-c", fg.scan_script(tok)], capture_output=True, text=True).stdout) == []  # a dead one is not reported (and a zombie would be skipped)


def test_two_sessions_gate_with_real_processes_proves_the_same_pid_and_start_time_survive_and_cleans_up(tmp_path, monkeypatch):
    real_sleep = time.sleep
    monkeypatch.setattr(fg.time, "sleep", lambda s: real_sleep(0.05))
    empty_proc = tmp_path / "emptyproc"
    empty_proc.mkdir()  # B's view of /proc: nothing, as in a separate PID namespace
    (tmp_path / "adir").mkdir()
    (tmp_path / "bdir").mkdir()
    a, b = LocalGuest(str(tmp_path / "adir")), LocalGuest(str(tmp_path / "bdir"))
    r = fg.two_session_gate(a, b, proc="/proc", proc_b=str(empty_proc))
    assert r["pass"], r
    assert len(r["canary_in_A"]) == 1 and r["canary_in_A"] == r["canary_in_A_after_B_reset"] and b.resets == 1
    assert fg.parse_scan(a.run(fg.scan_script(r["token"]))["stdout"]) == []  # killed at the end
    assert not os.path.exists(tmp_path / "adir" / f"canary_{r['token']}")  # the file canary is removed too


def test_the_gate_fails_when_the_canary_never_runs_leaks_to_b_or_dies_with_b_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(fg.time, "sleep", lambda s: None)

    class NoCanary(LocalGuest):
        def step(self, code):
            pass
    assert not fg.two_session_gate(NoCanary(str(tmp_path)), LocalGuest(str(tmp_path)), proc="/proc", proc_b=str(tmp_path))["pass"]  # absence is a failure, not a pass
    same_view = fg.two_session_gate(LocalGuest(str(tmp_path)), LocalGuest(str(tmp_path)), proc="/proc", proc_b="/proc")  # one shared /tmp and one /proc: a real leak
    assert not same_view["pass"] and same_view["canary_process_leaked_to_B"] and same_view["canary_file_leaked_to_B"]  # B sees A's process and file: a leak

    class KillerB(LocalGuest):
        def reset(self):
            for pid, _ in fg.parse_scan(self.run(fg.scan_script(self.token))["stdout"]):
                os.kill(pid, signal.SIGKILL)
                time.sleep(0.1)
    kb = KillerB(str(tmp_path))
    real = fg.uuid.uuid4
    monkeypatch.setattr(fg.uuid, "uuid4", lambda: type("U", (), {"hex": "fixedtoken0123456789"})())
    kb.token = "fixedtoken0123"[:10]
    r = fg.two_session_gate(LocalGuest(str(tmp_path)), kb, proc="/proc", proc_b="/proc")
    assert not r["pass"] and not r["A_same_process_survives_B_reset"]
    monkeypatch.setattr(fg.uuid, "uuid4", real)


def test_phases_are_separate_monotonic_clocks_and_the_timed_lease_attributes_reset_task_evaluation_and_buckets():
    t = {"now": 0.0}

    class C(fg.PhaseClock):
        pass
    clock = C(clock=lambda: t["now"])

    class Lease:
        def reset(self, task):
            t["now"] += 5
            return "obs"

        def step(self, a):
            t["now"] += 2
            return "s"

        def evaluate(self):
            t["now"] += 1
            return 1.0
        marker = "attr"
    tl = fg.TimedLease(Lease(), clock)
    assert tl.reset({}) == "obs" and tl.step("x") == "s" and tl.evaluate() == 1.0 and tl.marker == "attr"
    tl.bucket = "hygiene"
    tl.reset({})
    tl.step("y")
    tl.bucket = None
    r = clock.report()
    assert r["reset_setup"] == 5 and r["task"] == 2 and r["evaluation"] == 1 and r["hygiene"] == 7 and r["cold_setup"] is None and r["restore"] is None
    assert fg.PhaseClock().clock is time.monotonic
    with pytest.raises(ValueError):
        clock.add("bogus", 1)
    cold = fg.PhaseClock()
    cold.unavailable("restore", "no CRIU")
    assert cold.report()["unavailable"]["restore"] == "no CRIU" and cold.report()["restore"] is None


def test_the_unchanged_cold_score_gate_still_decides_pass_with_a_timed_lease():
    state = {}

    class L:
        def reset(self, t):
            state.clear()

        def step(self, code):
            state["a"] = code

        def evaluate(self):
            return 1.0 if state.get("a") == "C" else 0.0
    clock = fg.PhaseClock()
    r = hr.cold_score_gate(fg.TimedLease(L(), clock), {}, "C", "W")
    assert r["pass"] and r["before_action"] == 0.0 and r["correct"] == 1.0 and r["wrong"] == 0.0
    rep = clock.report()
    assert rep["reset_setup"] is not None and rep["task"] is not None and rep["evaluation"] is not None


def test_applicability_says_which_gates_run_and_labels_every_timing():
    a = fg.gate_applicability({"drums_base_root": "/mnt/img"})
    assert a["gates"]["score_before_correct_wrong"]["runs"] and a["gates"]["two_session_interference"]["runs"]
    assert a["gates"]["snapshot_state_and_gui_state"]["runs"] is False and "CRIU" in a["gates"]["checkpoint_save_restore_clocks"]["why"]
    assert a["label"] == "ARCHITECTURAL_NOT_FAIR_COMPARISON" and a["training_economics_claim"] is False


GOOD = {"drums_desktop": "gnome", "drums_user": "user", "drums_uid": 1000, "drums_gid": 1000, "drums_home": "/home/user", "drums_system_bus": True, "incumbent": "docker_kvm",
        "drums_base_root": "/mnt/vm-image-ro", "drums_template_overlay": "/opt/drums-overlay/upper", "task_sha256": "0" * 64, "osworld_src": "/opt/OSWorld"}


def test_config_scope_enforces_exact_values_the_full_root_netns_code_and_says_it_examined_no_host_files():
    ok = fg.preflight(GOOD, "config")
    assert ok["all_ok"], [c for c in ok["checks"] if not c["ok"]]
    assert "no image root, kit or task file was examined" in ok["scope_note"] and not any(c["name"].startswith("host_") for c in ok["checks"])
    assert any(c["name"] == "code_build_drums_full_root_netns" and c["ok"] for c in ok["checks"])
    for k, v in (("drums_desktop", "openbox"), ("drums_desktop", "x"), ("drums_uid", 1001), ("drums_uid", "1000"), ("drums_home", "/home/drums"), ("drums_user", "root"), ("drums_system_bus", "yes"), ("drums_system_bus", False),
                 ("drums_gid", 0), ("incumbent", "virtualbox"), ("drums_base_root", "/"), ("drums_base_root", "rel/path"), ("drums_base_root", "/a/../b"), ("drums_template_overlay", "/a:b"), ("task_sha256", "zz"), ("osworld_src", "")):
        r = fg.preflight(dict(GOOD, **{k: v}), "config")
        assert not r["all_ok"], (k, v)
    assert not fg.preflight({k: v for k, v in GOOD.items() if k != "task_sha256"}, "config")["all_ok"]
    assert not fg.preflight({}, "config")["all_ok"]


def test_host_scope_adds_the_files_the_pinned_task_hash_and_the_merged_root_check(tmp_path):
    import test_rootcheck as T
    r = T.image_root(tmp_path, programs=("dbus-daemon", "gnome-session", "gnome-shell", "xprop"))
    T.gnome_files(r)
    t = T.kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.10.12")
    src = tmp_path / "OSWorld"
    tp = src / "evaluation_examples" / "examples" / "os" / (hr.FIRST_TASK + ".json")
    tp.parent.mkdir(parents=True)
    tp.write_text('{"id": "x"}')
    cfg = dict(GOOD, drums_base_root=r, drums_template_overlay=t, osworld_src=str(src), task_sha256=hashlib.sha256(tp.read_bytes()).hexdigest())
    ok = fg.preflight(cfg, "host")
    assert ok["all_ok"], [c for c in ok["checks"] if not c["ok"]]
    assert {"host_base_root_is_dir", "host_overlay_is_dir", "host_task_sha256", "host_merged_root_rootcheck"} <= {c["name"] for c in ok["checks"]}
    assert not fg.preflight(dict(cfg, task_sha256="1" * 64), "host")["all_ok"]
    os.remove(os.path.join(r, "usr/bin/Xvfb"))
    bad = fg.preflight(cfg, "host")
    assert not bad["all_ok"] and any(c["name"] == "host_merged_root_rootcheck" and not c["ok"] for c in bad["checks"])
    assert not fg.preflight(dict(cfg, drums_base_root=str(tmp_path / "missing")), "host")["all_ok"]


def test_run_refuses_unless_the_host_scope_passes_and_the_supervisor_gets_the_gnome_config(tmp_path, capsys):
    cfgp = tmp_path / "c.json"
    cfgp.write_text(json.dumps(GOOD))
    assert fg.main(["run", "--config", str(cfgp), "--out", str(tmp_path / "o")]) == 1 and "HOST-scope preflight failed" in capsys.readouterr().out
    assert fg.main(["preflight", "--config", str(cfgp), "--out", str(tmp_path / "o")]) == 0  # config scope passes offline
    assert "the host scope is still to run" in capsys.readouterr().out
    assert stages.desktop_kw({}) == {}
    assert stages.desktop_kw({"drums_desktop": "gnome", "drums_uid": 1000, "drums_system_bus": True}) == {"desktop": "gnome", "uid": 1000, "gid": 1000, "user": "user", "system_bus": True}


# ---- run_live end to end with fakes: real guestexec parsing, the real lease class, the real gate functions
class FakeEnv:
    def __init__(self, idx, closer=None):
        self.idx, self.s, self.closed, self.closer = idx, {}, False, closer
        self.controller = type("Ctl", (), {})()
        self.controller.post_execute = self._post

    def reset(self, task_config=None):
        self.s = {}
        return {}

    def step(self, code):
        if code == stages.CORRECT:
            self.s["action"] = "correct"
        elif code == stages.WRONG:
            self.s["action"] = "wrong"
        elif "libreoffice" in code:
            self.s["writer"] = True
        elif "gate_marker" in code:
            self.s["marker"] = code.split("write('")[1].split("'")[0]
        elif "canary_" in code and "Popen" in code:
            self.s["cproc"] = True
        elif "canary_" in code and "write(" in code:
            self.s["cfile"] = True
        elif "gate_holder" in code or "Popen" in code:
            self.s["holder"] = True
        return {}, 0, False, {}

    def evaluate(self):
        return 1.0 if self.s.get("action") == "correct" else 0.0

    def close(self):
        self.closed = True
        if self.closer:
            raise RuntimeError("close failed")

    def _post(self, payload, timeout):
        sc, s = payload["command"], self.s
        out, err, rc = "", "", 0
        if "ENDPOINT_OK" in sc:
            out = "ENDPOINT_OK"
        elif "oops" in sc:
            err, rc = "oops", 3
        elif "create_connection" in sc:
            out = "tok" if s.get("holder") else ""
        elif "cat /tmp/gate_marker" in sc:
            out = s.get("marker", "")
        elif "/proc/[0-9]*" in sc and "soffice.bin" in sc:
            out = "9\n" if s.get("writer") else ""
        elif "SCAN" in sc or ("canary_" in sc and "python3 -c" in sc):
            out = "55 1234\n" if s.get("cproc") else ""
        elif sc.startswith("test -e /tmp/canary_"):
            out = "file" if s.get("cfile") else ""
        elif "stat -c" in sc and "system_bus_socket" in sc:
            out = f"{60 + self.idx * 10} 7"
        elif "readlink /proc/1/ns/mnt" in sc:
            out = f"mnt:[{100 + self.idx}]"
        elif "GetId" in sc:
            out = 'method return\n   string "%s"' % (ID_A if self.idx == 0 else ID_B)
        elif "ListActivatableNames" in sc:
            out = ACT_OK
        else:
            out = "x"
        return 200, {"status": "success", "output": out, "error": err, "returncode": rc}


class FakeSup:
    def __init__(self, root, close_fails=False):
        self.root, self.close_fails, self.closed = root, close_fails, False
        for i in range(2):
            os.makedirs(os.path.join(self._dir(i), "upper"))
            open(os.path.join(self._dir(i), "upper", "f"), "wb").write(b"x" * (10 + i))

    def _dir(self, i):
        return os.path.join(self.root, "state", f"s{i}")

    def close(self):
        self.closed = True
        if self.close_fails:
            raise RuntimeError("supervisor close failed")


def live_deps(tmp_path, env_closer=None, sup_fails=False, strict=None):
    envs = [FakeEnv(0, env_closer), FakeEnv(1, env_closer)]
    sup = FakeSup(str(tmp_path / "w"), sup_fails)
    built = []
    d = {"strict_import": strict or (lambda allow: (object(), [])), "task": lambda: {"id": "t"},
         "build_drums": lambda cfg, work: (built.append(1) or sup, object(), 0.5), "factory": lambda pool, de: (lambda i: envs[i])}
    return d, envs, sup, built


def test_run_live_executes_the_whole_path_with_fakes_and_writes_the_labelled_result(tmp_path, monkeypatch):
    monkeypatch.setattr(fg.time, "sleep", lambda s: None)
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)
    d, envs, sup, built = live_deps(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    rc = fg.run_live(dict(GOOD, work_dir=str(tmp_path / "w")), str(out), d)
    res = json.load(open(out / "fullroot_gates.json"))
    assert rc == 0 and res["all_ok"], res.get("error") or res
    assert res["label"] == "ARCHITECTURAL_NOT_FAIR_COMPARISON" and res["training_economics_claim"] is False and res["bus_problems"] == [] and "NOT captured here" in res["bus_scope"]
    assert res["scores"]["pass"] and res["reset_hygiene"]["pass"] and res["reset_hygiene"]["writer_running_before"] and res["two_session"]["pass"]
    assert all(x["pass"] for x in res["guest_exec_selftest"])  # the real guestexec parsed the fake /execute shape
    assert res["state_bytes"] == {"s0_upper": 10, "s1_upper": 11}  # read through the Supervisor's own _dir layout
    c = res["clocks"]
    assert c["cold_setup"] is not None and c["reset_setup"] is not None and c["task"] is not None and c["evaluation"] is not None and c["hygiene"] is not None
    assert c["checkpoint_save"] is None and c["restore"] is None and "CRIU" in c["unavailable"]["restore"]
    assert all(e.closed for e in envs) and sup.closed and res["cleanup_errors"] == []


def test_run_live_records_cleanup_failures_and_never_hides_them_and_refuses_stubs_before_building(tmp_path, monkeypatch):
    monkeypatch.setattr(fg.time, "sleep", lambda s: None)
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)
    d, envs, sup, built = live_deps(tmp_path, env_closer=True, sup_fails=True)
    out = tmp_path / "o1"
    out.mkdir()
    rc = fg.run_live(dict(GOOD, work_dir=str(tmp_path / "w")), str(out), d)
    res = json.load(open(out / "fullroot_gates.json"))
    assert rc == 1 and not res["all_ok"] and len(res["cleanup_errors"]) == 3 and all(e.closed for e in envs) and sup.closed  # every close was still attempted
    refuse = lambda allow: (_ for _ in ()).throw(RuntimeError("stubbed module refused: x"))
    d2, envs2, sup2, built2 = live_deps(tmp_path / "b", strict=refuse)
    out2 = tmp_path / "o2"
    out2.mkdir()
    rc = fg.run_live(dict(GOOD, work_dir=str(tmp_path / "w2")), str(out2), d2)
    res2 = json.load(open(out2 / "fullroot_gates.json"))
    assert rc == 1 and "stubbed module refused" in res2["error"] and built2 == [] and not res2["all_ok"]


def test_run_live_fails_on_a_leaky_hygiene_or_a_bus_problem_and_uses_the_real_factory_parameters(tmp_path, monkeypatch):
    monkeypatch.setattr(fg.time, "sleep", lambda s: None)
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)
    d, envs, sup, built = live_deps(tmp_path)
    envs[1].idx = 0  # both sessions answer with the SAME bus ID and namespace
    out = tmp_path / "o"
    out.mkdir()
    rc = fg.run_live(dict(GOOD, work_dir=str(tmp_path / "w")), str(out), d)
    res = json.load(open(out / "fullroot_gates.json"))
    assert rc == 1 and res["bus_problems"] and not res["all_ok"]
    # the real factory builder: the same public DesktopEnv class, the bridge region and the host_run keyword arguments
    from drums_compare import osworld_matched as om
    seen = {}

    class Mod:
        class DesktopEnv:
            def __init__(self, **kw):
                seen.update(kw)
    import envd_linux.scalecua.osworld_provider as op
    monkeypatch.setattr(op, "install", lambda pool: seen.update(installed=pool))
    pool = object()
    om.drums_factory(pool, desktop_env_module=Mod, **stages.hr_kw(dict(GOOD, work_dir=str(tmp_path))))(0)
    assert seen["installed"] is pool and seen["provider_name"] == "docker" and seen["region"] == op.REGION and seen["action_space"] == "pyautogui" and seen["require_a11y_tree"] is False
