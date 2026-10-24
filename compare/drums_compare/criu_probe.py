"""Image-base-root CRIU probe: ONE finite run on the authorized host that finds out whether a GNOME session with a private bus on the image root can be dumped and restored as two siblings.
PROTOTYPE: never run live. Nothing here claims CRIU compatibility; the result file is the evidence, and a failure keeps the raw CRIU log lines (the first `Error` line is the concrete blocker).

Sequence: build the Supervisor (CRIU, image receipt) and start session 0; capture versions/parity and a screenshot; set up state that only a real restore can preserve (an in-memory token behind a
listening socket, a file marker, an OPEN Writer); checkpoint (dump); restore two siblings from it; per sibling recapture versions and a screenshot, compare, check the token, marker, Writer and GNOME
Shell are back, and check the bus (the SAME bus ID is expected from a clone and is declared; the sockets and mount namespaces must differ); run the canary gate between the siblings (its reset is a
fresh restore); record checkpoint/restore/reseed/hygiene clocks and state bytes separately. NOT in this probe: the official task's pre/correct/wrong evaluation (needs the DesktopEnv lease on a restored
slot): it is recorded as not run, with the reason, unless a `task_eval` hook is supplied.
    python -m drums_compare.criu_probe preflight --config CFG --out DIR [--scope config|host]
    python -m drums_compare.criu_probe run --config CFG --out DIR                                 # host scope preflight first"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request

from . import fullroot_gates as fg
from . import host_run as hr

LABEL = "ARCHITECTURAL_NOT_FAIR_COMPARISON"
PORT = 47653
TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")  # the holder's token is os.urandom(8).hex()
# Pre-registered BEFORE the first live run (this commit): a restored sibling's desktop must differ from the pre-dump screenshot in at most 5% of the document region, and a desktop with fewer than
# 50 distinct colours in that region is BLANK and fails, before or after.
MAX_CHANGED_FRACTION = 0.05
MIN_DISTINCT_COLORS = 50
REQUIRED = {"drums_desktop": "gnome", "drums_user": "user", "drums_uid": 1000, "drums_gid": 1000, "drums_home": "/home/user", "drums_system_bus": True, "drums_criu": True}


class GuestHTTP:
    """A session through its OSWorld server: /execute (shell or python) and /screenshot, at the slot's CURRENT address (a restored session may get a new one)."""

    def __init__(self, ip_fn, resetter=None, port=5000, http=None):
        self.ip_fn, self.resetter, self.port, self.http = ip_fn, resetter, port, http or self._http

    @staticmethod
    def _http(url, data=None, timeout=60):
        req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def url(self, path):
        return f"http://{self.ip_fn()}:{self.port}{path}"

    def run(self, script):
        body = json.loads(self.http(self.url("/execute"), {"command": script, "shell": True}))
        rc = body.get("returncode")
        if body.get("status") != "success" or isinstance(rc, bool) or not isinstance(rc, int):
            raise RuntimeError(f"/execute fault: {str(body)[:300]}")
        return {"stdout": body.get("output") or "", "stderr": body.get("error") or "", "returncode": body["returncode"]}

    def step(self, code):
        import shlex
        return self.run("python3 -c " + shlex.quote(code))

    def reset(self):
        return self.resetter()

    def screenshot(self):
        return self.http(self.url("/screenshot"))


def criu_log_tails(ck, n=60):
    """Raw CRIU log lines kept on failure: every line with `Error` and the last `n` lines of dump/restore logs."""
    out = {}
    for name in ("dump.log", "restore.log"):
        p = os.path.join(getattr(ck, "img", ""), name) if ck is not None else ""
        if p and os.path.exists(p):
            lines = open(p, errors="replace").read().splitlines()
            out[name] = {"errors": [l[:300] for l in lines if "Error" in l][:20], "tail": [l[:300] for l in lines[-n:]]}
    return out


def first_error_line(tails):
    for t in tails.values():
        if t["errors"]:
            return t["errors"][0]
    return None


def need_ok(r, what):
    """A probe command must have RUN and succeeded; a non-zero return code is a failure of the probe, not data."""
    if r.get("returncode") != 0:
        raise RuntimeError(f"{what} failed (rc {r.get('returncode')}): {str(r.get('stderr'))[:200]}")
    return r


