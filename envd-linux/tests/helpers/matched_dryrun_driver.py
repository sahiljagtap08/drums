"""HARNESS VALIDATION ONLY (never a result): the matched-comparison runner driving the Drums arm through the REAL public `desktop_env`
against REAL local sessions with a SCRIPTED agent. Runs inside a venv that has desktop-env (checkout of public OSWorld main on PYTHONPATH).
Prints one JSON object."""

import json
import os
import sys
import tempfile
import time
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
from drums_compare import osworld_matched as om  # noqa: E402
from envd_linux.scalecua.pool import DrumsSessionPool  # noqa: E402
from envd_linux.session import Supervisor  # noqa: E402
from envd_linux.template import build_home_template  # noqa: E402

kit, task_file = sys.argv[1], sys.argv[2]
task = json.load(open(task_file))
tmp = tempfile.mkdtemp()
tpl_home = os.path.join(tmp, "tpl", "home", "drums")
build_home_template(tpl_home)
os.makedirs(os.path.join(tpl_home, "Desktop"), exist_ok=True)
sup = Supervisor(os.path.join(tmp, "state"), os.path.join(tmp, "tpl"), root_mode="full", netns=True, osworld_port=5000,
                 osworld_real_kit=kit, init_python=os.path.join(kit, "venv", "bin", "python"), server_mode="module", ready_timeout=120)
pool = DrumsSessionPool(sup, 2, health_timeout=90, eager_reset=False)


def used_kib():
    m = {k: int(v.split()[0]) for k, v in (ln.split(":") for ln in open("/proc/meminfo"))}
    return m["MemTotal"] - m["MemAvailable"]


out = {"harness_validation_only": True, "stubbed_modules": stubbed}
try:
    adapter = om.DesktopEnvAdapter("drums-dryrun", om.drums_factory(pool, screen_size=(1280, 800), desktop_env_module=de,
                                                                    cache_dir=os.path.join(tmp, "cache"), require_a11y_tree=False))
    seq = om.sample_tasks([task["id"]], 4, seed=1)
    action = ("import os; os.rename(os.path.expanduser('~/Desktop/todo_list_Jan_1'), os.path.expanduser('~/Desktop/todo_list_Jan_2'))")
    res = om.run_arm(adapter, {task["id"]: task}, seq, lambda: om.ScriptedAgent({task["id"]: [action]}), 2,
                     om.Prices(host_usd_per_hour=3600.0, label="dry-run placeholder, not a price"), max_steps=3,
                     mem_reader=used_kib, mem_period=0.5)
    out["summary"] = res["summary"]
    out["scores"] = [r["score"] for r in res["rows"]]
except BaseException as e:
    import traceback
    out["error"] = f"{type(e).__name__}: {str(e)[-400:]}"
    out["trace"] = traceback.format_exc()[-1200:]
finally:
    sup.close()
print("RESULT " + json.dumps(out))
