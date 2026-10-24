import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "envd-linux"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "envd-linux", "tests"))
from drums_compare import fullroot_gates as fg  # noqa: E402


def cap(dev, ino, bid, mnt, rc_id=0, act='   array [\n      string "org.freedesktop.DBus"\n   ]'):
    base = {k: {"rc": 0, "out": "x", "err": ""} for k in fg.PARITY_PROBES}
    base.update(system_bus_socket={"rc": 0, "out": f"{dev} {ino}", "err": ""}, mnt_ns={"rc": 0, "out": mnt, "err": ""},
                bus_id={"rc": rc_id, "out": f'method return\n   string "{bid}"' if rc_id == 0 else "Failed", "err": ""}, bus_activatable={"rc": 0, "out": act, "err": ""})
    return base


def test_equal_inodes_on_different_tmpfs_devices_are_not_a_collision_but_equal_device_and_inode_are():
    a, b = cap(62, 7, "aa", "mnt:[1]"), cap(74, 7, "bb", "mnt:[2]")
    assert fg.bus_problems([a, b]) == []  # the real run: inode 7 twice, devices 0:62 and 0:74
    assert any("same device and inode" in p for p in fg.bus_problems([a, cap(62, 7, "bb", "mnt:[2]")]))
    assert any("same ID" in p for p in fg.bus_problems([a, cap(74, 7, "aa", "mnt:[2]")]))
    assert any("one mount namespace" in p for p in fg.bus_problems([a, cap(74, 7, "bb", "mnt:[1]")]))


def test_probe_failures_and_activatable_names_are_problems_never_a_pass_by_absence():
    a, b = cap(62, 7, "aa", "mnt:[1]"), cap(74, 7, "bb", "mnt:[2]", rc_id=1)
    p = fg.bus_problems([a, b])
    assert any("probe bus_id failed" in x for x in p)
    leak = cap(74, 7, "bb", "mnt:[2]", act='   array [\n      string "org.freedesktop.DBus"\n      string "org.freedesktop.UPower"\n   ]')
    assert any("activatable names besides" in x for x in fg.bus_problems([a, leak]))
    empty = {k: {"rc": None, "out": "", "err": "probe failed"} for k in fg.PARITY_PROBES}
    assert len(fg.bus_problems([empty, empty])) >= 8


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
    assert none["vm_captured"] is False and len(none["not_comparable"]) == len(fg.PARITY_PROBES) and none["fair_comparison"] is False
    ids = {g["id"] for g in fg.PARITY_GAPS}
    assert {"xvfb_vs_xorg", "no_display_manager", "private_bus_non_activating", "kernel", "screen_lock_and_clock"} <= ids
    assert "kernel" in fg.PARITY_PROBES and "timezone_file" in fg.PARITY_PROBES and "fonts_names_sha" in fg.PARITY_PROBES and "chrome" in fg.PARITY_PROBES and "office" in fg.PARITY_PROBES


class FakeGuest:
    """Two guests sharing one dict mimic LEAKY sessions; separate dicts mimic isolated ones."""
    def __init__(self, state, name):
        self.s, self.name = state, name

    def step(self, code):
        if "canary" in code and "open(" in code:
            self.s["file"] = True
        if "Popen" in code and "canary" in code:
            self.s["proc"] = True

    def run(self, script):
        if script.startswith("test -e"):
            return {"stdout": "file\n" if self.s.get("file") else "", "stderr": "", "returncode": 0}
        if script.startswith("pgrep"):
            return {"stdout": "123\n" if self.s.get("proc") else "", "stderr": "", "returncode": 0}
        return {"stdout": "", "stderr": "", "returncode": 0}

    def reset(self):
        if self.s.get("shared"):
            self.s.clear()
            self.s["shared"] = True
        else:
            self.s.pop("file", None)
            self.s.pop("proc", None)


