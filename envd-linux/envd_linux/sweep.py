"""Outer cleanup: an idempotent sweep of what a CRASHED Drums Supervisor run left on a host.

A Supervisor's `close()` is the INNER teardown. If the process is killed (SIGKILL, OOM, a test timeout)
nothing runs, and the host keeps session processes, mounts, host-side veth links, an nftables table, X11
socket symlinks and, with `nat`, `net.ipv4.ip_forward=1`.

OWNERSHIP, NOT NAMES. The sweep deletes ONLY resources that carry an ownership record written by the run
that created them. It never deletes by name prefix alone, because on a shared host another process may
legitimately own something that looks similar (`dvh0`, a table called `drums`, a mount under the same path).
Every run has a unique tag `drums:<run id>` and writes a ledger entry
`<ledger_dir>/sup-<pid>-<n>.json` (default `/run/drums`; `DRUMS_SWEEP_DIR` or the `ledger_dir` argument
override it) BEFORE it changes anything, and appends to it as it creates things:

  resource            ownership record                                   how the sweep matches it
  session processes   `--tag drums:<run id>` on the session_init cmdline  cmdline carries the tag of a dead entry
  host veth link      interface alias `drums:<run id>` set at creation    alias equals the tag AND name in ledger
  nftables table      its name `drums_<run id>`, recorded in the ledger   exactly that table, nothing else
  mounts              mount points recorded in the ledger                 exactly those mount points
  X11 socket symlink  paths recorded in the ledger                        recorded path that is still a symlink
  ip_forward          prior value recorded BEFORE the run changed it      restored to that value, and only if
                                                                          the current value is still the one the
                                                                          run set (a foreign change is left alone)
  other sysfs/proc    {path, prior, set_to} recorded BEFORE the write     same rule as ip_forward (KSM knobs today)

A resource without a record (a process with no tag, a link with no alias, a table with another name, an
unlisted mount, a leaked resource from a run that never wrote a ledger entry) is NOT touched. That is the
price of safety: such a leak must be removed by hand.

CONTRACT of `sweep(...)`: idempotent and safe at any time; a no-op on a clean host; a LIVE Supervisor (its
ledger entry names a running pid with the same start time) is never touched unless `force=True`; each dead
entry is removed after its resources are; returns a report of what it removed.

NOT covered: files left in a state dir, cgroups (none are created). The instance-termination layer on a
cloud host is separate and is NOT implemented here.

Standalone: `python -m envd_linux.sweep [--ledger DIR] [--force]` prints the report as JSON."""

import argparse
import glob
import itertools
import json
import os
import signal
import threading
import subprocess
import sys
import time

_COUNTER = itertools.count()
_LOCK = threading.Lock()  # record() is read-modify-write on a file shared by all session-creating threads
DEFAULT_LEDGER = "/run/drums"
FORWARD = "/proc/sys/net/ipv4/ip_forward"
EMPTY = {"killed": [], "links": [], "unmounted": [], "tables": [], "ip_forward_restored": None,
         "entries_removed": [], "live_supervisors": [], "x11_links": [], "sysfs_restored": []}


def ledger_dir(path=None):
    return path or os.environ.get("DRUMS_SWEEP_DIR") or DEFAULT_LEDGER


def _starttime(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def _alive(entry):
    st = _starttime(entry.get("pid"))
    return st is not None and st == entry.get("starttime")


def read_forward():
    with open(FORWARD) as f:
        return f.read().strip()


def write_forward(v):
    with open(FORWARD, "w") as f:
        f.write(str(v))


def _save(path, entry):
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as f:
        json.dump({k: v for k, v in entry.items() if not k.startswith("_")}, f)
    os.replace(tmp, path)


def register(state_dir, run_id, ledger=None):
    """Called by a Supervisor before it changes anything. Returns the entry path."""
    d = ledger_dir(ledger)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"sup-{os.getpid()}-{next(_COUNTER)}.json")
    _save(path, {"pid": os.getpid(), "starttime": _starttime(os.getpid()), "run_id": run_id,
                 "tag": f"drums:{run_id}", "state_dir": os.path.abspath(state_dir), "t": time.time(),
                 "mounts": [], "x11_links": [], "tables": [], "links": [], "forward": None, "sysfs": []})
    return path


def record(path, key, value):
    """Append `value` to a list field of the ledger entry (or set a scalar field)."""
    with _LOCK:
        with open(path) as f:
            e = json.load(f)
        if isinstance(e.get(key), list):
            if value not in e[key]:
                e[key].append(value)
        else:
            e[key] = value
        _save(path, e)


def set_sysfs(ledger_path, path, value):
    """Write a sysfs/proc knob, recording {path, prior, set_to} in the ledger FIRST so a crash can be undone. Returns the prior."""
    with open(path) as f:
        prior = f.read().strip()
    record(ledger_path, "sysfs", {"path": path, "prior": prior, "set_to": str(value)})
    with open(path, "w") as f:
        f.write(str(value))
    return prior


