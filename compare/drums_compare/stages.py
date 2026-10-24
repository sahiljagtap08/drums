"""The LIVE stages of host_run (need the authorized host and the incumbent). Written without a host: only the pure gate functions in `host_run` are exercised with fakes; these
functions have never run. Imports are lazy so the fail-closed `check` works anywhere.

TIMING BOUNDARIES of `run` (per arm and block): INSIDE the run-arm clock: `adapter.open` (the first guest start: a Drums session, a VM start, or a container boot), every
`reset` (for the Docker incumbent a full stop-and-recreate cold boot), every step and evaluation, and `adapter.close` (guest or container stop). OUTSIDE it and reported apart:
building the Drums supervisor/template/pool (external prep), image fetch and checksum, `check` and `gates`, and between-block cleanup (snapshot deletion or container removal,
reported as between_block_s and added to the with-prep figures)."""

import json
import os
import subprocess
import time

from . import guestexec
from . import host_run as hr

CORRECT = "import os; os.rename(os.path.expanduser('~/Desktop/todo_list_Jan_1'), os.path.expanduser('~/Desktop/todo_list_Jan_2'))"
WRONG = "import os; os.rename(os.path.expanduser('~/Desktop/todo_list_Jan_1'), os.path.expanduser('~/Desktop/todo_list_Jan_3'))"
DE_KW = dict(require_a11y_tree=False)  # BOTH arms: the Drums arm has no accessibility tree; equalized, recorded in pins, differs from the published default
MIB = 1 << 20


def hr_kw(cfg):
    return dict(DE_KW, cache_dir=os.path.join(cfg.get("work_dir", "/tmp/drums-host-run"), "cache"))


def desktop_kw(cfg):
    """Supervisor keyword arguments for the GNOME/uid/private-bus configuration; empty (the defaults) unless the config asks for it."""
    kw = {}
    if cfg.get("drums_desktop", "openbox") != "openbox":
        kw["desktop"] = cfg["drums_desktop"]
    if cfg.get("drums_uid") is not None:
        kw.update(uid=int(cfg["drums_uid"]), gid=int(cfg.get("drums_gid", cfg["drums_uid"])), user=cfg.get("drums_user", "user"))
    if cfg.get("drums_system_bus"):
        kw["system_bus"] = True
    return kw


def image_kw(cfg):
    """The pinned image hash and its VERIFIED receipt for CRIU on an image base root; empty (the defaults) unless the config carries them."""
    return {"image_sha256": cfg["image_sha256"], "image_receipt": cfg.get("image_receipt")} if cfg.get("image_sha256") else {}


def build_drums(cfg, work):
    """Supervisor, template and pool with cgroup limits at the guest budget (cpus and memory from the config). Returns (supervisor, pool, prep_seconds): prep is EXTERNAL."""
    from envd_linux import limits as lim
    from envd_linux import realkit
    from envd_linux.criu import Criu
    from envd_linux.scalecua.pool import DrumsSessionPool
    from envd_linux.session import Supervisor
    from envd_linux.template import build_home_template
    from envd_linux.session import HOME as DEFAULT_HOME
    t0 = time.time()
    kit = realkit.DEFAULT_KIT
    tpl = os.path.join(work, "tpl")
    home = cfg.get("drums_home", DEFAULT_HOME)
    base_root = cfg.get("drums_base_root", "/")
    home_tpl = os.path.join(tpl, home.lstrip("/"))
    if base_root == "/":
        build_home_template(home_tpl)  # a Chromium first-run profile built from the host's browser path
    # with another base root (a read-only image root) the host's browser path need not exist there, and the home comes from that tree: only the Desktop directory is laid down
    os.makedirs(os.path.join(home_tpl, "Desktop"), exist_ok=True)
    if cfg.get("drums_template_overlay"):  # the owner-prepared, rootcheck-verified kit/runtime tree that must be visible inside a custom base root
        subprocess.run(["cp", "-a", cfg["drums_template_overlay"].rstrip("/") + "/.", tpl + "/"], check=True)
    limits = lim.Limits(cpus=float(cfg.get("guest_cpus", 2)), memory_bytes=int(cfg.get("guest_memory_mib", 4096)) * MIB)
    crit = cfg.get("incumbent") == "virtualbox" or bool(cfg.get("drums_criu"))
    sup = Supervisor(os.path.join(work, "state"), tpl, root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=kit,
                     init_python=os.path.join(kit, "venv", "bin", "python"), server_mode="module", ready_timeout=120, criu_friendly=crit,
                     criu=Criu() if crit else None, sweep_dir=os.path.join(work, "led"), limits=limits, home=home, base_root=base_root, **desktop_kw(cfg), **image_kw(cfg))
    pool = DrumsSessionPool(sup, 4, health_timeout=90, eager_reset=False)
    return sup, pool, time.time() - t0


