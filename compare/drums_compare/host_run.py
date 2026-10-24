"""Bounded, fail-closed entrypoint for a FIRST matched run on one authorized Linux x86 host: Drums against a STOCK OSWorld incumbent provider (OSWorld pinned at 04718ea, the public
`DesktopEnv` API). Two incumbents, kept separate, never substituted for each other:
    incumbent "virtualbox"  the original goal: stock VirtualBox provider, live snapshots, baseline reuse ON for both arms (bare-metal host, needs the 96 vCPU quota)
    incumbent "docker_kvm"  the small stage: stock Docker provider (QEMU/KVM in a container). Its save_state raises NotImplementedError and its revert is stop-and-recreate, so
                            this is a COLD-RESET baseline: reuse is OFF for BOTH arms, no VM snapshot exists or is claimed.
PROTOTYPE: written and unit-tested without a host. It has NEVER run against a real VM, container image or AWS.

    python -m drums_compare.host_run check  --config cfg.json --out RUN_DIR     # every precondition; writes checks.json; exit 1 on ANY failure
    python -m drums_compare.host_run gates  --config cfg.json --out RUN_DIR     # state, isolation and scoring gates on both arms
    python -m drums_compare.host_run run    --config cfg.json --out RUN_DIR     # alternating blocks, per-environment capture, runtime ceiling, always exports
    python -m drums_compare.host_run export --out RUN_DIR

Run as ROOT on the host (Drums needs namespaces and mounts; VirtualBox registers VMs per user, so the VM must be created by the same user). `run` and `gates` refuse unless `check`
passed and every execution INPUT (config, price file, task JSON bytes, image manifest, equivalence evidence, OSWorld head, this repository's commit and cleanliness) still has the
digest `check` recorded: the same config hash is not enough. Two modes: "comparison" needs the real image manifest plus measured, digest-bound evidence for every software
difference and NO stub; "architecture_measurement" is allowed to differ and to stub, and its output is labelled NOT_A_COMPARISON with the cross-arm ratio suppressed.
Nothing here installs, downloads or talks to AWS; a task other than the public unmodified `e0df059f` is refused unless it has a validated action sequence (and a VARIANT: label
for a custom JSON). No first result would support throughput, cost-advantage, training or benchmark claims."""

import argparse
import ast
import hashlib
import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import tarfile
import time
import uuid

OSWORLD_PIN = "04718eab9327856efe8192cc58161acc7a7627e2"
FIRST_TASK = "e0df059f-28a6-4169-924f-b9623e7184cc"
INCUMBENTS = {"virtualbox": {"reuse": "post_setup", "arm": "virtualbox"}, "docker_kvm": {"reuse": "none", "arm": "docker_kvm"}}
DOCKER_IMAGE = "happysixd/osworld-docker"
# Pre-registered BEFORE any measurement (2026-10-24): pixel thresholds of the Writer GUI-state gate. `changed` = pixels in the document region whose largest channel differs by more than 32.
GUI_THRESHOLDS = {"region": (40, 120, 1240, 760), "pixel_delta": 32, "min_destroy_px": 300, "restore_max_fraction_of_destroy": 0.25, "redo_min_fraction_of_destroy": 0.5}
KNOWN_RISKS = {
    "image_licence": "the official image's Hugging Face card License field is BLANK: unresolved, neither a ban nor permission",
    "image_composition": "the real image's composition is UNKNOWN until its manifest exists; 'same software' is a TARGET, not a state",
    "non_equivalence": "openbox vs GNOME, Chromium vs google-chrome, and different package identities are expected: see docs/incumbent-preflight-plan.md section 4a",
    "stub_analysis": "stub_reach inspects DIRECT imports only; a transitive path to a stubbed module is not excluded by it",
}


class CeilingReached(Exception):
    pass


def chk(name, ok, detail, kind="static"):
    return {"name": name, "ok": bool(ok), "detail": detail, "kind": kind}


def canon(x):
    return json.dumps(x, sort_keys=True, default=str)


def config_hash(cfg):
    return hashlib.sha256(canon(cfg).encode()).hexdigest()


def file_sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                return h.hexdigest()
            h.update(b)


