"""Bounded, fail-closed entrypoint for the FIRST actual matched run on one authorized Linux x86 bare-metal host: Drums against the stock OSWorld VirtualBox provider
(OSWorld pinned at 04718ea, `DesktopEnv` public API, baseline reuse ON for both arms). PROTOTYPE: written and unit-tested without a host; it has NEVER run against a real VM.

    python -m drums_compare.host_run check  --config host_run.json --out RUN_DIR     # every precondition; writes checks.json; exit 1 on ANY failure
    python -m drums_compare.host_run gates  --config host_run.json --out RUN_DIR     # live state survives snapshot/revert, correct/wrong scores; both arms
    python -m drums_compare.host_run run    --config host_run.json --out RUN_DIR     # alternating blocks, per-environment capture, runtime ceiling, always exports
    python -m drums_compare.host_run export --out RUN_DIR                            # tar.gz + SHA256SUMS of the run directory

`run` refuses unless `check` and `gates` both passed for the SAME config hash. Nothing here installs, downloads, creates a VM or talks to AWS: those are separate, owner-run steps
(deploy/aws_host/). Nothing silently substitutes another baseline (no Docker, no KVM, no disk-only provider) or another task: a task other than the public, unmodified
`e0df059f` is refused unless the config labels it a VARIANT and records where its action sequence was validated. A first result from this entrypoint would be an ENGINEERING
VALIDATION on one task: no throughput, no cost-advantage, no training and no benchmark claim, and `resource_parity_claimed` stays False unless both arms' readbacks pass."""

import argparse
import ast
import hashlib
import json
import os
import platform
import subprocess
import sys
import tarfile
import time
import uuid

OSWORLD_PIN = "04718eab9327856efe8192cc58161acc7a7627e2"
FIRST_TASK = "e0df059f-28a6-4169-924f-b9623e7184cc"
VM_CPUS, VM_MEMORY_MIB = 2, 4096
KNOWN_RISKS = {
    "image_licence": "the official image's Hugging Face card License field is BLANK: unresolved, neither a ban nor permission",
    "image_composition": "the real image's composition is UNKNOWN until the manifest the fetch script generates exists; 'same software' is a TARGET, not a state",
    "non_equivalence": "openbox vs GNOME, Chromium vs google-chrome, and different package identities are expected: see docs/incumbent-preflight-plan.md section 4a",
}


class CeilingReached(Exception):
    pass


def chk(name, ok, detail, kind="static"):
    return {"name": name, "ok": bool(ok), "detail": detail, "kind": kind}


def config_hash(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()


# ---- default dependencies (replaced in tests) ----
def sh(cmd, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def strict_import(allow=()):
    """Import the public `DesktopEnv`. A missing module is NEVER silently replaced: it is stubbed only if named in `allow`, and every stub is returned."""
    from unittest.mock import MagicMock
    stubbed = []
    while True:
        try:
            import desktop_env.desktop_env as d
            return d, stubbed
        except ModuleNotFoundError as e:
            m = (e.name or "").split(".")[0]
            if not m or m.startswith("desktop_env") or m in stubbed or m not in allow:
                raise
            stubbed.append(m)
            sys.modules[m] = MagicMock(name=m)
            for k in [k for k in sys.modules if k.startswith("desktop_env")]:
                del sys.modules[k]


def stub_reach(src, tasks, stubs):
    """Which scored-path functions live in a module that imports a stubbed module? AST scan of the metrics and getters packages; returns the offending (task, name) pairs."""
    blocked = {}
    for sub in ("metrics", "getters"):
        root = os.path.join(src, "desktop_env", "evaluators", sub)
        for f in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            if not f.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(root, f)).read())
            mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
            if mods & set(stubs):
                for n in tree.body:
                    if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                        blocked[n.name] = f"{sub}/{f}"
    bad = []
    for t in tasks:
        ev = t["task"].get("evaluator", {})
        funcs = ev.get("func")
        funcs = funcs if isinstance(funcs, list) else [funcs]
        names = [f for f in funcs if f]
        for k in ("result", "expected"):
            v = ev.get(k)
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, dict) and x.get("type"):
                    names.append("get_" + x["type"])
        bad += [(t["id"], n, blocked[n]) for n in names if n in blocked]
    return bad


