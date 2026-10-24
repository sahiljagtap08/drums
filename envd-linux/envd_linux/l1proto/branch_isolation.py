"""PROTOTYPE B isolation, targets, exposure, display and blast-radius measurements (M3 to M7 of docs/l1-shared-runtime-design.md
section 10). Needs root, the kit, Chromium, nft. Writes one JSON document; any cross-profile leak is reported, never patched over.

    python -m envd_linux.l1proto.branch_isolation --out branch_isolation.json"""

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time

from ..session import Supervisor
from ..template import build_home_template
from .branch import SharedBrowser, TestServer, WS, evaluate, http_json
from ..scalecua.client import GuestClient

KIT = "/opt/drums-osworld-real"
PORT = 9333
N = 20

WRITE_JS = """(async()=>{ const T=%(tag)s;
 document.cookie='c_'+T+'='+T+'; path=/; max-age=3600'; localStorage.setItem('ls_'+T,T);
 await new Promise(res=>{const r=indexedDB.open('d',1); r.onupgradeneeded=()=>r.result.createObjectStore('s');
   r.onsuccess=()=>{const tx=r.result.transaction('s','readwrite'); tx.objectStore('s').put(T,'k_'+T); tx.oncomplete=()=>res();}});
 const a=await (await fetch('/cached')).json(); const b=await (await fetch('/cached')).json(); return {a,b}; })()"""
READ_JS = """(async()=>{ const ck=document.cookie; const ls=Object.keys(localStorage);
 const idb=await new Promise(res=>{const r=indexedDB.open('d',1); r.onsuccess=()=>{const g=r.result.transaction('s').objectStore('s').getAllKeys();
   g.onsuccess=()=>res(g.result);}; r.onerror=()=>res('err');}); return {ck,ls,idb}; })()"""


def ws_url_for(sb, tid, host="127.0.0.1"):
    for t in sb.targets(host):
        if t["id"] == tid:
            return t["webSocketDebuggerUrl"]
    raise KeyError(tid)


def navigate(sb, tid, url, wait_js="document.readyState==='complete'"):
    ws = WS(ws_url_for(sb, tid))
    try:
        ws.call("Page.navigate", {"url": url})
    finally:
        ws.close()
    end = time.time() + 15
    while time.time() < end:
        try:
            if evaluate(ws_url_for(sb, tid), wait_js, 5):
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def profile_path(sb, tid):
    navigate(sb, tid, "chrome://version")
    txt = evaluate(ws_url_for(sb, tid), "document.body ? document.body.innerText : ''", 10) or ""
    m = re.search(r"Profile Path\s+(\S+)", txt)
    return m.group(1) if m else None


def responsive(sb, tid):
    try:
        return evaluate(ws_url_for(sb, tid), "1+1", 3) == 2
    except Exception:
        return False


