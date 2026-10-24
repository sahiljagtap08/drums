import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "envd-linux"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "envd-linux", "tests"))
from drums_compare import criu_probe as cp  # noqa: E402
from drums_compare import fullroot_gates as fg  # noqa: E402
from drums_compare import host_run as hr  # noqa: E402

SHA = "a" * 64
GOOD = {"drums_desktop": "gnome", "drums_user": "user", "drums_uid": 1000, "drums_gid": 1000, "drums_home": "/home/user", "drums_system_bus": True, "drums_criu": True, "image_sha256": SHA,
        "drums_base_root": "/mnt/vm-image-ro", "drums_template_overlay": "/opt/drums-overlay/upper", "incumbent": "docker_kvm"}
ID = "a" * 32


def png(color, w=1280, h=800):
    """A solid-colour PNG written by hand (no PIL needed)."""
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class World:
    """A fake host: per-slot guest state; checkpoint snapshots slot 0; restore copies the snapshot (or loses parts of it when asked)."""

    def __init__(self, lose=None, dump_fails=False):
        self.slots, self.snap, self.lose, self.dump_fails, self.destroyed, self.n, self.keep_ns = {}, None, lose or [], dump_fails, [], 0, False

    def fresh(self):
        self.n += 1
        return (f"mnt:[{1000 + (0 if self.keep_ns else self.n)}]", f"pid:[{2000 + (0 if self.keep_ns else self.n)}]")


class FakeCk:
    def __init__(self, path):
        self.path, self.img = path, os.path.join(path, "img")
        os.makedirs(self.img, exist_ok=True)

    def meta(self):
        return {"dump_seconds": 1.5, "image_bytes": 1234, "layers": [], "meta_version": 2, "base_root": "/mnt/vm-image-ro", "image_sha256": SHA}


class FakeSup:
    def __init__(self, world, root):
        self.w, self.root = world, root

    def _dir(self, i):
        return os.path.join(self.root, f"s{i}")

    def create(self, slot, from_checkpoint=None):
        if from_checkpoint is None:
            ns, pns = self.w.fresh()
            self.w.slots[slot] = {"token": "0123456789abcdef", "marker": None, "writer": [41], "gnome": [30], "canary": False, "ns": ns, "pns": pns, "dev": 60 + slot * 10, "x": 0}
        else:
            if self.w.dump_fails:
                raise RuntimeError("restore refused")
            st = dict(self.w.snap)
            for k in self.w.lose:
                st[k] = {"marker": None, "token": "", "writer": [], "gnome": []}[k]
            ns, pns = self.w.fresh()
            st.update(ns=ns, pns=pns, dev=60 + slot * 10, canary=False)
            self.w.slots[slot] = st
        return {"ip": f"10.0.0.{slot}", "criu_restore_s": 0.5, "reseed_s": 0.2, "restore_total_s": 0.9}

    def info(self, slot):
        return {"ip": f"10.0.0.{slot}"}

    def checkpoint(self, slot, path):
        if self.w.dump_fails:
            ck = FakeCk(path)
            open(os.path.join(ck.img, "dump.log"), "w").write("(00.01) info\n(00.02) Error (criu/sk-unix.c:99): unix socket /run/dbus/system_bus_socket not supported\n")
            raise RuntimeError("criu dump failed")
        self.w.snap = dict(self.w.slots.pop(slot))
        ck = FakeCk(path)
        os.makedirs(os.path.join(path, "layer", "upper"), exist_ok=True)
        open(os.path.join(path, "layer", "upper", "f"), "wb").write(b"x" * 77)
        return ck

    def destroy(self, slot, keep_state=False):
        if slot not in self.w.slots:
            raise RuntimeError("slot not running")
        self.w.destroyed.append(slot)
        self.w.slots.pop(slot)

    def close(self):
        pass


