import argparse
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "envd-linux"))
from drums_compare import boot_validate as bv  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "aws_host")
GOOD = {"mark": "identity", "wm_name": "GNOME Shell", "desktop_procs": [["gnome-shell", 1000], ["gnome-session-binary", 1000]], "server_uids": [1000]}


def test_identity_needs_the_running_wm_name_gnome_shell_and_the_requested_uid_not_a_binary_version():
    assert bv.identity_problems([GOOD], 1000) == []
    assert any("no identity" in p for p in bv.identity_problems([], 1000))
    assert any("'Openbox'" in p for p in bv.identity_problems([dict(GOOD, wm_name="Openbox")], 1000))
    assert any("no window manager name" in p for p in bv.identity_problems([dict(GOOD, wm_name=None)], 1000))
    assert any("no gnome-shell" in p for p in bv.identity_problems([dict(GOOD, desktop_procs=[["openbox", 1000]])], 1000))
    assert any("do not all run as uid" in p for p in bv.identity_problems([dict(GOOD, desktop_procs=[["gnome-shell", 0]])], 1000))
    assert any("server processes" in p for p in bv.identity_problems([dict(GOOD, server_uids=[0, 1000])], 1000))


def args(tmp_path, **kw):
    d = dict(root=str(tmp_path / "root"), overlay=str(tmp_path / "ov"), kit="/opt/drums-osworld-real", state=str(tmp_path / "state"), out=str(tmp_path / "out"), uid=1000, gid=1000, user="user")
    d.update(kw)
    return argparse.Namespace(**d)


def test_a_root_that_cannot_run_the_session_aborts_with_reasons_and_creates_no_session_or_state(tmp_path):
    os.makedirs(tmp_path / "root")
    os.makedirs(tmp_path / "ov")
    made = []
    r = bv.run(args(tmp_path), {"supervisor": lambda *a, **k: made.append(1)})
    assert r["status"] == "ABORT_ROOT" and not r["timing_allowed"] and r["problems"] and not made and not os.path.exists(tmp_path / "state")
    assert json.load(open(tmp_path / "out" / "boot_validation.json"))["status"] == "ABORT_ROOT"


def _ready_root(tmp_path):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "envd-linux", "tests"))
    import test_rootcheck as T
    r = T.image_root(tmp_path, programs=("dbus-daemon", "wmctrl", "gnome-session", "gnome-shell", "xprop"))
    t = T.kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.10.12")
    return r, t


class FakeSup:
    marks = [GOOD]

    def __init__(self, state, *a, **k):
        self.state, self.kw = state, k
        os.makedirs(os.path.join(state, "s0"))
        open(os.path.join(state, "s0", "timing.jsonl"), "w").write("".join(json.dumps(m) + "\n" for m in self.marks))
        self.closed = False

    def create(self, slot):
        return {"ip": "10.200.0.2"}

    def close(self):
        FakeSup.closed = True


def fake_http(url, data=None, timeout=20):
    if url.endswith("/screenshot"):
        return b"\x89PNG\r\n\x1a\n" + b"x" * 6000
    return json.dumps({"returncode": 0, "output": "(3, 10)\n"}).encode()


def test_all_checks_passing_allows_timing_and_a_mismatch_never_does_and_the_session_is_always_closed(tmp_path):
    r, t = _ready_root(tmp_path)
    a = args(tmp_path, root=r, overlay=t)
    out = bv.run(a, {"supervisor": FakeSup, "http": fake_http})
    assert out["status"] == "BOOT_OK" and out["timing_allowed"] and FakeSup.closed and out["server_python"] == "(3, 10)" and (tmp_path / "out" / "boot.png").exists()
    FakeSup.closed = False
    FakeSup.marks = [dict(GOOD, wm_name="Openbox")]
    import shutil
    shutil.rmtree(tmp_path / "state")
    out = bv.run(a, {"supervisor": FakeSup, "http": fake_http})
    assert out["status"] == "BOOT_MISMATCH" and not out["timing_allowed"] and FakeSup.closed
    FakeSup.marks = [GOOD]
    shutil.rmtree(tmp_path / "state")
    out = bv.run(a, {"supervisor": FakeSup, "http": lambda u, d=None, timeout=20: b"not a png" if u.endswith("screenshot") else json.dumps({"returncode": 0, "output": "(3, 12)"}).encode()})
    assert not out["timing_allowed"] and any("screenshot" in p for p in out["problems"]) and any("3.10" in p for p in out["problems"])


def test_the_kit_preparation_script_refuses_before_changing_anything_and_uses_the_images_own_python(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("the script refuses to run unless root")
    import subprocess
    sh = os.path.join(D, "prepare_kit_for_image.sh")
    assert subprocess.run(["bash", "-n", sh]).returncode == 0
    r = subprocess.run(["bash", sh, str(tmp_path), str(tmp_path), str(tmp_path / "ov")], capture_output=True, text=True)
    assert r.returncode == 2 and "not the image root" in r.stderr and not (tmp_path / "ov").exists()
    s = open(sh).read()
    code = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
    assert 'chroot "$OV/merged" /usr/bin/python3 -m venv' in s and "3.12" not in code and "apt" not in s and "lowerdir=$ROOT" in s and "rm -rf" not in s
