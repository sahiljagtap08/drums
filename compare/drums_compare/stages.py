"""The LIVE stages of host_run (need the authorized host, the VirtualBox VM and the Drums runtime). Written without a host: only `snapshot_gate` / `score_gate` (in host_run)
are exercised with fakes; these functions have never run. Imports are lazy so the fail-closed `check` works anywhere."""

import json
import os
import subprocess
import time

from . import host_run as hr

CORRECT = "import os; os.rename(os.path.expanduser('~/Desktop/todo_list_Jan_1'), os.path.expanduser('~/Desktop/todo_list_Jan_2'))"
WRONG = "import os; os.rename(os.path.expanduser('~/Desktop/todo_list_Jan_1'), os.path.expanduser('~/Desktop/todo_list_Jan_3'))"
DE_KW = dict(require_a11y_tree=False)  # BOTH arms: the Drums arm has no accessibility tree; equalized, recorded in pins, differs from the published default


def build_drums(cfg, work):
    """Supervisor, template and pool with the incumbent-shaped cgroup limits. Returns (supervisor, pool, prep_seconds): the caller reports prep as EXTERNAL."""
    from envd_linux import limits as lim
    from envd_linux import realkit
    from envd_linux.criu import Criu
    from envd_linux.scalecua.pool import DrumsSessionPool
    from envd_linux.session import Supervisor
    from envd_linux.template import build_home_template
    t0 = time.time()
    kit = realkit.DEFAULT_KIT
    tpl = os.path.join(work, "tpl")
    build_home_template(os.path.join(tpl, "home", "drums"))
    os.makedirs(os.path.join(tpl, "home", "drums", "Desktop"), exist_ok=True)
    sup = Supervisor(os.path.join(work, "state"), tpl, root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=kit,
                     init_python=os.path.join(kit, "venv", "bin", "python"), server_mode="module", ready_timeout=120, criu_friendly=True, criu=Criu(),
                     sweep_dir=os.path.join(work, "led"), limits=lim.INCUMBENT_SHAPE)
    pool = DrumsSessionPool(sup, 4, health_timeout=90, eager_reset=False)
    return sup, pool, time.time() - t0


def _arm_envs(cfg, pool, de):
    from . import osworld_matched as om
    return {"drums": om.drums_factory(pool, desktop_env_module=de, **hr_kw(cfg)), "virtualbox": om.incumbent_factory("virtualbox", path_to_vm=cfg["vm"], screen_size=(1280, 800),
                                                                                                                  desktop_env_module=de, **hr_kw(cfg))}


def hr_kw(cfg):
    return dict(DE_KW, cache_dir=os.path.join(cfg.get("work_dir", "/tmp/drums-host-run"), "cache"))