def test_two_isolated_sessions_pass_and_a_leak_or_a_reset_that_kills_the_neighbour_fails(monkeypatch):
    monkeypatch.setattr(fg.time, "sleep", lambda s: None)
    r = fg.two_session_gate(FakeGuest({}, "a"), FakeGuest({}, "b"))
    assert r["pass"] and r["canary_visible_in_A"] and not r["canary_file_leaked_to_B"]
    shared = {"shared": True}
    leak = fg.two_session_gate(FakeGuest(shared, "a"), FakeGuest(shared, "b"))
    assert not leak["pass"] and leak["canary_file_leaked_to_B"] and leak["canary_process_leaked_to_B"] and not leak["A_canary_survives_B_reset"]


def test_phases_are_separate_clocks_and_an_absent_phase_is_null_with_its_reason(tmp_path):
    t = iter([0, 5, 10, 12, 20, 30])
    c = fg.PhaseClock(clock=lambda: next(t))
    with c.phase("cold_setup"):
        pass
    with c.phase("hygiene"):
        pass
    with c.phase("task"):
        pass
    c.unavailable("checkpoint_save", "no CRIU with an image root")
    r = c.report()
    assert r["cold_setup"] == 5 and r["hygiene"] == 2 and r["task"] == 10 and r["checkpoint_save"] is None and r["restore"] is None and r["evaluation"] is None and "CRIU" in r["unavailable"]["checkpoint_save"]
    with pytest.raises(ValueError):
        with c.phase("bogus"):
            pass
    (tmp_path / "u" / "d").mkdir(parents=True)
    (tmp_path / "u" / "d" / "f").write_bytes(b"x" * 100)
    assert fg.state_bytes(str(tmp_path / "u")) == 100 and fg.state_bytes(str(tmp_path / "none")) is None


def test_applicability_says_which_gates_run_and_labels_every_timing():
    a = fg.gate_applicability({"drums_base_root": "/mnt/img"})
    assert a["gates"]["score_before_correct_wrong"]["runs"] and a["gates"]["two_session_interference"]["runs"]
    assert a["gates"]["snapshot_state_and_gui_state"]["runs"] is False and "CRIU" in a["gates"]["checkpoint_save_restore_clocks"]["why"]
    assert a["label"] == "ARCHITECTURAL_NOT_FAIR_COMPARISON" and a["training_economics_claim"] is False


def test_preflight_refuses_missing_config_and_an_unrunnable_root_and_passes_a_complete_one(tmp_path):
    import test_rootcheck as T
    bad = fg.preflight({})
    assert not bad["all_ok"] and any(c["name"] == "config_drums_template_overlay" and not c["ok"] for c in bad["checks"])
    r = T.image_root(tmp_path, programs=("dbus-daemon", "gnome-session", "gnome-shell", "xprop"))
    T.gnome_files(r)
    t = T.kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.10.12")
    cfg = {"drums_base_root": r, "drums_template_overlay": t, "drums_home": "/home/user", "drums_desktop": "gnome", "drums_uid": 1000, "drums_system_bus": True, "incumbent": "docker_kvm"}
    ok = fg.preflight(cfg)
    assert ok["all_ok"], ok["checks"]
    assert "fullroot_gates run" in ok["command"] and ok["applicability"]["label"] == "ARCHITECTURAL_NOT_FAIR_COMPARISON"
    os.remove(os.path.join(r, "usr/bin/Xvfb"))
    assert not fg.preflight(cfg)["all_ok"]
    assert not fg.preflight(dict(cfg, incumbent="virtualbox"))["all_ok"]


def test_the_run_stage_refuses_when_the_preflight_fails_and_the_supervisor_gets_the_gnome_config(tmp_path, capsys):
    cfgp = tmp_path / "c.json"
    cfgp.write_text("{}")
    assert fg.main(["run", "--config", str(cfgp), "--out", str(tmp_path / "o")]) == 1 and "REFUSED" in capsys.readouterr().out
    from drums_compare import stages
    assert stages.desktop_kw({}) == {}  # defaults unchanged
    assert stages.desktop_kw({"drums_desktop": "gnome", "drums_uid": 1000, "drums_system_bus": True}) == {"desktop": "gnome", "uid": 1000, "gid": 1000, "user": "user", "system_bus": True}