class FakeGuest:
    def __init__(self, world, slot, resetter=None, shot=None):
        self.w, self.slot, self.resetter, self.shot = world, slot, resetter, shot

    def st(self):
        return self.w.slots[self.slot]

    def step(self, code):
        s = self.st()
        ok = {"stdout": "", "stderr": "", "returncode": 0}
        if self.fail_step and self.fail_step in code:
            return {"stdout": "", "stderr": "boom", "returncode": 1}
        if "gate_holder" in code or ("socket" in code and "sendall" in code):
            return ok
        if "probe_marker" in code:
            s["marker"] = code.split("write('")[1].split("'")[0]
        elif "canary_" in code and "Popen" in code:
            s["canary"] = True
        elif "canary_" in code and "write(" in code:
            s["cfile"] = True
        return ok

    fail_step = None
    token_error = False

    def run(self, script):
        s, out, rc = self.st(), "", 0
        if "os.kill" in script:
            s["canary"] = False
        elif script.startswith("rm -f /tmp/canary_"):
            s.pop("cfile", None)
        elif "create_connection" in script:
            out = s["token"]
            if self.token_error:
                return {"stdout": "ERROR RuntimeError", "stderr": "boom", "returncode": 1}
        elif "cat /tmp/probe_marker" in script:
            out = s["marker"] or ""
        elif "/proc/[0-9]*" in script and "soffice.bin" in script:
            out = "".join(f"{p}\n" for p in s["writer"])
        elif "/proc/[0-9]*" in script and "gnome-shell" in script:
            out = "".join(f"{p}\n" for p in s["gnome"])
        elif "canary_" in script and "python3 -c" in script:
            out = "55 99\n" if s.get("canary") else ""
        elif script.startswith("test -e /tmp/canary_"):
            out = "file" if s.get("cfile") else ""
        elif "stat -c" in script and "system_bus_socket" in script:
            out = f"{s['dev']} 7"
        elif "readlink /proc/1/ns/mnt" in script:
            out = s["ns"]
        elif "readlink /proc/1/ns/pid" in script:
            out = s["pns"]
        elif "GetId" in script:
            out = f'method return\n   string "{ID}"'
        elif "ListActivatableNames" in script:
            out = '   array [\n      string "org.freedesktop.DBus"\n   ]'
        elif "gnome-shell --version" in script:
            out = "GNOME Shell 42.9"
        else:
            out = "x"
        return {"stdout": out, "stderr": "", "returncode": rc}

    def screenshot(self):
        return png((10, 10, 10) if self.shot is None else self.shot)

    def reset(self):
        return self.resetter()


class Imgs:
    """Fake screen metrics: identical bytes -> 0 change, different -> 1.0; colours settable (a blank desktop has few)."""
    def __init__(self, colors=100, raises=False):
        self.c, self.raises = colors, raises

    def fraction(self, a, b):
        if self.raises:
            raise ImportError("No module named 'PIL'")
        return 0.0 if a == b else 1.0

    def colors(self, png):
        return self.c


def deps_for(tmp_path, world, shot_after=None, images=None):
    sup = FakeSup(world, str(tmp_path / "w"))

    def make(slot, resetter=None):
        return FakeGuest(world, slot, resetter, shot=shot_after if slot else None)
    return {"build_drums": lambda cfg, work: (sup, object(), 0.5), "guest": make, "images": images or Imgs()}, sup


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(cp.time, "sleep", lambda s: None)
    monkeypatch.setattr(fg.time, "sleep", lambda s: None)


def run(tmp_path, world, **kw):
    d, sup = deps_for(tmp_path, world, **kw)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    rc = cp.run_probe(dict(GOOD, work_dir=str(tmp_path / "w")), str(out), d)
    return rc, json.load(open(out / "criu_probe.json")), sup


