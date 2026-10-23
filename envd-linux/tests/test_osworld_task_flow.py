"""A REAL public OSWorld task driven through the REAL `desktop_env` (main checkout of the Apache-2.0
xlang-ai/OSWorld) and its REAL metrics and getters, against the REAL OSWorld guest server running in a Drums
session (kit from `python -m envd_linux.realkit`). Claim level: this is the first test that exercises claim (c)
machinery for real, for ONE task, in this sandbox's Ubuntu 24.04 userland (no OSWorld image, no GNOME session, no
systemd, no LibreOffice/GIMP/VLC/Chrome). It is L0 and proves nothing about L1.

Task: os / e0df059f-... "rename the directory todo_list_Jan_1 to todo_list_Jan_2" (public JSON, unmodified).
Path covered: `DesktopEnv.reset(task_config=...)` -> `SetupController` execute steps (`sudo mkdir` and a real
pyautogui click through the real server) -> observation with REAL accessibility tree, screenshot, terminal ->
`env.evaluate()` BEFORE acting scores 0.0 (the test can lose) -> `env.step(<python>)` through the real
`/execute` -> `env.evaluate()` runs the real getter `get_vm_command_line` and the real metric `exact_match` -> 1.0.

Needs DRUMS_OSW_PYTHON (venv with desktop-env's light dependencies and the metrics/getters dependencies,
librosa stubbed by the driver), DRUMS_OSW_SRC (a checkout of OSWorld main), the kit, and the usual privileges."""

import json
import os
import pathlib
import subprocess

import pytest

from envd_linux import realkit

PY, SRC = os.environ.get("DRUMS_OSW_PYTHON"), os.environ.get("DRUMS_OSW_SRC")
KIT = os.environ.get("DRUMS_OSW_KIT", realkit.DEFAULT_KIT)
ROOT = pathlib.Path(__file__).resolve().parents[1]
DRIVER = pathlib.Path(__file__).parent / "helpers" / "osworld_task_driver.py"
TASK = "evaluation_examples/examples/os/e0df059f-28a6-4169-924f-b9623e7184cc.json"


def _prereqs():
    import shutil
    if not (PY and SRC):
        return "set DRUMS_OSW_PYTHON and DRUMS_OSW_SRC"
    if not os.path.exists(os.path.join(SRC, TASK)):
        return "task file not found in DRUMS_OSW_SRC"
    if os.geteuid() != 0 or not realkit.available(KIT) or realkit.missing():
        return "needs root and the built real-server kit (python -m envd_linux.realkit <checkout>)"
    for b in ("unshare", "nsenter", "Xvfb", "pivot_root", "mount", "nft", "sudo"):
        if shutil.which(b) is None:
            return f"missing {b}"
    if subprocess.run(["/usr/bin/python3", "-c", "import Xlib, pyroute2"], capture_output=True).returncode:
        return "system python3 needs python-xlib and pyroute2"


pytestmark = pytest.mark.skipif(_prereqs() is not None, reason=_prereqs() or "")


def test_public_task_runs_through_real_desktop_env_setup_step_and_evaluate():
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([SRC, str(ROOT)]))
    env.pop("OSWORLD_RECORDING_UPDATE", None)  # default `auto`: the real guest advertises a matching build
    r = subprocess.run([PY, str(DRIVER), KIT, os.path.join(SRC, TASK)], capture_output=True, text=True, env=env,
                       timeout=240, cwd="/tmp")
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, (r.stdout[-1200:], r.stderr[-1200:])
    out = json.loads(lines[-1][7:])
    assert "error" not in out, out.get("trace", out["error"])
    assert out["obs_a11y_is_real_xml"] and {"accessibility_tree", "screenshot", "terminal"} <= set(out["obs_keys"])
    assert out["score_before_acting"] == 0.0, "the control must fail before the task is done"
    assert out["score_after_acting"] == 1.0
    assert set(out["stubbed_modules"]) <= {"librosa"}, out["stubbed_modules"]
