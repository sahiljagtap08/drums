"""INTEGRATION: the OSWorld-contract plumbing in REAL Drums sessions.

Each session has: a private PID/mount/IPC/UTS namespace, a PRIVATE NETWORK NAMESPACE (veth,
10.200.<slot>.0/30), a private copy-on-write view of the WHOLE root filesystem (overlayfs on `/`
plus pivot_root; upper in RAM), its own Xvfb, and the OSWorld guest-server STUB on port 5000.
A Drums controller (the ScaleCUA-contract skeleton) hands the sessions out exclusively.

What this does not prove (see docs/osworld-contract-spike.md): the real OSWorld guest server,
pyautogui, AT-SPI, GNOME, the OSWorld Ubuntu image, KVM, internet access from a session, or
anything about the real ScaleCUA worker."""

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
sys.path.insert(0, os.path.join(PKG, "..", "verify-server"))


def _prereqs():
    if os.geteuid() != 0:
        return "needs root"
    for b in ("unshare", "nsenter", "Xvfb", "pgrep", "pivot_root", "mount"):
        if shutil.which(b) is None:
            return f"missing {b}"
    if not os.path.exists(CHROME):
        return "missing Playwright Chromium"
    if "overlay" not in open("/proc/filesystems").read():
        return "no overlayfs"
    r = subprocess.run(["/usr/bin/python3", "-c", "import Xlib, pyroute2"], capture_output=True)
    if r.returncode != 0:
        return "system python3 needs python-xlib and pyroute2"
    return None


pytestmark = pytest.mark.skipif(_prereqs() is not None, reason=_prereqs() or "")

from envd_linux.scalecua.client import GuestClient  # noqa: E402
from envd_linux.scalecua.controller import DrumsEnvironmentController, DrumsEnvironmentDelegation  # noqa: E402
from envd_linux.session import Supervisor  # noqa: E402
from envd_linux.template import build_home_template  # noqa: E402
from envd_linux.x11 import pidns_members  # noqa: E402
from drums_compare.resources import LinuxProcReader  # noqa: E402
from verify_server.app import make_server  # noqa: E402


class Stack:
    pass


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    s = Stack()
    root = tmp_path_factory.mktemp("osw")
    tpl = root / "tpl"
    build_home_template(str(tpl / "home" / "drums"))
    s.sup = Supervisor(str(root / "state"), str(tpl), root_mode="full", netns=True, osworld_port=5000)
    s.events = str(root / "events.jsonl")
    s.ctl = DrumsEnvironmentController(DrumsEnvironmentDelegation(), s.sup, 2, health_timeout=30,
                                       acquire_timeout=60, events_path=s.events)
    s.ctl.run_loop_in_thread()
    s.vport = 17600 + os.getpid() % 300
    s.verify = make_server(str(root / "v.db"), host="0.0.0.0", port=s.vport)
    threading.Thread(target=s.verify.serve_forever, daemon=True).start()
    s.verify_url = f"http://127.0.0.1:{s.vport}"
    yield s
    s.ctl.shutdown()
    s.sup.close()
    s.verify.shutdown()


def start(stack):
    sid, cont, addr = stack.ctl.sync_start_session("ubuntu")
    info = stack.ctl.session_info(sid)["ubuntu"]
    return sid, info, GuestClient(addr["ubuntu"])


LISTENER = ("import socketserver, http.server;"
            "socketserver.TCPServer.allow_reuse_address = True;"
            "socketserver.TCPServer(('0.0.0.0', 9222), http.server.SimpleHTTPRequestHandler).serve_forever()")


def png_size(b):
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", b[16:24])


XTEST = r'''
import sys, json
from Xlib import X, XK, display
from Xlib.ext import xtest
d = display.Display()
def key(ks, shift=False):
    kc = d.keysym_to_keycode(ks)
    if shift: xtest.fake_input(d, X.KeyPress, d.keysym_to_keycode(XK.XK_Shift_L))
    xtest.fake_input(d, X.KeyPress, kc); xtest.fake_input(d, X.KeyRelease, kc)
    if shift: xtest.fake_input(d, X.KeyRelease, d.keysym_to_keycode(XK.XK_Shift_L))
for a in json.loads(sys.argv[1]):
    if a[0] == "click":
        xtest.fake_input(d, X.MotionNotify, False, x=a[1], y=a[2])
        xtest.fake_input(d, X.ButtonPress, 1); xtest.fake_input(d, X.ButtonRelease, 1)
    elif a[0] == "press":
        key({"tab": XK.XK_Tab, "enter": XK.XK_Return}[a[1]])
    elif a[0] == "write":
        for ch in a[1]:
            ks = ord(ch)
            idx = list(d.keysym_to_keycodes(ks))[0][1]
            key(ks, shift=bool(idx & 1))
d.sync()
'''


def drive(client, actions):
    r = client.execute(["/usr/bin/python3", "-c", XTEST, json.dumps(actions)])
    assert r["returncode"] == 0, r


def settle(client, timeout=40, poll=0.5, quiet=4.0):
    start = time.time()
    last, changed, stable, first_t = None, False, 0, None
    while time.time() - start < timeout:
        h = hashlib.sha1(client.screenshot()).hexdigest()
        now = time.time()
        if last is None:
            first_t = now
        elif h != last:
            changed, stable = True, 0
        else:
            stable += 1
        last = h
        if (changed and stable >= 2) or (not changed and now - first_t >= quiet):
            return
        time.sleep(poll)
    raise AssertionError("display never settled")