def test_the_probe_runs_the_whole_sequence_with_a_world_that_preserves_everything(tmp_path):
    rc, res, sup = run(tmp_path, World())
    assert rc == 0 and res["all_ok"], res.get("error") or res
    assert res["criu_compatibility_claim"].startswith("NONE until") and res["label"] == "ARCHITECTURAL_NOT_FAIR_COMPARISON" and res["training_economics_claim"] is False
    for s in ("1", "2"):
        v = res["siblings"][s]
        assert v["pass"] and v["same_token"] and v["marker_restored"] and v["writer_same_pids"] and v["gnome_shell_live_after"] and v["screenshot_changed_pixel_fraction"] == 0 and v["screen_ok"]
    assert res["bus_problems"] == [] and res["bus_problems_after_reset"] == [] and "SAME dbus-daemon state" in res["bus_clone_note"] and res["canaries"]["pass"]
    assert res["after_reset_capture"]["mnt_ns"]["out"] and res["after_reset_capture"]["pid_ns"]["out"]
    c = res["clocks"]
    assert all(c[k] is not None for k in ("cold_setup", "parity_capture", "state_setup", "checkpoint_save", "restore", "reseed", "two_session_gate"))
    assert res["checkpoint"]["layer_upper_bytes"] == 77 and res["state_bytes"]["checkpoint_images"] == 1234
    assert res["task_eval"]["run"] is False and "DesktopEnv lease" in res["task_eval"]["why"]
    rec = res["wall_reconciliation"]
    assert abs(rec["wall_s"] - rec["sum_of_phases_s"] - rec["unattributed_s"]) < 1e-6 and res["cleanup_errors"] == []
    assert os.path.exists(tmp_path / "out" / "screenshot_before.png") and os.path.exists(tmp_path / "out" / "screenshot_sibling2.png")


@pytest.mark.parametrize("lose,field", [(["marker"], "marker_restored"), (["token"], "same_token"), (["writer"], "writer_live_after"), (["gnome"], "gnome_shell_live_after")])
def test_a_restore_that_loses_state_fails_the_named_check(tmp_path, lose, field):
    rc, res, sup = run(tmp_path, World(lose=lose))
    assert rc == 1 and not res["all_ok"] and not res["siblings"]["1"][field] and not res["siblings"]["1"]["pass"]


def test_a_changed_blank_or_unmeasurable_screen_fails_the_sibling_even_when_everything_else_is_back(tmp_path):
    rc, res, sup = run(tmp_path, World(), shot_after=(250, 250, 250))
    v = res["siblings"]["1"]
    assert rc == 1 and v["same_token"] and v["marker_restored"] and v["writer_same_pids"] and v["screenshot_changed_pixel_fraction"] == 1.0 and not v["screen_ok"] and not v["pass"]  # a changed desktop
    d, sup2 = deps_for(tmp_path / "b", World(), images=Imgs(colors=3))
    (tmp_path / "b").mkdir()
    out = tmp_path / "b" / "o"
    out.mkdir()
    cp.run_probe(dict(GOOD, work_dir=str(tmp_path / "b" / "w")), str(out), d)
    blank = json.load(open(out / "criu_probe.json"))
    assert not blank["all_ok"] and not blank["siblings"]["1"]["pass"] and not blank["siblings"]["1"]["screen_ok"] and blank["siblings"]["1"]["distinct_colors_after"] == 3  # a blank desktop never passes
    d, sup3 = deps_for(tmp_path / "c", World(), images=Imgs(raises=True))
    (tmp_path / "c").mkdir()
    out = tmp_path / "c" / "o"
    out.mkdir()
    cp.run_probe(dict(GOOD, work_dir=str(tmp_path / "c" / "w")), str(out), d)
    un = json.load(open(out / "criu_probe.json"))
    v = un["siblings"]["1"]
    assert not un["all_ok"] and v["screenshot_changed_pixel_fraction"] is None and "ImportError" in v["screenshot_diff_note"] and not v["screen_ok"] and not v["pass"]  # unmeasured is not a pass


def test_a_restore_that_changes_the_writer_pids_fails_even_if_a_writer_is_live(tmp_path):
    w = World()
    d, sup = deps_for(tmp_path, w)
    orig = sup.create

    def create(slot, from_checkpoint=None):
        r = orig(slot, from_checkpoint)
        if from_checkpoint is not None:
            w.slots[slot]["writer"] = [99]  # a Writer is live but it is not the one that was dumped
        return r
    sup.create = create
    out = tmp_path / "o"
    out.mkdir()
    cp.run_probe(dict(GOOD, work_dir=str(tmp_path / "w")), str(out), d)
    v = json.load(open(out / "criu_probe.json"))["siblings"]["1"]
    assert v["writer_live_after"] and not v["writer_same_pids"] and not v["pass"]


