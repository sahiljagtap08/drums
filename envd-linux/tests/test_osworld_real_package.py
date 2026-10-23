"""The bridge against the REAL `desktop_env` package (claim (b): interface and client-side flow, not
actual OSWorld). Needs, via environment variables:

  DRUMS_OSW_PYTHON   a python whose site-packages have desktop-env (PyPI, Apache-2.0) and its light
                     imports (gymnasium, requests, psutil, filelock, docker, pillow, lxml, numpy,
                     playwright, websocket-client, requests-toolbelt, pydrive, python-dotenv, pyyaml, tqdm, odfpy ...)
  DRUMS_OSW_SRC      (optional) a checkout of the public xlang-ai/OSWorld main, put first on PYTHONPATH
                     to test the current source instead of the PyPI release

plus the integration prerequisites (root, overlayfs, Xvfb, nft, system python3 with python-xlib and pyroute2).

What this proves: our classes satisfy the real abstract interface; the real `DesktopEnv` constructs with
them (`provider_name="docker"`, `region="drums"`), starts a real Drums session, parses the address with its
own code, builds its real `PythonController`/`SetupController`, resets, fetches a real PNG screenshot over HTTP from the
session's guest (STUB), takes the revert path to a fresh session on the next reset, and closes.

Where the real-host boundary is (found by running it):
  * the PyPI 1.0.2 release and the 2026-10-23 main (04718ea) differ: main's `ensure_recording_server`
    runs an installer probe inside the guest that requires the guest server to be a supported OSWorld
    systemd unit; against our stub it raises "Server is not in a supported OSWorld systemd unit". The run
    here therefore uses `OSWORLD_RECORDING_UPDATE=off` (a documented option). On a real image that probe is
    part of every start, which ties it to the systemd-in-a-namespace question in docs/gnome-namespace-investigation.md.
  * `evaluate()`, accessibility-tree and terminal observations, `step()` with real pyautogui, task `config`
    setup steps (browser launch, file download/open through the real server) are NOT exercised.
  * the `evaluators.metrics/getters` packages are stubbed (heavy dependencies); only `evaluate()` needs them."""

import json
import os
import pathlib
import subprocess

import pytest

PY = os.environ.get("DRUMS_OSW_PYTHON")
SRC = os.environ.get("DRUMS_OSW_SRC")
ROOT = pathlib.Path(__file__).resolve().parents[1]
DRIVER = pathlib.Path(__file__).parent / "helpers" / "osworld_real_driver.py"


def _prereqs():
    import shutil
    if not PY:
        return "set DRUMS_OSW_PYTHON to a python with desktop-env installed"
    if os.geteuid() != 0:
        return "needs root"
    for b in ("unshare", "nsenter", "Xvfb", "pivot_root", "mount", "nft"):
        if shutil.which(b) is None:
            return f"missing {b}"
    if subprocess.run(["/usr/bin/python3", "-c", "import Xlib, pyroute2"], capture_output=True).returncode:
        return "system python3 needs python-xlib and pyroute2"


pytestmark = pytest.mark.skipif(_prereqs() is not None, reason=_prereqs() or "")


def run(src):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (src, str(ROOT)) if p), OSWORLD_RECORDING_UPDATE="off")
    r = subprocess.run([PY, str(DRIVER)], capture_output=True, text=True, env=env, timeout=240, cwd="/tmp")
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, (r.stdout[-1500:], r.stderr[-1500:])
    return json.loads(lines[-1][7:])


CASES = [pytest.param(None, id="pypi-release")]
if SRC:
    CASES.append(pytest.param(SRC, id="public-main-checkout"))


@pytest.mark.parametrize("src", CASES)
def test_bridge_against_the_real_desktop_env(src):
    out = run(src)
    assert out["bridge_bases_are_real"] is True, "the bridge fell back to local ABC stand-ins"
    if src:
        assert out["package_file"].startswith(src)
    assert out["subclass"] and out["instantiable"]
    assert out["abstract_methods"]["Provider"], out["abstract_methods"]
    bad = [k for k, v in out["call_shapes"].items() if not v]
    # the PyPI release's base start_emulator takes (path, headless); DesktopEnv calls it with os_type
    # (docker-shaped); we accept both, so every shape must bind
    assert not bad, bad
    assert "error" not in out, out["error"]
    assert out["constructed"]["ports"] == [5000, 9222, 8006, 8080]
    assert out["first_reset_screenshot_png"] and out["second_reset_screenshot_png"]
    assert out["revert_took_a_fresh_session"] and out["closed_session_released"]
