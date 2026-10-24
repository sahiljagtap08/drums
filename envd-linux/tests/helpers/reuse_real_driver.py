"""Real-session validation of the snapshot-API bridge (docs/l1-shared-runtime-design.md sections 20 and 21), run in the OSWorld venv. PROTOTYPE.

V1: setup-only reuse through the matched runner on the REAL public `DesktopEnv` and REAL Drums sessions (public task e0df059f), with and without reuse.
V2: a mid-task branch: three siblings continue from one saved state and the REAL local evaluator scores them differently.
Prints one RESULT line. Task semantics validated: that one task only."""

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
from envd_linux import clone, realkit  # noqa: E402
from envd_linux.criu import Criu  # noqa: E402
from envd_linux.l1proto import criu_probe as cp  # noqa: E402
from envd_linux.scalecua import osworld_provider as op  # noqa: E402
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
                 init_python=os.path.join(kit, "venv", "bin", "python"), server_mode="module", ready_timeout=120,
                 criu_friendly=True, criu=Criu(), sweep_dir=os.path.join(tmp, "led"))
pool = DrumsSessionPool(sup, 4, health_timeout=90, eager_reset=False)
RENAME = "import os; os.rename(os.path.expanduser('~/Desktop/todo_list_Jan_1'), os.path.expanduser('~/Desktop/todo_list_Jan_2'))"
WRONG = "import os; os.rename(os.path.expanduser('~/Desktop/todo_list_Jan_1'), os.path.expanduser('~/Desktop/todo_list_Jan_3'))"
PRICE = om.Prices(host_usd_per_hour=3600.0, label="harness validation placeholder, not a price")
out = {"harness_validation_only": True, "task": task["id"]}


class PreEvalAgent(om.ScriptedAgent):
    """Records the real evaluator's score BEFORE the agent acts (0.0 expected), then plays the scripted action."""
    befores = []

    def __init__(self, adapter):
        super().__init__({task["id"]: [RENAME]})
        self.adapter = adapter

    def start(self, task_config, obs):
        super().start(task_config, obs)
        PreEvalAgent.befores.append(self.adapter.envs[0].evaluate())


def v1(reuse):
    PreEvalAgent.befores = []
    ad = om.DesktopEnvAdapter(f"drums-{reuse}", om.drums_factory(pool, screen_size=(1280, 800), desktop_env_module=de, cache_dir=os.path.join(tmp, "cache"),
                                                                 require_a11y_tree=False), reuse=reuse)
    seq = om.sample_tasks([task["id"]], 6, seed=1)
    res = om.run_arm(ad, {task["id"]: task}, seq, lambda: PreEvalAgent(ad), 1, PRICE, max_steps=3)
    s = res["summary"]
    return {"valid": s["valid"], "faults": s["faults"], "scores_after": [r["score"] for r in res["rows"]], "scores_before": list(PreEvalAgent.befores),
            "phases": [r["reuse_phase"] for r in res["rows"]], "reset_s": [round(r["reset_s"], 3) for r in res["rows"]], "wall_s": round(s["wall_s"], 3),
            "interference": s["interference_count"], "canary_clean": s["canary_clean"], "reuse": s["reuse"]}


def sh(env, script):
    return env.controller.run_bash_script(script, timeout=30).get("output", "")


def make_env(prov):
    return de.DesktopEnv(provider_name="docker", region=op.REGION, action_space="pyautogui", screen_size=(1280, 800),
                         cache_dir=os.path.join(tmp, "cache"), require_a11y_tree=False)