def sh(cmd, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def source_identity(runner=sh):
    rc, head = runner(["git", "-C", repo_root(), "rev-parse", "HEAD"])
    rc2, dirty = runner(["git", "-C", repo_root(), "status", "--porcelain"])
    return {"drums_commit": head.strip() if rc == 0 else "unavailable", "drums_tree_clean": rc2 == 0 and dirty.strip() == ""}


def compute_inputs(cfg, runner=sh):
    """The digest of every execution input that is a file or a checkout. `check` stores it; `gates` and `run` recompute it and refuse on ANY difference."""
    d = {"config": config_hash(cfg)}
    for k in ("prices", "image_manifest", "drums_manifest"):
        p = cfg.get(k)
        d[k] = file_sha256(p) if p and os.path.exists(p) else None
    for t in cfg.get("tasks", []):
        p = t.get("task_json") or os.path.join(cfg.get("osworld_src", ""), "evaluation_examples", "examples", t.get("domain", "os"), t["id"] + ".json")
        d["task:" + t["id"]] = file_sha256(p) if os.path.exists(p) else None
        d["actions:" + t["id"]] = hashlib.sha256(canon(t.get("actions")).encode()).hexdigest()
    for e in cfg.get("equivalence", {}).get("accepted", []):
        d["equivalence:" + str(e.get("aspect"))] = file_sha256(e["evidence"]) if e.get("evidence") and os.path.exists(e["evidence"]) else None
    rc, head = runner(["git", "-C", cfg.get("osworld_src", ""), "rev-parse", "HEAD"]) if cfg.get("osworld_src") else (1, "")
    d["osworld_head"] = head.strip() if rc == 0 else None
    d.update(source_identity(runner))
    if cfg.get("incumbent") == "docker_kvm":
        st = os.stat(cfg["qcow2"]) if cfg.get("qcow2") and os.path.exists(cfg["qcow2"]) else None
        d["qcow2_stat"] = [st.st_size, st.st_mtime_ns] if st else None
        d["qcow2_expected"] = [cfg.get("qcow2_bytes"), cfg.get("qcow2_sha256")]
        d["docker_image_digest"] = cfg.get("docker_image_digest")
    return d


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
    """DIRECT-IMPORT scan only: which scored-path functions live in a module that itself imports a stubbed module? A transitive path (module A imports B imports the stub) is NOT
    detected, so an empty result never proves the stub is unreached; that is why `comparison` mode refuses any stub."""
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


# ---- image manifest and the evidence needed before a performance comparison ----
IMAGE_FIELDS = ("source_url", "license_field", "os_release", "kernel", "desktop", "browser", "libreoffice", "dpkg")


def validate_image_manifest(m):
    why = [f"missing {k}" for k in IMAGE_FIELDS if not m.get(k)]
    if m.get("composition_known") is not True:
        why.append("composition_known is not true")
    if not (m.get("zip_sha256") or m.get("qcow2_sha256")):
        why.append("no recorded image checksum")
    return why


def _os_field(text, key):
    mm = re.search(rf'^{key}="?([^"\n]*)"?', text or "", re.M)
    return mm.group(1) if mm else None


def detect_differences(image, drums):
    """What the REAL image says against what the Drums rootfs says, per aspect. Every difference found needs accepted, evidenced equivalence before a comparison."""
    diffs = []
    iv, dv = _os_field(image.get("os_release"), "VERSION_ID"), (drums.get("os_release") or {}).get("VERSION_ID")
    if iv != dv:
        diffs.append({"aspect": "os_release", "vm": iv, "drums": dv})
    diffs.append({"aspect": "kernel", "vm": image.get("kernel"), "drums": drums.get("kernel"), "note": "the guest kernel is the image's own; Drums shares the host kernel: always different by design"})
    gnome = "gnome" in (image.get("desktop") or "").lower()
    d_gnome = any("gnome-shell" in k for k in (drums.get("dpkg") or {}))
    if gnome != d_gnome:
        diffs.append({"aspect": "desktop", "vm": image.get("desktop"), "drums": "openbox (no gnome-shell)" if not d_gnome else "gnome-shell"})
    def is_chrome(text):  # `google-chrome --version` prints "Google Chrome 120..." (a space), the binary is google-chrome (a hyphen): accept both
        t = (text or "").lower()
        return "google chrome" in t or "google-chrome" in t
    chrome = is_chrome(image.get("browser"))
    d_chrome = any(is_chrome(k) or is_chrome(v) for k, v in (drums.get("browsers") or {}).items())
    if chrome != d_chrome:
        diffs.append({"aspect": "browser", "vm": image.get("browser"), "drums": list((drums.get("browsers") or {}).values())})
    il, dl = (image.get("libreoffice") or "").strip(), (drums.get("dpkg") or {}).get("soffice_version_string") or ""
    if il.split("(")[0].strip() != dl.split("(")[0].strip():
        diffs.append({"aspect": "libreoffice", "vm": il, "drums": dl})
    return diffs


def equivalence_gaps(diffs, cfg):
    """A difference is closed only by an accepted entry naming its aspect, a scope, and an existing evidence file whose sha256 matches. No boolean flag closes anything."""
    acc = {e.get("aspect"): e for e in cfg.get("equivalence", {}).get("accepted", [])}
    gaps = []
    for d in diffs:
        e = acc.get(d["aspect"])
        if not e:
            gaps.append(f"{d['aspect']}: no accepted equivalence entry")
        elif not e.get("scope") or not e.get("measured") is True:
            gaps.append(f"{d['aspect']}: entry lacks scope or measured: true")
        elif not e.get("evidence") or not os.path.exists(e["evidence"]) or file_sha256(e["evidence"]) != e.get("sha256"):
            gaps.append(f"{d['aspect']}: evidence file missing or its sha256 differs")
    return gaps


def _real_preflight(cfg):
    from envd_linux import preflight
    return preflight.run(cfg.get("criu_home"), cfg.get("disk_path", "/"), probe=True)


def _real_kit_ok():
    from envd_linux import realkit
    return realkit.available(realkit.DEFAULT_KIT) and not realkit.missing()


def meminfo_mib(read):
    m = {k: int(v.split()[0]) for k, v in (ln.split(":") for ln in read("/proc/meminfo").splitlines() if ":" in ln)}
    return m["MemTotal"] // 1024, m["MemAvailable"] // 1024


def docker_provider_env(src):
    """The guest budget the STOCK provider hard-codes (`self.environment = {...}`), read from its source, not assumed."""
    text = open(os.path.join(src, "desktop_env", "providers", "docker", "provider.py")).read()
    m = re.search(r"self\.environment\s*=\s*(\{[^}]*\})", text)
    return ast.literal_eval(re.sub(r"\s*#.*", "", m.group(1))) if m else None


def check_all(cfg, deps=None, options=None):
    d = {"sh": sh, "import": strict_import, "exists": os.path.exists, "read": lambda p: open(p).read(), "machine": platform.machine, "euid": os.geteuid}
    d.update(deps or {})
    d["options"] = options or {}
    out, mode, inc = [], cfg.get("mode"), cfg.get("incumbent")
    src = cfg.get("osworld_src", "")
    out.append(chk("mode_and_incumbent_declared", mode in ("comparison", "architecture_measurement") and inc in INCUMBENTS, f"mode={mode!r} incumbent={inc!r}"))
    if inc not in INCUMBENTS:
        return _finish(cfg, out, d, [], [])
    out.append(chk("reuse_matches_the_incumbent", cfg.get("reuse", INCUMBENTS[inc]["reuse"]) == INCUMBENTS[inc]["reuse"],
                   f"{inc} runs reuse={INCUMBENTS[inc]['reuse']} on BOTH arms" + (" (no VM snapshot exists in the Docker provider; none is claimed)" if inc == "docker_kvm" else "")))
    out.append(chk("running_as_root", d["euid"]() == 0, "Drums namespaces and mounts need root, and VirtualBox's VM registry is per user" if d["euid"]() != 0 else "root"))
    rc, virt = d["sh"](["systemd-detect-virt"])
    virt = virt.strip()
    if cfg.get("host_kind") == "nested_kvm":
        out.append(chk("host_kind_nested_kvm", virt not in ("none", "") and d["exists"]("/dev/kvm"), f"systemd-detect-virt={virt!r}; /dev/kvm {'present' if d['exists']('/dev/kvm') else 'MISSING (the Docker provider would silently fall back to software emulation, so this refuses)'}; NESTED: timings are not bare-metal"))
    else:
        out.append(chk("host_is_not_virtualized", cfg.get("host_kind") == "baremetal" and virt == "none", f"host_kind={cfg.get('host_kind')!r} systemd-detect-virt={virt or 'unavailable'}"))
    out.append(chk("host_arch_x86_64", d["machine"]() == "x86_64", d["machine"]()))
    try:
        cpuinfo = d["read"]("/proc/cpuinfo")
    except OSError:
        cpuinfo = ""
    out.append(chk("cpu_virtualization_flag", " vmx" in cpuinfo or " svm" in cpuinfo, "vmx or svm in /proc/cpuinfo"))
    # memory admission: an OWNER-SET threshold, not a derived minimum and not a capacity claim
    try:
        total, avail = meminfo_mib(d["read"])
        thr = cfg.get("memory_admission_mib")
        out.append(chk("memory_admission", isinstance(thr, int) and thr > 0 and avail >= thr, f"MemTotal {total} MiB, MemAvailable {avail} MiB, owner-set admission threshold {thr!r} MiB (arms run strictly one after the other, concurrency 1; this is an admission rule, not a proven capacity)"))
    except Exception as e:
        out.append(chk("memory_admission", False, f"{type(e).__name__}: {e}"))
    rc, head = d["sh"](["git", "-C", src, "rev-parse", "HEAD"]) if src else (1, "no osworld_src")
    out.append(chk("osworld_pinned", head.strip() == OSWORLD_PIN, f"HEAD={head.strip()[:40]} want {OSWORLD_PIN}"))
    rc, dirty = d["sh"](["git", "-C", src, "status", "--porcelain"]) if src else (1, "no osworld_src")
    out.append(chk("osworld_unmodified", rc == 0 and dirty.strip() == "", "no local changes to the stock checkout" if rc == 0 and not dirty.strip() else f"status: {dirty[:120]}"))
    ident = d["source_identity"]() if "source_identity" in d else source_identity(d["sh"])
    out.append(chk("drums_source_clean_and_recorded", ident["drums_tree_clean"] and ident["drums_commit"] != "unavailable", f"commit {ident['drums_commit'][:12]}, tree clean: {ident['drums_tree_clean']} (a dirty tree has no identity)"))
    # strict import and the stub rule
    allow = tuple(cfg.get("allow_stub", []))
    stubs = []
    try:
        _, stubs = d["import"](allow)
        out.append(chk("desktop_env_imports", True, f"imported; stubbed modules: {stubs or 'none'}; allow_stub={list(allow)}"))
    except Exception as e:
        out.append(chk("desktop_env_imports", False, f"{type(e).__name__}: {str(e)[:160]} (a missing module is not stubbed unless named in allow_stub; in comparison mode install it on the host)"))
    out.append(chk("comparison_mode_has_no_stub", not (mode == "comparison" and (stubs or allow)),
                   "no stub, and none allowed" if not (stubs or allow) else f"comparison mode refuses stubs (allow_stub={list(allow)}, stubbed={stubs}): stub reachability is a direct-import scan only; use mode architecture_measurement, which is labelled NOT_A_COMPARISON"))
    tasks = load_tasks(cfg, src) if src else []
    ids = [t["id"] for t in tasks]
    bad = []
    for t in tasks:
        if t["task"] is None:
            bad.append(f"{t['id']}: task JSON not found at {t['path']}")
            continue
        if t.get("task_json") and not str((t.get("variant") or {}).get("label", "")).startswith("VARIANT:"):
            bad.append(f"{t['id']}: a custom task_json needs variant.label starting 'VARIANT:' (it is not the public task)")
        if t["id"] != FIRST_TASK:
            if not t.get("actions"):
                bad.append(f"{t['id']}: no scripted actions")
            if not t.get("actions_validated"):
                bad.append(f"{t['id']}: no actions_validated record (where and when the sequence was shown to score 0 then 1 through the GUI)")
    out.append(chk("tasks_allowed", bool(tasks) and not bad and ids[0] == FIRST_TASK, "; ".join(bad) or f"tasks {[i[:8] for i in ids]}; first is the public rename task"))
    if stubs and tasks and all(t["task"] for t in tasks):
        reach = stub_reach(src, tasks, stubs)
        out.append(chk("stub_not_directly_imported_by_scored_functions", not reach, ("no scored function lives in a module that directly imports a stub (ANALYSIS ONLY: transitive paths are not excluded)") if not reach else f"scored function in a module importing a stub: {reach}"))
    # incumbent-specific
    if inc == "virtualbox":
        _check_virtualbox(cfg, d, out)
    else:
        _check_docker(cfg, d, out, src)
    # Drums: an alternative base root must be a READ-ONLY mount (the image is evidence; nothing may write to it), and CRIU is not combinable with it
    base = cfg.get("drums_base_root", "/")
    if base != "/":
        rc, opts = d["sh"](["findmnt", "-n", "-o", "OPTIONS", "--target", base])
        out.append(chk("drums_base_root_read_only", rc == 0 and "ro" in opts.split(",") and INCUMBENTS[inc]["reuse"] == "none",
                       f"base root {base} mount options: {opts.strip() or 'unreadable'}; must contain ro, and only cold-reset stages may use it (CRIU assumes '/')"))
    # Drums
    try:
        pf = d["preflight"](cfg) if "preflight" in d else _real_preflight(cfg)
        need_restore = INCUMBENTS[inc]["reuse"] == "post_setup"  # CRIU restore matters only where the Drums arm reuses state; a cold-reset stage needs sessions, not restore
        ok = bool(pf["go_drums"] and (pf["go_drums_restore"] or not need_restore))
        out.append(chk("drums_preflight_go", ok, ("go_drums" + (" and go_drums_restore" if need_restore else " (restore not needed: cold reset)") + " (runtime probes run); the joint gates are the `gates` stage") if ok else "; ".join(pf["no_go_reasons"])[:300], "runtime_probe"))
        kit = d["kit_ok"]() if "kit_ok" in d else _real_kit_ok()
        out.append(chk("drums_real_server_kit", kit, "real-server kit present" if kit else "real-server kit missing or incomplete"))
    except Exception as e:
        out.append(chk("drums_preflight_go", False, f"{type(e).__name__}: {str(e)[:160]}", "runtime_probe"))
    # money and control
    pr = cfg.get("prices")
    try:
        p = json.load(open(pr))
        ok = isinstance(p.get("host_usd_per_hour"), (int, float)) and math.isfinite(p["host_usd_per_hour"]) and p["host_usd_per_hour"] > 0 and not any(w in str(p.get("label", "")).lower() for w in ("placeholder", "not a price", "harness"))
        out.append(chk("prices_are_owner_inputs", ok, f"label={p.get('label')!r}"))
    except Exception as e:
        out.append(chk("prices_are_owner_inputs", False, f"{type(e).__name__}: {e}"))
    ceil = cfg.get("max_runtime_s")
    out.append(chk("runtime_ceiling_set", isinstance(ceil, (int, float)) and 0 < ceil <= cfg.get("max_runtime_cap_s", 6 * 3600), f"max_runtime_s={ceil}"))
    out.append(chk("external_watcher_asserted", bool(cfg.get("external_watcher_confirmed_by")), f"asserted by {cfg.get('external_watcher_confirmed_by')!r} (NOT verified here; the host's own dead-man switch and ceiling are the enforced stops)"))
    # image manifest and equivalence
    image = drums = None
    diffs = []
    try:
        image = json.load(open(cfg["image_manifest"])) if cfg.get("image_manifest") and d["exists"](cfg["image_manifest"]) else None
        drums = json.load(open(cfg["drums_manifest"])) if cfg.get("drums_manifest") and d["exists"](cfg["drums_manifest"]) else None
    except Exception as e:
        out.append(chk("manifests_readable", False, f"{type(e).__name__}: {e}"))
    if image:
        why = validate_image_manifest(image)
        out.append(chk("image_manifest_valid", not why, "real image composition recorded" if not why else "; ".join(why)))
    if image and drums:
        diffs = detect_differences(image, drums)
    if mode == "comparison":
        gaps = ["no image manifest"] if not image else ["no Drums composition manifest"] if not drums else equivalence_gaps(diffs, cfg)
        out.append(chk("comparison_equivalence_evidenced", not gaps, "every detected software difference has measured, digest-bound evidence" if not gaps else "a performance comparison is refused: " + "; ".join(gaps)))
    else:
        out.append(chk("architecture_measurement_labelled", True, "NOT_A_COMPARISON: image composition " + ("recorded" if image else "UNKNOWN") + f"; differences found: {[x['aspect'] for x in diffs] or 'not computed'}; the cross-arm ratio is suppressed"))
    return _finish(cfg, out, d, stubs, diffs)


def _finish(cfg, out, d, stubs, diffs):
    inputs = compute_inputs(cfg, d["sh"])
    return {"all_ok": all(c["ok"] for c in out), "config_sha256": config_hash(cfg), "options": d.get("options", {}), "inputs": inputs, "inputs_sha256": hashlib.sha256(canon(inputs).encode()).hexdigest(), "mode": cfg.get("mode"),
            "incumbent": cfg.get("incumbent"), "known_risks": KNOWN_RISKS, "checks": out, "stubbed_modules": stubs, "allow_stub": list(cfg.get("allow_stub", [])), "differences": diffs, "at": time.time()}


def _check_virtualbox(cfg, d, out):
    from . import vbox
    vm = cfg.get("vm", "")
    rc, ver = d["sh"](["VBoxManage", "--version"])
    out.append(chk("virtualbox_installed", rc == 0, f"VBoxManage {ver[:40]}" if rc == 0 else "VBoxManage not found"))
    if rc == 0 and vm:
        try:
            rb = vbox.vm_readback(vm, runner=d.get("vbox_runner", subprocess.run))
            why = vbox.check_parity(rb, cfg.get("guest_cpus", 2), cfg.get("guest_memory_mib", 4096))
            out.append(chk("vm_settings_readback", not why, f"hypervisor reports {rb['cpus']} vCPU, {rb['memory_mib']} MiB; nic1={rb['nic1']}" if not why else "; ".join(why)))
            snaps = vbox.snapshot_names(vm, runner=d.get("vbox_runner", subprocess.run))
            out.append(chk("vm_has_init_state_snapshot", "init_state" in snaps, f"snapshots: {snaps}"))
        except Exception as e:
            out.append(chk("vm_settings_readback", False, f"{type(e).__name__}: {str(e)[:160]}"))
    else:
        out.append(chk("vm_settings_readback", False, "no VirtualBox or no vm named in config"))


def _check_docker(cfg, d, out, src):
    rc, ver = d["sh"](["docker", "version", "--format", "{{.Server.Version}}"])
    out.append(chk("docker_daemon", rc == 0, f"Docker server {ver[:30]}" if rc == 0 else "docker daemon not reachable"))
    rc, dig = d["sh"](["docker", "image", "inspect", DOCKER_IMAGE, "--format", "{{index .RepoDigests 0}}"])
    want = cfg.get("docker_image_digest")
    out.append(chk("docker_image_pinned_by_digest", rc == 0 and bool(want) and want in dig, f"local {dig[:90]!r} want {want!r} (the stock provider pulls the floating tag `latest`: the run requires the pre-pulled image to be this exact digest)"))
    q = cfg.get("qcow2", "")
    ok_file = bool(q) and d["exists"](q)
    out.append(chk("qcow2_present", ok_file, q or "no qcow2 path"))
    if ok_file and d["exists"](q):
        size = os.path.getsize(q) if "qcow2_size" not in d else d["qcow2_size"](q)
        out.append(chk("qcow2_size_matches_record", size == cfg.get("qcow2_bytes"), f"{size} bytes, recorded {cfg.get('qcow2_bytes')}"))
        skip_text = d.get("options", {}).get("skip_qcow2_hash")  # a command-line statement; it does NOT change the config, so earlier gate evidence keeps its config hash
        if cfg.get("verify_sha256", True) and not skip_text:
            sha = d["sha256"](q) if "sha256" in d else file_sha256(q)
            out.append(chk("qcow2_sha256_matches_record", sha == cfg.get("qcow2_sha256"), f"{sha[:16]} recorded {str(cfg.get('qcow2_sha256'))[:16]}"))
        else:  # skipping the multi-minute hash needs an explicit owner statement of the earlier verification; the size check above still runs and the file's stat is bound into the inputs
            earlier = skip_text or cfg.get("qcow2_sha256_verified_earlier")
            out.append(chk("qcow2_sha256_matches_record", bool(earlier), f"NOT RECOMPUTED here; owner-stated earlier verification: {earlier!r} (size still checked; file stat bound into the stamp)" if earlier else "verify_sha256 is false but no qcow2_sha256_verified_earlier statement is recorded"))
    try:
        env = d["docker_env"](src) if "docker_env" in d else docker_provider_env(src)
        want_env = {"CPU_CORES": str(cfg.get("guest_cpus", 4)), "RAM_SIZE": f"{cfg.get('guest_memory_mib', 4096) // 1024}G"}
        got = {k: (env or {}).get(k) for k in want_env}
        out.append(chk("docker_guest_budget_matches", got == want_env, f"stock provider hard-codes {env}; run needs {want_env}"))
    except Exception as e:
        out.append(chk("docker_guest_budget_matches", False, f"{type(e).__name__}: {e}"))


# ---- live gates (one DesktopEnv lease); pure functions so fakes can test them ----
HOLDER = ("import os,socket,time,sys\nT=os.urandom(8).hex()\ns=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('127.0.0.1',%d));s.listen(4)\n"
          "open('/tmp/gate_holder.pid','w').write(str(os.getpid()))\nwhile True:\n c,_=s.accept();c.sendall(T.encode());c.close()\n")
ASK = "import socket;s=socket.create_connection(('127.0.0.1',%d),timeout=5);print(s.recv(64).decode())"


def snapshot_gate(lease, task, sh_out, step, port=47653, name=None):
    """Snapshot incumbents only. A live background process holding an in-memory token and a file marker, mutated after the save, must be back after the revert."""
    name = name or f"gate-{uuid.uuid4().hex[:8]}"
    res = {"snapshot_name": name}
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
    res["pass"] = all(res[k] for k in ("token_before", "process_alive_after_revert", "same_token_after_revert", "file_marker_restored", "post_save_file_removed"))
    return res


def changed_pixels(png_a, png_b, region=GUI_THRESHOLDS["region"], delta=GUI_THRESHOLDS["pixel_delta"]):
    """Pixels in `region` whose largest channel differs by more than `delta`. Needs Pillow (fails closed without it)."""
    import io
    from PIL import Image, ImageChops
    a = Image.open(io.BytesIO(png_a)).convert("RGB").crop(region)
    b = Image.open(io.BytesIO(png_b)).convert("RGB").crop(region)
    if a.size != b.size:
        return None
    diff = ImageChops.difference(a, b)
    return sum(1 for px in diff.getdata() if max(px) > delta)


def gui_state_gate(lease, task, sh_out, step, shot, thresholds=GUI_THRESHOLDS, token=None, sleep=time.sleep, diff=None):
    """Snapshot incumbents only. A REAL Writer window with an UNSAVED typed token must come back, as pixels, after a snapshot/revert, and must differ from the post-save mutation:
    d12 = change from typed to mutated, d13 = typed versus restored, d23 = mutated versus restored. Registered thresholds (before any measurement): d12 >= min_destroy_px,
    d13 <= 0.25 * d12, d23 >= 0.5 * d12; the same soffice process must still be alive and no document file may have been written. Nothing writes any settings or target file.
    The window is confirmed by wmctrl OR by the screen having changed after launch (a guest without wmctrl has no other cheap proof)."""
    t = thresholds
    changed_pixels_ = diff or changed_pixels
    token = token or "".join(random.choice("abcdefghijklmnop") for _ in range(12))  # letters only: no keyboard-layout surprises
    res = {"token_len": len(token), "thresholds": {k: v for k, v in t.items() if k != "region"}, "region": list(t["region"])}
    lease.reset(task)
    blank = shot()
    sh_out("touch /tmp/gate_gui_start")
    step("import subprocess; subprocess.Popen(['libreoffice','--writer','--norestore','--nologo'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)")
    pid1 = ""
    for _ in range(60):
        pid1 = sh_out("pgrep -x soffice.bin | head -1").strip()
        win = sh_out("wmctrl -l 2>/dev/null | grep -ci writer").strip()
        if pid1 and (win not in ("", "0") or (changed_pixels_(blank, shot()) or 0) > 5000):
            break
        sleep(2)
    res["writer_process"] = bool(pid1)
    if not pid1:
        res["pass"] = False
        res["why"] = "no Writer process appeared (is LibreOffice Writer installed in the guest?)"
        return res
    sleep(3)
    step("import pyautogui; pyautogui.click(640, 420)")
    sleep(0.5)
    step("import pyautogui; pyautogui.write(%r, interval=0.05)" % token)
    sleep(1.0)
    s1 = shot()
    name = f"gate-gui-{uuid.uuid4().hex[:8]}"
    lease.save_branch(name)
    step("import pyautogui; pyautogui.hotkey('ctrl','a'); pyautogui.press('delete'); pyautogui.write('zzzzzzzzzzzz', interval=0.05)")
    sleep(1.0)
    s2 = shot()
    lease.restore_branch(name, task)
    sleep(3.0)
    s3 = shot()
    pid3 = sh_out("pgrep -x soffice.bin | head -1").strip()
    d12, d13, d23 = changed_pixels_(s1, s2), changed_pixels_(s1, s3), changed_pixels_(s2, s3)
    saved = sh_out("find $HOME /tmp -maxdepth 2 -newer /tmp/gate_gui_start \\( -name '*.odt' -o -name '*.docx' \\) 2>/dev/null | wc -l").strip()
    res.update({"snapshot_name": name, "d12_typed_to_mutated_px": d12, "d13_typed_to_restored_px": d13, "d23_mutated_to_restored_px": d23, "same_soffice_pid": bool(pid1) and pid1 == pid3,
                "no_document_saved": saved == "0"})
    ok = None not in (d12, d13, d23) and d12 >= t["min_destroy_px"] and d13 <= t["restore_max_fraction_of_destroy"] * d12 and d23 >= t["redo_min_fraction_of_destroy"] * d12
    res["pass"] = bool(ok and res["same_soffice_pid"] and res["no_document_saved"])
    return res


def score_gate(lease, task, correct, wrong, name):
    """Snapshot incumbents: correct 1.0, then (after a revert) wrong 0.0, then correct again 1.0, from the same saved state."""
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


def cold_score_gate(lease, task, correct, wrong):
    """Cold-reset incumbents: every score comes from a fresh reset. Before any action 0.0, correct 1.0, then after a cold reset wrong 0.0."""
    out = {}
    lease.reset(task)
    out["before_action"] = lease.evaluate()
    lease.step(correct)
    out["correct"] = lease.evaluate()
    lease.reset(task)
    lease.step(wrong)
    out["wrong"] = lease.evaluate()
    out["pass"] = out["before_action"] == 0.0 and out["correct"] == 1.0 and out["wrong"] == 0.0
    return out


def cold_isolation_gate(lease, task, sh_out, step, port=47653):
    """Cold-reset incumbents: state made before a reset must be GONE after it: a live process with an in-memory token, a file marker, and a running Writer."""
    res = {}
    lease.reset(task)
    step("import subprocess,sys; subprocess.Popen([sys.executable,'-c',%r], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)" % (HOLDER % port))
    step("import subprocess; subprocess.Popen(['libreoffice','--writer','--norestore','--nologo'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)")
    time.sleep(8)
    marker = uuid.uuid4().hex[:8]
    step("open('/tmp/gate_marker','w').write(%r)" % marker)
    res["state_existed"] = bool(sh_out("python3 -c %r 2>/dev/null" % (ASK % port)).strip()) and sh_out("cat /tmp/gate_marker").strip() == marker
    lease.reset(task)
    time.sleep(2)
    res.update({"holder_gone": sh_out("python3 -c %r 2>/dev/null" % (ASK % port)).strip() == "", "marker_gone": sh_out("cat /tmp/gate_marker 2>/dev/null").strip() == "",
                "writer_gone": sh_out("pgrep -x soffice.bin | head -1").strip() == ""})
    res["pass"] = all(res[k] for k in ("state_existed", "holder_gone", "marker_gone", "writer_gone"))
    return res


def guest_readback(sh_out):
    nproc = sh_out("nproc").strip()
    mem = sh_out("grep MemTotal /proc/meminfo").split()
    return {"nproc": int(nproc) if nproc.isdigit() else None, "mem_total_kib": int(mem[1]) if len(mem) > 1 and mem[1].isdigit() else None}


def parity_decision(arm, target_cpus, target_mem_mib, limits_readback=None, guest=None, tol=0.06):
    """A recorded DECISION per arm from READBACKS, never a claim: hypervisor/cgroup readbacks must equal the target; a VM guest's own view must agree within `tol` for memory."""
    d = {"arm": arm, "target_cpus": target_cpus, "target_memory_mib": target_mem_mib, "limits_readback": limits_readback, "guest_readback": guest, "reasons": []}
    if guest is not None:
        if guest.get("nproc") != target_cpus:
            d["reasons"].append(f"guest nproc {guest.get('nproc')} != {target_cpus}")
        mt = guest.get("mem_total_kib")
        if mt is None or abs(mt / 1024 - target_mem_mib) > tol * target_mem_mib:
            d["reasons"].append(f"guest MemTotal {mt} KiB is not within {int(tol * 100)}% of {target_mem_mib} MiB")
    if limits_readback is not None:
        if limits_readback.get("cpus") != target_cpus or limits_readback.get("memory_mib") != target_mem_mib:
            d["reasons"].append(f"limit readback {limits_readback} != {target_cpus} CPU / {target_mem_mib} MiB")
    if guest is None and limits_readback is None:
        d["reasons"].append("no readback at all")
    d["readbacks_equal_target"] = not d["reasons"]
    d["note"] = "a readback decision only: measured CPU and memory are sampled lower bounds, so no resource-parity claim follows"
    return d


def _verify_stamp(path, cfg, what, runner=sh):
    if not os.path.exists(path):
        raise SystemExit(f"REFUSED: run `{what}` first (no {os.path.basename(path)})")
    s = json.load(open(path))
    now = compute_inputs(cfg, runner)
    if not s.get("all_ok"):
        raise SystemExit(f"REFUSED: {os.path.basename(path)} did not pass")
    if s.get("config_sha256") != config_hash(cfg):
        raise SystemExit(f"REFUSED: {os.path.basename(path)} was made for a different config")
    changed = sorted(k for k in set(now) | set(s.get("inputs", {})) if now.get(k) != s.get("inputs", {}).get(k))
    if changed:
        raise SystemExit(f"REFUSED: execution inputs changed since `{what}`: {changed}")
    return s


def need_checks(out_dir, cfg, runner=sh):
    return _verify_stamp(os.path.join(out_dir, "checks.json"), cfg, "check", runner)


def need_gates(out_dir, cfg, runner=sh):
    return _verify_stamp(os.path.join(out_dir, "gates.json"), cfg, "gates", runner)


# The code the gates execute. Earlier gate evidence may be reused for a later commit ONLY if every one of these definitions is AST-identical to the commit the gates ran on.
GATE_BEARING = {
    "compare/drums_compare/host_run.py": ["snapshot_gate", "changed_pixels", "gui_state_gate", "score_gate", "cold_score_gate", "cold_isolation_gate", "guest_readback", "parity_decision", "HOLDER", "ASK", "GUI_THRESHOLDS"],
    "compare/drums_compare/stages.py": ["run_gates", "drums_limits_readback", "build_drums", "arm_factories", "hr_kw", "_sh", "CORRECT", "WRONG", "DE_KW", "MIB"],
    "compare/drums_compare/guestexec.py": None,  # None = the whole module
    "compare/drums_compare/docker_telemetry.py": None,
    "compare/drums_compare/osworld_matched.py": ["_DesktopEnvLease", "drums_factory", "incumbent_factory", "_bash", "_desktop_canary_probe", "_desktop_canary_write", "CANARY_PATHS"],
}


def _ast_parts(source, names):
    out = {}
    for node in ast.parse(source).body:
        nm = getattr(node, "name", None) or (node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) else None)
        if names is None or nm in names:
            out[nm or f"<{type(node).__name__}:{len(out)}>"] = ast.dump(node, include_attributes=False)
    return out