def setup_state(g, token_port=PORT):
    """State a restore must bring back: a token held in memory behind a listening socket, a file marker and an open Writer. Every launch and write must succeed, the token must be the holder's 16-hex
    random value, and the marker is read back."""
    import uuid
    marker = uuid.uuid4().hex[:8]
    need_ok(g.step("import subprocess,sys; subprocess.Popen([sys.executable,'-c',%r], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)" % (hr.HOLDER % token_port)), "holder launch")
    need_ok(g.step("import subprocess; subprocess.Popen(['libreoffice','--writer','--norestore','--nologo'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)"), "Writer launch")
    need_ok(g.step("open('/tmp/probe_marker','w').write(%r)" % marker), "marker write")
    if need_ok(g.run("cat /tmp/probe_marker"), "marker read-back")["stdout"].strip() != marker:
        raise RuntimeError("the marker file did not read back")
    writer = []
    for _ in range(60):
        writer = hr.live_pids(lambda s: need_ok(g.run(s), "Writer scan")["stdout"], "soffice.bin")
        if writer:
            break
        time.sleep(1)
    time.sleep(1)
    tok, err = ask_token(g, token_port)
    if tok is None:
        raise RuntimeError(f"the token holder did not answer with a valid token: {err}")
    return {"marker": marker, "token": tok, "writer_pids": writer, "gnome_shell_pids": hr.live_pids(lambda s: need_ok(g.run(s), "GNOME scan")["stdout"], "gnome-shell")}


def ask_token(g, port=PORT):
    """(token, None) only for a run with return code 0 whose output is EXACTLY 16 lowercase hex characters; otherwise (None, why). An error text is never a token value."""
    try:
        r = g.run("python3 -c %r" % (hr.ASK % port))
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:150]}"
    out = (r.get("stdout") or "").strip()
    if r.get("returncode") != 0:
        return None, f"rc {r.get('returncode')}: {str(r.get('stderr'))[:150]}"
    if not TOKEN_RE.match(out):
        return None, f"not a 16-hex token: {out[:40]!r}"
    return out, None


class PilImages:
    """Screen comparison with PIL (the host venv has it). `fraction` is the share of the document region whose pixels changed; `colors` the number of distinct colours in it (a blank desktop has few)."""

    @staticmethod
    def fraction(a, b):
        region = hr.GUI_THRESHOLDS["region"]
        return hr.changed_pixels(a, b) / max(1, (region[2] - region[0]) * (region[3] - region[1]))

    @staticmethod
    def colors(png):
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(png)).convert("RGB").crop(hr.GUI_THRESHOLDS["region"])
        return len(im.getcolors(maxcolors=1 << 24) or [])


def compare_sibling(g, before, state, shot_before, token_port=PORT, images=None):
    """What a restored sibling must show, from the live session. Raw values are kept next to each verdict, and EVERY verdict is part of `pass`: the token (valid 16-hex and equal), the marker, the Writer
    (live AND the same PIDs as before the dump), GNOME Shell live, unchanged versions, a pixel change within MAX_CHANGED_FRACTION, and a desktop that is not blank. An unmeasurable screen fails."""
    images = images or PilImages
    live = lambda s: g.run(s)["stdout"]
    tok, tok_err = ask_token(g, token_port)
    after = {"token": tok, "token_error": tok_err, "marker": g.run("cat /tmp/probe_marker 2>/dev/null")["stdout"].strip(), "writer_pids": hr.live_pids(live, "soffice.bin"),
             "gnome_shell_pids": hr.live_pids(live, "gnome-shell")}
    cap = fg.capture_parity(g.run)
    diff = sorted(k for k in fg.PARITY_PROBES if k not in ("date_utc", "date_local", "system_bus_socket", "mnt_ns", "pid_ns") and (cap[k]["rc"], cap[k]["out"]) != (before[k]["rc"], before[k]["out"]))
    shot = g.screenshot()
    try:
        frac, colors_before, colors_after, why = images.fraction(shot_before, shot), images.colors(shot_before), images.colors(shot), None
    except Exception as e:  # PIL missing or an undecodable screenshot: unmeasured, and an unmeasured screen cannot pass
        frac, colors_before, colors_after, why = None, None, None, f"{type(e).__name__}: {str(e)[:120]}"
    res = {"same_token": tok is not None and tok == state["token"] and TOKEN_RE.match(state["token"] or "") is not None, "marker_restored": after["marker"] == state["marker"],
           "writer_live_after": bool(after["writer_pids"]), "writer_same_pids": bool(state["writer_pids"]) and after["writer_pids"] == state["writer_pids"],
           "gnome_shell_live_after": bool(after["gnome_shell_pids"]), "parity_fields_changed": diff, "screenshot_changed_pixel_fraction": frac, "screenshot_diff_note": why,
           "distinct_colors_before": colors_before, "distinct_colors_after": colors_after, "max_changed_fraction": MAX_CHANGED_FRACTION, "min_distinct_colors": MIN_DISTINCT_COLORS,
           "screenshot_sha256": hashlib.sha256(shot).hexdigest(), "raw_after": after, "capture": cap}
    res["screen_ok"] = (frac is not None and frac <= MAX_CHANGED_FRACTION and colors_after is not None and colors_after >= MIN_DISTINCT_COLORS and colors_before is not None
                        and colors_before >= MIN_DISTINCT_COLORS)
    res["pass"] = (res["same_token"] and res["marker_restored"] and res["writer_live_after"] and res["writer_same_pids"] and res["gnome_shell_live_after"] and not diff and res["screen_ok"])
    return res, shot


