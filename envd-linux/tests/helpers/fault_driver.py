"""Helper for test_criu_fault_windows (docs/l1-shared-runtime-design.md section 24): a Supervisor that kills ITSELF (SIGKILL) at a registered point of a
restore, checkpoint or teardown. Prints READY with what it made, ARMED just before the armed operation, and SURVIVED if the operation completed."""

import json
import os
import sys
import time

from envd_linux import clone
from envd_linux.criu import Criu
from envd_linux.session import Supervisor
from envd_linux.template import build_home_template

state, ledger, kit, op = sys.argv[1:5]
tpl = os.path.join(state, "tpl")
build_home_template(os.path.join(tpl, "home", "drums"))
sup = Supervisor(os.path.join(state, "state"), tpl, root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=kit,
                 init_python=os.path.join(kit, "venv", "bin", "python"), server_mode="module", ready_timeout=120,
                 criu_friendly=True, criu=Criu(), sweep_dir=ledger)
sup.create(0)
time.sleep(0.5)
c1 = sup.checkpoint(0, os.path.join(state, "ck1"), clean=True)
sup.create(3)  # a cold friendly session that stays alive, so every window also has an unrelated live session of the same run
if op in ("checkpoint", "destroy"):
    sup.create(1, from_checkpoint=c1)
print("READY " + json.dumps({"tag": sup.tag, "table": sup.table, "run_id": sup.run_id, "state_dir": sup.state_dir, "pid": os.getpid()}), flush=True)
sup.fault_armed = True
print("ARMED", flush=True)
if op == "restore":
    sup.create(1, from_checkpoint=c1)
elif op == "failed_restore":
    def boom(*a, **k):
        raise RuntimeError("induced reseed failure")
    clone.reseed = boom
    try:
        sup.create(1, from_checkpoint=c1)
    except RuntimeError:
        pass
elif op == "checkpoint":
    sup.checkpoint(1, os.path.join(state, "ck2"), clean=True)
elif op == "destroy":
    sup.destroy(1)
print("SURVIVED", flush=True)
time.sleep(3600)