def test_session_serves_the_guest_contract_with_a_real_screenshot(stack):
    sid, info, c = start(stack)
    try:
        assert c.platform() == "Linux"
        assert c.screen_size() == {"width": 1280, "height": 800}
        assert png_size(c.screenshot()) == (1280, 800)
        assert len(c.cursor()) == 2
        assert info["ip"].startswith("10.200.") and info["root_mode"] == "full" and info["netns"]
    finally:
        stack.ctl.sync_end_session(sid)


def test_two_sessions_serve_identical_ports_at_the_same_time(stack):
    """The reason a per-session network namespace is forced: the guest contract has fixed ports."""
    sa, ia, ca = start(stack)
    sb, ib, cb = start(stack)
    try:
        assert ia["ip"] != ib["ip"] and ca.platform() == cb.platform() == "Linux"  # both on :5000
        for c in (ca, cb):
            c.launch(["/usr/bin/python3", "-c", LISTENER])  # both on :9222
        time.sleep(1.5)
        for ip in (ia["ip"], ib["ip"]):
            assert urllib.request.urlopen(f"http://{ip}:9222/", timeout=5).status == 200
    finally:
        stack.ctl.sync_end_session(sa)
        stack.ctl.sync_end_session(sb)


def test_full_root_is_private_and_a_fresh_session_has_no_residue(stack):
    sa, ia, ca = start(stack)
    sb, ib, cb = start(stack)
    try:
        r = ca.execute("echo x > /etc/drums-private; mkdir -p /opt/only-a; echo y > /opt/only-a/f; "
                       "echo z > /usr/local/bin/only-a; echo w > /tmp/only-a; echo v > /home/drums/only-a; "
                       "ls /etc/drums-private /opt/only-a/f /usr/local/bin/only-a /tmp/only-a /home/drums/only-a",
                       shell=True)
        assert r["returncode"] == 0, r
        out = cb.execute("ls /etc/drums-private /opt/only-a /usr/local/bin/only-a /tmp/only-a /home/drums/only-a 2>&1",
                         shell=True)["output"]
        assert out.count("No such file") == 5
        for p in ("/etc/drums-private", "/opt/only-a", "/usr/local/bin/only-a", "/home/drums/only-a"):
            assert not os.path.exists(p), f"{p} leaked to the host"
        # the shared template layer is visible to every session (profile) and unchanged by writes
        assert "profile" in cb.execute("ls /home/drums", shell=True)["output"]
        # end A: the next session on that slot is a rebuilt one with none of A's state
        slot = ia["slot"]
        stack.ctl.sync_end_session(sa)
        sc, ic, cc = start(stack)
        if ic["slot"] == slot:
            assert cc.execute("ls /etc/drums-private /opt/only-a 2>&1", shell=True)["output"].count("No such file") == 2
        stack.ctl.sync_end_session(sc)
    finally:
        stack.ctl.sync_end_session(sb)


def test_a_browser_task_runs_end_to_end_through_the_guest_contract(stack):
    sid, info, c = start(stack)
    slot, gw = info["slot"], info["gw"]
    try:
        urllib.request.urlopen(f"{stack.verify_url}/lease?env={slot}&gen=7").read()
        shm0 = [int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("Shmem:")][0]
        c.launch(["env", "DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent", CHROME, "--no-sandbox",
                  "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                  "--user-data-dir=/home/drums/profile", "--window-position=0,0", "--window-size=1280,800",
                  "--disable-dev-shm-usage", f"http://{gw}:{stack.vport}/form?env={slot}"])
        settle(c)
        drive(c, [["click", 640, 450], ["press", "tab"], ["write", "TOK-osworld-contract"], ["press", "enter"]])
        time.sleep(2)
        rows = json.loads(urllib.request.urlopen(f"{stack.verify_url}/rows?env={slot}").read())
        assert any(r["tok"] == "TOK-osworld-contract" and r["gen"] == 7 for r in rows), rows
        # one sample from this sandbox, not a benchmark
        pids = pidns_members(info["init_pid"])
        rss, pss, _ = LinuxProcReader().env_usage(pids)
        shm1 = [int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("Shmem:")][0]
        print(f"\nOSWORLD-CONTRACT SPIKE procs={len(pids)} rss_mib={rss/1024:.0f} pss_mib={(pss or 0)/1024:.0f} "
              f"host_shmem_delta_mib={(shm1-shm0)/1024:.1f}")
        assert pss and pss > 0
        shot = c.screenshot()
        assert png_size(shot) == (1280, 800)
    finally:
        stack.ctl.sync_end_session(sid)


def test_controller_events_count_every_start_and_reset(stack):
    rows = [json.loads(x) for x in open(stack.events)]
    kinds = {r["event"] for r in rows}
    assert {"start", "reset", "end"} <= kinds
    assert all("reset_s" in r for r in rows if r["event"] == "reset")
    assert not [r for r in rows if r["event"] == "fault"], "no faults expected in this run"


def test_session_to_session_is_refused_by_policy(stack):
    """The firewall (firewall.py) refuses session-to-session traffic whatever the host's ip_forward is.
    Here forwarding is whatever this host has; test_session_network_policy.py repeats the check with
    forwarding forced on, which is what proves it is policy and not an accident of the sysctl."""
    sa, ia, ca = start(stack)
    sb, ib, cb = start(stack)
    try:
        r = ca.execute(["/usr/bin/python3", "-c",
                        f"import urllib.request;print(urllib.request.urlopen('http://{ib['ip']}:5000/platform',timeout=4).read())"])
        assert "Linux" not in r["output"], r
    finally:
        stack.ctl.sync_end_session(sa)
        stack.ctl.sync_end_session(sb)