def reuse_gates(prior_path, cfg, out_dir, runner=sh, repo=None, gate_files=None, inputs_now=None):
    """Reuse an EARLIER `gates.json` PASS for a LATER commit without re-running the gates, openly and checkably. Nothing is rewritten or fabricated: the prior file is read as is and its
    hash recorded. It is accepted only if (1) it passed, for the same incumbent and mode, with the same config hash; (2) every bound input is identical EXCEPT the Drums commit and tree
    state (so prices, task JSON, actions, manifests, evidence, qcow2 and the OSWorld head are the same); (3) every gate-bearing definition (GATE_BEARING) is AST-identical between the
    gated commit and the code about to run. The files that DID change are listed in `gates_reuse.json`. Any difference refuses, naming it."""
    repo = repo or repo_root()
    gate_files = GATE_BEARING if gate_files is None else gate_files
    raw = open(prior_path, "rb").read()
    prior = json.loads(raw)
    if not prior.get("all_ok"):
        raise SystemExit("REFUSED: the prior gates.json did not pass")
    if prior.get("config_sha256") != config_hash(cfg) or prior.get("mode") != cfg.get("mode") or prior.get("incumbent") != cfg.get("incumbent"):
        raise SystemExit("REFUSED: the prior gates were made for a different config, mode or incumbent")
    now = inputs_now if inputs_now is not None else compute_inputs(cfg, runner)
    changed = sorted(k for k in set(now) | set(prior.get("inputs", {})) if k not in ("drums_commit", "drums_tree_clean") and now.get(k) != prior["inputs"].get(k))
    if changed:
        raise SystemExit(f"REFUSED: bound inputs other than the Drums commit differ from the gated run: {changed}")
    frm, to = prior["inputs"].get("drums_commit"), now.get("drums_commit")
    if not now.get("drums_tree_clean") or not frm or not to:
        raise SystemExit("REFUSED: the current Drums tree is not clean or a commit is unknown")
    equal, diffs = {}, []
    if frm != to:
        for path, names in gate_files.items():
            rc, old = runner(["git", "-C", repo, "show", f"{frm}:{path}"])
            if rc != 0:
                raise SystemExit(f"REFUSED: cannot read {path} at the gated commit {frm[:12]}")
            a, b = _ast_parts(old, names), _ast_parts(open(os.path.join(repo, path)).read(), names)
            bad = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            if bad:
                diffs.append((path, bad))
            else:
                equal[path] = sorted(a)
        if diffs:
            raise SystemExit(f"REFUSED: gate-bearing code changed since the gated commit, so the gates must be re-run: {diffs}")
    rc, files = runner(["git", "-C", repo, "diff", "--name-only", frm, to]) if frm != to else (0, "")
    rec = {"reused": True, "gated_commit": frm, "running_commit": to, "gates_json": os.path.abspath(prior_path), "gates_json_sha256": hashlib.sha256(raw).hexdigest(),
           "files_changed_since_gated_commit": files.split() if rc == 0 else None, "gate_bearing_definitions_identical": equal,
           "rule": "prior gates.json passed; same config hash; all bound inputs equal except the Drums commit; every GATE_BEARING definition AST-identical between the two commits",
           "not_claimed": "the changed files themselves were not gated: only the unchanged gate code and its earlier PASS are being reused"}
    json.dump(rec, open(os.path.join(out_dir, "gates_reuse.json"), "w"), indent=1)
    return prior, rec


