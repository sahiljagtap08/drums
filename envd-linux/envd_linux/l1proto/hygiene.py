"""State-hygiene checks for forked sessions (L1-F prototype, measurement d). Needs root, the kit, Chromium, nft.

    python -m envd_linux.l1proto.hygiene --out hygiene.json

Starts a zygote (numpy preloaded on purpose, as a stand-in for any preloaded library with global state), forks two
sessions, and checks: distinct PID namespaces and disjoint pid sets; each session has its own Xvfb and sees only its own X
socket; listening sockets live in separate network namespaces; no zygote file descriptor leaked into a session; and what a
fresh fork inherits (random, urandom, uuid4, numpy global RNG)."""

import argparse
import glob
import json
import os
import subprocess
import tempfile
import time

from ..session import Supervisor
from ..template import build_home_template
from ..x11 import pidns_members
from .zygote import ZygoteClient

KIT = "/opt/drums-osworld-real"


def nsx(pid, *cmd):
    r = subprocess.run(["nsenter", "-t", str(pid), "-m", "-p", "-n", "-u", "-i", "--", *cmd], capture_output=True, text=True)
    return r.stdout


def listening(pid):
    out = []
    for ln in nsx(pid, "cat", "/proc/net/tcp").splitlines()[1:]:
        f = ln.split()
        if f[3] == "0A":
            out.append(int(f[1].split(":")[1], 16))
    return sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    tmp = tempfile.mkdtemp(prefix="l1hyg")
    build_home_template(os.path.join(tmp, "tpl", "home", "drums"))
    py = os.path.join(KIT, "venv", "bin", "python")
    sup = Supervisor(os.path.join(tmp, "state"), os.path.join(tmp, "tpl"), root_mode="full", netns=True, osworld_port=5000,
                     osworld_real_kit=KIT, init_python=py, server_mode="fork", workload="chromium", ready_timeout=120)
    sup.t_request = {}
    os.environ["DRUMS_ZYGOTE_EXTRA"] = "numpy"
    zyg = ZygoteClient(KIT, os.path.join(tmp, "z.sock"), sup.tag)
    sup.zygote = zyg
    res = {"zygote_preloaded": zyg.hello["preloaded"], "zygote_failed": zyg.hello["failed"]}
    try:
        infos = [sup.create(0), sup.create(1)]
        time.sleep(2)
        pids = [i["init_pid"] for i in infos]
        res["pid_ns"] = [os.readlink(f"/proc/{p}/ns/pid") for p in pids]
        res["pid_ns_distinct"] = res["pid_ns"][0] != res["pid_ns"][1]
        members = [set(pidns_members(p)) for p in pids]
        res["pid_sets_disjoint"] = not (members[0] & members[1])
        res["net_ns_distinct"] = os.readlink(f"/proc/{pids[0]}/ns/net") != os.readlink(f"/proc/{pids[1]}/ns/net")
        res["mnt_ns_distinct"] = os.readlink(f"/proc/{pids[0]}/ns/mnt") != os.readlink(f"/proc/{pids[1]}/ns/mnt")
        res["xvfb_per_session"] = [sum(1 for m in mm if open(f"/proc/{m}/cmdline", "rb").read().startswith(b"Xvfb")) for mm in members]
        res["x_sockets_visible_in_each_session"] = [nsx(p, "ls", "/tmp/.X11-unix").split() for p in pids]
        res["listening_ports_per_session"] = [listening(p) for p in pids]
        leaked = []
        for p in pids:
            for fd in glob.glob(f"/proc/{p}/fd/*"):
                try:
                    if "z.sock" in os.readlink(fd) or "zygote" in os.readlink(fd):
                        leaked.append((p, os.readlink(fd)))
                except OSError:
                    pass
        res["zygote_fds_leaked_into_sessions"] = leaked
        res["fork_probe_siblings"] = [zyg.probe(), zyg.probe()]
        a0, a1 = res["fork_probe_siblings"]
        res["probe_distinct"] = {k: a0[k] != a1[k] for k in ("pid", "random", "urandom", "uuid4", "numpy_global")}
    finally:
        zyg.close()
        sup.close()
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
