"""Runs N concurrent slots against one side and writes raw results.

    python -m drums_compare.runner --side fake-drums --out results/linux-comparison/fake-drums \
        --tasks tasks/linux_comparison/tasks.json --slots 4 --attempts 40 --seed 1

Only the fake sides exist in this repo today. Real sides are added by registering a
factory in SIDES that builds one Env per slot (see envd_http.EnvdHttpEnv).

Output directory (all raw, nothing aggregated):
  run.json          side, hardware, cost basis, seed, task file hash, timing, fake flag
  attempts.jsonl    one AttemptRecord per attempt, after the run-level interference audit
  audit.json        rows the verifier found under a stale generation, mapped to attempts
  resources.jsonl   per-env RSS/CPU and host samples
"""

import argparse
import dataclasses
import hashlib
import json
import os
import subprocess
import threading
import time

from . import fakes
from .harness import run_attempt
from .resources import LinuxProcReader, Sampler
from .types import Task


def load_tasks(path):
    with open(path) as f:
        doc = json.load(f)
    return [Task(id=t["id"], instruction=t["instruction"], verifier=t["verifier"],
                 max_steps=t.get("max_steps", 30), timeout_s=t.get("timeout_s", 300.0))
            for t in doc["tasks"] if t.get("enabled", True)]


def file_sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return "unknown"


def _fake_side(args):
    world = fakes.FakeWorld()
    envs = [fakes.FakeEnv(i, world, seed=args.seed, latency_s=args.fake_latency_s,
                          p_reset_fail=args.fake_p_reset_fail, p_leak=args.fake_p_leak,
                          p_black=args.fake_p_black) for i in range(args.slots)]
    return envs, fakes.FakeVerifier(world), (lambda i: fakes.FakeAgent(seed=args.seed * 1000 + i)), True


SIDES = {"fake-drums": _fake_side, "fake-linux": _fake_side}


def plan(tasks, n, seed):
    """Attempt k always gets the same task on every side for a given seed."""
    import random
    rng = random.Random(seed)
    order = []
    while len(order) < n:
        block = list(tasks)
        rng.shuffle(block)
        order += block
    return order[:n]


def run(args):
    tasks = load_tasks(args.tasks)
    envs, verifier, agent_factory, is_fake = SIDES[args.side](args)
    schedule = plan(tasks, args.attempts, args.seed)
    os.makedirs(args.out, exist_ok=True)
    counter = {"next": 0}
    lock = threading.Lock()
    records = []

    sampler = None
    if args.sample_resources:
        sampler = Sampler(os.path.join(args.out, "resources.jsonl"), envs, LinuxProcReader(),
                          interval_s=args.sample_interval_s)
        sampler.start()

    t_run0 = time.time()

    def worker(env):
        gen = 0
        while True:
            with lock:
                k = counter["next"]
                if k >= len(schedule):
                    return
                counter["next"] += 1
            gen += 1
            task = schedule[k]
            token = f"D{env.slot}-G{gen}-R{k}"
            aid = f"{args.side}-{k:05d}"
            t = dataclasses.replace(task, instruction=task.instruction.format(token=token, slot=env.slot))
            rec = run_attempt(side=args.side, env=env, agent=agent_factory(k), verifier=verifier,
                              task=t, attempt_id=aid, gen=gen, token=token)
            with lock:
                records.append(rec)

    threads = [threading.Thread(target=worker, args=(e,)) for e in envs]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    t_run1 = time.time()
    if sampler:
        sampler.stop()
    for e in envs:
        e.close()

    audit_rows = _apply_audit(verifier, records, [e.slot for e in envs])
    records.sort(key=lambda r: r.attempt_id)
    with open(os.path.join(args.out, "attempts.jsonl"), "w") as f:
        for r in records:
            f.write(json.dumps(r.to_json()) + "\n")
    with open(os.path.join(args.out, "audit.json"), "w") as f:
        json.dump(audit_rows, f, indent=1)
    meta = {
        "side": args.side, "fake": is_fake, "seed": args.seed, "slots": args.slots,
        "attempts_requested": args.attempts, "t_start": t_run0, "t_end": t_run1,
        "provision_s": args.provision_s,  # billed time before the first attempt; operator-supplied
        "hardware": json.loads(args.hardware_json),
        "cost_basis": json.loads(args.cost_json),
        "tasks_file": args.tasks, "tasks_sha256": file_sha256(args.tasks),
        "git_commit": git_commit(), "agent": args.agent_id,
        "drums_level": args.drums_level,  # L0 or L1 for the Drums side, empty for incumbents
    }
    with open(os.path.join(args.out, "run.json"), "w") as f:
        json.dump(meta, f, indent=1)
    return records


def _apply_audit(verifier, records, slots):
    """Find writes that landed in a slot under a generation other than the one holding it.

    Each slot's timeline is the attempts' lease times (rec.t_start, taken just before the
    lease call). A row stamped inside attempt n's interval whose gen is not n's gen is a
    stale write: its owner is the attempt that held that gen, its victim is attempt n.
    Both are marked invalid. Assumes the verifier clock and this host's clock agree to
    well under the reset time (same host in the intended setup).
    """
    by_slot = {}
    for r in records:
        by_slot.setdefault(r.slot, []).append(r)
    out = []
    for slot in slots:
        timeline = sorted(by_slot.get(slot, []), key=lambda r: r.t_start)
        owner_of = {r.gen: r for r in timeline}
        for row in verifier.rows(slot):
            victim = None
            for r in timeline:
                if r.t_start <= row["ts"]:
                    victim = r
            if victim is None or row["gen"] == victim.gen:
                continue
            owner = owner_of.get(row["gen"])
            out.append({"slot": slot, "gen": row["gen"], "tok": row["tok"], "ts": row["ts"],
                        "owner": owner and owner.attempt_id, "victim": victim.attempt_id})
            for a, other in ((owner, victim), (victim, owner)):
                if a is not None:
                    if other is not None and other.attempt_id not in a.interference_with:
                        a.interference_with.append(other.attempt_id)
                    if "interference:stale_write_after_reset" not in a.invalid_reasons:
                        a.invalid_reasons = sorted(a.invalid_reasons + ["interference:stale_write_after_reset"])
                    a.valid = False
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", required=True, choices=sorted(SIDES))
    ap.add_argument("--out", required=True)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--slots", type=int, required=True)
    ap.add_argument("--attempts", type=int, required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--provision-s", type=float, default=0.0)
    ap.add_argument("--hardware-json", default="{}")
    ap.add_argument("--cost-json", default='{"usd_per_hour_total": 0.0, "source": "unset"}')
    ap.add_argument("--agent-id", default="fake-agent")
    ap.add_argument("--drums-level", default="", choices=["", "L0", "L1"])
    ap.add_argument("--sample-resources", action="store_true")
    ap.add_argument("--sample-interval-s", type=float, default=5.0)
    ap.add_argument("--fake-latency-s", type=float, default=0.0)
    ap.add_argument("--fake-p-reset-fail", type=float, default=0.0)
    ap.add_argument("--fake-p-leak", type=float, default=0.0)
    ap.add_argument("--fake-p-black", type=float, default=0.0)
    run(ap.parse_args(argv))


if __name__ == "__main__":
    main()
