"""Mechanism K semantics and hygiene checks (M-K3; pre-registration in docs/l1-shared-runtime-design.md section 12).

In K2 mode (merge flag on, address randomisation off): two real sessions with the real OSWorld guest server; for each, the guest contract
checks and the real-server checks (accessibility route, real pyautogui); isolation hygiene (separate PID, mount and network namespaces,
one Xvfb each); address randomisation really off (the same executable is mapped at the same base address in both sessions); and one public
OSWorld task still scores 0.0 then 1.0 through the real `desktop_env` (run as a subprocess in the OSWorld venv).

    python -m envd_linux.l1proto.ksm_semantics --out ksm_semantics.json --osw-python PY --osw-src SRC"""

import argparse
import json
import os
import subprocess
import tempfile
import time

from ..scalecua.client import GuestClient
from ..scalecua.smoke import check_guest, check_real_server
from ..session import Supervisor
from ..template import build_home_template
from ..x11 import pidns_members
from .ksm_bench import KNOBS, KSM

KIT = "/opt/drums-osworld-real"
TASK = "evaluation_examples/examples/os/e0df059f-28a6-4169-924f-b9623e7184cc.json"


def first_map_base(pid_list, name_contains):
    for p in pid_list:
        try:
            if name_contains in open(f"/proc/{p}/comm").read():
                line = open(f"/proc/{p}/maps").readline()
                return line.split("-")[0], line.split()[-1]
        except OSError:
            continue
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--osw-python", required=True)
    ap.add_argument("--osw-src", required=True)
    a = ap.parse_args(argv)
    tmp = tempfile.mkdtemp(prefix="l1ksmsem")
    tpl = os.path.join(tmp, "tpl")
    build_home_template(os.path.join(tpl, "home", "drums"))
    py = os.path.join(KIT, "venv", "bin", "python")
    sup = Supervisor(os.path.join(tmp, "state"), tpl, root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=KIT,
                     init_python=py, server_mode="module", workload="chromium", ready_timeout=120, ksm=True, no_aslr=True)
    res = {}
    try:
        for k, v in KNOBS.items():
            if os.path.exists(f"{KSM}/{k}"):
                sup.set_host_knob(f"{KSM}/{k}", v)
        infos = [sup.create(0), sup.create(1)]
        time.sleep(3)
        clients = [GuestClient(i["ip"]) for i in infos]
        res["guest_contract_failures"] = [check_guest(c) for c in clients]
        res["real_server_failures"] = [check_real_server(c) for c in clients]
        pids = [i["init_pid"] for i in infos]
        members = [set(pidns_members(p)) for p in pids]
        res["pid_ns_distinct"] = os.readlink(f"/proc/{pids[0]}/ns/pid") != os.readlink(f"/proc/{pids[1]}/ns/pid")
        res["mnt_ns_distinct"] = os.readlink(f"/proc/{pids[0]}/ns/mnt") != os.readlink(f"/proc/{pids[1]}/ns/mnt")
        res["net_ns_distinct"] = os.readlink(f"/proc/{pids[0]}/ns/net") != os.readlink(f"/proc/{pids[1]}/ns/net")
        res["pid_sets_disjoint"] = not (members[0] & members[1])
        res["xvfb_per_session"] = [sum(1 for m in mm if open(f"/proc/{m}/comm").read().strip() == "Xvfb") for mm in members]
        bases = [first_map_base(sorted(mm), "Xvfb") for mm in members]
        res["xvfb_first_mapping_per_session"] = bases
        res["aslr_off_same_base_in_both_sessions"] = bool(bases[0] and bases[0] == bases[1])
        chrome = [first_map_base(sorted(mm), "chrome") for mm in members]
        res["chrome_first_mapping_per_session"] = chrome
        res["personality"] = [open(f"/proc/{p}/personality").read().strip() for p in pids]
    finally:
        for s in (0, 1):
            sup.destroy(s)
        sup.close()
    # one public task through the real DesktopEnv, in K2 mode, with the same host knobs
    sup2 = Supervisor(os.path.join(tmp, "state2"), tpl, root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=KIT,
                      init_python=py, server_mode="module", ready_timeout=120)
    try:
        for k, v in KNOBS.items():
            if os.path.exists(f"{KSM}/{k}"):
                sup2.set_host_knob(f"{KSM}/{k}", v)
        here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        driver = os.path.join(here, "tests", "helpers", "osworld_task_driver.py")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([a.osw_src, here]), DRUMS_SUPERVISOR_EXTRA=json.dumps({"ksm": True, "no_aslr": True}))
        env.pop("OSWORLD_RECORDING_UPDATE", None)
        r = subprocess.run([a.osw_python, driver, KIT, os.path.join(a.osw_src, TASK)], capture_output=True, text=True, env=env,
                           timeout=300, cwd="/tmp")
        line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
        res["public_task_in_K2"] = json.loads(line[-1][7:]) if line else {"error": "no output", "stderr": r.stderr[-300:]}
    finally:
        sup2.close()
    res["PASS"] = (not any(res["guest_contract_failures"]) and not any(res["real_server_failures"]) and res["pid_ns_distinct"]
                   and res["mnt_ns_distinct"] and res["net_ns_distinct"] and res["pid_sets_disjoint"] and res["xvfb_per_session"] == [1, 1]
                   and res["aslr_off_same_base_in_both_sessions"] and res["public_task_in_K2"].get("score_before_acting") == 0.0
                   and res["public_task_in_K2"].get("score_after_acting") == 1.0)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
