"""The bounded smoke job, run as a subprocess in real sessions (skips without the prerequisites)."""

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)


def _prereqs():
    if os.geteuid() != 0:
        return "needs root"
    for b in ("unshare", "nsenter", "Xvfb", "pgrep", "pivot_root"):
        if shutil.which(b) is None:
            return f"missing {b}"
    r = subprocess.run(["/usr/bin/python3", "-c", "import Xlib, pyroute2"], capture_output=True)
    return None if r.returncode == 0 else "system python3 needs python-xlib and pyroute2"


pytestmark = pytest.mark.skipif(_prereqs() is not None, reason=_prereqs() or "")


def veths():
    r = subprocess.run(["/usr/bin/python3", "-c",
                        "from pyroute2 import IPRoute;print(' '.join(l.get_attr('IFLA_IFNAME') for l in IPRoute().get_links()))"],
                       capture_output=True, text=True)
    return [x for x in r.stdout.split() if x.startswith("dvh")]


def run_smoke(tmp_path, *extra, timeout=300):
    out = tmp_path / "smoke.json"
    cmd = ["/usr/bin/python3", "-m", "envd_linux.scalecua.smoke", "--state", str(tmp_path / "state"),
           "--out", str(out), "--events", str(tmp_path / "events.jsonl"), *extra]
    p = subprocess.run(cmd, env=dict(os.environ, PYTHONPATH=PKG), capture_output=True, text=True, timeout=timeout)
    return p, out


def test_smoke_runs_the_guest_contract_checks_and_cleans_up(tmp_path):
    p, out = run_smoke(tmp_path, "--slots", "2", "--attempts", "4", "--concurrency", "2", "--max-wall-s", "180")
    assert p.returncode == 0, p.stderr[-800:]
    res = json.loads(out.read_text())
    assert res["status"] == "ok"
    s = res["summary"]
    assert s["attempts"] == 4 and s["ok"] == 4 and not s["faults"]
    assert s["start_s_p50"] is not None and s["session_pss_mib_mean"] > 0
    assert res["host"]["dev_kvm"] in (True, False)
    assert veths() == [] and subprocess.run(["pgrep", "-x", "Xvfb"], capture_output=True).returncode == 1
    events = [json.loads(x) for x in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert sum(1 for e in events if e["event"] == "start") == 4


def test_wall_cap_stops_the_job_and_destroys_every_session(tmp_path):
    t0 = time.time()
    p, out = run_smoke(tmp_path, "--slots", "2", "--attempts", "200", "--concurrency", "2", "--max-wall-s", "4")
    assert p.returncode == 3, (p.returncode, p.stderr[-800:])
    assert time.time() - t0 < 90
    res = json.loads(out.read_text())
    assert res["status"] == "wall_cap_reached" and res["summary"]["attempts"] < 200
    assert veths() == [] and subprocess.run(["pgrep", "-x", "Xvfb"], capture_output=True).returncode == 1