def run_probe(cfg, out, deps=None):
    d = deps or {}
    from . import stages
    work = cfg.get("work_dir", os.path.join(out, "work"))
    os.makedirs(work, exist_ok=True)
    clock = fg.PhaseClock()
    wall0 = time.monotonic()
    res = {"label": LABEL, "training_economics_claim": False, "criu_compatibility_claim": "NONE until this result file shows a restore", "gaps": fg.PARITY_GAPS, "cleanup_errors": [], "siblings": {},
           "task_eval": {"run": False, "why": "pre/correct/wrong needs the DesktopEnv lease on a restored slot; supply a task_eval hook or run fullroot_gates on a restored slot"}}
    sup, ck, guests = None, None, {}
    try:
        with clock.phase("cold_setup"):
            sup, pool, prep = (d.get("build_drums") or stages.build_drums)(cfg, work)
            info = sup.create(0)
        res["drums_prep_s"] = prep
        ip_of = lambda slot: (lambda: sup.info(slot)["ip"])
        make = d.get("guest") or (lambda slot, resetter=None: GuestHTTP(ip_of(slot), resetter))
        g0 = make(0)
        with clock.phase("parity_capture"):
            before = fg.capture_parity(g0.run)
            shot_before = g0.screenshot()
        res["before"] = {"capture": before, "screenshot_sha256": hashlib.sha256(shot_before).hexdigest()}
        open(os.path.join(out, "screenshot_before.png"), "wb").write(shot_before)
        with clock.phase("state_setup"):
            state = setup_state(g0)
        res["state_before"] = state
        if not (state["token"] and state["writer_pids"] and state["gnome_shell_pids"]):
            raise RuntimeError(f"the session did not reach the state the probe needs (token/Writer/GNOME Shell): {state}")
        t0 = time.monotonic()
        try:
            with clock.phase("checkpoint_save"):
                ck = sup.checkpoint(0, os.path.join(work, "ck"))
        except Exception:
            ck = ck or d.get("ck_for_logs")
            raise
        res["checkpoint"] = {"wall_s": time.monotonic() - t0, "meta": {k: v for k, v in ck.meta().items() if k in ("dump_seconds", "image_bytes", "layers", "meta_version", "base_root", "image_sha256")},
                             "layer_upper_bytes": fg.state_bytes(os.path.join(ck.path, "layer", "upper"))}
        for slot in (1, 2):
            t1 = time.monotonic()
            tr = time.monotonic()
            inf = sup.create(slot, from_checkpoint=ck)
            rs = float(inf.get("reseed_s") or 0.0)
            clock.add("restore", max(0.0, time.monotonic() - tr - rs))  # the reseed is inside the restore wall: split so that nothing is counted twice
            clock.add("reseed", rs)
            guests[slot] = make(slot, (lambda s=slot: (sup.destroy(s), sup.create(s, from_checkpoint=ck))))
            cmp_, shot = compare_sibling(guests[slot], before, state, shot_before, images=d.get("images"))
            open(os.path.join(out, f"screenshot_sibling{slot}.png"), "wb").write(shot)
            cmp_["restore_wall_s"] = time.monotonic() - t1
            cmp_["criu_restore_s"], cmp_["reseed_s"], cmp_["restore_total_s"] = inf.get("criu_restore_s"), inf.get("reseed_s"), inf.get("restore_total_s")
            res["siblings"][str(slot)] = cmp_
        caps = [res["siblings"]["1"]["capture"], res["siblings"]["2"]["capture"]]
        res["bus_problems"] = fg.bus_problems(caps, allow_same_id=True)
        res["bus_clone_note"] = "a CRIU clone carries the SAME dbus-daemon state, so both siblings report the same bus ID (declared, not a fault); the sockets and mount namespaces must differ"
        with clock.phase("two_session_gate"):
            res["canaries"] = fg.two_session_gate(guests[1], guests[2])  # B's reset is a fresh restore
        recap = fg.capture_parity(guests[2].run)  # the reset sibling is a NEW session: recapture its bus and namespaces
        res["after_reset_capture"] = {k: recap[k] for k in fg.BUS_REQUIRED + ("pid_ns",)}
        res["bus_problems_after_reset"] = fg.bus_problems([res["siblings"]["1"]["capture"], recap], allow_same_id=True)
        pre = res["siblings"]["2"]["capture"]
        if recap["mnt_ns"]["out"] == pre["mnt_ns"]["out"] or recap["pid_ns"]["out"] == pre["pid_ns"]["out"]:
            res["bus_problems_after_reset"].append("the reset sibling kept its old mount or PID namespace: it was not a fresh restore")
        res["state_bytes"] = {"checkpoint_images": res["checkpoint"]["meta"].get("image_bytes"), "checkpoint_layer_upper": res["checkpoint"]["layer_upper_bytes"],
                              **{f"sibling{s}_stage_upper": fg.state_bytes_report(sup, s, criu_friendly=True) for s in (1, 2)}}
        if d.get("task_eval"):
            res["task_eval"] = d["task_eval"](guests)
        res["all_ok"] = (all(v["pass"] for v in res["siblings"].values()) and not res["bus_problems"] and not res["bus_problems_after_reset"] and res["canaries"].get("pass", False))
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {str(e)[:500]}"
        tails = criu_log_tails(ck if ck is not None else d.get("ck_for_logs") or _ck_from_disk(work))
        res["criu_logs"], res["blocker_first_error_line"] = tails, first_error_line(tails)
        res["all_ok"] = False
    finally:
        for slot in (1, 2, 0):
            try:
                if sup is not None:
                    sup.destroy(slot)
            except Exception as ce:
                if "not running" not in str(ce) and "no session" not in str(ce).lower():
                    res["cleanup_errors"].append(f"destroy {slot}: {type(ce).__name__}: {str(ce)[:150]}")
        if sup is not None:
            try:
                sup.close()
            except Exception as ce:
                res["cleanup_errors"].append(f"close: {type(ce).__name__}: {str(ce)[:150]}")
    if res["cleanup_errors"]:
        res["all_ok"] = False
    res["clocks"] = clock.report()
    wall = time.monotonic() - wall0
    att = sum(v for k, v in res["clocks"].items() if k != "unavailable" and v is not None)
    res["wall_reconciliation"] = {"wall_s": wall, "sum_of_phases_s": att, "unattributed_s": wall - att}
    json.dump(res, open(os.path.join(out, "criu_probe.json"), "w"), indent=1, default=str)
    print("CRIU PROBE " + ("PASS" if res["all_ok"] else "FAIL: see criu_probe.json") + f" ({LABEL}; no compatibility claim from a fake or a failure)")
    return 0 if res["all_ok"] else 1