def drums_limits_readback(sup):
    """The limits the KERNEL reports for a live session cgroup (v2 or v1), as {cpus, memory_mib}."""
    from envd_linux import limits as lim
    cg = next(iter(sup._cgroups.values()), None)
    if cg is None:
        return None
    rb = lim.read_back(cg)
    if "cpu.max" in rb:
        q, per = rb["cpu.max"].split()
        return {"cpus": int(q) / int(per), "memory_mib": int(rb["memory.max"]) / MIB, "raw": rb}
    return {"cpus": int(rb["cpu.cfs_quota_us"]) / int(rb["cpu.cfs_period_us"]), "memory_mib": int(rb["memory.limit_in_bytes"]) / MIB, "raw": rb}


def arm_factories(cfg, pool, de):
    from . import osworld_matched as om
    inc = cfg["incumbent"]
    kw = hr_kw(cfg)
    drums = om.drums_factory(pool, desktop_env_module=de, **kw)
    if inc == "virtualbox":
        other = om.incumbent_factory("virtualbox", path_to_vm=cfg["vm"], screen_size=(1280, 800), desktop_env_module=de, **kw)
    else:
        other = om.incumbent_factory("docker", path_to_vm=cfg["qcow2"], screen_size=(1280, 800), desktop_env_module=de, **kw)
    return {"drums": drums, hr.INCUMBENTS[inc]["arm"]: other}


def _sh(cmd, timeout=60):
    return hr.sh(cmd, timeout)


