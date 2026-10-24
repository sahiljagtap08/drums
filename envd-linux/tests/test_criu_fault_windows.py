"""Crash DURING restore, checkpoint, teardown and sweep (docs/l1-shared-runtime-design.md section 24): the registered fault windows and cleanup criteria.

Each window runs a driver whose Supervisor SIGKILLs itself at a named point; the test snapshots the host, runs the ownership sweep (twice for the windows that kill
the sweep itself), and checks P1 to P6 and the ownership checks O1 and O2. Every window's snapshot is written to $DRUMS_EVIDENCE_DIR/fault_windows.jsonl whether
it passes or not. Needs root, the private CRIU install, the real-server kit and nft."""

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
DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers", "fault_driver.py")

WINDOWS = [
    ("W1", "restore", "restore.before_overlay", None),
    ("W2", "restore", "restore.after_overlay", None),
    *[(f"W3_{ms}", "restore", f"restore.during_criu:{ms}", None) for ms in (30, 80, 150, 250)],
    *[(f"W4_{ms}", "restore", f"restore.during_criu_both:{ms}", None) for ms in (30, 80, 150, 250)],
    ("W5", "restore", "restore.after_criu", None),
    ("W6", "restore", "restore.after_register", None),
    ("W7", "restore", "restore.mid_reseed", None),
    ("W8", "checkpoint", "checkpoint.after_dump", None),
    ("W9", "checkpoint", "checkpoint.after_root_umount", None),
    ("W10", "checkpoint", "checkpoint.after_layer_bind", None),
    ("W11", "destroy", "destroy.after_kill", None),
    ("W12", "destroy", "destroy.after_root_umount", None),
    ("W13", "failed_restore", "destroy.after_kill", None),
    ("W14", "restore", "restore.after_register", "sweep.after_kill"),
    ("W15", "restore", "restore.after_register", "sweep.after_unmount"),
    ("W16", "restore", "restore.after_register", "sweep.after_links"),
]


def _prereqs():
    if not HOME or not os.path.exists(os.path.join(HOME, "x-4.2", "usr", "sbin", "criu")):
        return "set DRUMS_CRIU_HOME to the private CRIU directory"
    if os.geteuid() != 0 or not realkit.available(realkit.DEFAULT_KIT) or realkit.missing():
        return "needs root and the built real-server kit"
    for b in ("unshare", "nsenter", "Xvfb", "pivot_root", "mount", "nft", "pkill"):
        if shutil.which(b) is None:
            return f"missing {b}"


def _argvs():
    for d in os.listdir("/proc"):
        if d.isdigit() and int(d) != os.getpid():
            try:
                yield int(d), open(f"/proc/{d}/cmdline", "rb").read().split(b"\0")
            except OSError:
                pass


def _tagged(tag):
    t = tag.encode()
    return [p for p, a in _argvs() if b"--tag" in a and t in a]


def _criu_procs():
    return [p for p, a in _argvs() if a and os.path.basename(a[0].decode(errors="ignore")) == "criu"]


def _holds_mounts_of(path):
    out = []
    for p, _ in _argvs():
        try:
            if path in open(f"/proc/{p}/mountinfo").read():
                out.append(p)
        except OSError:
            pass
    return out


def _mounts(prefix):
    return sorted(ln.split()[1] for ln in open("/proc/self/mounts") if ln.split()[1].startswith(prefix) or "/.criu." in ln.split()[1])


def _ledger_mounts(ledger):
    out = []
    for f in glob.glob(os.path.join(ledger, "*.json")):
        out += json.load(open(f)).get("mounts", [])
    return out


def snapshot(tmp, state_dir, tag, table, ledger):
    return {"tagged_procs": _tagged(tag), "criu_procs": _criu_procs(), "state_mount_holders": _holds_mounts_of(state_dir),
            "mounts": _mounts(tmp), "ledger_mounts": _ledger_mounts(ledger),
            "dvh": sorted(n for n in os.listdir("/sys/class/net") if n.startswith("dvh")),
            "nft_table": subprocess.run(["nft", "list", "table", "inet", table], capture_output=True).returncode == 0,
            "x11_links": [x for x in glob.glob("/tmp/.X11-unix/X*") if os.path.islink(x) and state_dir in os.readlink(x)],
            "ledger_files": glob.glob(os.path.join(ledger, "*.json"))}


def run_sweep(ledger, env, fault=None):
    e = dict(env, **({"DRUMS_SWEEP_FAULT": fault} if fault else {}))
    r = subprocess.run([sys.executable, "-m", "envd_linux.sweep", "--ledger", ledger], capture_output=True, text=True, env=e)
    return r.returncode, (json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None)


