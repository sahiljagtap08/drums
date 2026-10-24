"""PROTOTYPE of mechanism C: clone hygiene for restored siblings (docs/l1-shared-runtime-design.md section 16.3, scope in 16.4).

Direct observation, F-style. Siblings restored from one checkpoint are clones. The test (1) SHOWS the clone problem with the reseed off, (2) shows the reseed
makes the non-task state differ, (3) shows it leaves a checkpoint NOT declared clean untouched in the browser (cookies preserved), and the semantics test
(public task e0df059f from a reseeded restore, scoring 0.0 then 1.0, wall clock honest) runs in the separate driver below. Needs root, the private CRIU
install, the real-server kit."""

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from envd_linux import clone, criu as cr, realkit
from envd_linux.l1proto import criu_probe as cp
from envd_linux.session import Supervisor
from envd_linux.template import build_home_template

HOME = os.environ.get("DRUMS_CRIU_HOME")
PY, SRC = os.environ.get("DRUMS_OSW_PYTHON"), os.environ.get("DRUMS_OSW_SRC")
TASK = "evaluation_examples/examples/os/e0df059f-28a6-4169-924f-b9623e7184cc.json"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers", "clone_task_driver.py")

_CDP_PROBE = r'''
import json, sys, urllib.request
sys.path.insert(0, sys.argv[1])
from envd_linux.l1proto.branch import WS
ver = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=3))
b = WS(ver["webSocketDebuggerUrl"])
def c(m, p=None):
    return b.call(m, p)["result"]
mode = sys.argv[2]
if mode == "set_cookie":
    c("Storage.setCookies", {"cookies": [{"name": "clone_secret", "value": sys.argv[3], "url": "http://example.test/"}]})
    print(json.dumps({"ok": True}))
else:
    cookies = [x["value"] for x in c("Storage.getCookies", {})["cookies"] if x["name"] == "clone_secret"]
    pages = [t for t in c("Target.getTargets")["targetInfos"] if t["type"] == "page"]
    out = {"cookie": cookies, "pages": len(pages)}
    if pages:
        s = c("Target.attachToTarget", {"targetId": pages[0]["targetId"], "flatten": True})["sessionId"]
        b.n += 1
        b.send({"id": b.n, "sessionId": s, "method": "Runtime.evaluate", "params": {"expression": "JSON.stringify([Math.random(),Math.random(),Math.random(),Array.from(crypto.getRandomValues(new Uint32Array(2)))])", "returnByValue": True}})
        while True:
            m = b.recv()
            if m and m.get("id") == b.n:
                out["page_random"] = json.loads(m["result"]["result"]["value"])
                break
    print(json.dumps(out))
'''


