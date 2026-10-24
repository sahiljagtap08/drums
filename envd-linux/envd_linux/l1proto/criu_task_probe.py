"""Mechanism C, section 18 of docs/l1-shared-runtime-design.md: branch the post-setup state of ONE real public task setup (PROTOTYPE).

The setup is the public task e1fc0df3's `launch libreoffice --writer`, run by OSWorld's real SetupController against the Drums guest server. The task's own
evaluation needs the internet and is NOT run: this tests branching of an application-launch state, nothing about that task's semantics.

    PYTHONPATH=<OSWorld checkout>:<repo>/envd-linux:<repo>/compare DRUMS_CRIU_HOME=<private criu dir> \\
        <osw venv python> -m envd_linux.l1proto.criu_task_probe --task <e1fc0df3...json> --out task_probe.json"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

from .bench import bootstrap_diff, log, ols_slope, pct, used_kib
from .criu_probe import audit_pair, changed_fraction, import_desktop_env, make_sup, screenshot, stage
from ..criu import Criu

def _in_session(info, *cmd, timeout=15):
    """Run a host tool inside the session's mount namespace against the session's own X display."""
    r = subprocess.run(["nsenter", "-t", str(info["init_pid"]), "-m", "--", "env", f"DISPLAY=:{info['display']}", *cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout


def writer_title(info):
    """Title of the Writer window from `wmctrl -l` (the accessibility tree is not available for this LibreOffice build, see 18.5)."""
    for ln in _in_session(info, "wmctrl", "-l").splitlines():
        if "LibreOffice Writer" in ln:
            return ln.split(None, 3)[3].strip()
    return None


def clipboard(info):
    return _in_session(info, "xclip", "-selection", "clipboard", "-o")


def task_ready(info, blank_png, timeout=90):
    """Ready = the X window list holds the Writer window AND the screenshot differs from a blank desktop in more than 10% of pixels."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if writer_title(info) and changed_fraction(screenshot(info["ip"]), blank_png) > 0.10:
                return time.time() - t0
        except Exception:
            pass
        time.sleep(0.05)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--starts", type=int, default=10)
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--max-n", type=int, default=12)
    ap.add_argument("--skip-memory", action="store_true")
    a = ap.parse_args(argv)
    de = import_desktop_env()  # stubs the optional heavy imports this venv lacks
    from desktop_env.controllers.python import PythonController
    from desktop_env.controllers.setup import SetupController
    task = json.load(open(a.task))
    criu = Criu()
    tmp = tempfile.mkdtemp(prefix="l1task")
    rec = {"host": {"kernel": os.uname().release, "cpus": os.cpu_count()}, "criu": criu.version(), "task": task["id"], "config": task["config"], "args": vars(a), "stages": {}}
    sup = make_sup(tmp, "t", "", 90, criu)  # server and desktop only: the application comes from the task's own setup
    st = {}

    def setup_into(info):
        ctl = SetupController(info["ip"], server_port=5000, cache_dir=os.path.join(tmp, "cache"), screen_width=1280, screen_height=800)
        return ctl.setup(task["config"], False)

    def cold_with_setup(slot):
        info = sup.create(slot)
        ok = setup_into(info)
        return info, ok

    def py(info, code):
        return PythonController(info["ip"], 5000).execute_python_command(code)

    try:
        def s_blank():
            sup.restore_from = None
            info = sup.create(9)
            st["blank"] = screenshot(info["ip"])
            sup.destroy(9)
        stage(rec, "baseline_blank_desktop", s_blank)

        def s_checkpoint():
            sup.restore_from = None
            info, ok = cold_with_setup(0)
            if ok is False:
                raise RuntimeError("the real SetupController reported the setup failed")
            w = task_ready(info, st["blank"])
            if w is None:
                raise RuntimeError("Writer never became ready in the cold session")
            time.sleep(2)
            st["title"] = writer_title(info)
            st["ck"] = sup.checkpoint(0, os.path.join(tmp, "ck"), clean=False)  # NOT clean: the task's state must be preserved, not reset
            return {"title_at_checkpoint": st["title"], "dump_seconds": st["ck"].meta()["dump_seconds"], "image_mib": st["ck"].meta()["image_bytes"] / 2**20}
        ok = stage(rec, "T1_checkpoint_libreoffice", s_checkpoint)

        if ok:
            sup.restore_from = st["ck"]
            live = {}

            def s_restore():
                for s in (1, 2):
                    live[s] = sup.create(s)
                w = [task_ready(live[s], st["blank"]) for s in (1, 2)]
                return {"restore_total_s": [live[s]["restore_total_s"] for s in (1, 2)], "ready_extra_s": w, "reseed": live[1].get("reseed", {}).get("browser")}
            ok_r = stage(rec, "T1b_restore_two_siblings", s_restore)

            if ok_r:
                def s_state():
                    t = [writer_title(live[s]) for s in (1, 2)]
                    return {"titles": t, "PASS": all(x == st["title"] and x for x in t)}
                stage(rec, "T2_state_preserved", s_state)

                def s_inapp():
                    toks = {1: f"TOKENA{os.urandom(3).hex()}", 2: f"TOKENB{os.urandom(3).hex()}"}
                    for s in (1, 2):
                        # LibreOffice's first-run "Tip of the Day" dialog is part of the preserved state and swallows keystrokes (found in run 1): dismiss it with OK first
                        py(live[s], f"import pyautogui, time; pyautogui.click(825, 489); time.sleep(0.5); pyautogui.click(640, 600); pyautogui.write('{toks[s]}', interval=0.02); pyautogui.hotkey('ctrl','a'); pyautogui.hotkey('ctrl','c')")
                    time.sleep(1.5)
                    clip = {s: clipboard(live[s]) for s in (1, 2)}
                    seen = {"own_a": toks[1] in clip[1], "own_b": toks[2] in clip[2], "a_in_b": toks[1] in clip[2], "b_in_a": toks[2] in clip[1]}
                    return {**seen, "PASS": seen["own_a"] and seen["own_b"] and not seen["a_in_b"] and not seen["b_in_a"]}
                stage(rec, "T3_in_app_state_works_and_is_private", s_inapp)

                def s_audit():
                    r = audit_pair(live[1], live[2])
                    sup.restore_from = None
                    c_a, c_b = cold_with_setup(3)[0], cold_with_setup(4)[0]
                    time.sleep(1)
                    ctl = audit_pair(c_a, c_b)
                    sup.destroy(3)
                    sup.destroy(4)
                    sup.restore_from = st["ck"]
                    dev = lambda ks: {k.split(":", 3)[3] for k in ks}
                    r["cold_pair_control_devices"] = ctl["shared_character_devices"]
                    r["PASS_revision_2a"] = bool(r["PASS_ignoring_devices"] and dev(r["shared_character_devices"]) <= {"/dev/null", "/dev/urandom"}
                                                 and set(r["shared_character_devices"]) <= set(ctl["shared_character_devices"]))
                    return r
                stage(rec, "T4_sharing_audit_revision_2a", s_audit)
            for s in list(live):
                sup.destroy(s)

            # ---- T5 latency: cold session + real setup vs restore of the post-setup checkpoint
            lat = {"SETUP": [], "BRANCH": []}
            faults = []
            for rep in range(a.starts):
                for arm in (["SETUP", "BRANCH"] if rep % 2 == 0 else ["BRANCH", "SETUP"]):
                    t0 = time.time()
                    slot = 0 if arm == "SETUP" else 1
                    try:
                        if arm == "SETUP":
                            sup.restore_from = None
                            info, ok2 = cold_with_setup(slot)
                        else:
                            sup.restore_from = st["ck"]
                            info = sup.create(slot)
                        w = task_ready(info, st["blank"])
                        if w is None:
                            faults.append({"arm": arm, "rep": rep, "why": "not ready"})
                        else:
                            lat[arm].append(time.time() - t0)
                    except Exception as e:
                        faults.append({"arm": arm, "rep": rep, "why": str(e)[-200:]})
                    sup.destroy(slot)
                log(f"task latency rep {rep}: " + " ".join(f"{k}={v[-1]:.2f}" for k, v in lat.items() if v))
            rec["latency"] = {k: {"n": len(v), "p50": pct(v, .5), "p95": pct(v, .95), "mean": statistics.mean(v) if v else None, "all": v} for k, v in lat.items()}
            rec["latency_faults"] = faults
            if lat["SETUP"] and lat["BRANCH"]:
                rec["latency"]["ratio_setup_over_branch_p50"] = pct(lat["SETUP"], .5) / pct(lat["BRANCH"], .5)

            # ---- T6 memory per added session
            if not a.skip_memory:
                CP = tuple(n for n in (1, 4, 8, 12, 16, 20) if n <= a.max_n)
                mem = []
                for trial in range(a.trials):
                    for arm in (["COLD", "RESTORE"] if trial % 2 == 0 else ["RESTORE", "COLD"]):
                        time.sleep(5)
                        base, _ = used_kib()
                        pts = []
                        for n in range(1, a.max_n + 1):
                            if arm == "COLD":
                                sup.restore_from = None
                                cold_with_setup(n - 1)
                            else:
                                sup.restore_from = st["ck"]
                                sup.create(n - 1)
                            if n in CP:
                                time.sleep(5)
                                now, _ = used_kib()
                                pts.append({"n": n, "delta_kib": now - base})
                        slope = ols_slope([p["n"] for p in pts], [p["delta_kib"] / 1024 for p in pts])
                        mem.append({"arm": arm, "trial": trial, "points": pts, "slope_mib_per_session": slope})
                        log(f"task memory trial={trial} {arm} slope={slope:.1f} MiB/session")
                        for n in range(a.max_n):
                            sup.destroy(n)
                rec["memory"] = mem
                sl = {arm: [m["slope_mib_per_session"] for m in mem if m["arm"] == arm] for arm in ("COLD", "RESTORE")}
                rec["memory_summary"] = {arm: statistics.mean(v) for arm, v in sl.items()}
                rec["memory_summary"]["restore_over_cold"] = statistics.mean(sl["RESTORE"]) / statistics.mean(sl["COLD"])
                rec["memory_summary"]["cold_minus_restore_ci95"] = list(bootstrap_diff(sl["COLD"], sl["RESTORE"]))
    finally:
        for s in range(0, 40):
            try:
                if s in sup._procs:
                    sup.destroy(s)
            except Exception:
                pass
        sup.close()
        with open(a.out, "w") as f:
            json.dump(rec, f, indent=1, default=str)
        log("wrote", a.out)


if __name__ == "__main__":
    main()