@pytest.mark.skipif(_prereqs() is not None, reason=str(_prereqs()))
@pytest.mark.parametrize("wid,op,point,sweep_fault", WINDOWS, ids=[w[0] for w in WINDOWS])
def test_fault_window(tmp_path, wid, op, point, sweep_fault):
    # A window is only meaningful on a clean host: a leftover CRIU, session or veth from an earlier window makes every later restore fail with "-17 File exists",
    # which looks like a failure of the window under test (it did, in run 2). Refuse to run, and say so, instead of recording a misleading failure.
    left = {"criu": _criu_procs(), "session_inits": [p for p, a in _argvs() if len(a) > 2 and a[1] == b"-m" and a[2] == b"envd_linux.session_init"],
            "dvh": [n for n in os.listdir("/sys/class/net") if n.startswith("dvh")]}
    if any(left.values()):
        ev = os.environ.get("DRUMS_EVIDENCE_DIR")
        if ev:
            with open(os.path.join(ev, "fault_windows.jsonl"), "a") as f:
                f.write(json.dumps({"window": wid, "INVALID": "host not clean before the window", "left": left}) + "\n")
        pytest.fail(f"host not clean before window {wid}: {left}")
    state, ledger = str(tmp_path / "w"), str(tmp_path / "ledger")
    os.makedirs(state)
    env = dict(os.environ, PYTHONPATH=ROOT)
    drv = subprocess.Popen([sys.executable, DRIVER, state, ledger, realkit.DEFAULT_KIT, op], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           env=dict(env, DRUMS_FAULT_AT=point))
    made, seen = None, []
    decoy = decoy_mnt = None
    rec = {"window": wid, "op": op, "point": point, "sweep_fault": sweep_fault}
    try:
        deadline = time.time() + 300
        while time.time() < deadline:
            ln = drv.stdout.readline()
            seen.append(ln)
            if ln.startswith("READY "):
                made = json.loads(ln[6:])
            if ln.startswith(("SURVIVED",)) or (not ln and drv.poll() is not None):
                break
            if ln.startswith("ARMED"):
                break
        assert made, "driver never became ready: " + "".join(seen)[-1500:]
        decoy = subprocess.Popen(["/usr/bin/python3", "-c", "import time; time.sleep(600)", "envd_linux.session_init", "--tag", "drums:decoyrun"])
        decoy_mnt = str(tmp_path / "decoy_mnt")
        os.makedirs(decoy_mnt)
        subprocess.run(["mount", "-t", "tmpfs", "tmpfs", decoy_mnt], check=True)
        try:
            drv.wait(timeout=120)
        except subprocess.TimeoutExpired:
            pass
        rec["driver_returncode"] = drv.poll()
        rec["driver_died_by_sigkill"] = drv.poll() == -signal.SIGKILL
        rec["driver_tail"] = "".join(seen)[-300:] + (drv.stdout.read() if drv.poll() is not None else "")[-300:]
        time.sleep(1.0)  # let a half-finished CRIU settle or die: what is left is what the sweep faces
        pre = snapshot(str(tmp_path), made["state_dir"], made["tag"], made["table"], ledger)
        rec["before_sweep"] = pre
        unledgered = [m for m in pre["mounts"] if m not in set(pre["ledger_mounts"]) and m != decoy_mnt]  # the decoy mount is the test's own, never the run's
        rec["O1_unledgered_mounts"] = unledgered
        sweeps = []
        if sweep_fault:
            rc, rep = run_sweep(ledger, env, sweep_fault)
            sweeps.append({"fault": sweep_fault, "returncode": rc})
            rec["sweep_killed_as_planned"] = rc == -signal.SIGKILL
        rc, rep = run_sweep(ledger, env)
        sweeps.append({"returncode": rc, "report": rep})
        time.sleep(0.5)
        post = snapshot(str(tmp_path), made["state_dir"], made["tag"], made["table"], ledger)
        rc3, rep3 = run_sweep(ledger, env)
        rec["sweeps"], rec["after_sweep"], rec["third_sweep_report"] = sweeps, post, rep3
        decoy_ok = decoy.poll() is None and any(b"drums:decoyrun" in a for _, a in _argvs()) and decoy_mnt in _mounts(decoy_mnt)
        crit = {
            "P1_no_process_of_the_run": not post["tagged_procs"] and not post["state_mount_holders"] and not post["criu_procs"],
            "P2_no_mount_left": [m for m in post["mounts"] if m != decoy_mnt] == [],
            "P3_no_link_table_or_x11_link": not post["dvh"] and not post["nft_table"] and not post["x11_links"],
            "P4_ledger_removed": not post["ledger_files"],
            "P5_decoys_untouched": decoy_ok,
            "P6_third_sweep_is_a_no_op": bool(rep3) and not (rep3["killed"] or rep3["unmounted"] or rep3["links"] or rep3["tables"] or rep3["x11_links"]),
            "O1_every_mount_was_in_the_ledger": not unledgered,
        }
        rec["criteria"] = crit
        rec["PASS"] = all(v for k, v in crit.items() if not k.startswith("O1")) and crit["O1_every_mount_was_in_the_ledger"]
    finally:
        ev = os.environ.get("DRUMS_EVIDENCE_DIR")
        if ev:
            with open(os.path.join(ev, "fault_windows.jsonl"), "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        if decoy:
            decoy.kill()
        if decoy_mnt:
            subprocess.run(["umount", decoy_mnt], capture_output=True)
        if drv.poll() is None:
            drv.kill()
        drv.wait(10)
        subprocess.run([sys.executable, "-m", "envd_linux.sweep", "--ledger", ledger], capture_output=True, env=env)
    assert rec.get("PASS"), json.dumps({k: rec.get(k) for k in ("window", "criteria", "O1_unledgered_mounts")}, default=str)
