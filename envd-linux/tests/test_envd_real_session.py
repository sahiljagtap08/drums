"""INTEGRATION: a real Chromium in a real private session (PID+mount+IPC+UTS namespaces,
overlayfs on $HOME, private /tmp, own Xvfb), driven through a real envd-linux daemon process
over HTTP by the comparison harness, verified by the real verify-server.

Skipped automatically unless: running as root, util-linux unshare/nsenter, Xvfb, the Playwright
Chromium, overlayfs, and a Python with python-xlib are all present. What these tests cannot
show is listed in docs/envd-linux-spike.md (no KVM, no window manager, XTEST only, no
network or cgroup isolation, $HOME-only overlay)."""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def _xlib_python():
    for py in (sys.executable, "/usr/bin/python3"):
        if py and os.path.exists(py) and subprocess.run([py, "-c", "import Xlib"], capture_output=True).returncode == 0:
            return py
    return None


def _prereqs():
    if os.geteuid() != 0:
        return "needs root"
    for b in ("unshare", "nsenter", "Xvfb", "pgrep"):
        if shutil.which(b) is None:
            return f"missing {b}"
    if not os.path.exists(CHROME):
        return "missing Playwright Chromium"
    if "overlay" not in open("/proc/filesystems").read():
        return "no overlayfs"
    if _xlib_python() is None:
        return "no python with python-xlib"
    return None


pytestmark = pytest.mark.skipif(_prereqs() is not None, reason=_prereqs() or "")

sys.path.insert(0, os.path.join(PKG, "..", "verify-server"))
from drums_compare import fakes  # noqa: E402
from drums_compare.conformance import run_conformance  # noqa: E402
from drums_compare.envd_http import EnvdHttpEnv  # noqa: E402
from drums_compare.harness import run_attempt  # noqa: E402
from drums_compare.protocol import Agent  # noqa: E402
from drums_compare.resources import LinuxProcReader  # noqa: E402
from drums_compare.types import DONE, Action, Task  # noqa: E402
from drums_compare.verify_http import VerifyServerVerifier  # noqa: E402
from envd_linux.session import Supervisor  # noqa: E402
from envd_linux.template import build_home_template  # noqa: E402
from envd_linux.x11 import pidns_members  # noqa: E402
from verify_server.app import make_server  # noqa: E402


def free_ports(n):
    for base in range(18800, 18900):
        socks = []
        try:
            for i in range(n):
                s = socket.socket()
                s.bind(("127.0.0.1", base + i))
                socks.append(s)
            return base
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
    raise RuntimeError("no free ports")


def tree_listing(path):
    out = {}
    for root, _, files in os.walk(path):
        for f in files:
            p = os.path.join(root, f)
            try:
                st = os.lstat(p)
                out[os.path.relpath(p, path)] = (st.st_size, int(st.st_mtime))
            except OSError:
                pass
    return out


class Stack:
    pass


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    s = Stack()
    root = tmp_path_factory.mktemp("spike")
    s.template = str(root / "tpl")
    build_home_template(s.template)
    s.sup = Supervisor(str(root / "state"), s.template, python="/usr/bin/python3")
    s.base = free_ports(2)
    s.vport = free_ports(1) + 50
    s.verify = make_server(str(root / "v.db"), port=s.vport)
    threading.Thread(target=s.verify.serve_forever, daemon=True).start()
    s.sup.create(0)
    s.sup.create(1)
    py = _xlib_python()
    s.daemon = subprocess.Popen([py, "-m", "envd_linux.daemon", "--state", str(root / "state"), "--slots", "0,1",
                                 "--base-port", str(s.base)], env=dict(os.environ, PYTHONPATH=PKG),
                                stdout=subprocess.PIPE, text=True)
    assert s.daemon.stdout.readline().startswith("ready")
    s.verify_url = f"http://127.0.0.1:{s.vport}"
    yield s
    s.daemon.terminate()
    s.daemon.wait(10)
    for slot in (0, 1):
        s.sup.destroy(slot)
    s.sup.close()  # removes the sweep ledger entry
    s.verify.shutdown()


