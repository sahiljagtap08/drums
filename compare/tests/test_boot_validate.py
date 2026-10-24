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
    d = dict(root=str(tmp_path / "root"), overlay=str(tmp_path / "ov"), kit="/opt/drums-osworld-real", state=str(tmp_path / "state"), out=str(tmp_path / "out"), uid=1000, gid=1000, user="user", home="/home/user")
    d.update(kw)
    return argparse.Namespace(**d)


def test_a_root_that_cannot_run_the_session_aborts_with_reasons_and_creates_no_session_or_state(tmp_path):
    os.makedirs(tmp_path / "root")
    os.makedirs(tmp_path / "ov")
    made = []
    r = bv.run(args(tmp_path), {"supervisor": lambda *a, **k: made.append(1)})
    assert r["status"] == "ABORT_ROOT" and not r["boot_identity_ok"] and r["problems"] and not made and not os.path.exists(tmp_path / "state")
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
        FakeSup.kw = k
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
    assert out["status"] == "BOOT_OK" and out["boot_identity_ok"] and out["timing_authorized"] is False and "gates" in out["note"] and FakeSup.kw["home"] == "/home/user" and FakeSup.kw["uid"] == 1000 and FakeSup.closed and out["server_python"] == "(3, 10)" and (tmp_path / "out" / "boot.png").exists()
    FakeSup.closed = False
    FakeSup.marks = [dict(GOOD, wm_name="Openbox")]
    import shutil
    shutil.rmtree(tmp_path / "state")
    out = bv.run(a, {"supervisor": FakeSup, "http": fake_http})
    assert out["status"] == "BOOT_MISMATCH" and not out["boot_identity_ok"] and FakeSup.closed
    FakeSup.marks = [GOOD]
    shutil.rmtree(tmp_path / "state")
    out = bv.run(a, {"supervisor": FakeSup, "http": lambda u, d=None, timeout=20: b"not a png" if u.endswith("screenshot") else json.dumps({"returncode": 0, "output": "(3, 12)"}).encode()})
    assert not out["boot_identity_ok"] and any("screenshot" in p for p in out["problems"]) and any("3.10" in p for p in out["problems"])


def test_a_supervisor_constructor_refusal_becomes_a_report_and_no_session_is_created(tmp_path):
    r, t = _ready_root(tmp_path)

    def boom(*a, **k):
        raise ValueError("the new base root cannot run the session: x")
    out = bv.run(args(tmp_path, root=r, overlay=t), {"supervisor": boom})
    assert out["status"] == "ABORT_PREFLIGHT" and not out["boot_identity_ok"] and "preflight refused" in out["problems"][0]
    assert json.load(open(tmp_path / "out" / "boot_validation.json"))["status"] == "ABORT_PREFLIGHT"


def test_the_default_home_is_the_images_user_home():
    import inspect
    assert 'default="/home/user"' in inspect.getsource(bv.main)


KFAKES = {
    "mount": 'echo "mount $*" >> "$FAKE_LOG"; last="${@: -1}"; rmdir "$last"; ln -s "$(dirname "$last")/upper" "$last"; touch "$FAKE_DIR/mounted"',
    "umount": 'echo "umount $*" >> "$FAKE_LOG"; [ -z "${FAKE_UMOUNT_FAIL:-}" ] || exit 1; rm -f "$1"; mkdir "$1"; rm -f "$FAKE_DIR/mounted"',
    "mountpoint": '[ -e "$FAKE_DIR/mounted" ]',
    "chroot": '''echo "chroot $*" >> "$FAKE_LOG"
case "$*" in
  *"-m venv"*) case "$*" in *--without-pip*) ;; *) echo "ensurepip is not available" >&2; exit 1;; esac; mkdir -p "$1/opt/drums-osworld-real/venv/bin"; [ "${FAKE_TERM_AT:-}" != venv ] || kill -TERM $PPID;;
  *"pip freeze"*) echo "flask==3.0.0";;
  *"print("*) echo 3.10;;
esac''',
}


