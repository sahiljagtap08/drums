"""Scores two raw result directories and states a verdict that can go either way.

    python -m drums_compare.scorer RESULTS_DRUMS RESULTS_BASELINE [--out report.json]

Primary metrics: valid rollouts per hour and cost per valid rollout. Secondary: RSS and CPU
per env, invalid-reason counts, interference events, success rate (fidelity check).

Cost model. total_cost = usd_per_hour_total * billed_hours, where billed_hours =
(provision_s + wall time of the run) / 3600, i.e. idle time and boot are paid for. For
confidence intervals attempts are bootstrapped; each attempt carries its slot time
(wall_s), and cost per valid = overhead_factor * sum(slot_s) / sum(valid) where
overhead_factor = total_cost / sum(slot_s) of the real run. That keeps the point
estimate exactly equal to total_cost / valid.

The verdict rule is fixed in docs/linux-comparison-design.md section 7 and implemented in
`verdict()`. Fake runs, runs below MIN_VALID, or runs with missing hardware or cost
inputs get no verdict.
"""

import argparse
import json
import math
import os
import random

MIN_VALID = 50
BOOT = 2000


def load(dirpath):
    with open(os.path.join(dirpath, "run.json")) as f:
        run = json.load(f)
    attempts = [json.loads(line) for line in open(os.path.join(dirpath, "attempts.jsonl"))]
    res_path = os.path.join(dirpath, "resources.jsonl")
    samples = [json.loads(line) for line in open(res_path)] if os.path.exists(res_path) else []
    return run, attempts, samples


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def percentile(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    i = (len(xs) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def resource_summary(samples):
    envs = [s for s in samples if s.get("kind") == "env"]
    out = {"n_samples": len(envs), "rss_mib_mean": None, "rss_mib_p95": None,
           "pss_mib_mean": None, "cpu_cores_mean_per_env": None, "host_mem_used_frac_mean": None}
    if envs:
        rss = [s["rss_kib"] / 1024 for s in envs]
        out["rss_mib_mean"], out["rss_mib_p95"] = sum(rss) / len(rss), percentile(rss, 0.95)
        pss = [s["pss_kib"] / 1024 for s in envs if s.get("pss_kib") is not None]
        out["pss_mib_mean"] = sum(pss) / len(pss) if pss else None
        by_slot = {}
        for s in envs:
            by_slot.setdefault(s["slot"], []).append(s)
        rates = []
        for rows in by_slot.values():
            rows.sort(key=lambda r: r["t"])
            for a, b in zip(rows, rows[1:]):
                dt = b["t"] - a["t"]
                if dt > 0 and b["cpu_ticks"] >= a["cpu_ticks"]:  # a decrease means a process tree restarted
                    rates.append((b["cpu_ticks"] - a["cpu_ticks"]) / b["clk_tck"] / dt)
        out["cpu_cores_mean_per_env"] = sum(rates) / len(rates) if rates else None
    hosts = [s for s in samples if s.get("kind") == "host"]
    if hosts:
        used = [1 - s["mem_avail_kib"] / s["mem_total_kib"] for s in hosts]
        out["host_mem_used_frac_mean"] = sum(used) / len(used)
    return out


def side_metrics(run, attempts, samples):
    n = len(attempts)
    valid = [a for a in attempts if a["valid"]]
    wall_s = run["t_end"] - run["t_start"]
    billed_s = run.get("provision_s", 0.0) + wall_s
    usd_h = run["cost_basis"].get("usd_per_hour_total", 0.0)
    total_cost = usd_h * billed_s / 3600
    slot_s = sum(a["wall_s"] for a in attempts)
    reasons = {}
    for a in attempts:
        for r in a["invalid_reasons"]:
            reasons[r] = reasons.get(r, 0) + 1
    succ = sum(1 for a in valid if a["passed"])
    invalid_slot_s = sum(a["wall_s"] for a in attempts if not a["valid"])
    # Optional costs the harness cannot see, supplied by the operator in run.json under
    # cost_basis.other_usd, e.g. {"inference": 12.3, "training": 0.0}. Phase A has none.
    other = run["cost_basis"].get("other_usd", {})
    other_total = sum(other.values())
    interf = [a for a in attempts if a["interference_with"] or "interference:stale_write_after_reset" in a["invalid_reasons"]]
    m = {
        "side": run["side"], "fake": run.get("fake", False), "n_attempts": n, "n_valid": len(valid),
        "valid_fraction": len(valid) / n if n else None,
        "invalid_reasons": reasons,
        "wall_s": wall_s, "billed_s": billed_s, "total_cost_usd": total_cost,
        "valid_per_hour_end_to_end": len(valid) / (billed_s / 3600) if billed_s > 0 else None,
        "valid_per_hour_steady": len(valid) / (wall_s / 3600) if wall_s > 0 else None,
        "cost_per_valid_usd": total_cost / len(valid) if valid else None,
        # Failed-run cost: environment cost attributable to attempts that were not valid.
        "failed_run_cost_usd": total_cost * invalid_slot_s / slot_s if slot_s > 0 else None,
        "other_cost_usd": other,
        # Environment cost plus operator-supplied inference/training cost. The verdict still
        # uses environment cost only (section 7 of the design doc) until Phase B supplies the rest.
        "total_cost_all_usd": total_cost + other_total,
        "total_cost_all_per_valid_usd": (total_cost + other_total) / len(valid) if valid else None,
        "success_rate_among_valid": succ / len(valid) if valid else None,
        "success_ci95": wilson(succ, len(valid)),
        "interference_attempts": len(interf),
        "reset_s_mean": sum(a["reset_s"] for a in attempts) / n if n else None,
        "reset_s_p50": percentile([a["reset_s"] for a in attempts], 0.5),
        "reset_s_p95": percentile([a["reset_s"] for a in attempts], 0.95),
        "resources": resource_summary(samples),
        "slot_seconds": slot_s,
    }
    m["bootstrap"] = bootstrap(attempts, run, billed_s, total_cost, slot_s)
    return m


def bootstrap(attempts, run, billed_s, total_cost, slot_s, seed=12345):
    n = len(attempts)
    nv = sum(1 for a in attempts if a["valid"])
    if n == 0 or nv == 0 or slot_s <= 0:
        return {"valid_per_hour": [None, None], "cost_per_valid": [None, None]}
    rng = random.Random(seed)
    base_rate = nv / (billed_s / 3600)
    base_ratio = slot_s / nv
    rates, costs = [], []
    for _ in range(BOOT):
        s = [attempts[rng.randrange(n)] for _ in range(n)]
        v = sum(1 for a in s if a["valid"])
        ss = sum(a["wall_s"] for a in s)
        if v == 0:
            continue
        ratio = ss / v
        costs.append(total_cost / nv * ratio / base_ratio)  # point estimate scaled by resampled slot-seconds per valid
        rates.append(base_rate * base_ratio / ratio)
    return {"valid_per_hour": [percentile(rates, 0.025), percentile(rates, 0.975)],
            "cost_per_valid": [percentile(costs, 0.025), percentile(costs, 0.975)]}


def _sep(a_lo, a_hi, b_lo, b_hi, lower_is_better):
    """Compare CI A (Drums) with CI B (baseline). Returns 'a', 'b' or 'inconclusive'."""
    if None in (a_lo, a_hi, b_lo, b_hi):
        return "inconclusive"
    if lower_is_better:
        return "a" if a_hi < b_lo else "b" if b_hi < a_lo else "inconclusive"
    return "a" if a_lo > b_hi else "b" if b_lo > a_hi else "inconclusive"


def verdict(d, b):
    """d = Drums metrics, b = baseline metrics. Rule is fixed before any real run."""
    why = []
    for m in (d, b):
        if m["fake"]:
            why.append(f"{m['side']}: fake run, no verdict")
        if m["n_valid"] < MIN_VALID:
            why.append(f"{m['side']}: only {m['n_valid']} valid rollouts, need {MIN_VALID}")
    if why:
        return {"headline": "no_verdict", "reasons": why}
    cost = _sep(*d["bootstrap"]["cost_per_valid"], *b["bootstrap"]["cost_per_valid"], True)
    rate = _sep(*d["bootstrap"]["valid_per_hour"], *b["bootstrap"]["valid_per_hour"], False)
    interf_worse = d["interference_attempts"] / d["n_attempts"] > b["interference_attempts"] / b["n_attempts"] + 0.02
    fid = _sep(*[x for x in d["success_ci95"]], *[x for x in b["success_ci95"]], False)
    out = {"cost_per_valid": {"a": "drums_better", "b": "baseline_better", "inconclusive": "inconclusive"}[cost],
           "valid_per_hour": {"a": "drums_better", "b": "baseline_better", "inconclusive": "inconclusive"}[rate],
           "interference_worse_on_drums": interf_worse,
           "success_rates_differ": fid != "inconclusive"}
    if out["success_rates_differ"]:
        out["headline"] = "no_verdict"
        out["reasons"] = ["same agent but success rates differ beyond CI: environments are not equivalent, fix before comparing"]
    elif cost == "b":
        out["headline"] = "baseline_wins"
    elif cost == "a" and rate != "b" and not interf_worse:
        out["headline"] = "drums_wins"
    elif cost == "a":
        out["headline"] = "mixed"
    else:
        out["headline"] = "inconclusive"
    return out


def check_inputs(run, label):
    problems = []
    if not run.get("hardware"):
        problems.append(f"{label}: hardware missing in run.json")
    if not run.get("cost_basis", {}).get("usd_per_hour_total"):
        problems.append(f"{label}: cost_basis.usd_per_hour_total missing or zero")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("drums")
    ap.add_argument("baseline")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    dr, da, ds = load(a.drums)
    br, ba, bs = load(a.baseline)
    problems = check_inputs(dr, "drums") + check_inputs(br, "baseline")
    if dr["tasks_sha256"] != br["tasks_sha256"]:
        problems.append("task files differ between sides")
    if dr["seed"] != br["seed"]:
        problems.append("seeds differ, so the task order differs")
    if dr.get("agent") != br.get("agent"):
        problems.append("agent ids differ")
    d, b = side_metrics(dr, da, ds), side_metrics(br, ba, bs)
    report = {"drums": d, "baseline": b, "input_problems": problems,
              "verdict": {"headline": "no_verdict", "reasons": problems} if problems
              and not (dr.get("fake") and br.get("fake")) else verdict(d, b)}
    text = json.dumps(report, indent=1, default=str)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
    print(text)
    return report


if __name__ == "__main__":
    main()