def load_tasks(cfg, src):
    out = []
    for t in cfg.get("tasks", []):
        path = t.get("task_json") or os.path.join(src, "evaluation_examples", "examples", t.get("domain", "os"), t["id"] + ".json")
        out.append({**t, "task": json.load(open(path)) if os.path.exists(path) else None, "path": path})
    return out


def _real_preflight(cfg):
    from envd_linux import preflight
    return preflight.run(cfg.get("criu_home"), cfg.get("disk_path", "/"), probe=True)


def _real_kit_ok():
    from envd_linux import realkit
    return realkit.available(realkit.DEFAULT_KIT) and not realkit.missing()


def check_all(cfg, deps=None):
    d = {"sh": sh, "import": strict_import, "exists": os.path.exists, "read": lambda p: open(p).read(), "machine": platform.machine}
    d.update(deps or {})
    out = []
    src = cfg.get("osworld_src", "")
    # host
    rc, virt = d["sh"](["systemd-detect-virt"])
    out.append(chk("host_is_not_virtualized", virt.strip() == "none", f"systemd-detect-virt: {virt.strip() or 'unavailable'} (a metal host reports none; nested or virtualized hosts are not the planned host)"))
    out.append(chk("host_arch_x86_64", d["machine"]() == "x86_64", d["machine"]()))
    try:
        cpuinfo = d["read"]("/proc/cpuinfo")
    except OSError:
        cpuinfo = ""
    out.append(chk("cpu_virtualization_flag", " vmx" in cpuinfo or " svm" in cpuinfo, "vmx or svm in /proc/cpuinfo"))
    # pinned stock OSWorld
    rc, head = d["sh"](["git", "-C", src, "rev-parse", "HEAD"]) if src else (1, "no osworld_src")
    out.append(chk("osworld_pinned", head.strip() == OSWORLD_PIN, f"HEAD={head.strip()[:40]} want {OSWORLD_PIN}"))
    rc, dirty = d["sh"](["git", "-C", src, "status", "--porcelain"]) if src else (1, "no osworld_src")
    out.append(chk("osworld_unmodified", rc == 0 and dirty.strip() == "", "no local changes to the stock checkout" if rc == 0 and not dirty.strip() else f"status: {dirty[:120]}"))
    # strict import, stub policy
    allow = tuple(cfg.get("allow_stub", []))
    stubs = []
    try:
        _, stubs = d["import"](allow)
        out.append(chk("desktop_env_imports", True, f"imported; stubbed modules: {stubs or 'none'}" + (" (explicitly allowed in config)" if stubs else "")))
    except Exception as e:
        out.append(chk("desktop_env_imports", False, f"{type(e).__name__}: {str(e)[:160]} (not stubbed: add the module to allow_stub only if the scored path never reaches it, or install it on the host)"))
    tasks = load_tasks(cfg, src) if src else []
    ids = [t["id"] for t in tasks]
    bad = []
    for t in tasks:
        if t["task"] is None:
            bad.append(f"{t['id']}: task JSON not found at {t['path']}")
            continue
        if t.get("task_json") and not str((t.get("variant") or {}).get("label", "")).startswith("VARIANT:"):
            bad.append(f"{t['id']}: a custom task_json needs variant.label starting 'VARIANT:' (it is not the public task)")
        if t["id"] != FIRST_TASK:  # any further task needs a recorded, validated action sequence: no blind GUI scripts and no direct writes to the evaluator's target
            if not t.get("actions"):
                bad.append(f"{t['id']}: no scripted actions")
            if not t.get("actions_validated"):
                bad.append(f"{t['id']}: no actions_validated record (where and when the sequence was shown to score 0 then 1 through the GUI)")
    out.append(chk("tasks_allowed", bool(tasks) and not bad and ids[0] == FIRST_TASK, "; ".join(bad) or f"tasks {[i[:8] for i in ids]}; first is the public rename task"))
    if stubs and tasks and all(t["task"] for t in tasks):
        reach = stub_reach(src, tasks, stubs)
        out.append(chk("stub_not_reached_by_scored_path", not reach, "no scored function lives in a module importing a stub" if not reach else f"needs a stubbed module: {reach}"))
    # VirtualBox incumbent
    vm = cfg.get("vm", "")
    rc, ver = d["sh"](["VBoxManage", "--version"])
    out.append(chk("virtualbox_installed", rc == 0, f"VBoxManage {ver[:40]}" if rc == 0 else "VBoxManage not found"))
    if rc == 0 and vm:
        from . import vbox
        try:
            rb = vbox.vm_readback(vm, runner=d.get("vbox_runner", subprocess.run))
            why = vbox.check_parity(rb, VM_CPUS, VM_MEMORY_MIB)
            out.append(chk("vm_settings_readback", not why, f"hypervisor reports {rb['cpus']} vCPU, {rb['memory_mib']} MiB; nic1={rb['nic1']}" if not why else "; ".join(why)))
            snaps = vbox.snapshot_names(vm, runner=d.get("vbox_runner", subprocess.run))
            out.append(chk("vm_has_init_state_snapshot", "init_state" in snaps, f"snapshots: {snaps}"))
        except Exception as e:
            out.append(chk("vm_settings_readback", False, f"{type(e).__name__}: {str(e)[:160]}"))
    else:
        out.append(chk("vm_settings_readback", False, "no VirtualBox or no vm named in config"))
    # Drums
    try:
        pf = d["preflight"](cfg) if "preflight" in d else _real_preflight(cfg)
        ok = pf["go_drums"] and pf["go_drums_restore"]
        out.append(chk("drums_preflight_go", ok, "go_drums and go_drums_restore (runtime probes run); joint gates are the `gates` stage" if ok else "; ".join(pf["no_go_reasons"])[:300], "runtime_probe"))
        kit = d["kit_ok"]() if "kit_ok" in d else _real_kit_ok()
        out.append(chk("drums_real_server_kit", kit, "real-server kit present" if kit else "real-server kit missing or incomplete"))
    except Exception as e:
        out.append(chk("drums_preflight_go", False, f"{type(e).__name__}: {str(e)[:160]}", "runtime_probe"))
    # money and control
    pr = cfg.get("prices")
    try:
        p = json.load(open(pr))
        ok = p.get("host_usd_per_hour", 0) > 0 and not any(w in str(p.get("label", "")).lower() for w in ("placeholder", "not a price", "harness"))
        out.append(chk("prices_are_owner_inputs", ok, f"label={p.get('label')!r}"))
    except Exception as e:
        out.append(chk("prices_are_owner_inputs", False, f"{type(e).__name__}: {e}"))
    ceil = cfg.get("max_runtime_s")
    out.append(chk("runtime_ceiling_set", isinstance(ceil, (int, float)) and 0 < ceil <= cfg.get("max_runtime_cap_s", 6 * 3600), f"max_runtime_s={ceil}"))
    out.append(chk("external_watcher_asserted", bool(cfg.get("external_watcher_confirmed_by")), f"asserted by {cfg.get('external_watcher_confirmed_by')!r} (NOT verified here)"))
    return {"all_ok": all(c["ok"] for c in out), "config_sha256": config_hash(cfg), "known_risks": KNOWN_RISKS, "checks": out, "stubbed_modules": stubs, "at": time.time()}


