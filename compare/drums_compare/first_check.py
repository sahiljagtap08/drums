"""First matched check driver (docs/incumbent-preflight-plan.md, item I3): ENGINEERING VALIDATION ONLY. Alternating blocks (ABAB...), one fresh adapter per block so the pool
build and the first setup are inside every block's wall time and cost, concurrency 1 by default, the same tasks and agent on both arms, baseline reuse on for both,
every raw row, fault and pin kept. It computes no verdict: the summary is flagged `engineering_validation_only` and carries no win field.

Real arms (needs a host that can run the incumbent: see the preflight plan) are built with `real_arms`; the fake-adapter test shows the driver's own accounting."""

import json
import os
import time

from .osworld_matched import DesktopEnvAdapter, Prices, compare, drums_factory, incumbent_factory, run_arm


def real_arms(pool, vbox_path_to_vm, reuse="post_setup", desktop_env_module=None, screen_size=(1280, 800), **kw):
    """Two arm factories over the SAME public `DesktopEnv` and the SAME reuse flag: the Drums provider bridge and the VirtualBox provider AS SHIPPED
    (its fixed sleeps stay; shortening them would be a disclosed, separately labelled tuning)."""
    return {
        "drums": lambda: DesktopEnvAdapter("drums", drums_factory(pool, screen_size=screen_size, desktop_env_module=desktop_env_module, **kw), reuse=reuse),
        "virtualbox": lambda: DesktopEnvAdapter("virtualbox", incumbent_factory("virtualbox", path_to_vm=vbox_path_to_vm, screen_size=screen_size,
                                                                                 desktop_env_module=desktop_env_module, **kw), reuse=reuse),
    }


def run_alternating(arms, tasks_by_id, sequence, agent_factory, blocks, prices, out_dir, pins, concurrency=1, max_steps=50, canary=True, clock=time.time):
    """arms: {name: callable -> fresh Adapter}; prices: {name: Prices} (the owner's own price inputs); pins: any JSON-able facts (versions, commits, settings)."""
    names = list(arms)
    if len(names) != 2:
        raise ValueError("a matched check has exactly two arms")
    os.makedirs(out_dir, exist_ok=True)
    json.dump(pins, open(os.path.join(out_dir, "pins.json"), "w"), indent=1, default=str)
    per_arm = {n: {"blocks": 0, "rollouts": 0, "valid": 0, "faults": {}, "wall_s": 0.0, "cost_usd": 0.0, "first_rollout_work_s": 0.0, "interference": 0, "results": []} for n in names}
    order = []
    for i in range(blocks):
        for n in (names if i % 2 == 0 else names[::-1]):
            order.append((i, n))
            res = run_arm(arms[n](), tasks_by_id, sequence, agent_factory, concurrency, prices[n], max_steps=max_steps, clock=clock, canary=canary)
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
    summary = {"engineering_validation_only": True, "no_headline_claim": True, "no_training_claim": True, "block_order": order, "concurrency": concurrency, "arms": {}}
    for n, a in per_arm.items():
        summary["arms"][n] = {k: v for k, v in a.items() if k != "results"}
        summary["arms"][n]["cost_per_valid_usd"] = (a["cost_usd"] / a["valid"]) if a["valid"] else None
        summary["arms"][n]["valid_per_hour"] = (a["valid"] / (a["wall_s"] / 3600)) if a["wall_s"] else None
    if all(per_arm[n]["valid"] for n in names):
        merged = {n: {"summary": {"cost_usd": per_arm[n]["cost_usd"]}, "rows": [r for res in per_arm[n]["results"] for r in res["rows"]]} for n in names}
        summary["raw_ratio_first_over_second_NOT_A_CLAIM"] = compare(merged[names[0]], merged[names[1]])["valid_per_dollar_ratio_A_over_B"]
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w"), indent=1, default=str)
    return summary
