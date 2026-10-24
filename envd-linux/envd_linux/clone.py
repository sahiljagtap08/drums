"""PROTOTYPE of mechanism C: per-sibling reseed of a session restored from a checkpoint (docs/l1-shared-runtime-design.md sections 16 and 17).

Siblings restored from one checkpoint are clones: identical in-process random-number state, identical hostname, and, in a browser,
identical renderer randomness and cookie jar. `reseed()` makes the parts that are not task state differ, and leaves the rest alone:

  every restored sibling       hostname (UTS namespace); the guest server's Python `random` and, if loaded, `numpy.random`, reseeded from
                               fresh os.urandom by a signal handler the session installed BEFORE the checkpoint (SERVER_HOOK).
  clean checkpoints only       the browser's default-context cookies, and a NEW browser context (a new renderer process: a new V8 isolate with fresh
                               randomness) opened on the same page with the old page closed. A checkpoint is declared clean when it is created.
  never                        the wall clock, /dev/urandom semantics, files, and, for a checkpoint not declared clean, anything in the browser: cookies,
                               storage, logged-in sessions and task setup are PRESERVED, because erasing them would change the task.

Not fixable without restarting the process: Python's string-hash seed and the address-space layout are identical across clones."""

import glob
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

# Installed in the guest-server process by session_init (friendly mode), before the checkpoint.
SERVER_HOOK = (
    "import os,sys,signal,random,json\n"
    "def _reseed(*a):\n"
    "    random.seed(os.urandom(32))\n"
    "    n=sys.modules.get('numpy')\n"
    "    if n is not None: n.random.seed(int.from_bytes(os.urandom(4),'little'))\n"
    "def _probe(*a):\n"
    "    n=sys.modules.get('numpy')\n"
    "    json.dump({'pid':os.getpid(),'python_random':random.random(),'numpy_loaded':n is not None,"
    "'numpy_random':(float(n.random.random()) if n is not None else None),'hash':hash('drums')},open('/tmp/.rng_probe.json','w'))\n"
    "signal.signal(signal.SIGUSR1,_reseed)\n"
    "signal.signal(signal.SIGUSR2,_probe)\n"
)

_CDP = r'''
import json, sys, time, urllib.request
sys.path.insert(0, sys.argv[1])
from envd_linux.l1proto.branch import WS
url = sys.argv[2]
ver = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=3))
b = WS(ver["webSocketDebuggerUrl"])
def c(m, p=None):
    return b.call(m, p)["result"]
old = [t for t in c("Target.getTargets")["targetInfos"] if t["type"] == "page"]
c("Storage.clearCookies", {})
ctx = c("Target.createBrowserContext", {"disposeOnDetach": False})["browserContextId"]
new = c("Target.createTarget", {"url": url, "browserContextId": ctx, "newWindow": True})["targetId"]
deadline = time.time() + 15
title = ""
while time.time() < deadline:
    t = [x for x in c("Target.getTargets")["targetInfos"] if x["targetId"] == new]
    title = t[0]["title"] if t else ""
    if "drums-ready" in title:
        break
    time.sleep(0.02)
for t in old:
    c("Target.closeTarget", {"targetId": t["targetId"]})
print(json.dumps({"context": ctx, "target": new, "title": title, "closed": len(old)}))
'''

PAGE = "data:text/html,<title>drums-ready</title><h1>ready</h1>"


def descendants(pid):
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        for t in glob.glob(f"/proc/{p}/task/*/children"):
            try:
                todo += [int(x) for x in open(t).read().split()]
            except OSError:
                pass
    return sorted(set(out))


def server_pids(init_pid):
    out = []
    for p in descendants(init_pid):
        try:
            if "main.app.run" in open(f"/proc/{p}/cmdline").read():
                out.append(p)
        except OSError:
            pass
    return out


def rng_probe(init_pid, timeout=5):
    """What the guest-server process's random state would produce next (consumes one draw). For tests and the audit."""
    res = []
    for p in server_pids(init_pid):
        path = f"/proc/{init_pid}/root/tmp/.rng_probe.json"
        if os.path.exists(path):
            os.unlink(path)
        os.kill(p, signal.SIGUSR2)
        end = time.time() + timeout
        while not os.path.exists(path) and time.time() < end:
            time.sleep(0.02)
        time.sleep(0.05)
        res.append(json.load(open(path)))
    return res


def reseed(info, slot, clean, python="/usr/bin/python3", pkg_dir=None, page=PAGE):
    """Apply the per-sibling reseed to a freshly restored session. Returns what was done."""
    pid = info["init_pid"]
    done = {"hostname": None, "server_reseeded": 0, "browser": "preserved (checkpoint not declared clean, or it has no browser)"}
    name = f"drums-s{slot}-{os.urandom(3).hex()}"
    subprocess.run(["nsenter", "-t", str(pid), "-u", "hostname", name], check=True)
    done["hostname"] = name
    for p in server_pids(pid):
        os.kill(p, signal.SIGUSR1)
        done["server_reseeded"] += 1
    if clean:
        r = subprocess.run(["nsenter", "-t", str(pid), "-n", python, "-c", _CDP, pkg_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__))), page],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"browser reseed failed: {r.stderr[-300:]}")
        done["browser"] = json.loads(r.stdout.strip().splitlines()[-1])
    return done
