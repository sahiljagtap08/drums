"""First matched check driver (docs/incumbent-preflight-plan.md, item I3): ENGINEERING VALIDATION ONLY. Alternating blocks (ABAB...), one fresh adapter per block, concurrency 1 by
default, the same tasks and agent on both arms, baseline reuse on for both, every raw row, fault and pin kept. It computes no verdict: the summary is flagged
`engineering_validation_only` and carries no win field.

WHAT IS AND IS NOT INSIDE THE CLOCK. Inside each block's run_arm clock: the adapter's `open` (building the DesktopEnv objects), the first start of each slot (a Drums pool
creates sessions lazily on first use unless it was warmed beforehand), the first setup, the save and every revert. OUTSIDE it: anything done before `run_alternating` is called
(building the supervisor, the template, the pool, a VM image; `pool.warm()`), and the time the arm factory takes to build the adapter object. Those are reported, not hidden:
the factory time is measured per block (`adapter_build_s`) and any earlier preparation is passed in as `external_prep` ({arm: seconds}); the summary carries cost and cost per valid
rollout both WITHOUT and WITH that preparation, and the raw ratio uses the WITH figures. Nothing here says that all pool or image build is counted.

RESOURCES. Host-wide memory (sampled from /proc/meminfo during each block) and host-wide CPU (busy time from /proc/stat across each block) are recorded. Per-environment figures (a
VM process against a session cgroup) are NOT captured: `resource_parity_claimed` is False and the per-environment capture is marked MISSING.

Real arms (needs a host that can run the incumbent: see the preflight plan) are built with `real_arms`; the fake-adapter test shows the driver's own accounting."""

import json
import os
import time

from .osworld_matched import DesktopEnvAdapter, Prices, compare, drums_factory, incumbent_factory, run_arm


def host_used_kib():
    """Host memory in use (MemTotal - MemAvailable) from /proc/meminfo, in KiB."""
    m = {k: int(v.split()[0]) for k, v in (ln.split(":") for ln in open("/proc/meminfo"))}
    return m["MemTotal"] - m["MemAvailable"]


def host_cpu_busy_s():
    """Cumulative non-idle CPU seconds of the whole host from /proc/stat (user, nice, system, irq, softirq, steal)."""
    f = open("/proc/stat").readline().split()[1:]
    v = [int(x) for x in f]
    hz = os.sysconf("SC_CLK_TCK")
    return (v[0] + v[1] + v[2] + v[5] + v[6] + (v[7] if len(v) > 7 else 0)) / hz


def real_arms(pool, vbox_path_to_vm, reuse="post_setup", desktop_env_module=None, screen_size=(1280, 800), **kw):
    """Two arm factories over the SAME public `DesktopEnv` and the SAME reuse flag: the Drums provider bridge and the VirtualBox provider AS SHIPPED
    (its fixed sleeps stay; shortening them would be a disclosed, separately labelled tuning)."""
    return {
        "drums": lambda: DesktopEnvAdapter("drums", drums_factory(pool, screen_size=screen_size, desktop_env_module=desktop_env_module, **kw), reuse=reuse),
        "virtualbox": lambda: DesktopEnvAdapter("virtualbox", incumbent_factory("virtualbox", path_to_vm=vbox_path_to_vm, screen_size=screen_size,
                                                                                 desktop_env_module=desktop_env_module, **kw), reuse=reuse),
    }