def test_the_token_must_be_a_valid_random_value_with_return_code_zero_and_an_error_text_never_matches_itself(tmp_path):
    w = World()
    sup = FakeSup(w, str(tmp_path))
    sup.create(0)
    g = FakeGuest(w, 0)
    assert cp.ask_token(g) == ("0123456789abcdef", None)
    g.token_error = True
    tok, why = cp.ask_token(g)
    assert tok is None and "rc 1" in why  # 'ERROR RuntimeError' is not a token
    g.token_error = False
    w.slots[0]["token"] = "ERROR RuntimeError"
    assert cp.ask_token(g)[0] is None and "not a 16-hex" in cp.ask_token(g)[1]
    w.slots[0]["token"] = "0123456789ABCDEF"
    assert cp.ask_token(g)[0] is None  # uppercase / wrong alphabet
    w.slots[0]["token"] = ""
    assert cp.ask_token(g)[0] is None
    # an error before AND after the restore must not make "same token" true
    st = {"token": "ERROR RuntimeError", "marker": "m", "writer_pids": [41], "gnome_shell_pids": [30]}
    sup.create(1)
    g1 = FakeGuest(w, 1)
    g1.token_error = True
    before = fg.capture_parity(g1.run)
    res, _ = cp.compare_sibling(g1, before, st, png((1, 2, 3)), images=Imgs())
    assert res["same_token"] is False and res["raw_after"]["token"] is None and res["raw_after"]["token_error"]


def test_setup_state_requires_every_launch_write_and_marker_to_succeed(tmp_path):
    w = World()
    sup = FakeSup(w, str(tmp_path))
    sup.create(0)
    for bad in ("probe_marker", "libreoffice", "gate_holder"):
        g = FakeGuest(w, 0)
        g.fail_step = bad
        with pytest.raises(RuntimeError, match="failed"):
            cp.setup_state(g)
    ok = cp.setup_state(FakeGuest(w, 0))
    assert TOKEN_OK(ok["token"]) and ok["marker"] and ok["writer_pids"] == [41]


def TOKEN_OK(t):
    return cp.TOKEN_RE.match(t) is not None


def test_a_reset_sibling_that_kept_its_old_namespaces_or_buses_fails(tmp_path):
    w = World()
    w.keep_ns = False
    rc, res, sup = run(tmp_path, w)
    assert rc == 0 and res["bus_problems_after_reset"] == []
    w2 = World()
    w2.keep_ns = True  # every restore reports the same namespace ids: sibling buses and a "fresh" restore are indistinguishable
    rc, res, sup = run(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, w2)
    assert rc == 1 and not res["all_ok"] and (res["bus_problems"] or res["bus_problems_after_reset"])


def test_a_failed_dump_keeps_the_raw_criu_error_and_names_the_blocker(tmp_path):
    rc, res, sup = run(tmp_path, World(dump_fails=True))
    assert rc == 1 and "criu dump failed" in res["error"] and "sk-unix.c" in res["blocker_first_error_line"] and "dump.log" in res["criu_logs"]
    assert res["criu_logs"]["dump.log"]["tail"] and not res["all_ok"]


def test_cleanup_errors_fail_the_probe_and_a_missing_session_is_not_one(tmp_path):
    w = World()
    d, sup = deps_for(tmp_path, w)
    orig = sup.destroy

    def bad(slot, keep_state=False):
        if slot == 1:
            raise RuntimeError("umount busy")
        return orig(slot, keep_state)
    sup.destroy = bad
    out = tmp_path / "o"
    out.mkdir()
    rc = cp.run_probe(dict(GOOD, work_dir=str(tmp_path / "w")), str(out), d)
    res = json.load(open(out / "criu_probe.json"))
    assert rc == 1 and any("umount busy" in e for e in res["cleanup_errors"]) and not any("not running" in e for e in res["cleanup_errors"])


