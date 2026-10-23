"""EXPERIMENT (read as a probe, not a feature): can systemd run as PID 1 inside our session namespace?

Needs no OSWorld image: the session's root is this host's Ubuntu (copy-on-write over `/`), which has
systemd. `Supervisor(exec_systemd=True)` makes the session's PID 1 `exec` /lib/systemd/systemd after the
root setup, instead of starting Xvfb. This script creates one such session, waits, and records what it can
observe from outside with `nsenter`: whether systemd is still PID 1, `systemctl is-system-running`, failed
units, the cgroup view, and the tail of systemd's console output. It prints one JSON object and always
tears the session down. It makes no claim beyond what it prints.

WARNING. The one run that was made WITHOUT a network namespace had host side effects: systemd started the host
rootfs's enabled units, so docker.service created `docker0`, Docker's netfilter tables and set ip_forward=1 in the
host network namespace, and cgroups were written into the host hierarchy (docs/gnome-namespace-investigation.md).
The Supervisor now refuses `exec_systemd` unless `netns=True` and DRUMS_ALLOW_SYSTEMD_PROBE=1. Even then the cgroup
writes are not contained. Do not run this on a host you care about.

    DRUMS_ALLOW_SYSTEMD_PROBE=1 python -m envd_linux.systemd_probe [--wait 12]"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time


def nsx(pid, *cmd, timeout=20):
    r = subprocess.run(["nsenter", "-t", str(pid), "-m", "-p", "-u", "-i", "--", *cmd], capture_output=True,
                       text=True, timeout=timeout)
    return {"rc": r.returncode, "out": (r.stdout + r.stderr).strip()[-1500:]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=12)
    a = ap.parse_args(argv)
    from .session import Supervisor
    from .template import build_home_template
    tmp = tempfile.mkdtemp()
    tpl = os.path.join(tmp, "tpl")
    os.makedirs(os.path.join(tpl, "home", "drums"))
    sup = Supervisor(os.path.join(tmp, "state"), tpl, root_mode="full", netns=True, osworld_port=0,
                     exec_systemd=True)
    out = {"host": {"cgroup": open("/proc/self/mounts").read().count("cgroup2 ") and "v2-visible" or "v1-or-hybrid",
                    "systemd": subprocess.run(["systemd", "--version"], capture_output=True, text=True).stdout.split("\n")[0]}}
    try:
        info = sup.create(0)
        pid = info["init_pid"]
        time.sleep(a.wait)
        out["pid1_comm"] = nsx(pid, "cat", "/proc/1/comm")["out"]
        out["pid1_cmdline"] = nsx(pid, "sh", "-c", "tr '\\0' ' ' < /proc/1/cmdline")["out"]
        out["is_system_running"] = nsx(pid, "systemctl", "is-system-running")
        out["failed_units"] = nsx(pid, "systemctl", "list-units", "--state=failed", "--no-legend", "--plain")["out"].splitlines()[:15]
        out["active_units"] = len(nsx(pid, "systemctl", "list-units", "--no-legend", "--plain")["out"].splitlines())
        out["detect_virt"] = nsx(pid, "systemd-detect-virt", "--container")["out"]
        out["proc1_cgroup"] = nsx(pid, "cat", "/proc/1/cgroup")["out"].splitlines()[:6]
        out["cgroup_mounts"] = nsx(pid, "sh", "-c", "grep cgroup /proc/self/mounts | head -8")["out"].splitlines()
        out["init_log_tail"] = open(os.path.join(tmp, "state", "s0", "init.log")).read()[-1800:].splitlines()[-18:]
    except Exception as e:  # report, do not hide
        out["error"] = f"{type(e).__name__}: {e}"
        try:
            out["init_log_tail"] = open(os.path.join(tmp, "state", "s0", "init.log")).read()[-1800:].splitlines()[-18:]
        except OSError:
            pass
    finally:
        sup.close()
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    sys.exit(main())