# ---- live-state gate (one DesktopEnv lease) ----
HOLDER = ("import os,socket,time,sys\nT=os.urandom(8).hex()\ns=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('127.0.0.1',%d));s.listen(4)\n"
          "open('/tmp/gate_holder.pid','w').write(str(os.getpid()))\nwhile True:\n c,_=s.accept();c.sendall(T.encode());c.close()\n")
ASK = "import socket;s=socket.create_connection(('127.0.0.1',%d),timeout=5);print(s.recv(64).decode())"


def snapshot_gate(lease, task, sh_out, step, port=47653, name=None):
    """Mid-task live state survives the provider's snapshot/revert: a live background process holding an in-memory token and a file marker, mutated after the save, must be back
    after the revert; then the real evaluator scores a correct and a wrong continuation. `sh_out(script)->str` runs bash in the guest; `step(code)` runs python through the
    public step API. The application-window part of the plan's gate 2 is NOT covered here and is reported as such."""
    name = name or f"gate-{uuid.uuid4().hex[:8]}"
    res = {"snapshot_name": name, "application_window_covered": False}
    lease.reset(task)
    step("import subprocess,sys; subprocess.Popen([sys.executable,'-c',%r], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)" % (HOLDER % port))
    time.sleep(1.5)
    token = sh_out("python3 -c %r" % (ASK % port)).strip()
    pid = sh_out("cat /tmp/gate_holder.pid").strip()
    marker = uuid.uuid4().hex[:8]
    step("open('/tmp/gate_marker','w').write(%r)" % marker)
    res["token_before"] = bool(token) and len(token) == 16
    lease.save_branch(name)
    step("import os,signal; os.kill(int(open('/tmp/gate_holder.pid').read()), signal.SIGKILL); os.remove('/tmp/gate_marker'); open('/tmp/gate_after_save','w').write('x')")
    lease.restore_branch(name, task)
    time.sleep(1.0)
    alive = sh_out("kill -0 %s 2>/dev/null && echo yes || echo no" % pid).strip()
    token_after = sh_out("python3 -c %r 2>/dev/null" % (ASK % port)).strip()
    res.update({"process_alive_after_revert": alive == "yes", "same_token_after_revert": bool(token) and token_after == token,
                "file_marker_restored": sh_out("cat /tmp/gate_marker 2>/dev/null").strip() == marker,
                "post_save_file_removed": sh_out("test -e /tmp/gate_after_save && echo yes || echo no").strip() == "no"})
    return res


