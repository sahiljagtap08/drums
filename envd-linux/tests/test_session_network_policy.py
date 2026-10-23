"""INTEGRATION: per-session NAT and the inter-session firewall, in real sessions (L0 precursor).

An "uplink" network namespace stands in for the outside world, so the test is deterministic and does
not depend on this sandbox's internet: a listener at 198.51.100.2 (TEST-NET-2, not a private range)
replies with the client address it saw. If NAT works, a session's connection arrives from the host's
uplink address 198.51.100.1, never from its own 10.200.N.2. A second listener at 192.168.77.2 stands
for a private-range target, which policy must refuse. Session-to-session and session-to-host-address
are checked with host forwarding forced ON.

Needs: root, nft, CAP_NET_ADMIN on the host network namespace, pyroute2 in system python. Not covered:
DNS inside sessions, IPv6, the real internet (one informational test tries 1.1.1.1:443 and skips if
the host itself cannot)."""

import os
import shutil
import socket
import subprocess
import textwrap
import threading
import time

import pytest

from envd_linux.scalecua.client import GuestClient
from envd_linux.scalecua.controller import DrumsEnvironmentController, DrumsEnvironmentDelegation
from envd_linux.session import Supervisor
from envd_linux.template import build_home_template


def _prereqs():
    if os.geteuid() != 0:
        return "needs root"
    for b in ("unshare", "nsenter", "Xvfb", "pivot_root", "mount", "nft"):
        if shutil.which(b) is None:
            return f"missing {b}"
    if "overlay" not in open("/proc/filesystems").read():
        return "no overlayfs"
    if subprocess.run(["/usr/bin/python3", "-c", "import Xlib, pyroute2"], capture_output=True).returncode:
        return "system python3 needs python-xlib and pyroute2"
    if subprocess.run(["nft", "list", "ruleset"], capture_output=True).returncode:
        return "nft is not permitted here (needs CAP_NET_ADMIN and nf_tables)"


pytestmark = pytest.mark.skipif(_prereqs() is not None, reason=_prereqs() or "")

SERVER = textwrap.dedent('''
    import socket, sys
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((sys.argv[1], int(sys.argv[2]))); s.listen(8)
    while True:
        c, a = s.accept(); c.sendall(("seen " + a[0]).encode()); c.close()
''')
CLIENT = ("import socket,sys\n"
          "s=socket.socket(); s.settimeout(3)\n"
          "try:\n s.connect((sys.argv[1],int(sys.argv[2]))); s.sendall(b'GET /platform HTTP/1.0\\r\\n\\r\\n'); print(s.recv(300).decode().replace(chr(10),' '))\n"
          "except Exception as e: print('FAIL', type(e).__name__)\n")
UPLINK = textwrap.dedent('''
    import sys
    from pyroute2 import IPRoute
    pid = int(sys.argv[1]); ipr = IPRoute()
    ipr.link("add", ifname="extv0", kind="veth", peer={"ifname": "extv1", "net_ns_pid": pid})
    i = ipr.link_lookup(ifname="extv0")[0]
    ipr.addr("add", index=i, address="198.51.100.1", prefixlen=24); ipr.link("set", index=i, state="up")
''')
UPLINK_INSIDE = textwrap.dedent('''
    from pyroute2 import IPRoute
    ipr = IPRoute()
    ipr.link("set", index=ipr.link_lookup(ifname="lo")[0], state="up")
    i = ipr.link_lookup(ifname="extv1")[0]
    ipr.addr("add", index=i, address="198.51.100.2", prefixlen=24)
    ipr.addr("add", index=i, address="192.168.77.2", prefixlen=24)
    ipr.link("set", index=i, state="up")
    ipr.route("add", dst="default", gateway="198.51.100.1")
''')


class Net:
    pass


def forward():
    return open("/proc/sys/net/ipv4/ip_forward").read().strip()


def set_forward(v):
    with open("/proc/sys/net/ipv4/ip_forward", "w") as f:
        f.write(v)


@pytest.fixture(scope="module")
def uplink():
    n = Net()
    n.holder = subprocess.Popen(["unshare", "--net", "sleep", "300"])
    time.sleep(0.3)
    kids = subprocess.run(["pgrep", "-P", str(n.holder.pid)], capture_output=True, text=True).stdout.split()
    n.pid = int(kids[0]) if kids else n.holder.pid
    subprocess.run(["/usr/bin/python3", "-c", UPLINK, str(n.pid)], check=True)
    subprocess.run(["nsenter", "-t", str(n.pid), "-n", "/usr/bin/python3", "-c", UPLINK_INSIDE], check=True)
    n.servers = [subprocess.Popen(["nsenter", "-t", str(n.pid), "-n", "/usr/bin/python3", "-c", SERVER, ip, "8443"])
                 for ip in ("198.51.100.2", "192.168.77.2")]
    time.sleep(0.5)
    yield n
    for p in n.servers:
        p.kill()
    n.holder.kill()
    n.holder.wait()


def make_stack(tmp_path, nat, firewall=None):
    n = Net()
    tpl = tmp_path / "tpl"
    build_home_template(str(tpl / "home" / "drums"))
    n.saved = forward()
    n.sup = Supervisor(str(tmp_path / "state"), str(tpl), root_mode="full", netns=True, osworld_port=5000, nat=nat, firewall=firewall)
    n.ctl = DrumsEnvironmentController(DrumsEnvironmentDelegation(), n.sup, 2, health_timeout=30, acquire_timeout=60)
    n.ctl.run_loop_in_thread()
    return n