def run_alternating(arms, tasks_by_id, sequence, agent_factory, blocks, prices, out_dir, pins, concurrency=1, max_steps=50, canary=True, clock=time.time,
                    external_prep=None, mem_reader=host_used_kib, cpu_reader=host_cpu_busy_s, mem_period=1.0):
    """arms: {name: callable -> fresh Adapter}; prices: {name: Prices} (the owner's own price inputs); pins: any JSON-able facts (versions, commits, settings);
    external_prep: {arm: seconds} of preparation done BEFORE this call (reported, and added in the 'with prep' figures). mem_reader / cpu_reader may be None to record nothing
    (then the telemetry is reported as MISSING, never as zero)."""
    external_prep = external_prep or {}
    names = list(arms)
    if len(names) != 2:
        raise ValueError("a matched check has exactly two arms")
    os.makedirs(out_dir, exist_ok=True)
    json.dump(pins, open(os.path.join(out_dir, "pins.json"), "w"), indent=1, default=str)
    per_arm = {n: {"blocks": 0, "rollouts": 0, "valid": 0, "faults": {}, "wall_s": 0.0, "cost_usd": 0.0, "first_rollout_work_s": 0.0, "interference": 0, "adapter_build_s": 0.0,
                   "external_prep_s": float(external_prep.get(n, 0.0)), "host_memory_blocks": [], "host_cpu_busy_s_blocks": [], "results": []} for n in names}
    order = []
    for i in range(blocks):
        for n in (names if i % 2 == 0 else names[::-1]):
            order.append((i, n))
            tb = time.time()
            adapter = arms[n]()
            per_arm[n]["adapter_build_s"] += time.time() - tb
            c0 = cpu_reader() if cpu_reader else None
            res = run_arm(adapter, tasks_by_id, sequence, agent_factory, concurrency, prices[n], max_steps=max_steps, clock=clock, canary=canary,
                          mem_reader=mem_reader, mem_period=mem_period)
            if cpu_reader:
                per_arm[n]["host_cpu_busy_s_blocks"].append(cpu_reader() - c0)
            per_arm[n]["host_memory_blocks"].append(res["summary"].get("memory"))
            json.dump(res, open(os.path.join(out_dir, f"block{i}_{n}.json"), "w"), indent=1, default=str)
            s, a = res["summary"], per_arm[n]
            a["blocks"] += 1
            a["rollouts"] += s["rollouts"]
            a["valid"] += s["valid"]
            a["wall_s"] += s["wall_s"]
            a["cost_usd"] += s["cost_usd"]
            a["first_rollout_work_s"] += s["reuse"]["first_rollout_work_s"]
            a["interference"] += s["interference_count"]
            for k, v in s["faults"].items():
                a["faults"][k] = a["faults"].get(k, 0) + v
            a["results"].append(res)
    summary = {"engineering_validation_only": True, "no_headline_claim": True, "no_training_claim": True, "block_order": order, "concurrency": concurrency, "arms": {},
               "resource_parity_claimed": False,
               "per_environment_resource_capture": "MISSING: only host-wide memory and CPU are recorded; no per-VM or per-session figures, so no resource-parity statement can be made",
               "preparation_note": "run_arm clock excludes anything done before run_alternating and the arm-factory time; those are reported as external_prep_s and adapter_build_s and added in the *_with_prep figures"}
    for n, a in per_arm.items():
        o = {k: v for k, v in a.items() if k != "results"}
        prep_s = a["external_prep_s"] + a["adapter_build_s"]
        prep_cost = prices[n].total(prep_s / 3600) - prices[n].fixed_usd if prep_s else 0.0  # the host price over the prep time (the fixed price is counted once, in the blocks)
        o["cost_per_valid_usd_run_arm_only"] = (a["cost_usd"] / a["valid"]) if a["valid"] else None
        o["cost_with_prep_usd"] = a["cost_usd"] + prep_cost
        o["cost_per_valid_usd_with_prep"] = (o["cost_with_prep_usd"] / a["valid"]) if a["valid"] else None
        o["valid_per_hour_run_arm_only"] = (a["valid"] / (a["wall_s"] / 3600)) if a["wall_s"] else None
        o["valid_per_hour_with_prep"] = (a["valid"] / ((a["wall_s"] + prep_s) / 3600)) if (a["wall_s"] + prep_s) else None
        o["host_memory"] = "MISSING (no reader)" if mem_reader is None else [m for m in a["host_memory_blocks"]]
        o["host_cpu_busy_s"] = "MISSING (no reader)" if cpu_reader is None else a["host_cpu_busy_s_blocks"]
        summary["arms"][n] = o
    if all(per_arm[n]["valid"] for n in names):
        merged = {n: {"summary": {"cost_usd": summary["arms"][n]["cost_with_prep_usd"]}, "rows": [r for res in per_arm[n]["results"] for r in res["rows"]]} for n in names}
        summary["raw_ratio_first_over_second_with_prep_NOT_A_CLAIM"] = compare(merged[names[0]], merged[names[1]])["valid_per_dollar_ratio_A_over_B"]
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w"), indent=1, default=str)
    return summary