INSIDE = r'''
import json, sys
from envd_linux.l1proto.branch import http_json, evaluate, WS
gw, port, sibling_title = sys.argv[1], int(sys.argv[2]), sys.argv[3]
out = {}
try:
    tl = http_json(gw, port, "/json/list", timeout=5)
    out["reachable"] = True
    out["targets_visible"] = len(tl)
    out["sibling_titles_visible"] = sorted(t["title"] for t in tl if t["type"] == "page")[:30]
    sib = [t for t in tl if t["title"] == sibling_title][0]
    ver = http_json(gw, port, "/json/version", timeout=5)
    ws = WS(ver["webSocketDebuggerUrl"], 10)
    info = ws.call("SystemInfo.getProcessInfo")
    out["process_info_entries"] = len(info.get("result", {}).get("processInfo", []))
    out["process_info_types"] = sorted({p["type"] for p in info.get("result", {}).get("processInfo", [])})
    tg = ws.call("Target.getTargets")["result"]["targetInfos"]
    ctx = [t.get("browserContextId") for t in tg if t.get("title") == sibling_title][0]
    ck = ws.call("Storage.getCookies", {"browserContextId": ctx})
    out["sibling_cookies_read_via_browser_endpoint"] = [c["name"] for c in ck.get("result", {}).get("cookies", [])]
    ws.close()
    out["sibling_secret_read"] = evaluate(sib["webSocketDebuggerUrl"], "window.__secret", 8)
    out["sibling_script_executed"] = evaluate(sib["webSocketDebuggerUrl"], "document.title='HIJACKED'; document.title", 8)
except Exception as e:
    out["error"] = type(e).__name__ + ": " + str(e)[:200]
print(json.dumps(out))
'''


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    tmp = tempfile.mkdtemp(prefix="l1iso")
    tpl = os.path.join(tmp, "tpl")
    build_home_template(os.path.join(tpl, "home", "drums"))
    py = os.path.join(KIT, "venv", "bin", "python")
    sup = Supervisor(os.path.join(tmp, "state"), tpl, root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=KIT,
                     init_python=py, server_mode="module", workload="", ready_timeout=120)
    sup.t_request = {}
    srv = TestServer()
    sb = SharedBrowser(os.path.join(tpl, "home", "drums", "profile"), os.path.join(tmp, "b"), port=PORT)
    res = {"n_profiles": N}
    try:
        sb.start()
        time.sleep(2)
        # ---- M6 prerequisite: two real sessions, screenshot before attaching
        infos = [sup.create(0), sup.create(1)]
        clients = [GuestClient(i["ip"]) for i in infos]
        before = [hashlib.sha256(c.screenshot()).hexdigest() for c in clients]
        # ---- attach 20 profiles
        names = [f"P{k}" for k in range(1, N + 1)]
        att = {n: sb.attach(n) for n in names}
        tids = {n: att[n]["target"] for n in names}
        assert all(tids.values()), "an attach did not produce a target"
        after = [hashlib.sha256(c.screenshot()).hexdigest() for c in clients]
        res["M6_display_and_observation"] = {
            "session_screenshot_unchanged_after_attach": before == after,
            "note": "the profile windows live in the shared host Xvfb, not in the session's own display",
            "windows_in_shared_xvfb": len(re.findall(r"^\s+0x", subprocess.run(
                ["xwininfo", "-display", sb.display, "-root", "-children"], capture_output=True, text=True).stdout, re.M))}
        # ---- M3 storage isolation (same origin, 20 profiles at once)
        origin = f"http://127.0.0.1:{srv.port}"
        for n in names:
            assert navigate(sb, tids[n], f"{origin}/index"), f"{n} did not load the origin"
        write = {}
        for n in names:
            write[n] = evaluate(ws_url_for(sb, tids[n]), WRITE_JS % {"tag": json.dumps(n)}, 20)
        reads, foreign = {}, []
        for n in names:
            r = evaluate(ws_url_for(sb, tids[n]), READ_JS, 20)
            reads[n] = r
            for field, expect in (("ck", f"c_{n}={n}"), ("ls", [f"ls_{n}"]), ("idb", [f"k_{n}"])):
                if r[field] != expect:
                    foreign.append({"profile": n, "store": field, "saw": r[field], "expected": expect})
        cached = [e for e in srv.log if e["path"].startswith("/cached")]
        cookie_leaks = [e for e in cached if len(re.findall(r"c_P\d+=", e["cookie"])) != 1]
        second_fetch_cached = sum(1 for n in names if write[n]["a"] == write[n]["b"])
        res["M3_storage_isolation"] = {
            "profiles": N, "stores_checked_per_profile": 3, "foreign_observations": foreign,
            "server_cached_requests": len(cached), "expected_if_isolated": N,
            "cross_profile_cache_hits": N - len(cached), "requests_with_foreign_cookie_header": len(cookie_leaks),
            "second_fetch_served_from_own_cache": second_fetch_cached,
            "PASS": not foreign and len(cached) == N and not cookie_leaks}
        # ---- M4 targets, contexts, profile mapping
        ver = http_json("127.0.0.1", PORT, "/json/version")
        bws = WS(ver["webSocketDebuggerUrl"], 10)
        tinfo = {t["targetId"]: t for t in bws.call("Target.getTargets")["result"]["targetInfos"]}
        ctxs = {n: tinfo[tids[n]].get("browserContextId") for n in names}
        paths = {n: profile_path(sb, tids[n]) for n in names}
        wrong = [n for n in names if not paths[n] or not paths[n].rstrip("/").endswith("/" + n)]
        res["M4_targets"] = {"distinct_target_ids": len(set(tids.values())), "distinct_browser_context_ids": len(set(ctxs.values())),
                             "profile_path_mismatches": wrong, "sample_profile_path": paths["P1"]}
        conc = {"attempts": 0, "correct": 0, "failures": []}
        for rep in range(3):
            out, lock = {}, threading.Lock()

            def go(n):
                nm = f"C{rep}x{n}"
                try:
                    r = sb.attach(nm)
                    with lock:
                        out[nm] = r["target"]
                except Exception as e:
                    with lock:
                        out[nm] = f"ERR {e}"
            ts = [threading.Thread(target=go, args=(n,)) for n in range(N)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            for nm, tid in out.items():
                conc["attempts"] += 1
                ok = False
                try:
                    p = profile_path(sb, tid) if tid and not str(tid).startswith("ERR") else None
                    ok = bool(p) and p.rstrip("/").endswith("/" + nm)
                except Exception as e:
                    conc["failures"].append(f"{nm}: {e}")
                conc["correct"] += ok
                if not ok:
                    conc["failures"].append(nm)
            for tid in out.values():
                if tid and not str(tid).startswith("ERR"):
                    sb.close_target(tid)
        res["M4_concurrent_attach"] = conc
        bws.close()
        # ---- M5 exposure from inside a session
        # P2 was left on chrome://version by the M4 check: restore its origin, title and a secret held only in that page's memory
        navigate(sb, tids["P2"], f"{origin}/index")
        evaluate(ws_url_for(sb, tids["P2"]), "window.__secret='SECRET-IN-P2'; document.title='ready-P2'; 1", 8)
        gw = infos[0]["gw"]
        pkg = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        def from_session():
            r = subprocess.run(["nsenter", "-t", str(infos[0]["init_pid"]), "-n", py, "-c", INSIDE, gw, str(PORT), "ready-P2"],
                               capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=pkg), timeout=60)
            try:
                return json.loads(r.stdout.strip().splitlines()[-1])
            except Exception:
                return {"error": "no output", "stderr": r.stderr[-300:]}
        listen = []
        for ln in open("/proc/net/tcp").read().splitlines()[1:]:
            f = ln.split()
            if f[3] == "0A" and int(f[1].split(":")[1], 16) == PORT:
                h = f[1].split(":")[0]
                listen.append(".".join(str(int(h[i:i + 2], 16)) for i in (6, 4, 2, 0)))
        # (a) as built: Chromium's remote-debugging-address=0.0.0.0 is requested; what does it actually bind?
        res["M5a_as_built"] = {"debug_port_bound_on": listen, "from_session_0": from_session()}
        # (b) the practical configuration: any session that is to drive ITS OWN profile needs the endpoint reachable, so a plain TCP
        #     forwarder on the session's gateway address exposes it (no per-session filtering exists in this prototype)
        fwd = subprocess.Popen(["socat", f"TCP-LISTEN:{PORT},bind={gw},fork,reuseaddr", f"TCP:127.0.0.1:{PORT}"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(0.5)
            inside = from_session()
            title_after = next((t["title"] for t in sb.targets() if t["id"] == tids["P2"]), None)
            inside["sibling_title_seen_by_host_after"] = title_after
            inside["sibling_was_modified_by_a_session"] = title_after == "HIJACKED"
            res["M5b_with_forwarder_on_the_gateway"] = inside
        finally:
            fwd.terminate()
            fwd.wait(5)
        # shared-by-design inventory and socket reuse
        res["M5_process_inventory"] = {k: len(v) for k, v in sb.process_types().items()}
        ports = {}
        for n in ("P3", "P4"):
            navigate(sb, tids[n], f"{origin}/index")
            evaluate(ws_url_for(sb, tids[n]), "(async()=>{for(let i=0;i<4;i++){await fetch('/ping?x='+i,{cache:'no-store'});} return 1})()", 15)
            ports[n] = sorted({e["client_port"] for e in srv.log if e["path"].startswith("/ping") and
                               e["t"] > time.time() - 30} - set())
        # attribute ports per profile by request order: P3 requests came first
        pings = [e for e in srv.log if e["path"].startswith("/ping")]
        p3, p4 = {e["client_port"] for e in pings[:4]}, {e["client_port"] for e in pings[4:8]}
        res["M5_socket_reuse"] = {"P3_client_ports": sorted(p3), "P4_client_ports": sorted(p4),
                                  "shared_connection_between_profiles": bool(p3 & p4)}
        # ---- M7 blast radius (last: destroys the browser)
        for n in ("P5", "P6", "P7", "P8"):
            navigate(sb, tids[n], f"{origin}/index")
        alive0 = {n: responsive(sb, tids[n]) for n in ("P5", "P6", "P7", "P8")}
        types = sb.process_types()
        blast = {"before": alive0}
        if types.get("renderer"):
            deaths = []
            attached = [n for n in names]
            for pid in types["renderer"][:8]:
                before_alive = {n: responsive(sb, tids[n]) for n in attached}
                os.kill(pid, signal.SIGKILL)
                time.sleep(1.5)
                after_alive = {n: responsive(sb, tids[n]) for n in attached}
                deaths.append(sum(1 for n in attached if before_alive[n] and not after_alive[n]))
            blast["profiles_lost_per_renderer_kill"] = deaths
            blast["after_renderer_kills_P5_to_P8_alive"] = {n: responsive(sb, tids[n]) for n in ("P5", "P6", "P7", "P8")}
        if types.get("gpu-process"):
            os.kill(types["gpu-process"][0], signal.SIGKILL)
            time.sleep(3)
            blast["after_killing_gpu_process"] = {n: responsive(sb, tids[n]) for n in ("P5", "P6", "P7", "P8")}
        os.kill(sb.proc.pid, signal.SIGKILL)
        time.sleep(2)
        try:
            http_json("127.0.0.1", PORT, "/json/version", timeout=2)
            blast["debug_endpoint_after_killing_browser"] = "still up"
        except Exception:
            blast["debug_endpoint_after_killing_browser"] = "down"
        res["M7_blast_radius"] = blast
    finally:
        for s in (0, 1):
            sup.destroy(s)
        sb.stop()
        srv.stop()
        sup.close()
    leaks = (res.get("M3_storage_isolation", {}).get("foreign_observations") or []) or \
        not res.get("M3_storage_isolation", {}).get("PASS", False)
    m5 = res.get("M5b_with_forwarder_on_the_gateway", {})
    channel = bool(m5.get("sibling_secret_read") or m5.get("sibling_script_executed") or m5.get("sibling_cookies_read_via_browser_endpoint"))
    res["VERDICT"] = {"storage_isolation_leak": bool(leaks), "shared_debug_channel_leak": channel,
                      "isolation_check": "FAIL" if (leaks or channel) else "PASS"}
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
