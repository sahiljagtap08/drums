"""HARNESS VALIDATION: the matched-comparison runner (compare/drums_compare/osworld_matched.py) with the Drums adapter over the REAL public
`desktop_env` and REAL local sessions, scripted agent, one public task four times at concurrency 2. This proves the plumbing end to end.
It is NOT a result and is not comparable to anything: no incumbent arm exists here (no KVM), the agent is a hand-written action, the
"price" is a placeholder. Needs DRUMS_OSW_PYTHON, DRUMS_OSW_SRC, the kit and the usual privileges."""

import json
import os
import pathlib
import shutil
import subprocess

import pytest

from envd_linux import realkit

PY, SRC = os.environ.get("DRUMS_OSW_PYTHON"), os.environ.get("DRUMS_OSW_SRC")
KIT = os.environ.get("DRUMS_OSW_KIT", realkit.DEFAULT_KIT)
ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPARE = ROOT.parent / "compare"
DRIVER = pathlib.Path(__file__).parent / "helpers" / "matched_dryrun_driver.py"
TASK = "evaluation_examples/examples/os/e0df059f-28a6-4169-924f-b9623e7184cc.json"


def _prereqs():
    if not (PY and SRC) or not os.path.exists(os.path.join(SRC, TASK)):
        return "set DRUMS_OSW_PYTHON and DRUMS_OSW_SRC"
    if os.geteuid() != 0 or not realkit.available(KIT) or realkit.missing():
        return "needs root and the built real-server kit"
    for b in ("unshare", "nsenter", "Xvfb", "pivot_root", "mount", "nft", "sudo"):
        if shutil.which(b) is None:
            return f"missing {b}"
    if subprocess.run(["/usr/bin/python3", "-c", "import Xlib, pyroute2"], capture_output=True).returncode:
        return "system python3 needs python-xlib and pyroute2"


pytestmark = pytest.mark.skipif(_prereqs() is not None, reason=_prereqs() or "")


def test_runner_drives_the_drums_arm_through_the_real_desktop_env_four_valid_rollouts():
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([SRC, str(ROOT), str(COMPARE)]))
    r = subprocess.run([PY, str(DRIVER), KIT, os.path.join(SRC, TASK)], capture_output=True, text=True, env=env, timeout=300, cwd="/tmp")
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, (r.stdout[-1200:], r.stderr[-1200:])
    out = json.loads(lines[-1][7:])
    assert "error" not in out, out.get("trace", out["error"])
    s = out["summary"]
    assert out["harness_validation_only"] is True
    assert s["rollouts"] == 4 and s["valid"] == 4 and not s["faults"], s
    assert out["scores"] == [1.0, 1.0, 1.0, 1.0]
    assert s["valid_per_hour"] > 0 and s["cost_per_valid_usd"] > 0 and s["memory"]["samples"] > 0
    # interference canary on REAL sessions (READINESS): every rollout probed, none saw a marker from an earlier rollout on its slot
    assert s["canary_enabled"] and s["canary_clean"] == 4 and s["canary_unsupported"] == 0 and s["interference_count"] == 0, s
    ctl = out["canary_control"]
    assert ctl["empty_before_write"] and ctl["visible_after_write"] == ["positive-control"], ctl  # the probe can see a marker
    assert ctl["after_reset"] == [], ctl  # and a Drums reset wipes it