def v2():
    prov = op.install(pool)
    ev = {}
    env1 = make_env(prov)
    l1 = om._DesktopEnvLease(env1)
    l1.reset(task)
    token = os.urandom(6).hex()
    env1.step("import os, subprocess; open(os.path.expanduser('~/Desktop/branch_prefix.txt'), 'w').write('%s'); "
              "code = 'import random,signal,time,os\\nrandom.seed(4242)\\n"
              "def h(*a):\\n open(\"/tmp/agent_draw.txt\",\"w\").write(repr(random.random()))\\n"
              "signal.signal(signal.SIGUSR1,h)\\nopen(\"/tmp/agent_proc.pid\",\"w\").write(str(os.getpid()))\\nwhile True: time.sleep(0.2)'; "
              "subprocess.Popen(['python3','-c',code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)" % token)
    time.sleep(1.5)
    ev["score_at_branch_point"] = env1.evaluate()
    t0 = time.time()
    l1.save_branch("mid")  # A continues in place from the restored state
    ev["save_branch_s"] = time.time() - t0
    env2 = make_env(prov)
    l2 = om._DesktopEnvLease(env2)
    t0 = time.time()
    l2.restore_branch("mid", task)  # B
    ev["restore_branch_s"] = time.time() - t0

    def info(env, slot_key):
        sid, _ = prov._sessions[env.path_to_vm]
        i = dict(pool.session_info(sid))
        return i

    ia, ib = info(env1, "a"), info(env2, "b")

    def observe(env, i):
        draw = sh(env, "kill -USR1 $(cat /tmp/agent_proc.pid); sleep 0.5; cat /tmp/agent_draw.txt")
        return {"prefix_file": sh(env, "cat ~/Desktop/branch_prefix.txt").strip(), "agent_proc_alive": "agent_proc" in sh(env, "ls /tmp") and sh(env, "kill -0 $(cat /tmp/agent_proc.pid) && echo alive").strip() == "alive",
                "agent_next_draw": draw.strip(), "desktop": sorted(sh(env, "ls ~/Desktop").split()),
                "hostname": subprocess.run(["nsenter", "-t", str(i["init_pid"]), "-u", "hostname"], capture_output=True, text=True).stdout.strip(),
                "server_draw": clone.rng_probe(i["init_pid"])[0]["python_random"]}
    oa, ob = observe(env1, ia), observe(env2, ib)
    ev["token"] = token
    ev["a"], ev["b"] = oa, ob
    ev["i_task_state_preserved"] = (oa["prefix_file"] == ob["prefix_file"] == token and oa["agent_proc_alive"] and ob["agent_proc_alive"]
                                    and oa["agent_next_draw"] == ob["agent_next_draw"] and oa["agent_next_draw"] != "")
    ev["ii_infra_reseeded"] = oa["hostname"] != ob["hostname"] and oa["server_draw"] != ob["server_draw"]
    ev["iv_b_before_acting"] = {"has_jan1": "todo_list_Jan_1" in ob["desktop"], "has_jan2": "todo_list_Jan_2" in ob["desktop"]}
    ev["score_before_a"], ev["score_before_b"] = env1.evaluate(), env2.evaluate()
    # (v) sharing audit under revision 2a with a cold control (two cold sessions outside the pool)
    sa, sb = sup.info(next(k for k, v in sup._restored.items() if v and pool.session_info(prov._sessions[env1.path_to_vm][0])["slot"] == k)), None
    ia2 = dict(ia, display=sa["display"])
    slot_b = pool.session_info(prov._sessions[env2.path_to_vm][0])["slot"]
    ib2 = dict(ib, display=sup.info(slot_b)["display"])
    aud = cp.audit_pair(ia2, ib2)
    ca, cb = sup.create(10, from_checkpoint=None), sup.create(11, from_checkpoint=None)
    time.sleep(1)
    ctl = cp.audit_pair(ca, cb)
    sup.destroy(10)
    sup.destroy(11)
    dev = lambda ks: {k.split(":", 3)[3] for k in ks}
    ev["v_audit"] = {"PASS_revision_2a": bool(aud["PASS_ignoring_devices"] and dev(aud["shared_character_devices"]) <= {"/dev/null", "/dev/urandom"}
                                                 and set(aud["shared_character_devices"]) <= set(ctl["shared_character_devices"])),
                     "shared_devices": aud["shared_character_devices"], "control_devices": ctl["shared_character_devices"],
                     "shared_locks": aud["shared_lock_entries"], "shared_pipe_or_socket": aud["shared_pipe_or_socket_inodes"],
                     "write_visible_across": aud["write_in_a_visible_in_b"]}
    # divergent continuations under the real evaluator
    env1.step(RENAME)
    env2.step(WRONG)
    ev["score_a_correct"], ev["score_b_wrong"] = env1.evaluate(), env2.evaluate()
    ev["iv_b_after_a_acted"] = sorted(sh(env2, "ls ~/Desktop").split())
    # C: revert B to the saved state again, repeat A's correct continuation
    l2.restore_branch("mid", task)
    ev["c_desktop_after_restore"] = sorted(sh(env2, "ls ~/Desktop").split())
    env2.step(RENAME)
    ev["score_c_correct"] = env2.evaluate()
    ev["PASS_iii_divergent"] = (ev["score_a_correct"], ev["score_b_wrong"], ev["score_c_correct"]) == (1.0, 0.0, 1.0)
    for e in (env1, env2):
        e.close()
    return ev


try:
    out["v1_none"] = v1("none")
    out["v1_post_setup"] = v1("post_setup")
    out["v2"] = v2()
except BaseException as e:
    import traceback
    out["error"] = f"{type(e).__name__}: {str(e)[-500:]}"
    out["trace"] = traceback.format_exc()[-1800:]
finally:
    try:
        pool.shutdown()
    except Exception:
        pass
    sup.close()
print("RESULT " + json.dumps(out, default=str))