def score_gate(lease, task, correct, wrong, name):
    """Correct continuation 1.0, then (after a revert) the wrong one 0.0, then correct again 1.0, from the same saved state."""
    lease.reset(task)
    lease.save_branch(name)
    out = {}
    lease.step(correct)
    out["correct"] = lease.evaluate()
    lease.restore_branch(name, task)
    lease.step(wrong)
    out["wrong"] = lease.evaluate()
    lease.restore_branch(name, task)
    lease.step(correct)
    out["correct_again"] = lease.evaluate()
    out["pass"] = out["correct"] == 1.0 and out["wrong"] == 0.0 and out["correct_again"] == 1.0
    return out


def need_checks(out_dir, cfg):
    p = os.path.join(out_dir, "checks.json")
    if not os.path.exists(p):
        raise SystemExit("REFUSED: run `check` first (no checks.json)")
    c = json.load(open(p))
    if not c["all_ok"] or c["config_sha256"] != config_hash(cfg):
        raise SystemExit("REFUSED: checks.json did not pass for this exact config")
    return c


def need_gates(out_dir, cfg):
    p = os.path.join(out_dir, "gates.json")
    if not os.path.exists(p):
        raise SystemExit("REFUSED: run `gates` first (no gates.json)")
    g = json.load(open(p))
    if not g["all_ok"] or g["config_sha256"] != config_hash(cfg):
        raise SystemExit("REFUSED: gates.json did not pass for this exact config")
    return g


def export(out_dir):
    names = []
    for dp, _, fs in os.walk(out_dir):
        for f in fs:
            if f.endswith((".tar.gz", "SHA256SUMS")):
                continue
            names.append(os.path.relpath(os.path.join(dp, f), out_dir))
    sums = {n: hashlib.sha256(open(os.path.join(out_dir, n), "rb").read()).hexdigest() for n in sorted(names)}
    open(os.path.join(out_dir, "SHA256SUMS"), "w").write("".join(f"{h}  {n}\n" for n, h in sums.items()))
    tgz = os.path.join(out_dir, "run_export.tar.gz")
    with tarfile.open(tgz, "w:gz") as t:
        for n in sorted(names) + ["SHA256SUMS"]:
            t.add(os.path.join(out_dir, n), arcname=n)
    return tgz, sums


def main(argv=None):
    ap = argparse.ArgumentParser(prog="host_run")
    ap.add_argument("stage", choices=["check", "gates", "run", "export"])
    ap.add_argument("--config")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    if a.stage == "export":
        print(export(a.out)[0])
        return 0
    cfg = json.load(open(a.config))
    if a.stage == "check":
        r = check_all(cfg)
        json.dump(r, open(os.path.join(a.out, "checks.json"), "w"), indent=1, default=str)
        for c in r["checks"]:
            print(("PASS " if c["ok"] else "FAIL ") + c["name"] + ": " + c["detail"])
        print("ALL OK" if r["all_ok"] else "REFUSED: at least one check failed")
        return 0 if r["all_ok"] else 1
    from . import stages  # the live stages (need the host, the VM and the Drums runtime)
    need_checks(a.out, cfg)
    if a.stage == "gates":
        return stages.run_gates(cfg, a.out)
    need_gates(a.out, cfg)
    return stages.run_matched(cfg, a.out)


if __name__ == "__main__":
    sys.exit(main())
