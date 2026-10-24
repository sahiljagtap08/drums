import json, os, sys, tempfile, time
sys.path.insert(0, "/home/user/drums/envd-linux")
from envd_linux.l1proto.criu_probe import import_desktop_env, make_sup, screenshot, changed_fraction
from envd_linux.l1proto.criu_task_probe import writer_title, clipboard, _in_session
from envd_linux.criu import Criu
de = import_desktop_env()
from desktop_env.controllers.setup import SetupController
from desktop_env.controllers.python import PythonController
task = json.load(open("/tmp/claude-0/scratch/osw-main/evaluation_examples/examples/multi_apps/e1fc0df3-c8b9-4ee7-864c-d0b590d3aa56.json"))
tmp = tempfile.mkdtemp(prefix="lodbg2"); sup = make_sup(tmp, "t", "", 90, Criu())
try:
    info = sup.create(0)
    SetupController(info["ip"], server_port=5000, cache_dir=tmp+"/c", screen_width=1280, screen_height=800).setup(task["config"], False)
    time.sleep(5); print("title", writer_title(info))
    pc = PythonController(info["ip"], 5000)
    s0 = screenshot(info["ip"])
    r = pc.execute_python_command("import pyautogui; pyautogui.click(640, 400); pyautogui.write('HELLOTOKEN123', interval=0.02); pyautogui.hotkey('ctrl','a'); pyautogui.hotkey('ctrl','c')")
    print("EXEC", repr(r)[:300]); time.sleep(2)
    print("pixels changed after typing", round(changed_fraction(screenshot(info["ip"]), s0), 4))
    open("/tmp/claude-0/scratch/lo_after.png","wb").write(screenshot(info["ip"])); open("/tmp/claude-0/scratch/lo_before.png","wb").write(s0)
    print("clipboard repr", repr(clipboard(info)))
    print("xclip stderr:", _in_session(info, "sh", "-c", "xclip -selection clipboard -o 2>&1 | head -3"))
    print("targets:", _in_session(info, "sh", "-c", "xclip -selection clipboard -o -t TARGETS 2>&1 | head"))
    print("primary:", repr(_in_session(info, "sh", "-c", "xclip -selection primary -o 2>&1 | head -3")))
finally:
    sup.close()