EXPORT_TOP = (re.compile(r"^(checks|gates|summary|pins|run_status|parity_decision|export_manifest)\.json$"), re.compile(r"^block\d+_[A-Za-z0-9_.-]+\.json$"), re.compile(r"^[A-Za-z0-9_.-]+\.log$"))
EXPORT_DIRS = ("manifests", "fixtures")  # populated ONLY by stage_evidence; never walked recursively
EXPORT_EXT = (".json", ".log", ".txt")
EXPORT_MAX_BYTES = 20 << 20
SECRET_PATTERNS = (re.compile(rb"AKIA[0-9A-Z]{16}"), re.compile(rb"ASIA[0-9A-Z]{16}"), re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"), re.compile(rb"aws_secret_access_key", re.I),
                   re.compile(rb"aws_session_token", re.I))


def stage_evidence(cfg, out_dir):
    """Copy the EXPLICIT evidence into the two export directories: the manifests that bound the run (image, Drums composition, equivalence evidence, prices) and the task JSON
    fixtures it executed (public or labelled variants). Only files with an exportable extension; nothing else is ever copied here."""
    import shutil
    staged = []

    def put(src, sub, name):
        if src and os.path.isfile(src) and not os.path.islink(src) and src.endswith(EXPORT_EXT) and os.path.getsize(src) <= EXPORT_MAX_BYTES:
            os.makedirs(os.path.join(out_dir, sub), exist_ok=True)
            shutil.copyfile(src, os.path.join(out_dir, sub, name))
            staged.append(f"{sub}/{name}")
    put(cfg.get("image_manifest"), "manifests", "image_manifest.json")
    put(cfg.get("drums_manifest"), "manifests", "drums_manifest.json")
    put(cfg.get("prices"), "manifests", "prices.json")
    for e in cfg.get("equivalence", {}).get("accepted", []):
        put(e.get("evidence"), "manifests", f"equivalence_{re.sub(r'[^A-Za-z0-9_]', '_', str(e.get('aspect')))}.json")
    for t in cfg.get("tasks", []):
        src = t.get("task_json") or os.path.join(cfg.get("osworld_src", ""), "evaluation_examples", "examples", t.get("domain", "os"), t["id"] + ".json")
        put(src, "fixtures", t["id"] + ".json")
    return staged


