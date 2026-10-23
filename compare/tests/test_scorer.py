import json
import os

import pytest

from drums_compare import scorer


def make(tmp_path, name, *, n=200, valid_frac=0.9, slot_s=60.0, usd_h=10.0, wall_s=3600.0,
         fake=False, passed_frac=0.5, hardware=None, seed=1, sha="x", cost=True):
    d = tmp_path / name
    d.mkdir()
    run = {"side": name, "fake": fake, "seed": seed, "t_start": 0.0, "t_end": wall_s, "provision_s": 0.0,
           "hardware": hardware if hardware is not None else {"x": 1},
           "cost_basis": {"usd_per_hour_total": usd_h if cost else 0}, "tasks_sha256": sha, "agent": "a"}
    json.dump(run, open(d / "run.json", "w"))
    with open(d / "attempts.jsonl", "w") as f:
        for i in range(n):
            ok = (i % 100) < valid_frac * 100
            f.write(json.dumps({"attempt_id": f"{name}-{i}", "valid": ok, "passed": ok and (i % 10) < passed_frac * 10,
                                "wall_s": slot_s, "reset_s": 1.0,
                                "invalid_reasons": [] if ok else ["fault:reset_failed"],
                                "interference_with": []}) + "\n")
    return str(d)


def report(tmp_path, d_kw, b_kw):
    d, b = make(tmp_path, "drums", **d_kw), make(tmp_path, "base", **b_kw)
    return scorer.main([d, b, "--out", str(tmp_path / "r.json")])


def test_drums_wins_when_clearly_cheaper_and_faster(tmp_path):
    r = report(tmp_path, dict(n=400, usd_h=5.0), dict(n=200, usd_h=10.0))
    assert r["verdict"]["headline"] == "drums_wins"


def test_baseline_wins_when_drums_is_worse(tmp_path):
    r = report(tmp_path, dict(n=200, usd_h=20.0, valid_frac=0.5), dict(n=400, usd_h=5.0))
    assert r["verdict"]["headline"] == "baseline_wins"


def test_identical_sides_are_inconclusive(tmp_path):
    r = report(tmp_path, dict(), dict())
    assert r["verdict"]["headline"] == "inconclusive"


def test_fake_runs_never_get_a_verdict(tmp_path):
    r = report(tmp_path, dict(fake=True, n=400, usd_h=1.0), dict(fake=True))
    assert r["verdict"]["headline"] == "no_verdict"


def test_too_few_valid_rollouts_no_verdict(tmp_path):
    r = report(tmp_path, dict(n=20), dict(n=20))
    assert r["verdict"]["headline"] == "no_verdict"


def test_missing_cost_or_hardware_blocks_verdict(tmp_path):
    r = report(tmp_path, dict(cost=False), dict())
    assert r["verdict"]["headline"] == "no_verdict" and r["input_problems"]


def test_different_task_files_block_verdict(tmp_path):
    r = report(tmp_path, dict(sha="a"), dict(sha="b"))
    assert r["verdict"]["headline"] == "no_verdict"


def test_success_rate_gap_blocks_verdict(tmp_path):
    r = report(tmp_path, dict(n=1000, passed_frac=0.9, usd_h=1.0), dict(n=1000, passed_frac=0.3))
    assert r["verdict"]["headline"] == "no_verdict"


def test_point_estimates_are_simple_arithmetic(tmp_path):
    d = make(tmp_path, "drums", n=100, valid_frac=0.8, usd_h=10.0, wall_s=1800.0)
    run, att, samp = scorer.load(d)
    m = scorer.side_metrics(run, att, samp)
    assert m["n_valid"] == 80
    assert m["total_cost_usd"] == pytest.approx(5.0)
    assert m["cost_per_valid_usd"] == pytest.approx(5.0 / 80)
    assert m["valid_per_hour_end_to_end"] == pytest.approx(160.0)
    lo, hi = m["bootstrap"]["cost_per_valid"]
    assert lo < m["cost_per_valid_usd"] < hi


def test_provision_time_is_billed(tmp_path):
    d = make(tmp_path, "drums", n=100, valid_frac=1.0, usd_h=10.0, wall_s=3600.0)
    run = json.load(open(os.path.join(d, "run.json")))
    run["provision_s"] = 1800.0
    json.dump(run, open(os.path.join(d, "run.json"), "w"))
    run, att, samp = scorer.load(d)
    assert scorer.side_metrics(run, att, samp)["total_cost_usd"] == pytest.approx(15.0)


def test_resource_summary_cpu_and_rss():
    s = [{"kind": "env", "slot": 0, "t": 0.0, "rss_kib": 1024 * 100, "pss_kib": None, "cpu_ticks": 0, "clk_tck": 100},
         {"kind": "env", "slot": 0, "t": 10.0, "rss_kib": 1024 * 300, "pss_kib": None, "cpu_ticks": 500, "clk_tck": 100}]
    r = scorer.resource_summary(s)
    assert r["rss_mib_mean"] == 200 and r["cpu_cores_mean_per_env"] == pytest.approx(0.5)
