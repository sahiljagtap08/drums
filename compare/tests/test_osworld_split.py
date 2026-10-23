import json

from drums_compare import osworld_split as s


def make_tree(tmp_path):
    ex = tmp_path / "evaluation_examples"
    meta, n = {}, 0
    for dom, funcs in {"chrome": ["a", "a", "b", "c"], "os": ["d", "d", "e"], "gimp": ["f", ["g", "h"]]}.items():
        (ex / "examples" / dom).mkdir(parents=True)
        ids = []
        for fn in funcs:
            tid = f"t{n}"
            n += 1
            (ex / "examples" / dom / f"{tid}.json").write_text(json.dumps({"evaluator": {"func": fn}}))
            ids.append(tid)
        meta[dom] = ids
    (ex / "test_nogdrive.json").write_text(json.dumps(meta))
    return tmp_path


def test_split_is_disjoint_by_task_and_by_judge_signature_and_deterministic(tmp_path):
    root = make_tree(tmp_path)
    a, b = s.build(str(root), seed=3, eval_fraction=0.4), s.build(str(root), seed=3, eval_fraction=0.4)
    assert a == b
    assert not set(a["train"]) & set(a["heldout"])
    assert a["n_train"] + a["n_heldout"] == a["n_pool"] == 9
    rows = {r["id"]: r["sig"] for r in s.load_pool(str(root))}
    assert not {rows[i] for i in a["train"]} & {rows[i] for i in a["heldout"]}


def test_different_seed_can_change_the_split(tmp_path):
    root = make_tree(tmp_path)
    assert any(s.build(str(root), seed=k)["heldout"] != s.build(str(root), seed=0)["heldout"] for k in range(1, 8))