def test_preflight_scopes_enforce_exact_values_and_the_host_scope_needs_a_verified_receipt_and_a_runnable_criu(tmp_path):
    import test_rootcheck as T
    ok = cp.preflight(GOOD, "config")
    assert ok["all_ok"] and "OFFLINE" in ok["scope_note"] and ok["receipt"] is None
    for k, v in (("drums_criu", False), ("drums_desktop", "openbox"), ("drums_uid", 1001), ("image_sha256", "zz"), ("drums_base_root", "/"), ("incumbent", "virtualbox")):
        assert not cp.preflight(dict(GOOD, **{k: v}), "config")["all_ok"], k
    r = T.image_root(tmp_path, programs=("dbus-daemon", "gnome-session", "gnome-shell", "xprop"))
    T.gnome_files(r)
    t = T.kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.10.12")
    os.symlink("python3.10", os.path.join(r, "usr/bin/python3"))
    from envd_linux import session as S
    for m in ("session_init.py", "clone.py"):
        dst = os.path.join(t, S.PKG_DIR.lstrip("/"), "envd_linux", m)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, "w").write(open(os.path.join(S.PKG_DIR, "envd_linux", m)).read())
    cfg = dict(GOOD, drums_base_root=r, drums_template_overlay=t)
    good_receipt = {"verified": True, "problems": [], "computed_sha256": SHA, "image_path": "/i.qcow2", "nbd_device": "/dev/nbd0", "base_root": r}
    deps = {"verify": lambda base, sha: good_receipt, "criu_check": lambda: (True, "ok"), "pil_check": lambda: None}
    h = cp.preflight(cfg, "host", deps)
    assert h["all_ok"], [c for c in h["checks"] if not c["ok"]] and h["receipt"] == good_receipt
    assert not cp.preflight(cfg, "host", dict(deps, verify=lambda b, s: {"verified": False, "problems": ["the served image's sha256 is not the pinned"]}))["all_ok"]
    assert not cp.preflight(cfg, "host", dict(deps, criu_check=lambda: (False, "criu check failed")))["all_ok"]
    def nopil():
        raise ImportError("PIL")
    assert not cp.preflight(cfg, "host", dict(deps, pil_check=nopil))["all_ok"]  # unmeasurable screens could never pass
    assert any(c["name"] == "host_pil" for c in h["checks"])
    os.remove(os.path.join(t, S.PKG_DIR.lstrip("/"), "envd_linux", "session_init.py"))
    bad = cp.preflight(cfg, "host", deps)
    assert not bad["all_ok"] and any("stage-2" in c["detail"] for c in bad["checks"] if not c["ok"])


def test_run_refuses_without_a_host_scope_pass_and_the_receipt_reaches_the_supervisor_config(tmp_path, capsys):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(GOOD))
    assert cp.main(["preflight", "--config", str(p), "--out", str(tmp_path / "o")]) == 0
    assert cp.main(["run", "--config", str(p), "--out", str(tmp_path / "o")]) == 1 and "HOST-scope preflight failed" in capsys.readouterr().out
    from drums_compare import stages
    assert stages.image_kw({}) == {} and stages.image_kw({"image_sha256": SHA, "image_receipt": {"verified": True}}) == {"image_sha256": SHA, "image_receipt": {"verified": True}}


def test_the_guest_client_rejects_a_boolean_return_code_like_guestexec():
    for bad in (True, False, "0", None):
        g = cp.GuestHTTP(lambda: "x", http=lambda *a, bad=bad, **k: json.dumps({"status": "success", "output": "", "returncode": bad}).encode())
        with pytest.raises(RuntimeError, match="/execute fault"):
            g.run("x")


def test_the_guest_client_uses_the_current_address_and_raises_on_a_fault():
    seen = []
    ip = ["10.0.0.5"]

    def http(url, data=None, timeout=60):
        seen.append((url, data))
        return json.dumps({"status": "success", "output": "o", "error": "", "returncode": 0}).encode()
    g = cp.GuestHTTP(lambda: ip[0], http=http)
    assert g.run("echo hi")["stdout"] == "o" and seen[0][0] == "http://10.0.0.5:5000/execute" and seen[0][1] == {"command": "echo hi", "shell": True}
    ip[0] = "10.0.0.6"
    g.run("x")
    assert seen[1][0].startswith("http://10.0.0.6:5000")  # a restored session may have a new address
    bad = cp.GuestHTTP(lambda: "x", http=lambda *a, **k: json.dumps({"status": "error"}).encode())
    with pytest.raises(RuntimeError, match="/execute fault"):
        bad.run("x")