def _exportable(out_dir):
    """Explicit allowlist, no recursion beyond the two evidence directories, no symlinks, no oversize files, no secret-looking bytes. Returns (included, excluded_count, blocked)."""
    included, excluded, blocked = [], 0, []
    candidates = []
    for n in sorted(os.listdir(out_dir)):
        p = os.path.join(out_dir, n)
        if os.path.islink(p):
            excluded += 1
        elif os.path.isfile(p):
            if any(r.match(n) for r in EXPORT_TOP):
                candidates.append((n, p))
            else:
                excluded += 1
        elif os.path.isdir(p) and n in EXPORT_DIRS:
            for m in sorted(os.listdir(p)):
                q = os.path.join(p, m)
                if os.path.isfile(q) and not os.path.islink(q) and m.endswith(EXPORT_EXT):
                    candidates.append((f"{n}/{m}", q))
                else:
                    excluded += 1
        else:
            excluded += 1  # an unlisted directory (work trees, profiles, rootfs, state) is never entered
    for name, p in candidates:
        if os.path.getsize(p) > EXPORT_MAX_BYTES:
            excluded += 1
            continue
        data = open(p, "rb").read()
        if any(r.search(data) for r in SECRET_PATTERNS):
            blocked.append(name)
            continue
        included.append(name)
    return included, excluded, blocked