def run_gates(cfg, out):
    from . import docker_telemetry as dt
    from . import osworld_matched as om
    from . import vbox
    work = cfg.get("work_dir", os.path.join(out, "work"))
    os.makedirs(work, exist_ok=True)
    inc, cpus, mem = cfg["incumbent"], cfg.get("guest_cpus", 2), cfg.get("guest_memory_mib", 4096)
    snap_mode = hr.INCUMBENTS[inc]["reuse"] == "post_setup"
    de, stubs = hr.strict_import(tuple(cfg.get("allow_stub", [])))
    task = json.load(open(os.path.join(cfg["osworld_src"], "evaluation_examples", "examples", "os", hr.FIRST_TASK + ".json")))
    inputs = hr.compute_inputs(cfg)
    res = {"config_sha256": hr.config_hash(cfg), "inputs": inputs, "inputs_sha256": None, "mode": cfg["mode"], "incumbent": inc, "stubbed_modules": stubs, "arms": {}, "parity_decisions": {},
           "gate_set": "snapshot (process/file token, Writer pixel state, scores)" if snap_mode else "cold reset (state isolation across resets, scores); no snapshot exists or is claimed"}
    sup = None
    try:
        sup, pool, prep_s = build_drums(cfg, work)
        res["drums_prep_s"] = prep_s
        for arm, factory in arm_factories(cfg, pool, de).items():
            env = factory(0)
            lease = om._DesktopEnvLease(env)
            sh_out = lambda script, env=env: guestexec.sh_out(env, script)  # /execute via the shared helper; a fault raises, it never returns blank output
            shot = lambda env=env: env.controller.get_screenshot()
            try:
                lease.reset(task)
                a = {"guest_exec_selftest": guestexec.selftest(lambda script, env=env: guestexec.guest_run(env, script))}
                if not a["guest_exec_selftest"]["pass"]:
                    raise guestexec.GuestExecError(f"the probe helper failed its own selftest on {arm}: {a['guest_exec_selftest']}")
                if arm == "drums":
                    pd = hr.parity_decision(arm, cpus, mem, limits_readback=drums_limits_readback(sup))
                elif inc == "virtualbox":
                    rb = vbox.vm_readback(cfg["vm"])
                    pd = hr.parity_decision(arm, cpus, mem, limits_readback={"cpus": rb["cpus"], "memory_mib": rb["memory_mib"]}, guest=hr.guest_readback(sh_out))
                else:
                    facts = dt.container_facts(_sh)
                    a["container_facts"] = facts
                    a["kvm_in_use"] = bool(facts) and all(f["has_kvm_device"] and not f["kvm_disabled"] for f in facts)
                    pd = hr.parity_decision(arm, cpus, mem, guest=hr.guest_readback(sh_out))
                res["parity_decisions"][arm] = pd
                step = lambda code, env=env: env.step(code)
                if arm != "drums" and not snap_mode:
                    a["cold_isolation"] = hr.cold_isolation_gate(lease, task, sh_out, step)
                    a["cold_scores"] = hr.cold_score_gate(lease, task, CORRECT, WRONG)
                    a["pass"] = a["cold_isolation"]["pass"] and a["cold_scores"]["pass"] and a.get("kvm_in_use", True)
                elif snap_mode:
                    a["snapshot_state"] = hr.snapshot_gate(lease, task, sh_out, step)
                    a["gui_state"] = hr.gui_state_gate(lease, task, sh_out, step, shot)
                    a["scores"] = hr.score_gate(lease, task, CORRECT, WRONG, f"gate-score-{arm}")
                    a["pass"] = a["snapshot_state"]["pass"] and a["gui_state"]["pass"] and a["scores"]["pass"]
                else:  # the Drums arm of a cold-reset stage
                    a["cold_isolation"] = hr.cold_isolation_gate(lease, task, sh_out, step)
                    a["cold_scores"] = hr.cold_score_gate(lease, task, CORRECT, WRONG)
                    a["pass"] = a["cold_isolation"]["pass"] and a["cold_scores"]["pass"]
                a["pass"] = bool(a["pass"] and pd["readbacks_equal_target"])
            except Exception as e:  # kept raw: a failed gate is a result
                a = {"pass": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
            finally:
                try:
                    env.close()
                except Exception:
                    pass
            res["arms"][arm] = a
        if inc == "virtualbox":
            names = [v.get("snapshot_state", {}).get("snapshot_name") for v in res["arms"].values()] + [v.get("gui_state", {}).get("snapshot_name") for v in res["arms"].values()] + ["gate-score-virtualbox"]
            res["gate_snapshot_cleanup"] = vbox.delete_snapshots(cfg["vm"], [n for n in names if n])
    finally:
        if sup is not None:
            sup.close()
    import hashlib
    res["inputs_sha256"] = hashlib.sha256(hr.canon(inputs).encode()).hexdigest()
    res["all_ok"] = len(res["arms"]) == 2 and all(a.get("pass") for a in res["arms"].values())
    res["application_window_covered"] = bool(snap_mode)
    json.dump(res, open(os.path.join(out, "gates.json"), "w"), indent=1, default=str)
    print("GATES " + ("PASS" if res["all_ok"] else "FAIL: see gates.json"))
    return 0 if res["all_ok"] else 1


def _git(path, *args):
    try:
        return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return "unavailable"


EXPECT_PROVIDER = {"drums": "DrumsProvider", "docker_kvm": "DockerProvider", "virtualbox": "VirtualBoxProvider"}


def make_arms(factories, reuse):
    """{arm label: callable building that arm's adapter}. Label, factory and expected provider are bound PER ITERATION through a helper function: a comprehension lambda that closes over
    the loop variable binds late, and the first real run labelled the Drums factory's artifacts with the incumbent's name."""
    def bind(label, factory):
        return lambda: om_adapter(label, factory, reuse, EXPECT_PROVIDER.get(label))
    return {label: bind(label, factory) for label, factory in factories.items()}


def om_adapter(label, factory, reuse, expect):
    from . import osworld_matched as om
    return om.DesktopEnvAdapter(label, factory, reuse=reuse, expect_provider=expect)


def run_matched(cfg, out, gates=None, gates_reuse=None):
    from envd_linux.cgroup_telemetry import CgroupProbe
    from . import docker_telemetry as dt
    from . import first_check as fc
    from . import osworld_matched as om
    from . import vbox
    work = cfg.get("work_dir", os.path.join(out, "work"))
    os.makedirs(work, exist_ok=True)
    inc, mode = cfg["incumbent"], cfg["mode"]
    reuse = hr.INCUMBENTS[inc]["reuse"]
    arm_name = hr.INCUMBENTS[inc]["arm"]
    de, stubs = hr.strict_import(tuple(cfg.get("allow_stub", [])))
    tasks = hr.load_tasks(cfg, cfg["osworld_src"])
    by_id = {t["id"]: t["task"] for t in tasks}
    scripts = {t["id"]: ([CORRECT] if t["id"] == hr.FIRST_TASK else t["actions"]) for t in tasks}
    seq = om.sample_tasks(list(by_id), cfg.get("rollouts_per_task", 5), seed=cfg.get("seed", 1))
    price = om.Prices.from_json(cfg["prices"])
    t_start, ceiling = time.time(), cfg["max_runtime_s"]
    status = {"started": t_start, "max_runtime_s": ceiling, "aborted": None, "mode": mode}
    gates = gates or json.load(open(os.path.join(out, "gates.json")))
    hr.stage_evidence(cfg, out)
    sup = None
    try:
        sup, pool, prep_s = build_drums(cfg, work)
        factories = arm_factories(cfg, pool, de)
        arms = make_arms(factories, reuse)
        if inc == "virtualbox":
            rb = vbox.vm_readback(cfg["vm"])
            inc_probe = vbox.VBoxProbe(rb["uuid"], rb["name"])
            incumbent_readback = rb
        else:
            inc_probe = CgroupProbe(lambda: dt.docker_cgroup_dirs(_sh))
            incumbent_readback = {"docker_image_digest": cfg.get("docker_image_digest"), "qcow2_sha256": cfg.get("qcow2_sha256")}
        probes = {"drums": CgroupProbe(lambda: [d for cg in list(sup._cgroups.values()) for d in cg.dirs]), arm_name: inc_probe}

        def ceiling_hook(adapter):
            if time.time() - t_start > ceiling:
                raise hr.CeilingReached(f"runtime ceiling {ceiling}s reached after a completed block")

        def vm_hook(adapter):
            vbox.delete_snapshots(cfg["vm"], adapter.snapshots_taken)
            ceiling_hook(adapter)

        def docker_hook(adapter):  # leftover containers of the stock image after a block are removed (and the time reported), never silently ignored
            rc, ids = _sh(["docker", "ps", "-aq", "--filter", f"ancestor={hr.DOCKER_IMAGE}"])
            if ids.split():
                _sh(["docker", "rm", "-f", "-v", *ids.split()])
            ceiling_hook(adapter)
        src_id = hr.source_identity()
        pins = {"stage": "host_run", "mode": mode, "incumbent": inc, "reuse_both_arms": reuse, "engineering_validation_only": True, "config_sha256": hr.config_hash(cfg),
                "inputs": gates["inputs"], "inputs_sha256": gates["inputs_sha256"], "parity_decisions": gates["parity_decisions"], "osworld_commit": _git(cfg["osworld_src"], "rev-parse", "HEAD"),
                **src_id, "stubbed_modules": stubs, "allow_stub": cfg.get("allow_stub", []), "incumbent_readback_before": incumbent_readback, "host_kind": cfg.get("host_kind"),
                "kernel": os.uname().release, "instance_type": cfg.get("instance_type"), "tasks": list(by_id), "sequence": seq, "blocks": cfg.get("blocks", 4), "concurrency": 1,
                "guest_budget": {"cpus": cfg.get("guest_cpus", 2), "memory_mib": cfg.get("guest_memory_mib", 4096)}, "require_a11y_tree": False, "screen_size": [1280, 800],
                "prices_file": cfg["prices"], "image_manifest": cfg.get("image_manifest"), "incumbent_image_prep": f"NOT timed unless vm_prep_s_reported is set: {cfg.get('vm_prep_s_reported')}",
                "known_risks": hr.KNOWN_RISKS, "non_equivalence": "docs/incumbent-preflight-plan.md section 4a",
                "drums_home": cfg.get("drums_home", "/home/drums"), "drums_base_root": cfg.get("drums_base_root", "/"), "gates_reuse": gates_reuse, "arm_provider_expect": {n: EXPECT_PROVIDER[n] for n in factories}, "timing_boundaries": "inside the run clock: guest open, every reset (a cold reboot for docker_kvm), steps, evaluation, close; reported apart: Drums pool build, image fetch, between-block cleanup"}
        label = None if mode == "comparison" else "architecture measurement only: image composition not shown equivalent; no cross-arm ratio, no cost-per-valid comparison, no claim"
        hooks = {"drums": ceiling_hook, arm_name: vm_hook if inc == "virtualbox" else docker_hook}
        summary = fc.run_alternating(arms, by_id, seq, lambda: om.ScriptedAgent(scripts), cfg.get("blocks", 4), {"drums": price, arm_name: price}, out, pins, concurrency=1,
                                     max_steps=cfg.get("max_steps", 5), external_prep={"drums": prep_s, arm_name: float(cfg.get("vm_prep_s_reported") or 0.0)}, env_probes=probes,
                                     block_hooks=hooks, name_prefix=(reuse == "post_setup"), not_a_comparison=label,
                                     arm_provider_expect={n: EXPECT_PROVIDER[n] for n in factories})
        json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1, default=str)
        status["finished"] = True
    except hr.CeilingReached as e:
        status["aborted"] = str(e)
    except Exception as e:  # kept raw
        status["aborted"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        status["ended"] = time.time()
        json.dump(status, open(os.path.join(out, "run_status.json"), "w"), indent=1)
        if sup is not None:
            sup.close()
        hr.export(out)
    print("RUN " + ("COMPLETE" if status.get("finished") else f"ABORTED: {status['aborted']}"))
    return 0 if status.get("finished") else 1