def run_gates(cfg, out):
    from . import osworld_matched as om
    from . import vbox
    work = cfg.get("work_dir", os.path.join(out, "work"))
    os.makedirs(work, exist_ok=True)
    de, stubs = hr.strict_import(tuple(cfg.get("allow_stub", [])))
    task = json.load(open(os.path.join(cfg["osworld_src"], "evaluation_examples", "examples", "os", hr.FIRST_TASK + ".json")))
    sup = None
    res = {"config_sha256": hr.config_hash(cfg), "stubbed_modules": stubs, "arms": {}, "application_window_covered": False,
           "note": "gate 2 of the plan: live process + in-memory token + file marker survive snapshot/revert; the application-window part is NOT covered"}
    try:
        sup, pool, prep_s = build_drums(cfg, work)
        res["drums_prep_s"] = prep_s
        for arm, factory in _arm_envs(cfg, pool, de).items():
            env = factory(0)
            lease = om._DesktopEnvLease(env)
            sh_out = lambda script, env=env: env.controller.run_bash_script(script, timeout=30).get("output", "")
            try:
                a = hr.snapshot_gate(lease, task, sh_out, lambda code, env=env: env.step(code))
                a["scores"] = hr.score_gate(lease, task, CORRECT, WRONG, f"gate-score-{arm}")
                a["pass"] = all(a[k] for k in ("process_alive_after_revert", "same_token_after_revert", "file_marker_restored", "post_save_file_removed", "token_before")) and a["scores"]["pass"]
            except Exception as e:  # kept raw: a failed gate is a result
                a = {"pass": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
            finally:
                try:
                    env.close()
                except Exception:
                    pass
            res["arms"][arm] = a
        if cfg.get("vm"):
            names = [v.get("snapshot_name") for v in res["arms"].values() if v.get("snapshot_name")] + [f"gate-score-virtualbox"]
            res["gate_snapshot_cleanup"] = vbox.delete_snapshots(cfg["vm"], names)
    finally:
        if sup is not None:
            sup.close()
    res["all_ok"] = len(res["arms"]) == 2 and all(a.get("pass") for a in res["arms"].values())
    json.dump(res, open(os.path.join(out, "gates.json"), "w"), indent=1, default=str)
    print("GATES " + ("PASS" if res["all_ok"] else "FAIL: see gates.json"))
    return 0 if res["all_ok"] else 1


def _git(path, *args):
    try:
        return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return "unavailable"


def run_matched(cfg, out):
    from envd_linux.cgroup_telemetry import CgroupProbe
    from . import first_check as fc
    from . import osworld_matched as om
    from . import vbox
    work = cfg.get("work_dir", os.path.join(out, "work"))
    os.makedirs(work, exist_ok=True)
    de, stubs = hr.strict_import(tuple(cfg.get("allow_stub", [])))
    tasks = hr.load_tasks(cfg, cfg["osworld_src"])
    by_id = {t["id"]: t["task"] for t in tasks}
    scripts = {t["id"]: (t.get("actions") or [CORRECT]) if t["id"] != hr.FIRST_TASK else [CORRECT] for t in tasks}
    seq = om.sample_tasks(list(by_id), cfg.get("rollouts_per_task", 5), seed=cfg.get("seed", 1))
    rb = vbox.vm_readback(cfg["vm"])
    price = om.Prices.from_json(cfg["prices"])
    t_start = time.time()
    ceiling = cfg["max_runtime_s"]
    status = {"started": t_start, "max_runtime_s": ceiling, "aborted": None}
    sup = None
    try:
        sup, pool, prep_s = build_drums(cfg, work)
        arms = fc.real_arms(pool, cfg["vm"], reuse="post_setup", desktop_env_module=de, screen_size=(1280, 800), **hr_kw(cfg))
        probes = {"drums": CgroupProbe(lambda: [d for cg in list(sup._cgroups.values()) for d in cg.dirs]),
                  "virtualbox": vbox.VBoxProbe(rb["uuid"], rb["name"])}

        def ceiling_hook(adapter):
            if time.time() - t_start > ceiling:
                raise hr.CeilingReached(f"runtime ceiling {ceiling}s reached after a completed block")

        def vm_hook(adapter):
            vbox.delete_snapshots(cfg["vm"], adapter.snapshots_taken)  # this run's own snapshots; the time is reported as between_block_s
            ceiling_hook(adapter)
        pins = {"stage": "host_run", "engineering_validation_only": True, "config_sha256": hr.config_hash(cfg), "osworld_commit": _git(cfg["osworld_src"], "rev-parse", "HEAD"),
                "drums_commit": _git(os.path.dirname(os.path.abspath(__file__)), "rev-parse", "HEAD"), "drums_tree_dirty": bool(_git(os.path.dirname(os.path.abspath(__file__)), "status", "--porcelain")),
                "stubbed_modules": stubs, "vm_readback_before": rb, "virtualbox_version": subprocess.run(["VBoxManage", "--version"], capture_output=True, text=True).stdout.strip(),
                "kernel": os.uname().release, "instance_type": cfg.get("instance_type"), "tasks": list(by_id), "sequence": seq, "blocks": cfg.get("blocks", 4), "concurrency": 1,
                "require_a11y_tree": False, "screen_size": [1280, 800], "reuse": "post_setup (both arms, public snapshot API)", "prices_file": cfg["prices"],
                "image_manifest": cfg.get("image_manifest"), "incumbent_image_prep": f"NOT timed in this run unless vm_prep_s_reported is set: {cfg.get('vm_prep_s_reported')}",
                "known_risks": hr.KNOWN_RISKS, "non_equivalence": "docs/incumbent-preflight-plan.md section 4a", "vm_network": rb.get("nic1")}
        summary = fc.run_alternating(arms, by_id, seq, lambda: om.ScriptedAgent(scripts), cfg.get("blocks", 4), {"drums": price, "virtualbox": price}, out, pins, concurrency=1,
                                     max_steps=cfg.get("max_steps", 5), external_prep={"drums": prep_s, "virtualbox": float(cfg.get("vm_prep_s_reported") or 0.0)},
                                     env_probes=probes, block_hooks={"virtualbox": vm_hook, "drums": ceiling_hook})
        summary["parity_readbacks"] = {"vm": {k: rb[k] for k in ("cpus", "memory_mib", "cpuexecutioncap")}, "drums_cgroup_limits_as_read_by_kernel":
                                       [b["sessions"] for b in summary["arms"]["drums"]["per_environment"]][:1]}
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
