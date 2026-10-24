"""PROTOTYPE of mechanism C (docs/l1-shared-runtime-design.md sections 14 and 15): CRIU checkpoint and restore of a session.

Unit part: the exact CRIU arguments (what is made external, which flags carry the hard-won fixes). Integration part (needs root, the private CRIU
install in DRUMS_CRIU_HOME, and the real-server kit): a server-only session is checkpointed, two siblings are restored from the one checkpoint, both
answer on their own address, the sharing audit finds nothing shared that a pair of cold sessions does not also share, and teardown leaves nothing behind."""

import os
import shutil
import subprocess
import time
import urllib.request

import pytest

from envd_linux import criu as cr
from envd_linux import realkit
from envd_linux.session import Supervisor
from envd_linux.template import build_home_template

HOME = os.environ.get("DRUMS_CRIU_HOME")


class FakeCriu:
    def __init__(self):
        self.calls = []

    def run(self, *args):
        self.calls.append(args)

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()


def test_dump_marks_the_veth_x11_dir_and_device_nodes_external_and_keeps_locks(tmp_path):
    f = FakeCriu()
    cr.dump(f, 4242, str(tmp_path), inner_if="dvs40")
    a = f.calls[0]
    assert a[0] == "dump" and "-t" in a and "4242" in a
    assert "veth[dvs40]:ext" in a and "mnt[/tmp/.X11-unix]:x11" in a and "mnt[/dev/null]:null" in a
    assert "--file-locks" in a and "--tcp-close" in a and "--force-irmap" in a and "--ghost-limit" in a
    assert "--leave-running" not in a  # the checkpoint is taken once, offline


def test_restore_binds_the_new_root_x11_dir_and_names_the_host_veth(tmp_path):
    f = FakeCriu()
    cr.restore(f, str(tmp_path), "/r/root", "/r/x11", "dvh7", "/r/pid", inner_if="dvs40")
    a = f.calls[0]
    assert a[0] == "restore" and "--root" in a and "/r/root" in a and "-d" in a
    assert "veth[dvs40]:dvh7" in a and "mnt[x11]:/r/x11" in a and "mnt[null]:/dev/null" in a and "--file-locks" in a


def test_criu_requires_a_private_home():
    old = os.environ.pop("DRUMS_CRIU_HOME", None)
    try:
        with pytest.raises(cr.CriuError):
            cr.Criu()
    finally:
        if old:
            os.environ["DRUMS_CRIU_HOME"] = old


def _prereqs():
    if not HOME or not os.path.exists(os.path.join(HOME, "x-4.2", "usr", "sbin", "criu")):
        return "set DRUMS_CRIU_HOME to the private CRIU directory"
    if os.geteuid() != 0 or not realkit.available(realkit.DEFAULT_KIT) or realkit.missing():
        return "needs root and the built real-server kit"
    for b in ("unshare", "nsenter", "Xvfb", "pivot_root", "mount", "nft"):
        if shutil.which(b) is None:
            return f"missing {b}"
    if subprocess.run(["/usr/bin/python3", "-c", "import Xlib, pyroute2, PIL"], capture_output=True).returncode:
        return "system python3 needs python-xlib, pyroute2 and Pillow"


@pytest.mark.skipif(_prereqs() is not None, reason=str(_prereqs()))
def test_two_siblings_restored_from_one_checkpoint_serve_audit_clean_and_leave_nothing(tmp_path):
    from envd_linux.l1proto import criu_probe as cp
    kit = realkit.DEFAULT_KIT
    tpl = tmp_path / "tpl"
    build_home_template(str(tpl / "home" / "drums"))
    sup = Supervisor(str(tmp_path / "state"), str(tpl), root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=kit,
                     init_python=os.path.join(kit, "venv", "bin", "python"), server_mode="module", ready_timeout=120,
                     criu_friendly=True, criu=cr.Criu(HOME), sweep_dir=str(tmp_path / "led"))
    try:
        sup.create(0)
        time.sleep(1)
        ck = sup.checkpoint(0, str(tmp_path / "ck"))
        sup.restore_from = ck
        a, b = sup.create(1), sup.create(2)
        assert a["restored"] and b["restored"] and a["ip"] != b["ip"]
        for i in (a, b):
            assert urllib.request.urlopen(f"http://{i['ip']}:5000/platform", timeout=3).read() == b"Linux"
        res = cp.audit_pair(a, b)
        assert res["PASS_ignoring_devices"], res
        assert not res["shared_pipe_or_socket_inodes"] and not res["shared_lock_entries"] and not res["write_in_a_visible_in_b"]
    finally:
        sup.close()
    assert subprocess.run(["pgrep", "-f", "envd_linux.session_init"], capture_output=True).stdout.split() == []
    assert str(tmp_path) not in open("/proc/self/mounts").read()


@pytest.mark.skipif(_prereqs() is not None, reason=str(_prereqs()))
def test_a_restore_that_fails_after_the_session_is_registered_leaves_nothing_behind(tmp_path, monkeypatch):
    """Regression for a leak found while measuring: the reseed step raised after the restored session was registered, destroy() could not find
    session.json, and the processes and veth outlived the supervisor."""
    from envd_linux import clone
    kit = realkit.DEFAULT_KIT
    tpl = tmp_path / "tpl"
    build_home_template(str(tpl / "home" / "drums"))
    sup = Supervisor(str(tmp_path / "state"), str(tpl), root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=kit,
                     init_python=os.path.join(kit, "venv", "bin", "python"), server_mode="module", ready_timeout=120,
                     criu_friendly=True, criu=cr.Criu(HOME), sweep_dir=str(tmp_path / "led"))
    try:
        sup.create(0)
        sup.restore_from = sup.checkpoint(0, str(tmp_path / "ck"), clean=True)
        monkeypatch.setattr(clone, "reseed", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            sup.create(1)
        time.sleep(0.5)
        assert 1 not in sup._procs
        assert subprocess.run(["pgrep", "-f", "envd_linux.session_init"], capture_output=True).stdout.split() == []
        assert not [n for n in os.listdir("/sys/class/net") if n.startswith("dvh")]
    finally:
        sup.close()
