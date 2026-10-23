"""INTEGRATION: the outer sweep (envd_linux/sweep.py) after a holder process is killed with SIGKILL.

A holder process builds a Supervisor with NAT and two real sessions, then is killed -9 so no teardown runs.
The test first asserts the leak is really there (so it can lose), then runs the sweep and asserts that
everything the run OWNED is gone: its session processes, its table, its mounts, `ip_forward` back to the value
it had BEFORE the holder, and the ledger entry. FOREIGN resources with similar names but no ownership record
(a link `dvh9`, tables `drums` and `drums_foreign`, a tagless process under the same state dir, a mount under
it, an X11 symlink) must survive untouched. Also: a no-op on a clean host, a second sweep is a no-op, a live
Supervisor is left alone, a foreign change to ip_forward is not clobbered, and a new Supervisor sweeps its
predecessor's leftovers at start.

Local sandbox only (root, overlayfs, Xvfb, nft, system python3 with python-xlib and pyroute2)."""

import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from envd_linux import sweep as sw
from envd_linux.template import build_home_template

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
        return "nft not permitted"


pytestmark = pytest.mark.skipif(_prereqs() is not None, reason=_prereqs() or "")

HOLDER = textwrap.dedent('''
    import sys, time
    from envd_linux.session import Supervisor
    state, tpl, ledger = sys.argv[1:4]
    sup = Supervisor(state, tpl, root_mode="full", netns=True, osworld_port=5000, nat=True, sweep_dir=ledger)
    sup.create(0)
    sup.create(1)
    print("READY", flush=True)
    time.sleep(600)
''')

def nft_tables():
    return subprocess.run(["nft", "list", "tables"], capture_output=True, text=True).stdout


def owned_tables():
    return [ln.split()[-1] for ln in nft_tables().splitlines() if ln.split()[-1].startswith("drums_")
            and "foreign" not in ln]


LIST_LINKS = ("from pyroute2 import IPRoute\n"
              "i = IPRoute()\n"
              "print(' '.join(l.get_attr('IFLA_IFNAME') for l in i.get_links() if l.get_attr('IFLA_IFNAME').startswith('dvh')))")


def links():
    r = subprocess.run(["/usr/bin/python3", "-c", LIST_LINKS], capture_output=True, text=True)
    return r.stdout.split()


def sessions(state):
    out = []
    for p in sw.glob.glob("/proc/[0-9]*/cmdline"):
        try:
            argv = open(p, "rb").read().decode(errors="replace").split("\0")
        except OSError:
            continue
        if "envd_linux.session_init" in argv and "--tag" in argv and argv[argv.index("--upper") + 1].startswith(state + "/"):
            out.append(int(p.split("/")[2]))
    return out


def mounts(state):
    return [ln.split()[1] for ln in open("/proc/self/mounts") if ln.split()[1].startswith(state)]


FOREIGN_VETH = ("from pyroute2 import IPRoute\n"
                "i = IPRoute(); i.link('add', ifname='dvh9', kind='veth', peer={'ifname': 'dvs9'})")


class Foreign:
    """Resources with look-alike names and NO ownership record, created before the holder runs."""

    def __init__(self, w):
        self.w = w
        subprocess.run(["/usr/bin/python3", "-c", FOREIGN_VETH], check=True)
        for t in ("drums", "drums_foreign"):
            subprocess.run(["nft", "add", "table", "inet", t], check=True)
        os.makedirs(w.state + "/foreign-mount", exist_ok=True)
        subprocess.run(["mount", "-t", "tmpfs", "tmpfs", w.state + "/foreign-mount"], check=True)
        # a process that looks like a session (same state dir, same module) but has no tag
        self.proc = subprocess.Popen(["/usr/bin/python3", "-c", "import time; time.sleep(600)",
                                      "envd_linux.session_init", "--upper", w.state + "/s9/upper"])
        os.makedirs("/tmp/.X11-unix", exist_ok=True)
        self.link = "/tmp/.X11-unix/X77"
        if os.path.lexists(self.link):
            os.unlink(self.link)
        os.symlink("/nonexistent-foreign-target", self.link)

    def survives(self):
        assert "dvh9" in links(), "foreign link was deleted"
        t = nft_tables()
        assert "inet drums\n" in t + "\n" and "inet drums_foreign" in t, "foreign table was deleted"
        assert self.w.state + "/foreign-mount" in mounts(self.w.state), "foreign mount was unmounted"
        assert self.proc.poll() is None, "foreign process was killed"
        assert os.path.islink(self.link), "foreign X11 link was removed"

    def cleanup(self):
        self.proc.kill()
        self.proc.wait()
        subprocess.run(["umount", self.w.state + "/foreign-mount"], capture_output=True)
        subprocess.run(["/usr/bin/python3", "-c", "from pyroute2 import IPRoute\ni=IPRoute()\n"
                        "for n in ('dvh9',):\n    ix=i.link_lookup(ifname=n)\n    ix and i.link('del', index=ix[0])"])
        for t in ("drums", "drums_foreign"):
            subprocess.run(["nft", "delete", "table", "inet", t], capture_output=True)
        if os.path.islink(self.link):
            os.unlink(self.link)


