"""Deterministic train / held-out split of the PUBLIC OSWorld tasks (Apache-2.0, xlang-ai/OSWorld).

The public tasks are the incumbents' EVALUATION set, so a licensed training path that uses them must
never train and evaluate on the same task. This writes a split where:
  * the pool is `evaluation_examples/test_nogdrive.json` (361 tasks, the list ScaleCUA's own eval script
    defaults to; the 8 Google Drive tasks in `test_all.json` are excluded and never used on either side);
  * tasks are grouped by evaluator-function signature (`evaluator.func`), and a whole group goes to ONE
    side, so a held-out task never shares its judge function with a training task;
  * the split is a pure function of (task ids, seed, eval_fraction); the output records all three.

OUR split is OUR benchmark, not comparable to published headlines. Grouping by judge signature reduces
contamination but does not prove there is none.

Limits, stated: grouping by judge function does NOT separate tasks that share a source page, an app
template or a setup file (a union over `source` as well collapses 242 of 361 tasks into one component,
so it cannot be enforced); same-domain tasks therefore still resemble each other across the split. The
held-out set is a subset of the standard benchmark, not the standard benchmark: its score is not
comparable to published OSWorld numbers.

Usage: python -m drums_compare.osworld_split <path to OSWorld checkout> --out split.json
       [--seed 0] [--eval-fraction 0.4]"""

import argparse
import json
import os
import random


def load_pool(root, meta="test_nogdrive.json"):
    ex = os.path.join(root, "evaluation_examples")
    with open(os.path.join(ex, meta)) as f:
        by_domain = json.load(f)
    rows = []
    for domain, ids in by_domain.items():
        for tid in ids:
            with open(os.path.join(ex, "examples", domain, f"{tid}.json")) as f:
                func = json.load(f).get("evaluator", {}).get("func")
            sig = tuple(func) if isinstance(func, list) else (func,)
            rows.append({"id": tid, "domain": domain, "sig": sig})
    return rows


def split(rows, seed=0, eval_fraction=0.4):
    groups = {}
    for r in rows:
        groups.setdefault(r["sig"], []).append(r)
    keys = sorted(groups, key=lambda k: json.dumps(k))
    random.Random(seed).shuffle(keys)
    target = round(len(rows) * eval_fraction)
    held, train = [], []
    for k in keys:
        (held if len(held) < target else train).extend(groups[k])
    return train, held


def build(root, seed=0, eval_fraction=0.4):
    rows = load_pool(root)
    train, held = split(rows, seed, eval_fraction)
    assert not {r["id"] for r in train} & {r["id"] for r in held}
    assert not {r["sig"] for r in train} & {r["sig"] for r in held}
    domains = sorted({r["domain"] for r in rows})
    return {"pool": "evaluation_examples/test_nogdrive.json", "seed": seed, "eval_fraction": eval_fraction,
            "n_pool": len(rows), "n_train": len(train), "n_heldout": len(held),
            "train": sorted(r["id"] for r in train), "heldout": sorted(r["id"] for r in held),
            "heldout_by_domain": {d: sum(1 for r in held if r["domain"] == d) for d in domains},
            "train_by_domain": {d: sum(1 for r in train if r["domain"] == d) for d in domains}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--eval-fraction", type=float, default=0.4)
    a = ap.parse_args(argv)
    out = build(a.root, a.seed, a.eval_fraction)
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1)
    print({k: v for k, v in out.items() if k not in ("train", "heldout")})


if __name__ == "__main__":
    main()