def export(out_dir):
    """Archive ONLY the explicit raw logs, manifests and task fixtures (see EXPORT_TOP / EXPORT_DIRS). Everything else under the run directory (work trees, browser profiles, rootfs
    or session state, credentials, keys, symlinks, anything unlisted) is left out, counted, and never named in the archive; a listed file whose bytes look like a credential is
    withheld and reported by name. A previous export's outputs are never re-archived."""
    for stale in ("SHA256SUMS", "run_export.tar.gz"):
        if os.path.exists(os.path.join(out_dir, stale)):
            os.remove(os.path.join(out_dir, stale))
    if os.path.exists(os.path.join(out_dir, "export_manifest.json")):
        os.remove(os.path.join(out_dir, "export_manifest.json"))
    included, excluded, blocked = _exportable(out_dir)
    sums = {n: hashlib.sha256(open(os.path.join(out_dir, n), "rb").read()).hexdigest() for n in included}
    man = {"included": sorted(included), "excluded_count": excluded, "withheld_by_secret_scan": blocked, "policy": "explicit allowlist: top-level run JSON and logs plus manifests/ and fixtures/ only; no work trees, profiles, rootfs, state, keys or symlinks"}
    json.dump(man, open(os.path.join(out_dir, "export_manifest.json"), "w"), indent=1)
    sums["export_manifest.json"] = hashlib.sha256(open(os.path.join(out_dir, "export_manifest.json"), "rb").read()).hexdigest()
    open(os.path.join(out_dir, "SHA256SUMS"), "w").write("".join(f"{h}  {n}\n" for n, h in sorted(sums.items())))
    tgz = os.path.join(out_dir, "run_export.tar.gz")
    with tarfile.open(tgz, "w:gz") as t:
        for n in sorted(sums) + ["SHA256SUMS"]:
            t.add(os.path.join(out_dir, n), arcname=n)
    return tgz, sums