@pytest.fixture
def world(tmp_path):
    w = type("W", (), {})()
    w.state, w.ledger = str(tmp_path / "state"), str(tmp_path / "ledger")
    os.makedirs(w.state)
    w.tpl = tmp_path / "tpl"
    build_home_template(str(w.tpl / "home" / "drums"))
    w.holders, w.foreign = [], None
    w.saved = sw.read_forward()
    yield w
    for h in w.holders:
        h.kill()
        h.wait()
    sw.sweep(ledger=w.ledger, force=True)
    if w.foreign:
        w.foreign.cleanup()
    sw.write_forward(w.saved)


def spawn_holder(w):
    env = dict(os.environ, PYTHONPATH=PKG)
    h = subprocess.Popen(["/usr/bin/python3", "-c", HOLDER, w.state, str(w.tpl), w.ledger], env=env,
                         stdout=subprocess.PIPE, text=True)
    w.holders.append(h)
    line = h.stdout.readline()
    assert line.strip() == "READY", line
    return h


def assert_owned_gone(w, forward):
    assert sessions(w.state) == [], "session processes leaked"
    assert owned_tables() == [], "owned nftables table leaked"
    assert sw.read_forward() == forward, "ip_forward not restored"
    assert [m for m in mounts(w.state) if m.endswith("/tplfs")] == [], "owned mount leaked"
    assert not os.path.isdir(w.ledger) or not [f for f in os.listdir(w.ledger) if f.startswith("sup-")]


@pytest.mark.parametrize("prior", ["0", "1"])
def test_sweep_after_kill_9_removes_what_the_run_owned_and_restores_the_prior_forward_value(world, prior):
    sw.write_forward(prior)
    world.foreign = Foreign(world)
    h = spawn_holder(world)
    h.send_signal(signal.SIGKILL)
    h.wait()
    # the leak is real before the sweep (so this test can lose)
    assert len(sessions(world.state)) >= 2 and owned_tables() and sw.read_forward() == "1"
    rep = sw.sweep(ledger=world.ledger)
    assert rep["killed"] and rep["tables"] and rep["entries_removed"] == [h.pid]
    assert rep["ip_forward_restored"] == (prior if prior == "0" else None)
    assert_owned_gone(world, prior)
    world.foreign.survives()


def test_foreign_untagged_resources_survive_a_forced_sweep_too(world):
    sw.write_forward("0")
    world.foreign = Foreign(world)
    h = spawn_holder(world)
    sw.sweep(ledger=world.ledger, force=True)  # even force only removes what has our record
    h.kill()  # force removes the run's resources, not the holder process itself
    h.wait()
    assert_owned_gone(world, "0")
    world.foreign.survives()


def test_a_foreign_ip_forward_change_is_not_clobbered(world):
    sw.write_forward("0")
    h = spawn_holder(world)
    h.kill()
    h.wait()
    sw.write_forward("0")  # someone else turned it off after the crash: current != what the run set
    sw.write_forward("2") if False else None
    rep = sw.sweep(ledger=world.ledger)
    assert rep["ip_forward_restored"] is None and sw.read_forward() == "0"
    assert_owned_gone(world, "0")


def test_sweep_twice_and_on_a_clean_host_is_a_noop(world):
    sw.write_forward("0")
    h = spawn_holder(world)
    h.kill()
    h.wait()
    sw.sweep(ledger=world.ledger)
    assert sw.sweep(ledger=world.ledger) == sw.EMPTY
    assert sw.sweep(ledger=world.ledger + "-does-not-exist") == sw.EMPTY


def test_a_live_supervisor_is_not_touched(world):
    sw.write_forward("0")
    h = spawn_holder(world)
    before = sessions(world.state)
    tables = owned_tables()
    rep = sw.sweep(ledger=world.ledger)
    assert rep["live_supervisors"] == [h.pid] and not rep["killed"] and not rep["tables"]
    assert sessions(world.state) == before and owned_tables() == tables and sw.read_forward() == "1"
    sw.sweep(ledger=world.ledger, force=True)
    h.kill()
    h.wait()
    assert_owned_gone(world, "0")


def test_a_new_supervisor_sweeps_its_predecessors_leftovers_at_start(world):
    from envd_linux.session import Supervisor
    sw.write_forward("0")
    world.foreign = Foreign(world)
    h = spawn_holder(world)
    h.kill()
    h.wait()
    sup = Supervisor(world.state + "-2", str(world.tpl), root_mode="full", netns=True, osworld_port=5000,
                     sweep_dir=world.ledger)
    try:
        assert sup.start_sweep["killed"] and sup.start_sweep["tables"] and sup.start_sweep["ip_forward_restored"] == "0"
        assert sessions(world.state) == []
        sup.create(0)  # no 'File exists' on the veth the dead run used
    finally:
        sup.close()
    assert_owned_gone(world, "0")
    assert sessions(world.state + "-2") == [] and mounts(world.state + "-2") == []
    world.foreign.survives()


def test_standalone_cli_reports_json_and_exits_zero(world):
    sw.write_forward("0")
    h = spawn_holder(world)
    h.kill()
    h.wait()
    r = subprocess.run([sys.executable, "-m", "envd_linux.sweep", "--ledger", world.ledger],
                       capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=PKG))
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["killed"] and rep["tables"]
    assert_owned_gone(world, "0")
    r2 = subprocess.run([sys.executable, "-m", "envd_linux.sweep", "--ledger", world.ledger],
                        capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=PKG))
    assert r2.returncode == 0 and json.loads(r2.stdout) == sw.EMPTY
