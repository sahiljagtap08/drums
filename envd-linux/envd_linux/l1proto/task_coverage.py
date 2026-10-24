"""Task-setup dependency and coverage report from the PUBLIC task JSON plus the ACTUAL Drums rootfs capabilities. MEASURED from files and `which`; runs nothing, installs
nothing, downloads nothing.

    python -m envd_linux.l1proto.task_coverage <OSWorld checkout> --out task_coverage.json

Every task is classified by what its setup (`config`) and evaluator need, against what this host's rootfs (the Drums template root) actually provides. The result is a
set of tiers and the explicit reasons a task falls out of the smallest tier. It says nothing about whether a task WORKS: only e0df059f has been run on Drums. A task in the
smallest tier is a CANDIDATE for a local run, not a validated one."""

import argparse
import collections
import glob
import json
import os
import shutil

# launch/open commands the Drums rootfs would need; presence is checked with `which`, not assumed
APPS = {"google-chrome": "Chrome", "chromium": "Chromium", "socat": "socat", "code": "VS Code", "thunderbird": "Thunderbird", "gimp": "GIMP", "vlc": "VLC",
        "nautilus": "GNOME Files", "libreoffice": "LibreOffice", "soffice": "LibreOffice", "gnome-terminal": "GNOME Terminal"}
GNOME_HINTS = ("gsettings", "dconf", "gnome-", "nautilus", "xdg-", "gio ", "org.gnome")
GNOME_GETTERS = {"vm_wallpaper", "default_video_player", "shortcuts_on_desktop", "vm_screen_size", "vm_window_size"}
A11Y_GETTERS = {"accessibility_tree", "active_url_from_accessTree"}
NETWORK_GETTERS = {"cloud_file", "googledrive_file", "info_from_website", "pdf_from_url", "gotoRecreationPage_and_get_html_content", "page_info"}
BROWSER_GETTER_PREFIX = ("active_tab", "open_tabs", "chrome_", "bookmarks", "cookie_data", "history", "default_search_engine", "enable_", "new_startup_page", "profile_name",
                         "find_", "data_delete", "url_", "find_installed", "find_unpacked")


def _cmd(c):
    p = c.get("parameters", {})
    cmd = p.get("command", [])
    return cmd if isinstance(cmd, str) else " ".join(map(str, cmd))


def _getters(e):
    out = []
    for k in ("result", "expected"):
        v = e.get(k)
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, dict) and x.get("type"):
                out.append(x["type"])
    return out


def classify(d, have):
    cfg, e = d.get("config", []), d.get("evaluator", {})
    why = []
    types = {c["type"] for c in cfg}
    cmds = [_cmd(c) for c in cfg if c["type"] in ("launch", "command", "execute", "open")]
    first = [next((t for t in c.split() if "=" not in t), "") for c in cmds]  # skip VAR=value prefixes
    needs_apps = sorted({os.path.basename(f) for f in first if os.path.basename(f) in APPS})
    missing_apps = [a for a in needs_apps if a not in have]
    getters = set(_getters(e))
    funcs = e.get("func")
    funcs = funcs if isinstance(funcs, list) else [funcs]
    if "download" in types:
        why.append("setup downloads files (network; not done in this build)")
    if types & {"chrome_open_tabs", "chrome_close_tabs", "login", "googledrive", "update_browse_history"}:
        why.append("browser/login setup step")
    if "google-chrome" in needs_apps and "google-chrome" not in have:
        why.append("launches google-chrome (not on the Drums rootfs; it has Playwright Chromium)")
    for a in missing_apps:
        if a != "google-chrome":
            why.append(f"launches {a} (not on the Drums rootfs)")
    if any(h in c for c in cmds for h in GNOME_HINTS) or getters & GNOME_GETTERS:
        why.append("GNOME-dependent step or getter (the Drums arm has openbox, no GNOME)")
    if getters & A11Y_GETTERS:
        why.append("evaluator uses the accessibility tree")
    if getters & NETWORK_GETTERS:
        why.append("evaluator getter needs the network / cloud")
    if any(g.startswith(BROWSER_GETTER_PREFIX) for g in getters if g) and "chrome" in " ".join(d.get("related_apps", [])).lower():
        why.append("evaluator reads Chrome state (debug port, profile, tabs)")
    if "infeasible" in funcs:
        why.append("infeasible-task evaluator (scored on the agent's FAIL action, not on state)")
    if d.get("proxy"):
        why.append("task asks for a proxy")
    if d.get("fixed_ip"):
        why.append("task asks for a fixed IP")
    gdep = sorted(getters | {f for f in funcs if f})
    return {"id": d["id"], "related_apps": d.get("related_apps", []), "setup_types": sorted(types), "launch_apps": needs_apps, "getters": sorted(getters),
            "evaluator_funcs": sorted(f for f in funcs if f), "reasons_not_smallest_tier": why, "smallest_tier_candidate": not why, "dependencies": gdep}


def report(root, rootfs_which=shutil.which):
    have = {a for a in APPS if rootfs_which(a)}
    have |= {"chromium"} if os.path.exists("/opt/pw-browsers") else set()
    rows = []
    for f in sorted(glob.glob(os.path.join(root, "evaluation_examples", "examples", "*", "*.json"))):
        d = json.load(open(f))
        r = classify(d, have)
        r["domain"] = f.split(os.sep)[-2]
        rows.append(r)
    reasons = collections.Counter(x for r in rows for x in r["reasons_not_smallest_tier"])
    cand = [r for r in rows if r["smallest_tier_candidate"]]
    return {"tasks": len(rows), "rootfs_apps_present": sorted(have), "rootfs_apps_absent": sorted(set(APPS) - have),
            "smallest_tier_candidates": len(cand), "candidates_by_domain": dict(collections.Counter(r["domain"] for r in cand)),
            "candidates_by_evaluator": dict(collections.Counter(f for r in cand for f in r["evaluator_funcs"]).most_common()),
            "reason_counts": dict(reasons.most_common()), "validated_on_drums": ["e0df059f-28a6-4169-924f-b9623e7184cc"],
            "label": "Candidates only: coverage by static dependency. Only one task has been RUN on Drums. Heuristic classifier: a task can be wrongly kept or dropped; read rows.",
            "rows": rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out")
    a = ap.parse_args()
    out = report(a.root)
    if a.out:
        json.dump(out, open(a.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))
