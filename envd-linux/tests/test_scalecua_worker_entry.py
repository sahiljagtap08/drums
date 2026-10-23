"""The worker entry point against the real `agentrl-worker==0.4.0` (MIT), run in a separate
interpreter. Skipped unless DRUMS_AGW_PYTHON names a python with agentrl-worker installed.

Proves: the driver is installed before the task module imports `create_controller`, and the worker's
own task factory then gets a Drums controller. Does NOT prove anything about OSWorld (the task here
is our own, and the provider bridge is quarantined/missing)."""

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

PY = os.environ.get("DRUMS_AGW_PYTHON")
pytestmark = pytest.mark.skipif(not PY, reason="set DRUMS_AGW_PYTHON to a python with agentrl-worker==0.4.0")
ROOT = pathlib.Path(__file__).resolve().parents[1]

TASK = textwrap.dedent('''
    import sys
    from agentrl.worker.environment import create_controller   # binds the name at import time
    from envd_linux.scalecua.controller import DrumsEnvironmentDelegation

    class FakeBackend:
        def create(self, slot): return {"ip": "10.0.0.1"}
        def destroy(self, slot): pass

    class FakeTask:
        def __init__(self):
            c = create_controller("drums", DrumsEnvironmentDelegation(), backend=FakeBackend(), slots=2)
            print("CONTROLLER", type(c).__name__, flush=True)
            "envd_linux.scalecua._quarantined_provider" in sys.modules and print("PROVIDER-LOADED")
            try:
                create_controller("nope", DrumsEnvironmentDelegation())
            except ValueError as e:
                print("ORIGINAL-STILL-RAISES", e, flush=True)
            c.shutdown()
            raise SystemExit(0)
''')


def test_entry_point_installs_driver_before_task_import(tmp_path):
    (tmp_path / "faketask.py").write_text(TASK)
    (tmp_path / "c.yaml").write_text("fake:\n  module: faketask.FakeTask\n  parameters: {}\n")
    env = dict(os.environ, PYTHONPATH=f"{tmp_path}:{ROOT}")
    env.pop("DRUMS_ALLOW_QUARANTINED", None)
    r = subprocess.run([PY, "-m", "envd_linux.scalecua.worker_entry", "fake", "--config", str(tmp_path / "c.yaml")],
                       capture_output=True, text=True, env=env, timeout=60, cwd=tmp_path)
    assert "CONTROLLER DrumsEnvironmentController" in r.stdout, (r.stdout, r.stderr[-2000:])
    assert "ORIGINAL-STILL-RAISES" in r.stdout
    assert "PROVIDER-LOADED" not in r.stdout
