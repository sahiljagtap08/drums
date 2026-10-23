"""Runs INSIDE a venv that has the real `desktop_env` package (PyPI desktop-env or a checkout of the
public Apache-2.0 xlang-ai/OSWorld on PYTHONPATH). Prints one JSON object. See
tests/test_osworld_real_package.py for what it proves and what it does not.

The two `desktop_env.evaluators.{metrics,getters}` package __init__ files are replaced by empty modules
(their import pulls in torch, librosa, easyocr and more, and only `DesktopEnv.evaluate()` needs them);
`evaluators.metrics.utils`, which `controllers.setup` imports, stays the real code."""

import inspect
import json
import logging
import os
import sys
import tempfile
import types

import desktop_env

base = os.path.dirname(desktop_env.__file__)
for n, sub in (("desktop_env.evaluators", "evaluators"), ("desktop_env.evaluators.metrics", "evaluators/metrics"),
               ("desktop_env.evaluators.getters", "evaluators/getters")):
    m = types.ModuleType(n)
    m.__path__ = [os.path.join(base, sub)]
    sys.modules[n] = m
sys.modules["desktop_env.evaluators"].metrics = sys.modules["desktop_env.evaluators.metrics"]
sys.modules["desktop_env.evaluators"].getters = sys.modules["desktop_env.evaluators.getters"]
logging.disable(logging.CRITICAL)

import desktop_env.desktop_env as de  # noqa: E402
from desktop_env.providers.base import Provider, VMManager  # noqa: E402
from envd_linux.scalecua import osworld_provider as op  # noqa: E402

out = {"package_file": desktop_env.__file__, "bridge_bases_are_real": op.REAL}


def bind_ok(fn, *a, **k):
    try:
        inspect.signature(fn).bind(*a, **k)
        return True
    except TypeError:
        return False


# 1. interface conformance (no sessions needed)
abstract = {c.__name__: sorted(c.__abstractmethods__) for c in (Provider, VMManager)}
mgr, prov = op.DrumsVMManager(), op.DrumsProvider(object())
out["abstract_methods"] = abstract
out["subclass"] = issubclass(op.DrumsProvider, Provider) and issubclass(op.DrumsVMManager, VMManager)
out["instantiable"] = True  # reaching here means the ABC machinery accepted both classes
out["call_shapes"] = {
    "start_emulator(path, headless, os_type)": bind_ok(prov.start_emulator, "p", True, "Ubuntu"),
    "start_emulator(path, headless)": bind_ok(prov.start_emulator, "p", True),
    "get_ip_address(path)": bind_ok(prov.get_ip_address, "p"),
    "revert_to_snapshot(path, name)": bind_ok(prov.revert_to_snapshot, "p", "s"),
    "save_state(path, name)": bind_ok(prov.save_state, "p", "s"),
    "stop_emulator(path)": bind_ok(prov.stop_emulator, "p"),
    "get_vm_path(os_type=, region=, screen_size=)": bind_ok(mgr.get_vm_path, os_type="Ubuntu", region="drums", screen_size=(1, 1)),
    "delete_vm(path, region)": bind_ok(mgr.delete_vm, "p", "drums"),
    "add_vm(path, region)": bind_ok(mgr.add_vm, "p", "drums"),
    "occupy_vm(path, pid, region)": bind_ok(mgr.occupy_vm, "p", 1, "drums"),
}

# 2. the real DesktopEnv over real Drums sessions
from envd_linux.scalecua.pool import DrumsSessionPool  # noqa: E402
from envd_linux.session import Supervisor  # noqa: E402
from envd_linux.template import build_home_template  # noqa: E402

tmp = tempfile.mkdtemp()
build_home_template(os.path.join(tmp, "tpl", "home", "drums"))
sup = Supervisor(os.path.join(tmp, "state"), os.path.join(tmp, "tpl"), root_mode="full", netns=True, osworld_port=5000)
pool = DrumsSessionPool(sup, 1, health_timeout=30, eager_reset=False)
try:
    op.install(pool)
    env = de.DesktopEnv(provider_name="docker", region="drums", action_space="pyautogui", require_a11y_tree=False)
    out["constructed"] = {"vm_ip": env.vm_ip, "ports": [env.server_port, env.chromium_port, env.vnc_port, env.vlc_port],
                          "path_to_vm": env.path_to_vm}
    obs = env.reset()
    out["first_reset_screenshot_png"] = obs["screenshot"][:8] == b"\x89PNG\r\n\x1a\n"
    first_sid = env.provider._sessions[env.path_to_vm][0]
    # mark used, as DesktopEnv does after task setup, so the next reset takes the revert path
    env.is_environment_used = True
    env.reset()
    out["revert_took_a_fresh_session"] = env.provider._sessions[env.path_to_vm][0] != first_sid
    out["second_reset_screenshot_png"] = env._get_obs()["screenshot"][:8] == b"\x89PNG\r\n\x1a\n"
    env.close()
    out["closed_session_released"] = env.path_to_vm not in env.provider._sessions
except BaseException as e:  # report, do not hide
    out["error"] = f"{type(e).__name__}: {str(e)[-400:]}"
finally:
    sup.close()
print("RESULT " + json.dumps(out))
