"""Helper for test_criu_orphan_cleanup: a Supervisor that makes a cold friendly session, checkpoints it and restores two siblings, prints what it
made, then waits to be killed with SIGKILL (so nothing in it ever gets to clean up)."""

import json
import os
import sys
import time

from envd_linux.criu import Criu
from envd_linux.session import Supervisor
from envd_linux.template import build_home_template

state, ledger, kit = sys.argv[1:4]
tpl = os.path.join(state, "tpl")
build_home_template(os.path.join(tpl, "home", "drums"))
sup = Supervisor(os.path.join(state, "state"), tpl, root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=kit,
                 init_python=os.path.join(kit, "venv", "bin", "python"), server_mode="module", workload="chromium", ready_timeout=120,
                 criu_friendly=True, criu=Criu(), sweep_dir=ledger)
sup.create(0)
time.sleep(1)
ck = sup.checkpoint(0, os.path.join(state, "ck"))
sup.restore_from = ck
infos = [sup.create(1), sup.create(2)]
sup.restore_from = None
infos.append(sup.create(3))  # and one COLD friendly session left running, so both kinds of orphan are covered
print("READY " + json.dumps({"run_id": sup.run_id, "tag": sup.tag, "table": sup.table, "state_dir": sup.state_dir,
                             "init_pids": [i["init_pid"] for i in infos], "pid": os.getpid()}), flush=True)
time.sleep(3600)
