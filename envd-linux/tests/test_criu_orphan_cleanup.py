"""PROTOTYPE of mechanism C: what happens when the Supervisor dies with restored sessions live (section 16.2 of the L1 design doc).

criu_friendly sessions cleared `unshare --kill-child`, so the kernel no longer kills them with their Supervisor. This test kills a Supervisor with SIGKILL
while a cold friendly session's checkpoint and two restored siblings exist, confirms the sessions SURVIVE (the regression is real), runs the ownership sweep,
and checks it reaps everything the run owns and nothing it does not. Needs root, the private CRIU install, the real-server kit, nft."""

import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

from envd_linux import realkit

HOME = os.environ.get("DRUMS_CRIU_HOME")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers", "orphan_driver.py")


def _prereqs():
    if not HOME or not os.path.exists(os.path.join(HOME, "x-4.2", "usr", "sbin", "criu")):
        return "set DRUMS_CRIU_HOME to the private CRIU directory"
    if os.geteuid() != 0 or not realkit.available(realkit.DEFAULT_KIT) or realkit.missing():
        return "needs root and the built real-server kit"
    for b in ("unshare", "nsenter", "Xvfb", "pivot_root", "mount", "nft"):
        if shutil.which(b) is None:
            return f"missing {b}"


def _procs_with(text):
    out = []
    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            if text in open(p).read().replace("\0", " ") and int(p.split("/")[2]) != os.getpid():
                out.append(int(p.split("/")[2]))
        except OSError:
            pass
    return out


def _mounts_under(path):
    return [ln.split()[1] for ln in open("/proc/self/mounts") if path in ln.split()[1]]


@pytest.mark.skipif(_prereqs() is not None, reason=str(_prereqs()))
def test_sweep_reaps_restored_sessions_orphaned_by_a_killed_supervisor_and_spares_a_decoy(tmp_path):
    state, ledger = str(tmp_path / "w"), str(tmp_path / "ledger")
    os.makedirs(state)
    env = dict(os.environ, PYTHONPATH=ROOT)
    drv = subprocess.Popen([sys.executable, DRIVER, state, ledger, realkit.DEFAULT_KIT], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    made, seen = None, []
    deadline = time.time() + 240
    while time.time() < deadline:
        ln = drv.stdout.readline()
        seen.append(ln)
        if ln.startswith("READY "):
            made = json.loads(ln[6:])
            break
        if not ln and drv.poll() is not None:
            break
    assert made, "driver did not get ready: " + "".join(seen)[-1500:]
    tag = made["tag"]
    # decoys that are not ours: a process carrying another run's tag, and a tmpfs at an unrecorded path
    decoy = subprocess.Popen(["/usr/bin/python3", "-c", "import time; time.sleep(600)", "envd_linux.session_init", "--tag", "drums:decoyrun"])
    decoy_mnt = str(tmp_path / "decoy_mnt")
    os.makedirs(decoy_mnt)
    subprocess.run(["mount", "-t", "tmpfs", "tmpfs", decoy_mnt], check=True)
    try:
        before = {"procs": _procs_with(f"--tag {tag}"), "mounts": _mounts_under(state), "links": sorted(os.listdir("/sys/class/net"))}
        assert set(made["init_pids"]) <= set(before["procs"]) and len(made["init_pids"]) == 3 and before["mounts"], before  # two restored siblings and one cold friendly session (whose unshare wrapper carries the tag too)
        os.kill(made["pid"], signal.SIGKILL)  # the supervisor dies; nothing in it gets to clean up
        drv.wait(10)
        time.sleep(1)
        survivors = _procs_with(f"--tag {tag}")
        assert survivors, "the sessions did not survive the supervisor: the regression this test is about was not reproduced"
        after_kill = {"surviving_processes_with_tag": len(survivors), "veth_links": sorted(n for n in os.listdir("/sys/class/net") if n.startswith("dvh")),
                      "mounts_under_state_dir": len(_mounts_under(state))}
        assert any(n.startswith("dvh") for n in os.listdir("/sys/class/net")), "no veth left to clean"
        r = subprocess.run([sys.executable, "-m", "envd_linux.sweep", "--ledger", ledger], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        rep = json.loads(r.stdout)
        time.sleep(0.5)
        assert _procs_with(f"--tag {tag}") == [], rep
        assert _mounts_under(state) == [], (_mounts_under(state), rep)
        assert not [n for n in os.listdir("/sys/class/net") if n.startswith("dvh")], rep
        assert subprocess.run(["nft", "list", "table", "inet", made["table"]], capture_output=True).returncode != 0
        assert not [x for x in glob.glob("/tmp/.X11-unix/X*") if os.path.islink(x) and state in os.readlink(x)]
        assert glob.glob(os.path.join(ledger, "*.json")) == []
        # the decoys were not touched
        assert decoy.poll() is None and _procs_with("--tag drums:decoyrun"), "the sweep killed a process that is not ours"
        assert decoy_mnt in _mounts_under(decoy_mnt), "the sweep unmounted a mount that is not ours"
        assert rep["killed"] and rep["unmounted"], rep
        ev = os.environ.get("DRUMS_EVIDENCE_DIR")
        if ev:
            json.dump({"before_kill": {"processes_with_tag": len(before["procs"]), "mounts_under_state_dir": len(before["mounts"])},
                       "after_supervisor_sigkill": after_kill, "sweep_report": rep,
                       "after_sweep": {"processes_with_tag": len(_procs_with(f"--tag {tag}")), "mounts_under_state_dir": len(_mounts_under(state)),
                                       "dvh_links": [n for n in os.listdir("/sys/class/net") if n.startswith("dvh")],
                                       "nft_table_present": subprocess.run(["nft", "list", "table", "inet", made["table"]], capture_output=True).returncode == 0,
                                       "ledger_files": len(glob.glob(os.path.join(ledger, "*.json")))},
                       "decoys": {"process_alive": decoy.poll() is None, "mount_present": decoy_mnt in _mounts_under(decoy_mnt)}},
                      open(os.path.join(ev, "orphan_cleanup.json"), "w"), indent=1)
    finally:
        decoy.kill()
        subprocess.run(["umount", decoy_mnt], capture_output=True)
        if drv.poll() is None:
            drv.kill()
        drv.wait(10)  # a dead-but-unreaped supervisor still looks alive to the sweep
        subprocess.run([sys.executable, "-m", "envd_linux.sweep", "--ledger", ledger], capture_output=True, env=env)
