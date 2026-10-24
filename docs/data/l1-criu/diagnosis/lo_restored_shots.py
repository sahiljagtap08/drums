"""Capture actual screenshots of a LibreOffice session RESTORED from a post-setup checkpoint: as restored (the first-run dialog is part of the preserved state),
and after the corrected T3 steps (dismiss the dialog with OK, click the document, type a token). Diagnosis evidence for the T3 harness failure in run 1."""
import json, os, sys, tempfile, time
sys.path.insert(0, "/home/user/drums/envd-linux")
from envd_linux.l1proto.criu_probe import import_desktop_env, make_sup, screenshot
from envd_linux.l1proto.criu_task_probe import writer_title, clipboard
from envd_linux.criu import Criu
de = import_desktop_env()
from desktop_env.controllers.setup import SetupController
from desktop_env.controllers.python import PythonController
out = sys.argv[1]
task = json.load(open(sys.argv[2]))
tmp = tempfile.mkdtemp(prefix="loshots"); sup = make_sup(tmp, "t", "", 90, Criu())
try:
    a = sup.create(0)
    SetupController(a["ip"], server_port=5000, cache_dir=tmp + "/c", screen_width=1280, screen_height=800).setup(task["config"], False)
    time.sleep(4)
    sup.restore_from = sup.checkpoint(0, tmp + "/ck", clean=False)
    b = sup.create(1)
    time.sleep(1)
    open(os.path.join(out, "restored_as_restored_dialog_present.png"), "wb").write(screenshot(b["ip"]))
    PythonController(b["ip"], 5000).execute_python_command("import pyautogui, time; pyautogui.click(825, 489); time.sleep(0.5); pyautogui.click(640, 600); pyautogui.write('TOKENSHOT123', interval=0.02); pyautogui.hotkey('ctrl','a'); pyautogui.hotkey('ctrl','c')")
    time.sleep(1.5)
    open(os.path.join(out, "restored_after_dialog_dismissed_and_typed.png"), "wb").write(screenshot(b["ip"]))
    print("title", writer_title(b), "clipboard", repr(clipboard(b)))
finally:
    sup.close()
