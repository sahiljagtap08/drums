"""Give a session its own network namespace plumbing: a veth pair, host side 10.200.<slot>.1/30,
session side 10.200.<slot>.2/30 with a default route via the host side.

Run as root with a Python that has pyroute2 (`pip install pyroute2`; dual GPL-2.0-or-later /
Apache-2.0). The session's namespace is created by `unshare --net`; when its last process dies
the kernel removes the veth pair, so there is no teardown step.

Policy (NAT, session-to-session firewall, input restriction) lives in firewall.py and is installed by
the Supervisor; `setup` adds the per-slot input chain before the link comes up when `firewall=True`.
Not done: DNS for sessions."""

import argparse
import json
import subprocess
import sys


def addresses(slot, base="10.200"):
    return f"{base}.{slot}.1", f"{base}.{slot}.2"


INSIDE_CODE = """
import sys
from pyroute2 import IPRoute
ifname, ip, gw = sys.argv[1:4]
ipr = IPRoute()
i = ipr.link_lookup(ifname=ifname)[0]
ipr.addr("add", index=i, address=ip, prefixlen=30)
ipr.link("set", index=i, state="up")
lo = ipr.link_lookup(ifname="lo")[0]
ipr.link("set", index=lo, state="up")
ipr.route("add", dst="default", gateway=gw)
"""


def setup(slot, init_pid, python=sys.executable, base="10.200", firewall=False, tag="", table="drums"):
    from pyroute2 import IPRoute
    gw, ip = addresses(slot, base)
    host_if, sess_if = f"dvh{slot}", f"dvs{slot}"
    if firewall:
        from . import firewall as fw
        fw.add_slot(slot, base, table)
    ipr = IPRoute()
    try:
        ipr.link("add", ifname=host_if, kind="veth", peer={"ifname": sess_if, "net_ns_pid": init_pid})
        idx = ipr.link_lookup(ifname=host_if)[0]
        if tag:
            ipr.link("set", index=idx, ifalias=tag)  # the ownership record on the host-side link
        ipr.addr("add", index=idx, address=gw, prefixlen=30)
        ipr.link("set", index=idx, state="up")
    finally:
        ipr.close()
    r = subprocess.run(["nsenter", "-t", str(init_pid), "-n", python, "-c", INSIDE_CODE, sess_if, ip, gw],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"session-side network setup failed: {r.stderr[-300:]}")
    return {"ip": ip, "gw": gw, "host_if": host_if}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, required=True)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--base", default="10.200")
    ap.add_argument("--firewall", action="store_true")
    ap.add_argument("--tag", default="")
    ap.add_argument("--table", default="drums")
    a = ap.parse_args(argv)
    print(json.dumps(setup(a.slot, a.pid, base=a.base, firewall=a.firewall, tag=a.tag, table=a.table)))


if __name__ == "__main__":
    main()