def close_stack(n):
    n.ctl.shutdown()
    n.sup.close()
    set_forward(n.saved)


def start(n):
    sid, _, addr = n.ctl.sync_start_session("ubuntu")
    return sid, n.ctl.session_info(sid)["ubuntu"], GuestClient(addr["ubuntu"])


def tcp(client, host, port):
    return client.execute(["/usr/bin/python3", "-c", CLIENT, host, str(port)])["output"].strip()


@pytest.fixture
def natstack(tmp_path, uplink):
    n = make_stack(tmp_path, nat=True)
    yield n
    close_stack(n)


def test_nat_gives_a_session_outbound_and_the_peer_sees_the_masqueraded_address(natstack, uplink):
    assert forward() == "1"  # egress turned it on (and close restores it)
    sid, info, c = start(natstack)
    try:
        out = tcp(c, "198.51.100.2", 8443)
        assert out == "seen 198.51.100.1", out  # not 10.200.N.2: masqueraded
    finally:
        natstack.ctl.sync_end_session(sid)


def test_private_ranges_are_refused_even_with_nat(natstack, uplink):
    sid, info, c = start(natstack)
    try:
        assert tcp(c, "192.168.77.2", 8443).startswith("FAIL")
        assert tcp(c, "169.254.169.254", 80).startswith("FAIL")  # the cloud metadata address
    finally:
        natstack.ctl.sync_end_session(sid)


def test_session_to_session_is_refused_with_host_forwarding_on(natstack):
    assert forward() == "1"
    sa, ia, ca = start(natstack)
    sb, ib, cb = start(natstack)
    try:
        assert tcp(ca, ib["ip"], 5000).startswith("FAIL"), "A reached B"
        assert tcp(cb, ia["ip"], 5000).startswith("FAIL"), "B reached A"
        # a session can still reach its own guest port through its own gateway path (sanity: the
        # guest answers its host-side health probe, which is how the pool saw it healthy)
        assert GuestClient(ib["ip"]).platform() == "Linux"
    finally:
        natstack.ctl.sync_end_session(sa)
        natstack.ctl.sync_end_session(sb)


def test_session_can_reach_only_its_own_gateway_on_the_host(natstack, uplink):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 0))
    srv.listen(8)
    port = srv.getsockname()[1]

    def serve():
        while True:
            try:
                c, a = srv.accept()
            except OSError:
                return
            c.sendall(b"seen " + a[0].encode())
            c.close()
    threading.Thread(target=serve, daemon=True).start()
    sa, ia, ca = start(natstack)
    sb, ib, cb = start(natstack)
    try:
        assert tcp(ca, ia["gw"], port) == f"seen {ia['ip']}"
        assert tcp(ca, ib["gw"], port).startswith("FAIL"), "A used B's gateway address on the host"
        assert tcp(ca, "198.51.100.1", port).startswith("FAIL"), "A used another host address"
    finally:
        srv.close()
        natstack.ctl.sync_end_session(sa)
        natstack.ctl.sync_end_session(sb)


def test_firewall_holds_without_nat_when_forwarding_is_forced_on(tmp_path, uplink):
    n = make_stack(tmp_path, nat=False)
    try:
        set_forward("1")
        sa, ia, ca = start(n)
        sb, ib, cb = start(n)
        try:
            assert tcp(ca, ib["ip"], 5000).startswith("FAIL")
            assert tcp(ca, "198.51.100.2", 8443).startswith("FAIL"), "no NAT was requested, so no outbound"
        finally:
            n.ctl.sync_end_session(sa)
            n.ctl.sync_end_session(sb)
    finally:
        close_stack(n)


def test_close_removes_the_table_and_restores_forwarding(tmp_path):
    before = forward()
    n = make_stack(tmp_path, nat=True)
    assert f"inet {n.sup.table}" in subprocess.run(["nft", "list", "tables"], capture_output=True, text=True).stdout
    close_stack(n)
    assert n.sup.table not in subprocess.run(["nft", "list", "tables"], capture_output=True, text=True).stdout
    assert forward() == before


def test_real_internet_from_a_session_if_the_host_has_it(natstack):
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
    except OSError:
        pytest.skip("this host cannot reach 1.1.1.1:443 itself")
    sid, info, c = start(natstack)
    try:
        r = c.execute(["/usr/bin/python3", "-c",
                       "import socket;socket.create_connection(('1.1.1.1',443),timeout=5).close();print('ok')"])
        assert r["output"].strip() == "ok", r
    finally:
        natstack.ctl.sync_end_session(sid)


def test_control_without_the_firewall_sessions_do_reach_each_other(tmp_path):
    """The test can lose: the same probe succeeds when the policy is off and forwarding is on, so the
    refusals above are the firewall's doing and not a broken probe."""
    n = make_stack(tmp_path, nat=False, firewall=False)
    try:
        set_forward("1")
        sa, ia, ca = start(n)
        sb, ib, cb = start(n)
        try:
            assert "Linux" in tcp(ca, ib["ip"], 5000), "probe is broken: no firewall and forwarding on must reach"
        finally:
            n.ctl.sync_end_session(sa)
            n.ctl.sync_end_session(sb)
    finally:
        close_stack(n)


def tcp_http(client, host):
    return client.execute(["/usr/bin/python3", "-c",
                           f"import urllib.request;print(urllib.request.urlopen('http://{host}:5000/platform',timeout=4).read())"]
                          )["output"]