def restore_sysfs(entry_sysfs, others_live=()):
    """Undo recorded writes in reverse order, only where the knob still has the value we set. Returns the restored paths."""
    done = []
    for rec in reversed(entry_sysfs):
        try:
            with open(rec["path"]) as f:
                cur = f.read().strip()
            if cur == rec["set_to"] and rec["prior"] != cur and rec["path"] not in others_live:
                with open(rec["path"], "w") as f:
                    f.write(rec["prior"])
                done.append(rec["path"])
        except OSError:
            pass
    return done


def unregister(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _entries(d):
    out = []
    for p in sorted(glob.glob(os.path.join(d, "sup-*.json"))):
        try:
            with open(p) as f:
                e = json.load(f)
            e["_path"] = p
            out.append(e)
        except (OSError, ValueError):
            pass  # unreadable: left in place, never guessed at
    return out


def _tagged_procs(tag):
    out = []
    me = os.getpid()
    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            argv = open(p, "rb").read().split(b"\0")
        except OSError:
            continue
        argv = [a.decode(errors="replace") for a in argv]
        # session_init processes, and the L1-F prototype's zygote plus every session it forked (they share its argv)
        if ("envd_linux.session_init" in argv or "envd_linux.l1proto.zygote" in argv) and "--tag" in argv \
                and argv[argv.index("--tag") + 1] == tag:
            pid = int(p.split("/")[2])
            if pid != me:
                out.append(pid)
    return out


def _zombie_or_gone(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return True


def _kill_tagged(tag):
    killed = _tagged_procs(tag)
    for pid in killed:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.time() + 5
    while time.time() < deadline and not all(_zombie_or_gone(p) for p in killed):
        time.sleep(0.05)
    return killed


def _unmount(points):
    done = []
    with open("/proc/self/mounts") as f:
        mounted = {ln.split()[1].replace("\\040", " ") for ln in f}
    for mp in sorted(points, key=len, reverse=True):
        if mp not in mounted:
            continue
        for _ in range(10):
            if subprocess.run(["umount", mp], capture_output=True).returncode == 0:
                break
            time.sleep(0.1)
        else:
            subprocess.run(["umount", "-l", mp], capture_output=True)
        done.append(mp)
    return done


LINKS_CODE = """
import json, sys
from pyroute2 import IPRoute
tag, names = sys.argv[1], set(sys.argv[2].split(','))
ipr = IPRoute(); gone = []
for l in ipr.get_links():
    n = l.get_attr('IFLA_IFNAME')
    if n in names and l.get_attr('IFLA_IFALIAS') == tag:
        ipr.link('del', index=l['index']); gone.append(n)
print(json.dumps(gone))
"""


def _delete_links(tag, names):
    """Only links whose alias is exactly our tag AND whose name the ledger lists."""
    if not names:
        return []
    r = subprocess.run(["/usr/bin/python3", "-c", LINKS_CODE, tag, ",".join(names)], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []


def _delete_table(name):
    if subprocess.run(["nft", "list", "table", "inet", name], capture_output=True).returncode != 0:
        return False
    subprocess.run(["nft", "delete", "table", "inet", name], capture_output=True)
    return True


def sweep(ledger=None, force=False):
    d = ledger_dir(ledger)
    rep = {k: (list(v) if isinstance(v, list) else v) for k, v in EMPTY.items()}
    entries = _entries(d) if os.path.isdir(d) else []
    live = [e for e in entries if _alive(e) and not force]
    dead = [e for e in entries if e not in live]
    rep["live_supervisors"] = [e["pid"] for e in live]
    live_forward_setter = any(e.get("forward") for e in live)

    for e in sorted(dead, key=lambda e: e.get("t", 0)):
        rep["killed"] += _kill_tagged(e["tag"])
        rep["unmounted"] += _unmount(e.get("mounts", []))
        rep["links"] += _delete_links(e["tag"], e.get("links", []))
        for t in e.get("tables", []):
            if _delete_table(t):
                rep["tables"].append(t)
        for x in e.get("x11_links", []):
            if os.path.islink(x):
                os.unlink(x)
                rep["x11_links"].append(x)
        fw = e.get("forward")
        if fw and not live_forward_setter and read_forward() == fw["set_to"] and rep["ip_forward_restored"] is None:
            if fw["prior"] != fw["set_to"]:
                write_forward(fw["prior"])
                rep["ip_forward_restored"] = fw["prior"]
        live_paths = {r["path"] for le in live for r in le.get("sysfs", [])}
        rep["sysfs_restored"] += restore_sysfs(e.get("sysfs", []), live_paths)
        unregister(e["_path"])
        rep["entries_removed"].append(e["pid"])
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--force", action="store_true", help="also remove state of LIVE supervisors")
    a = ap.parse_args(argv)
    print(json.dumps(sweep(a.ledger, a.force)))


if __name__ == "__main__":
    sys.exit(main())