def cdp(pid, mode, *extra):
    r = subprocess.run(["nsenter", "-t", str(pid), "-n", "/usr/bin/python3", "-c", _CDP_PROBE, ROOT, mode, *extra], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def hostname(pid):
    return subprocess.run(["nsenter", "-t", str(pid), "-u", "hostname"], capture_output=True, text=True).stdout.strip()


def observe(info):
    probe = clone.rng_probe(info["init_pid"])[0]
    return {"hostname": hostname(info["init_pid"]), "server": probe, **cdp(info["init_pid"], "get")}


def _prereqs():
    if not HOME or not os.path.exists(os.path.join(HOME, "x-4.2", "usr", "sbin", "criu")):
        return "set DRUMS_CRIU_HOME to the private CRIU directory"
    if os.geteuid() != 0 or not realkit.available(realkit.DEFAULT_KIT) or realkit.missing():
        return "needs root and the built real-server kit"
    for b in ("unshare", "nsenter", "Xvfb", "pivot_root", "mount", "nft"):
        if shutil.which(b) is None:
            return f"missing {b}"
    if subprocess.run(["/usr/bin/python3", "-c", "import Xlib, pyroute2, PIL"], capture_output=True).returncode:
        return "system python3 needs python-xlib, pyroute2 and Pillow"


def _sup(tmp_path, **kw):
    kit = realkit.DEFAULT_KIT
    tpl = tmp_path / "tpl"
    build_home_template(str(tpl / "home" / "drums"))
    return Supervisor(str(tmp_path / "state"), str(tpl), root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=kit,
                      init_python=os.path.join(kit, "venv", "bin", "python"), server_mode="module", workload="chromium", ready_timeout=120,
                      criu_friendly=True, criu=cr.Criu(HOME), sweep_dir=str(tmp_path / "led"), **kw)


@pytest.mark.skipif(_prereqs() is not None, reason=str(_prereqs()))
def test_clones_are_shown_then_diverge_with_reseed_and_a_non_clean_checkpoint_keeps_browser_state(tmp_path):
    sup = _sup(tmp_path, restore_reseed=False)
    evidence = {}
    try:
        # ---- a CLEAN checkpoint with browser state that a clone would inherit
        a = sup.create(0)
        cdp(a["init_pid"], "set_cookie", "secret-token-pre-checkpoint")
        time.sleep(1)
        ck_clean = sup.checkpoint(0, str(tmp_path / "ck-clean"), clean=True)

        # ---- 1. reseed OFF: the clone problem must be real
        sup.restore_from = ck_clean
        x, y = sup.create(1), sup.create(2)
        ox, oy = observe(x), observe(y)
        evidence["clones_reseed_off"] = {"a": ox, "b": oy}
        assert ox["hostname"] == oy["hostname"], "hostnames already differ: the clone problem is not real for hostnames"
        assert ox["server"]["python_random"] == oy["server"]["python_random"], "server RNG already differs: not clones"
        assert ox["server"]["hash"] == oy["server"]["hash"]  # recorded as a known unfixable clone property
        assert ox["cookie"] == oy["cookie"] == ["secret-token-pre-checkpoint"], "pre-checkpoint cookie not inherited by both"
        assert ox["page_random"][:3] == oy["page_random"][:3], "the page's Math.random sequence is not cloned"
        assert ox["page_random"][3] != oy["page_random"][3]  # crypto.getRandomValues is kernel-backed and already differs: only Math.random (V8 state) is cloned
        sup.destroy(1)
        sup.destroy(2)

        # ---- 2. reseed ON (clean checkpoint): non-task state differs
        sup.restore_reseed = True
        x, y = sup.create(1), sup.create(2)
        assert x["reseed"]["browser"]["context"] != y["reseed"]["browser"]["context"]
        ox, oy = observe(x), observe(y)
        evidence["siblings_reseed_on"] = {"a": ox, "b": oy, "reseed_a": x["reseed"], "reseed_b": y["reseed"], "reseed_s": [x["reseed_s"], y["reseed_s"]]}
        assert ox["hostname"] != oy["hostname"]
        assert ox["server"]["python_random"] != oy["server"]["python_random"]
        if ox["server"]["numpy_loaded"]:
            assert ox["server"]["numpy_random"] != oy["server"]["numpy_random"]
        assert ox["page_random"][:3] != oy["page_random"][:3], "new-context pages share the Math.random sequence"
        assert ox["cookie"] == oy["cookie"] == [], "pre-checkpoint cookie survived the reseed"
        assert ox["pages"] == oy["pages"] == 1
        # the browser must still show the page
        assert cp.agent_ready(x, _blank(tmp_path), timeout=20) is not None and cp.agent_ready(y, _blank(tmp_path), timeout=20) is not None
        sup.destroy(1)
        sup.destroy(2)

        # ---- 3. a checkpoint NOT declared clean keeps its browser state
        sup.restore_from = None
        n = sup.create(3)
        cdp(n["init_pid"], "set_cookie", "task-login-token")
        time.sleep(1)
        ck_task = sup.checkpoint(3, str(tmp_path / "ck-task"), clean=False)
        sup.restore_from = ck_task
        p, q = sup.create(4), sup.create(5)
        op, oq = observe(p), observe(q)
        evidence["not_clean_checkpoint"] = {"a": op, "b": oq, "reseed_a": p["reseed"]}
        assert p["reseed"]["browser"].startswith("preserved")
        assert op["cookie"] == oq["cookie"] == ["task-login-token"], "login state was erased from a checkpoint not declared clean"
        assert op["hostname"] != oq["hostname"] and op["server"]["python_random"] != oq["server"]["python_random"]  # non-task state still reseeded
        assert op["page_random"][:3] == oq["page_random"][:3]  # the stated residual: the renderer is a clone for such checkpoints
    finally:
        ev = os.environ.get("DRUMS_EVIDENCE_DIR")
        if ev and evidence:
            json.dump(evidence, open(os.path.join(ev, "clone_hygiene.json"), "w"), indent=1)
        sup.close()


def _blank(tmp_path):
    return cp.blank_png()


@pytest.mark.skipif(_prereqs() is not None or not (PY and SRC and os.path.exists(os.path.join(SRC or "", TASK))),
                    reason=str(_prereqs()) or "set DRUMS_OSW_PYTHON and DRUMS_OSW_SRC")
def test_public_task_still_scores_zero_then_one_from_a_reseeded_restore_and_the_clock_is_honest(tmp_path):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([SRC, ROOT, os.path.join(os.path.dirname(ROOT), "compare")]))
    r = subprocess.run([PY, DRIVER, os.path.join(SRC, TASK), str(tmp_path)], capture_output=True, text=True, env=env, timeout=400, cwd="/tmp")
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, (r.stdout[-1000:], r.stderr[-1000:])
    out = json.loads(lines[-1][7:])
    assert "error" not in out, out.get("trace", out["error"])
    ev = os.environ.get("DRUMS_EVIDENCE_DIR")
    if ev:
        json.dump(out, open(os.path.join(ev, "clone_task_semantics.json"), "w"), indent=1)
    assert out["sessions_were_restored"] and out["reseeded"]
    assert out["score_before"] == 0.0 and out["score_after"] == 1.0
    assert abs(out["session_clock_minus_host_s"]) < 2.0