def nsrun(sup, slot, *cmd):
    info = sup.info(slot)
    return subprocess.run(["nsenter", "-t", str(info["init_pid"]), "-m", "-p", "-i", "-u", "--", *cmd],
                          capture_output=True, text=True)


def call(port, method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


BROWSER_ARGS = ["--no-sandbox", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                "--user-data-dir=/home/drums/profile", "--window-position=0,0", "--window-size=1280,800",
                "--disable-dev-shm-usage", "{url}"]
BROWSER_ENV = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent"}


class FormAgent(Agent):
    """Keyboard-driven: focus the page, Tab to the Token field, type, Enter."""

    def start(self, task, obs):
        tok = task.instruction.rsplit(" ", 1)[-1]
        self.q = [Action("click", x=640, y=450), Action("key", key="tab"), Action("type", text=tok),
                  Action("key", key="return"), DONE]

    def next_action(self, obs):
        return self.q.pop(0)


def make_env(stack, slot):
    return EnvdHttpEnv(slot, stack.base + slot, {"path": CHROME, "args": BROWSER_ARGS, "env": BROWSER_ENV},
                       pre_reset=lambda s, g: stack.sup.reset(s),
                       pids_fn=lambda: pidns_members(stack.sup.info(slot)["init_pid"]),
                       settle_timeout_s=40, settle_quiet_s=4.0)


def make_task(stack):
    return Task(id="web_form_token", instruction="Type this token into the form and submit: {token}",
                verifier={"start_url": f"{stack.verify_url}/form?env={{slot}}"}, max_steps=10, timeout_s=120)


def attempt(stack, slot, gen, token):
    env = make_env(stack, slot)
    task = make_task(stack)
    import dataclasses
    t = dataclasses.replace(task, instruction=task.instruction.format(token=token))
    return run_attempt(side="drums-linux-spike", env=env, agent=FormAgent(),
                       verifier=VerifyServerVerifier(stack.verify_url), task=t,
                       attempt_id=f"spike-{gen}", gen=gen, token=token), env


def test_real_daemon_passes_the_conformance_suite(stack):
    res = run_conformance(stack.base)
    failed = [r for r in res if not r[1]]
    assert not failed, failed


def test_one_real_browser_task_end_to_end(stack):
    before = tree_listing(stack.template)
    rec, env = attempt(stack, 0, 1, "TOK-e2e-1")
    assert rec.valid, rec.invalid_reasons
    assert rec.passed and rec.terminated_by == "agent_done" and rec.steps == 4
    # real measurements from this sandbox, one sample, not a benchmark
    pids = env.pids()
    rss, pss, _ = LinuxProcReader().env_usage(pids)
    upper = stack.sup.upper_bytes(0)
    print(f"\nSPIKE reset_s={rec.reset_s:.2f} wall_s={rec.wall_s:.2f} session_procs={len(pids)} "
          f"rss_mib={rss/1024:.0f} pss_mib={(pss or 0)/1024:.0f} upper_kib={upper/1024:.0f}")
    assert pss and pss > 0 and upper > 0
    assert tree_listing(stack.template) == before, "the shared lower layer must not change"
    st, h, _ = call(stack.base, "GET", "/screenshot")
    assert st == 200 and h["X-Drums-Black"] == "false"


def test_reset_leaves_no_residue_and_second_attempt_is_valid(stack):
    marker = nsrun(stack.sup, 0, "sh", "-c", "echo secret > /home/drums/marker; echo t > /tmp/marker; ls /home/drums")
    assert "marker" in marker.stdout
    rec, _ = attempt(stack, 0, 2, "TOK-e2e-2")  # resets the session first
    assert rec.valid and rec.passed, rec.invalid_reasons
    after = nsrun(stack.sup, 0, "sh", "-c", "ls /home/drums /tmp")
    assert "marker" not in after.stdout
    rows = json.loads(urllib.request.urlopen(f"{stack.verify_url}/rows?env=0").read())
    assert {r["tok"] for r in rows} >= {"TOK-e2e-1", "TOK-e2e-2"}
    assert rec.foreign_rows == 0 and not rec.dirty_reset


def test_two_sessions_are_isolated(stack):
    # files: written in session 0, invisible in session 1 and on the host
    nsrun(stack.sup, 0, "sh", "-c", "echo a > /home/drums/only-in-0; echo a > /tmp/only-in-0")
    one = nsrun(stack.sup, 1, "sh", "-c", "ls /home/drums /tmp")
    assert "only-in-0" not in one.stdout
    assert not os.path.exists("/home/drums/only-in-0") and not os.path.exists("/tmp/only-in-0")
    # processes: the namespace sees only its own tree, and its init is pid 1
    ps = nsrun(stack.sup, 1, "sh", "-c", "echo $$; ls /proc | grep -c '^[0-9]'")
    assert int(ps.stdout.split()[1]) < 20
    host_pid = str(os.getpid())
    assert host_pid not in nsrun(stack.sup, 1, "ls", "/proc").stdout.split()
    # X: session 0's display is neither on the filesystem nor on an abstract socket from session 1
    code = ("import os,socket\n"
            "print(os.path.exists('/tmp/.X11-unix/X90'))\n"
            "s=socket.socket(socket.AF_UNIX)\n"
            "try:\n s.connect('\\0/tmp/.X11-unix/X90');print('LEAK')\n"
            "except Exception as e:\n print(type(e).__name__)\n")
    out = nsrun(stack.sup, 1, "/usr/bin/python3", "-c", code).stdout.split()
    assert out[0] == "False" and "LEAK" not in out
    # kill-all in session 0 does not touch session 1's processes
    call(stack.base + 1, "POST", "/gen", {"gen": 50})
    call(stack.base, "POST", "/gen", {"gen": 50})
    st, _, raw = call(stack.base + 1, "POST", "/launch", {"gen": 50, "path": "/bin/sleep", "args": ["300"]})
    sleeper = json.loads(raw)["pid"]
    assert st == 200 and os.path.exists(f"/proc/{sleeper}")
    assert call(stack.base, "POST", "/kill", {"gen": 50, "all": True})[0] == 200
    assert os.path.exists(f"/proc/{sleeper}")
    assert call(stack.base + 1, "GET", "/health")[0] == 200
    call(stack.base + 1, "POST", "/kill", {"gen": 50, "all": True})


def test_input_to_one_session_does_not_change_the_other(stack):
    """Two real browsers; typing into session 0 changes only session 0's frame."""
    for slot in (0, 1):
        gen = 60 + slot
        env = make_env(stack, slot)
        env.reset(make_task(stack), gen, f"TOK-iso-{slot}")
    _, h1a, _ = call(stack.base + 1, "GET", "/screenshot")
    _, h1b, _ = call(stack.base + 1, "GET", "/screenshot")
    assert h1b["X-Drums-Stale"] == "true"  # session 1 is quiet
    call(stack.base, "GET", "/screenshot")
    _, h0pre, _ = call(stack.base, "GET", "/screenshot")  # primes session 0's last-frame hash
    call(stack.base, "POST", "/action", {"gen": 60, "seq": 1, "type": "click", "x": 640, "y": 450})
    call(stack.base, "POST", "/action", {"gen": 60, "seq": 2, "type": "key", "key": "tab"})
    call(stack.base, "POST", "/action", {"gen": 60, "seq": 3, "type": "type", "text": "typed-in-zero"})
    time.sleep(0.5)
    _, h0, _ = call(stack.base, "GET", "/screenshot")
    _, h1c, _ = call(stack.base + 1, "GET", "/screenshot")
    assert h1c["X-Drums-Stale"] == "true", "session 1's display changed after input to session 0"
    assert h0["X-Drums-Stale"] == "false", "typing into session 0 should have changed session 0's display"
    r0 = call(stack.base, "GET", "/log?since=0")[2].decode()
    assert '"type": "type"' in r0


def test_late_action_from_previous_attempt_is_refused_during_rebuild(stack):
    env = make_env(stack, 0)
    env.reset(make_task(stack), 70, "TOK-late")
    call(stack.base, "POST", "/gen", {"gen": 71})  # a new attempt takes the slot
    st, _, raw = call(stack.base, "POST", "/action", {"gen": 70, "seq": 9, "type": "click", "x": 5, "y": 5})
    assert st == 409 and json.loads(raw)["current_gen"] == 71
