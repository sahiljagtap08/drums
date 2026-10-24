"""Real-session validation of the snapshot-API bridge (docs/l1-shared-runtime-design.md sections 20 and 21): setup-only reuse through the matched runner
and a mid-task branch with divergent continuations scored by the REAL local evaluator of public task e0df059f, on the real public `desktop_env` and real
Drums sessions. One task, one host, no incumbent. Needs DRUMS_CRIU_HOME, DRUMS_OSW_PYTHON, DRUMS_OSW_SRC, root and the real-server kit."""

import json
import os
import pathlib
import shutil
import subprocess

import pytest

from envd_linux import realkit

HOME = os.environ.get("DRUMS_CRIU_HOME")
PY, SRC = os.environ.get("DRUMS_OSW_PYTHON"), os.environ.get("DRUMS_OSW_SRC")
TASK = "evaluation_examples/examples/os/e0df059f-28a6-4169-924f-b9623e7184cc.json"
ROOT = pathlib.Path(__file__).resolve().parents[1]
DRIVER = pathlib.Path(__file__).parent / "helpers" / "reuse_real_driver.py"


def _prereqs():
    if not HOME or not os.path.exists(os.path.join(HOME, "x-4.2", "usr", "sbin", "criu")):
        return "set DRUMS_CRIU_HOME to the private CRIU directory"
    if not (PY and SRC) or not os.path.exists(os.path.join(SRC, TASK)):
        return "set DRUMS_OSW_PYTHON and DRUMS_OSW_SRC"
    if os.geteuid() != 0 or not realkit.available(realkit.DEFAULT_KIT) or realkit.missing():
        return "needs root and the built real-server kit"
    for b in ("unshare", "nsenter", "Xvfb", "pivot_root", "mount", "nft"):
        if shutil.which(b) is None:
            return f"missing {b}"


@pytest.mark.skipif(_prereqs() is not None, reason=str(_prereqs()))
def test_setup_reuse_and_a_mid_task_branch_on_real_sessions(tmp_path):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([SRC, str(ROOT), str(ROOT.parent / "compare")]))
    r = subprocess.run([PY, str(DRIVER), os.path.join(SRC, TASK), str(tmp_path)], capture_output=True, text=True, env=env, timeout=900, cwd="/tmp")
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, (r.stdout[-1000:], r.stderr[-1000:])
    out = json.loads(lines[-1][7:])
    ev = os.environ.get("DRUMS_EVIDENCE_DIR")
    if ev:
        json.dump(out, open(os.path.join(ev, "matched_reuse_real.json"), "w"), indent=1)
    assert "error" not in out, out.get("trace", out["error"])
    n, p = out["v1_none"], out["v1_post_setup"]
    for arm in (n, p):  # V1: both arms score 0.0 then 1.0 on every rollout, with no interference
        assert arm["valid"] == 6 and not arm["faults"] and arm["scores_after"] == [1.0] * 6 and arm["scores_before"] == [0.0] * 6
        assert arm["interference"] == 0 and arm["canary_clean"] == 6
    assert p["phases"] == ["setup+save"] + ["revert"] * 5 and p["reuse"]["valid_by_state_kind"] == {"setup_only": 6}
    v = out["v2"]  # V2
    assert v["i_task_state_preserved"] and v["ii_infra_reseeded"] and v["PASS_iii_divergent"] and v["v_audit"]["PASS_revision_2a"]
    assert v["iv_b_before_acting"] == {"has_jan1": True, "has_jan2": False}
    assert "todo_list_Jan_2" not in v["iv_b_after_a_acted"] and "todo_list_Jan_1" in v["c_desktop_after_restore"]