def main(argv=None):
    ap = argparse.ArgumentParser(prog="host_run")
    ap.add_argument("stage", choices=["check", "gates", "run", "export"])
    ap.add_argument("--config")
    ap.add_argument("--out", required=True)
    ap.add_argument("--reuse-gates", metavar="GATES_JSON", help="run: reuse an earlier gates.json PASS for this later commit (see reuse_gates for the exact rule)")
    ap.add_argument("--skip-qcow2-hash", action="store_true", help="check: do not recompute the qcow2 sha256 (needs --earlier-verified)")
    ap.add_argument("--earlier-verified", help="check: the recorded statement of the earlier sha256 verification(s)")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    if a.stage == "export":
        print(export(a.out)[0])
        return 0
    cfg = json.load(open(a.config))
    if a.stage == "check":
        if a.skip_qcow2_hash and not a.earlier_verified:
            raise SystemExit("REFUSED: --skip-qcow2-hash needs --earlier-verified 'where and when the sha256 was verified'")
        r = check_all(cfg, options={"skip_qcow2_hash": a.earlier_verified} if a.skip_qcow2_hash else None)
        json.dump(r, open(os.path.join(a.out, "checks.json"), "w"), indent=1, default=str)
        for c in r["checks"]:
            print(("PASS " if c["ok"] else "FAIL ") + c["name"] + ": " + c["detail"])
        print("ALL OK" if r["all_ok"] else "REFUSED: at least one check failed")
        return 0 if r["all_ok"] else 1
    from . import stages
    need_checks(a.out, cfg)
    if a.stage == "gates":
        return stages.run_gates(cfg, a.out)
    if a.reuse_gates:
        gates, rec = reuse_gates(a.reuse_gates, cfg, a.out)
        return stages.run_matched(cfg, a.out, gates=gates, gates_reuse=rec)
    need_gates(a.out, cfg)
    return stages.run_matched(cfg, a.out)


if __name__ == "__main__":
    sys.exit(main())
