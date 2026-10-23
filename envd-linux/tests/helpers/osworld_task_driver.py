"""Runs INSIDE a venv with the real `desktop_env` (checkout of the public Apache-2.0 xlang-ai/OSWorld on
PYTHONPATH) and its REAL evaluators. Prints one JSON object. See tests/test_osworld_task_flow.py.

The only substitution: modules that cannot be installed lightly (librosa today) are replaced by MagicMock
modules at import time, and the driver reports which. The metrics and getters themselves are the real code."""

import json
import os
import sys
import tempfile
from unittest.mock import MagicMock

stubbed = []


def import_desktop_env():
    while True:
        try:
            import desktop_env.desktop_env as d
            return d
        except ModuleNotFoundError as e:
            m = (e.name or "").split(".")[0]
            if not m or m.startswith("desktop_env") or m in stubbed:
                raise
            stubbed.append(m)
            sys.modules[m] = MagicMock(name=m)
            for k in [k for k in sys.modules if k.startswith("desktop_env")]:
                del sys.modules[k]


de = import_desktop_env()
from envd_linux.scalecua import osworld_provider as op  # noqa: E402
from envd_linux.scalecua.pool import DrumsSessionPool  # noqa: E402
from envd_linux.session import Supervisor  # noqa: E402
from envd_linux.template import build_home_template  # noqa: E402

kit, task_file = sys.argv[1], sys.argv[2]
out = {"stubbed_modules": stubbed}
task = json.load(open(task_file))
tmp = tempfile.mkdtemp()
tpl_home = os.path.join(tmp, "tpl", "home", "drums")
build_home_template(tpl_home)
os.makedirs(os.path.join(tpl_home, "Desktop"), exist_ok=True)  # the OSWorld image's home has a Desktop
sup = Supervisor(os.path.join(tmp, "state"), os.path.join(tmp, "tpl"), root_mode="full", netns=True,
                 osworld_port=5000, osworld_real_kit=kit)
pool = DrumsSessionPool(sup, 1, health_timeout=90, eager_reset=False)
try:
    op.install(pool)
    env = de.DesktopEnv(provider_name="docker", region="drums", action_space="pyautogui", screen_size=(1280, 800),
                        require_a11y_tree=True, require_terminal=True, cache_dir=os.path.join(tmp, "cache"))
    obs = env.reset(task_config=task)
    out["instruction"] = task["instruction"]
    out["obs_keys"] = sorted(obs)
    out["obs_a11y_is_real_xml"] = bool(obs.get("accessibility_tree")) and "desktop-frame" in obs["accessibility_tree"]
    out["setup_ran_through_real_server"] = True
    out["score_before_acting"] = env.evaluate()          # negative control: the task is not done yet
    action = ("import os; os.rename(os.path.expanduser('~/Desktop/todo_list_Jan_1'), "
              "os.path.expanduser('~/Desktop/todo_list_Jan_2'))")
    env.step(action, pause=0.5)                           # the real step() -> real /execute
    out["score_after_acting"] = env.evaluate()
    env.close()
except BaseException as e:
    import traceback
    out["error"] = f"{type(e).__name__}: {str(e)[-500:]}"
    out["trace"] = traceback.format_exc()[-1500:]
finally:
    sup.close()
print("RESULT " + json.dumps(out))