@pytest.fixture
def kit_env(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("the script refuses to run unless root")
    import subprocess
    fake = tmp_path / "bin"
    fake.mkdir()
    for n, b in KFAKES.items():
        (fake / n).write_text("#!/bin/bash\n" + b + "\n")
        (fake / n).chmod(0o755)
    root = tmp_path / "root"
    for f, mode in (("usr/bin/python3", 0o755), ("usr/lib/python3/dist-packages/pyatspi/__init__.py", 0o644)):
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text("x")
        (root / f).chmod(mode)
    (root / "etc").mkdir()
    os.symlink("../run/systemd/resolve/stub-resolv.conf", root / "etc" / "resolv.conf")  # dangling, as on a stock image
    src = tmp_path / "osw"
    (src / "desktop_env" / "server").mkdir(parents=True)
    for f in ("main.py", "pyxcursor.py", "screen_recording.py", "__init__.py"):
        (src / "desktop_env" / "server" / f).write_text("# " + f + "\n")
    (src / "LICENSE").write_text("Apache License\n")
    import hashlib
    whl = tmp_path / "pip-24.0-py3-none-any.whl"
    whl.write_bytes(b"PK-not-a-real-wheel")
    wsha = hashlib.sha256(whl.read_bytes()).hexdigest()
    res = tmp_path / "resolv.real"
    res.write_text("nameserver 10.0.0.2\n")
    d = tmp_path / "fstate"
    d.mkdir()
    ov = tmp_path / "ov"

    def run(wsha_override=None, **env):
        e = {"PATH": f"{fake}:/usr/bin:/bin", "FAKE_LOG": str(d / "log"), "FAKE_DIR": str(d), "DRUMS_RESOLVER_FILE": str(res), "HOME": "/nonexistent"}
        e.update({k: str(v) for k, v in env.items()})
        r = subprocess.run(["bash", os.path.join(D, "prepare_kit_for_image.sh"), str(root), str(src), str(ov), str(whl), wsha_override or wsha], capture_output=True, text=True, env=e, timeout=60)
        return r, ((d / "log").read_text().splitlines() if (d / "log").exists() else [])
    run.root, run.ov, run.res = root, ov, res
    return run


def test_the_kit_script_uses_a_real_resolver_in_the_overlay_only_writes_wrappers_and_provenance_and_never_imports_pyautogui_without_a_display(kit_env):
    r, log = kit_env()
    assert r.returncode == 0, r.stderr
    kit = kit_env.ov / "upper" / "opt" / "drums-osworld-real"
    assert (kit / "bin" / "python").read_text() == '#!/bin/sh\nexec "/opt/drums-osworld-real/venv/bin/python" "$@"\n' and os.access(kit / "bin" / "python3", os.X_OK)
    assert (kit / "PROVENANCE.json").exists() and (kit / "LICENSE").exists() and "flask==3.0.0" in (kit / "pip-freeze.txt").read_text() and "nameserver" not in (kit / "resolver-used.txt").read_text()
    assert "resolver used for pip" in (kit / "resolver-used.txt").read_text()
    assert os.path.islink(kit_env.root / "etc" / "resolv.conf") and not os.path.exists(kit_env.root / "etc" / "resolv.conf")  # the lower root is untouched
    assert not (kit_env.ov / "upper" / "etc" / "resolv.conf").exists()  # and the temporary resolver is gone again
    imp = [x for x in log if "import flask" in x]
    assert len(imp) == 1 and "import pyautogui" not in imp[0] and "find_spec" in imp[0]
    assert not [x for x in log if x.startswith("chroot") and "pip install" in x and "==" in x]  # the dependency list is realkit.PIP, unpinned
    assert any("pip install -q flask lxml pyautogui" in x for x in log)
    assert any("-m venv --without-pip --system-site-packages" in x for x in log)  # never needs ensurepip
    seed = [x for x in log if "--no-index --no-deps" in x]
    assert len(seed) == 1 and "PYTHONPATH=/tmp/drums-seed/pip-24.0-py3-none-any.whl" in seed[0]
    assert log.index(seed[0]) < log.index([x for x in log if "pip install -q flask" in x][0])  # pip is seeded before the dependency install
    assert "sha256" in (kit / "pip-seed.txt").read_text() and "pip-24.0" in (kit / "pip-seed.txt").read_text()
    assert not (kit_env.ov / "upper" / "tmp" / "drums-seed").exists()  # the seed copy is removed again


def test_the_resolver_is_in_place_while_pip_runs(kit_env):
    # the fake chroot cannot see it, so check the order in the script text instead: copy, then pip, then removal in the trap
    s = open(os.path.join(D, "prepare_kit_for_image.sh")).read()
    assert s.index('cp -L -- "$RES" "$M/etc/resolv.conf"') < s.index("-m pip install -q --no-index") and 'rm -f -- "$OV/upper/etc/resolv.conf"' in s and [l for l in s.splitlines() if "rm -rf" in l and not l.lstrip().startswith("#")][0].count("drums-seed") == 1 and s.count("rm -rf --") == 1 and "resolv.conf.drums" not in s


def test_a_wrong_wheel_hash_or_missing_seed_is_refused_before_anything_is_mounted(kit_env):
    r, log = kit_env(wsha_override="0" * 64)
    assert r.returncode == 2 and "does not match" in r.stderr and not log and not kit_env.ov.exists()
    r, log = kit_env(wsha_override="xyz")
    assert r.returncode == 2 and "64 lowercase hex" in r.stderr and not log


def test_a_venv_that_would_need_ensurepip_fails_loudly_in_the_fake_so_the_without_pip_branch_is_what_is_tested(kit_env):
    s = open(os.path.join(D, "prepare_kit_for_image.sh")).read()
    assert "-m venv --without-pip" in s and "-m venv --system-site-packages" not in s


def test_a_failed_unmount_is_a_failure_that_names_the_retained_mount(kit_env):
    r, log = kit_env(FAKE_UMOUNT_FAIL="1")
    assert r.returncode != 0 and "STILL MOUNTED" in r.stderr and str(kit_env.ov / "merged") in r.stderr


def test_a_term_during_the_build_unmounts_removes_the_resolver_and_exits_143(kit_env):
    r, log = kit_env(FAKE_TERM_AT="venv")
    assert r.returncode == 143 and any(x.startswith("umount") for x in log) and not (kit_env.ov / "upper" / "etc" / "resolv.conf").exists()


def test_the_kit_script_refuses_a_non_image_root_before_changing_anything(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("the script refuses to run unless root")
    import subprocess
    sh = os.path.join(D, "prepare_kit_for_image.sh")
    assert subprocess.run(["bash", "-n", sh]).returncode == 0
    r = subprocess.run(["bash", sh, str(tmp_path), str(tmp_path), str(tmp_path / "ov"), "/x/pip-1-py3-none-any.whl", "0" * 64], capture_output=True, text=True)
    assert r.returncode == 2 and not (tmp_path / "ov").exists()
    code = "\n".join(l for l in open(sh).read().splitlines() if not l.lstrip().startswith("#"))
    assert 'chroot "$M" /usr/bin/python3 -m venv' in code and "3.12" not in code and "apt" not in code and "lowerdir=$ROOT" in code
