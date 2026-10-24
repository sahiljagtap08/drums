"""Helper for test_criu_clone_hygiene: the public task e0df059f through the real evaluator from a RESEEDED restored session, plus a wall-clock check.
Runs in the OSWorld venv. Prints one RESULT line."""

import json
import os
import subprocess
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
from envd_linux import realkit  # noqa: E402
from envd_linux.criu import Criu  # noqa: E402
from envd_linux.scalecua.pool import DrumsSessionPool  # noqa: E402
from envd_linux.session import Supervisor  # noqa: E402
from envd_linux.template import build_home_template  # noqa: E402

task_file, tmp = sys.argv[1], sys.argv[2]
task = json.load(open(task_file))
kit = realkit.DEFAULT_KIT
tpl = os.path.join(tmp, "tpl")
build_home_template(os.path.join(tpl, "home", "drums"))
os.makedirs(os.path.join(tpl, "home", "drums", "Desktop"), exist_ok=True)
sup = Supervisor(os.path.join(tmp, "state"), tpl, root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=kit,
                 init_python=os.path.join(kit, "venv", "bin", "python"), server_mode="module", workload="chromium", ready_timeout=120,
                 criu_friendly=True, criu=Criu(), sweep_dir=os.path.join(tmp, "led"))
out = {}
try:
    sup.create(0)
    time.sleep(1)
    sup.restore_from = sup.checkpoint(0, os.path.join(tmp, "ck"), clean=True)
    pool = DrumsSessionPool(sup, 1, health_timeout=90, eager_reset=False)
    ad = om.DesktopEnvAdapter("clone-task", om.drums_factory(pool, screen_size=(1280, 800), desktop_env_module=de, cache_dir=os.path.join(tmp, "cache"),
                                                              require_a11y_tree=False))
    ad.open(1)
    lease = ad.acquire(0)
    lease.reset(task)
    out["score_before"] = lease.evaluate()
    lease.step("import os; os.rename(os.path.expanduser('~/Desktop/todo_list_Jan_1'), os.path.expanduser('~/Desktop/todo_list_Jan_2'))")
    out["score_after"] = lease.evaluate()
    pid = next(iter(sup._procs.values())).pid
    out["sessions_were_restored"] = bool(sup._restored)
    out["reseeded"] = bool(sup.restore_reseed) and bool(json.load(open(os.path.join(sup._dir(next(iter(sup._restored))), "session.json"))).get("reseed"))
    t = subprocess.run(["nsenter", "-t", str(pid), "-a", "date", "+%s"], capture_output=True, text=True).stdout.strip()
    out["session_clock_minus_host_s"] = int(t) - time.time()
    ad.close()
    pool.shutdown()
except BaseException as e:
    import traceback
    out["error"] = f"{type(e).__name__}: {str(e)[-400:]}"
    out["trace"] = traceback.format_exc()[-1200:]
finally:
    sup.close()
print("RESULT " + json.dumps(out))
