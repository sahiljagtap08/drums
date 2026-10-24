import json, os, sys, tempfile, time
sys.argv = ["x", "/tmp/claude-0/scratch/osw-main/evaluation_examples/examples/os/e0df059f-28a6-4169-924f-b9623e7184cc.json", tempfile.mkdtemp(prefix="rdec")]
src = open("/home/user/drums/envd-linux/tests/helpers/reuse_real_driver.py").read().split("try:\n    out[\"v1_none\"]")[0]
exec(compile(src, "driver", "exec"))
import desktop_env.controllers.setup as setup_mod
T = []
def wrap(obj, name, label):
    f = getattr(obj, name)
    def g(*a, **k):
        t = time.time()
        try: return f(*a, **k)
        finally: T.append((label, round(time.time() - t, 2)))
    setattr(obj, name, g)
try:
    prov = op.install(pool)
    wrap(prov, "start_emulator", "provider.start_emulator"); wrap(prov, "revert_to_snapshot", "provider.revert_to_snapshot")
    env = make_env(prov)
    wrap(env, "_start_emulator", "env._start_emulator"); wrap(env, "_get_obs", "env._get_obs"); wrap(env, "_set_task_info", "env._set_task_info")
    wrap(setup_mod.SetupController, "setup", "SetupController.setup"); wrap(setup_mod.SetupController, "_execute_setup", "  _execute_setup")
    wrap(setup_mod, "ensure_recording_server" if hasattr(setup_mod, "ensure_recording_server") else "SetupController", "x") if False else None
    for i in range(2):
        T.clear(); t = time.time(); env.reset(task_config=task); tot = round(time.time() - t, 2)
        env.step(RENAME)
        print(f"reset #{i+1} total {tot}s:", T, flush=True)
finally:
    pool.shutdown(); sup.close()