def _ck_from_disk(work):
    from envd_linux import criu as cr
    p = os.path.join(work, "ck")
    return cr.Checkpoint(p) if os.path.isdir(p) else None


def preflight(cfg, scope="config", deps=None):
    d = deps or {}
    import re
    checks = []

    def chk(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
    for k, want in REQUIRED.items():
        got = cfg.get(k)
        chk(f"config_{k}", got == want and type(got) is type(want), f"{k}={got!r} (required exactly {want!r})")
    chk("config_image_sha256", isinstance(cfg.get("image_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", cfg["image_sha256"]) is not None, "the pinned qcow2 sha256 (64 hex)")
    for k in ("drums_base_root", "drums_template_overlay"):
        v = cfg.get(k)
        chk(f"config_{k}", isinstance(v, str) and os.path.isabs(v) and os.path.normpath(v) == v and v != "/" and re.fullmatch(r"[A-Za-z0-9_./-]+", v) is not None, f"{k}={v!r}")
    chk("config_incumbent_not_virtualbox", cfg.get("incumbent") != "virtualbox", "this probe has no incumbent arm")
    receipt = None
    if scope == "host":
        from envd_linux import baseid, rootcheck
        base, ov = cfg.get("drums_base_root", ""), cfg.get("drums_template_overlay", "")
        receipt = (d.get("verify") or baseid.verify_image_binding)(base, cfg.get("image_sha256", ""))
        chk("host_image_receipt", receipt.get("verified") is True, "; ".join(receipt.get("problems", [])) or f"computed {receipt.get('computed_sha256')} for {receipt.get('image_path')} on {receipt.get('nbd_device')}")
        if os.path.isdir(base) and os.path.isdir(ov):
            from envd_linux import session as S
            found = rootcheck.check_userland([ov, base], kit=cfg.get("kit", "/opt/drums-osworld-real"), desktop=cfg["drums_desktop"], uid=int(cfg["drums_uid"]), gid=int(cfg["drums_gid"]), user=cfg["drums_user"],
                                             home=cfg["drums_home"], system_bus=True, xvfb=cfg.get("drums_xvfb", "Xvfb"), criu=True, pkg_dir=S.PKG_DIR)
            chk("host_merged_root_rootcheck_criu", not found["problems"], "; ".join(found["problems"]) or f"ok; warnings: {found['warnings']}")
        else:
            chk("host_dirs", False, f"{base} / {ov} must exist")
        crit = (d.get("criu_check") or _criu_check)()
        chk("host_criu_runnable", crit[0], crit[1])
        try:
            (d.get("pil_check") or (lambda: __import__("PIL")))()
            chk("host_pil", True, "PIL is importable (screens are measured)")
        except ImportError:
            chk("host_pil", False, "PIL is missing: the screenshot comparison cannot be measured, so no restore could pass")
    return {"scope": scope, "checks": checks, "all_ok": all(c["ok"] for c in checks), "receipt": receipt,
            "scope_note": "OFFLINE config checks only" if scope == "config" else "host checks: image receipt (hash COMPUTED over the served qcow2), merged-root rootcheck with the stage-2 audit, CRIU binary",
            "command": "python -m drums_compare.criu_probe run --config CONFIG.json --out OUTDIR  (authorized host only, after `preflight --scope host` is ALL OK)"}


def _criu_check():
    try:
        from envd_linux.criu import Criu
        return Criu().check()
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="criu_probe")
    ap.add_argument("stage", choices=["preflight", "run"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scope", choices=["config", "host"], default="config")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    cfg = json.load(open(a.config))
    if a.stage == "preflight":
        r = preflight(cfg, a.scope)
        json.dump(r, open(os.path.join(a.out, f"criu_preflight_{a.scope}.json"), "w"), indent=1, default=str)
        for c in r["checks"]:
            print(("PASS " if c["ok"] else "FAIL ") + c["name"] + ": " + c["detail"])
        print(f"{a.scope.upper()} CHECKS " + ("OK" if r["all_ok"] else "FAILED") + f" ({r['scope_note']})")
        return 0 if r["all_ok"] else 1
    r = preflight(cfg, "host")
    if not r["all_ok"]:
        print("REFUSED: the HOST-scope preflight failed")
        return 1
    cfg = dict(cfg, image_receipt=r["receipt"])  # the verified receipt travels into the Supervisor
    return run_probe(cfg, a.out)


if __name__ == "__main__":
    sys.exit(main())
