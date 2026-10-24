"""I3 driver smoke (docs/incumbent-preflight-plan.md): the first-check driver run against REAL Drums sessions on this host. PROTOTYPE, run in the OSWorld venv.

NOT a comparison: both arms are Drums (reuse "post_setup" against reuse "none"), there is no incumbent, one public task (e0df059f), one host, a scripted agent, placeholder
prices. It validates that the driver (alternating blocks, per-block adapters, canary, raw faults, external prep and host telemetry accounting) runs end to end on real sessions.
Supervisor / template / pool construction happens BEFORE the driver clock and is passed in as `external_prep`. Writes raw output under argv[2]; prints one RESULT line."""

import json
import os
import sys
import time
import traceback
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
from drums_compare import first_check as fc  # noqa: E402
from drums_compare import osworld_matched as om  # noqa: E402
from envd_linux import realkit  # noqa: E402
from envd_linux.criu import Criu  # noqa: E402
from envd_linux.scalecua.pool import DrumsSessionPool  # noqa: E402
from envd_linux.session import Supervisor  # noqa: E402
from envd_linux.template import build_home_template  # noqa: E402

task_file, out_dir = sys.argv[1], sys.argv[2]
blocks = int(sys.argv[3]) if len(sys.argv) > 3 else 2
os.makedirs(out_dir, exist_ok=True)
tmp = os.path.join(out_dir, "work")
os.makedirs(tmp, exist_ok=True)
task = json.load(open(task_file))
RENAME = "import os; os.rename(os.path.expanduser('~/Desktop/todo_list_Jan_1'), os.path.expanduser('~/Desktop/todo_list_Jan_2'))"
result = {"driver_smoke_only": True, "task": task["id"], "blocks": blocks}
sup = None
try:
    t0 = time.time()
    kit = realkit.DEFAULT_KIT
    tpl = os.path.join(tmp, "tpl")
    build_home_template(os.path.join(tpl, "home", "drums"))
    os.makedirs(os.path.join(tpl, "home", "drums", "Desktop"), exist_ok=True)
    sup = Supervisor(os.path.join(tmp, "state"), tpl, root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=kit,
                     init_python=os.path.join(kit, "venv", "bin", "python"), server_mode="module", ready_timeout=120,
                     criu_friendly=True, criu=Criu(), sweep_dir=os.path.join(tmp, "led"))
    pool = DrumsSessionPool(sup, 4, health_timeout=90, eager_reset=False)
    prep_s = time.time() - t0
    result["prep_s_before_driver_clock"] = prep_s
    arms = {
        "drums_post_setup": lambda: om.DesktopEnvAdapter("drums_post_setup", om.drums_factory(pool, desktop_env_module=de, cache_dir=os.path.join(tmp, "cache"),
                                                                                              require_a11y_tree=False), reuse="post_setup"),
        "drums_no_reuse": lambda: om.DesktopEnvAdapter("drums_no_reuse", om.drums_factory(pool, desktop_env_module=de, cache_dir=os.path.join(tmp, "cache"),
                                                                                          require_a11y_tree=False), reuse="none"),
    }
    price = om.Prices(host_usd_per_hour=3600.0, label="driver smoke placeholder, not a price")
    seq = om.sample_tasks([task["id"]], 3, seed=1)
    pins = {"smoke": True, "task": task["id"], "sequence": seq, "composition_manifest": "docs/data/l1-criu/drums_composition_manifest.json",
            "prices": "placeholder 3600 USD/hour, so cost == seconds; not a price", "pool": "DrumsSessionPool(4, eager_reset=False)"}
    summary = fc.run_alternating(arms, {task["id"]: task}, seq, lambda: om.ScriptedAgent({task["id"]: [RENAME]}), blocks, {n: price for n in arms}, out_dir, pins,
                                 concurrency=1, max_steps=3, external_prep={n: prep_s for n in arms})
    result["summary"] = summary
except Exception as e:  # kept raw
    result["error"] = f"{type(e).__name__}: {e}"
    result["trace"] = traceback.format_exc()[-3000:]
finally:
    if sup is not None:
        try:
            sup.close()
        except Exception as e:
            result["close_error"] = f"{type(e).__name__}: {e}"
print("RESULT " + json.dumps(result, default=str))
