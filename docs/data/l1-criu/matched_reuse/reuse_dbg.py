import json, os, sys, tempfile, time
sys.path.insert(0, "/home/user/drums/envd-linux/tests/helpers")
sys.argv = ["x", "/tmp/claude-0/scratch/osw-main/evaluation_examples/examples/os/e0df059f-28a6-4169-924f-b9623e7184cc.json", tempfile.mkdtemp(prefix="rdbg")]
src = open("/home/user/drums/envd-linux/tests/helpers/reuse_real_driver.py").read().split("try:\n    out[\"v1_none\"]")[0]
exec(compile(src, "driver", "exec"))
try:
    prov = op.install(pool)
    env = make_env(prov)
    l = om._DesktopEnvLease(env)
    print("reset", flush=True); l.reset(task)
    print("ls after setup:", sh(env, "ls -la ~/Desktop; echo HOME=$HOME; id -un"), flush=True)
    env.step("import os; print(1)")
    print("pre-save python:", sh(env, "which python; python -c 'print(1+1)'; echo PATH=$PATH"), flush=True)
    print("pre-save exec:", env.controller.execute_python_command("print(40+2)"), flush=True)
    print("saving", flush=True); l.save_branch("mid")
    print("post-save python:", sh(env, "which python; python -c 'print(1+1)'; echo PATH=$PATH"), flush=True)
    print("post-save exec:", env.controller.execute_python_command("print(40+2)"), flush=True)
    print("post-save exec rename:", env.controller.execute_python_command(RENAME), flush=True)
    print("ls after save/restore:", sh(env, "ls -la ~/Desktop; echo HOME=$HOME; id -un"), flush=True)
    print("score", env.evaluate(), flush=True)
    r = env.step(RENAME)
    time.sleep(1)
    print("ls after rename:", sh(env, "ls -la ~/Desktop"), "score", env.evaluate(), flush=True)
finally:
    pool.shutdown(); sup.close()
