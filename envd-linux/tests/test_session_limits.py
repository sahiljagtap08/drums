"""READINESS (not a result): per-session cgroup limits that match the incumbent's shipped VM shape (2 vCPU, 4 GiB).

Unit part: the v2 and v1 backends write the right files in the right form, against fake hierarchies. The kernel is not
involved, so this proves the form of the writes, not enforcement. Real part: on this host's real cgroup hierarchy a real
session joins its cgroup before forking, every process in it is a member, the kernel reports the requested limits, and the
cgroup is removed afterwards. The development host is v1/hybrid, so the real part exercises the v1 backend; the v2 backend
has never run against a real v2 kernel here."""

import json
import os
import shutil
import subprocess

import pytest

from envd_linux import limits as L
from envd_linux.session import Supervisor
from envd_linux.template import build_home_template


def _fake_v2(tmp_path):
    root = tmp_path / "cg2"
    root.mkdir()
    (root / "cgroup.controllers").write_text("cpuset cpu io memory pids")
    (root / "cgroup.subtree_control").write_text("")
    return str(root)


def _fake_v1(tmp_path):
    root = tmp_path / "cg1"
    for c in ("cpu", "memory", "pids"):
        (root / c).mkdir(parents=True)
    return str(root)


def test_incumbent_shape_is_two_vcpu_four_gib():
    assert L.INCUMBENT_SHAPE.cpus == 2.0 and L.INCUMBENT_SHAPE.memory_bytes == 4 * 2**30


def test_v2_backend_writes_cpu_memory_swap_pids(tmp_path):
    root = _fake_v2(tmp_path)
    assert L.backend(root) == "v2"
    cg = L.SessionCgroup("run1", 3, root=root)
    cg.create()
    d = cg.dirs[0]
    assert d.endswith("drums/run1/s3")
    assert open(f"{d}/cpu.max").read() == "200000 100000"
    assert open(f"{d}/memory.max").read() == str(4 * 2**30)
    assert open(f"{d}/memory.swap.max").read() == "0"
    assert open(f"{d}/pids.max").read() == "4096"
    assert cg.procs_files() == [f"{d}/cgroup.procs"]
    assert cg.record()["backend"] == "v2" and cg.record()["cpus"] == 2.0


def test_v1_backend_writes_each_controller(tmp_path):
    root = _fake_v1(tmp_path)
    assert L.backend(root) == "v1"
    cg = L.SessionCgroup("run1", 0, L.Limits(cpus=1.5, memory_bytes=2**30), root=root)
    cg.create()
    cpu, mem, pids = cg.dirs
    assert open(f"{cpu}/cpu.cfs_quota_us").read() == "150000" and open(f"{cpu}/cpu.cfs_period_us").read() == "100000"
    assert open(f"{mem}/memory.limit_in_bytes").read() == str(2**30)
    assert open(f"{pids}/pids.max").read() == "4096"
    assert len(cg.procs_files()) == 3


def test_no_hierarchy_is_an_error(tmp_path):
    assert L.backend(str(tmp_path)) is None
    with pytest.raises(OSError):
        L.SessionCgroup("r", 0, root=str(tmp_path))


def test_remove_deletes_session_and_run_dirs(tmp_path):
    root = _fake_v2(tmp_path)
    cg = L.SessionCgroup("run1", 0, root=root)
    cg.create()
    for f in os.listdir(cg.dirs[0]):  # a real cgroup dir holds only kernel pseudo-files; empty the fake
        os.unlink(os.path.join(cg.dirs[0], f))
    assert cg.remove() == cg.dirs
    assert not os.path.exists(os.path.join(root, "drums"))


def _prereqs():
    if os.geteuid() != 0:
        return "needs root"
    if L.backend() is None:
        return "no usable cgroup hierarchy"
    for b in ("unshare", "nsenter", "Xvfb", "pivot_root", "mount"):
        if shutil.which(b) is None:
            return f"missing {b}"
    return None


@pytest.mark.skipif(_prereqs() is not None, reason=str(_prereqs()))
def test_real_session_joins_its_cgroup_and_limits_read_back(tmp_path):
    tpl = tmp_path / "tpl"
    build_home_template(str(tpl / "home" / "drums"))
    sup = Supervisor(str(tmp_path / "state"), str(tpl), root_mode="full", netns=False, sweep_dir=str(tmp_path / "led"),
                     limits=L.Limits(cpus=2.0, memory_bytes=4 * 2**30))
    try:
        info = sup.create(0)
        rec = info["limits"]
        assert rec["enforced_on_this_host"] and rec["cpus"] == 2.0 and rec["memory_bytes"] == 4 * 2**30
        cg = sup._cgroups[0]
        back = L.read_back(cg)
        if cg.kind == "v1":
            assert back["cpu.cfs_quota_us"] == "200000"
            assert int(back["memory.limit_in_bytes"]) == 4 * 2**30
        else:
            assert back["cpu.max"] == "200000 100000"
            assert back["memory.max"] == str(4 * 2**30)
        # every process of the session (init, Xvfb, and whatever they forked) is in the cgroup
        members = set()
        for f in cg.procs_files():
            members |= {int(x) for x in open(f).read().split()}
        assert info["init_pid"] in members
        xvfb = subprocess.run(["pgrep", "-f", f"Xvfb :{info['display']}"], capture_output=True, text=True).stdout.split()
        assert xvfb and all(int(p) in members for p in xvfb)
        # the ledger knows the directories, so a crash would not leak them
        led = [json.load(open(os.path.join(tmp_path / "led", n))) for n in os.listdir(tmp_path / "led")]
        assert set(cg.dirs) <= {c for e in led for c in e["cgroups"]}
    finally:
        dirs = list(sup._cgroups[0].dirs) if 0 in sup._cgroups else []
        sup.close()
    assert not any(os.path.exists(d) for d in dirs)
