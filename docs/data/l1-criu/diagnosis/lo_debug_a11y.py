import json, os, sys, tempfile, time, re
sys.path.insert(0, "/home/user/drums/envd-linux")
from envd_linux.l1proto.criu_probe import import_desktop_env, make_sup, screenshot, changed_fraction
from envd_linux.l1proto.criu_task_probe import a11y
from envd_linux.criu import Criu
de = import_desktop_env()
from desktop_env.controllers.setup import SetupController
task = json.load(open("/tmp/claude-0/scratch/osw-main/evaluation_examples/examples/multi_apps/e1fc0df3-c8b9-4ee7-864c-d0b590d3aa56.json"))
tmp = tempfile.mkdtemp(prefix="lodbg"); sup = make_sup(tmp, "t", "", 90, Criu())
try:
    info = sup.create(0); blank = screenshot(info["ip"])
    ok = SetupController(info["ip"], server_port=5000, cache_dir=tmp+"/c", screen_width=1280, screen_height=800).setup(task["config"], False)
    print("setup returned", ok, flush=True)
    for i in range(12):
        time.sleep(2)
        t = a11y(info["ip"])
        names = re.findall(r'name="([^"]{0,60})"', t)
        print(i, len(t), [n for n in names if "Libre" in n or "Writer" in n or "Untitled" in n][:4], round(changed_fraction(screenshot(info["ip"]), blank), 3), flush=True)
finally:
    sup.close()
