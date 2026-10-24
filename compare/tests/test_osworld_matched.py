"""Accounting tests for the matched-comparison runner, with FAKE adapters. HARNESS VALIDATION, not a result."""

import json
import threading

import pytest

from drums_compare import osworld_matched as om


class Clock:
    """A deterministic clock the fake adapters advance, so wall time is exact."""

    def __init__(self):
        self.t, self.lock = 1000.0, threading.Lock()

    def __call__(self):
        return self.t

    def advance(self, s):
        with self.lock:
            self.t += s


class FakeLease(om.Lease):
    def __init__(self, adapter, worker):
        self.a, self.w = adapter, worker

    def reset(self, task):
        self.a.clock.advance(self.a.reset_s)
        if task["id"] in self.a.fault_tasks:
            raise om.AdapterFault("reset_failed", "boom")
        self.a.seen.append(task["id"])
        return {"obs": 1}

    def step(self, action):
        self.a.clock.advance(self.a.step_s)
        return {"obs": 2}

    def evaluate(self):
        return None if self.a.noscore else 1.0


class FakeAdapter(om.Adapter):
    def __init__(self, name, clock, reset_s=1.0, step_s=0.5, open_s=0.0, fault_tasks=(), noscore=False):
        self.name, self.clock, self.reset_s, self.step_s, self.open_s = name, clock, reset_s, step_s, open_s
        self.fault_tasks, self.noscore, self.seen, self.closed = set(fault_tasks), noscore, [], False

    def open(self, slots):
        self.clock.advance(self.open_s)

    def acquire(self, worker):
        return FakeLease(self, worker)

    def close(self):
        self.closed = True


TASKS = {t: {"id": t} for t in ("a", "b", "c")}
PRICE = om.Prices(host_usd_per_hour=3600.0, hosts=1)  # 1 USD per second, so the arithmetic is readable


def run(adapter, seq, clock, conc=1, **kw):
    scripts = {"a": ["x", "y"], "b": ["x"], "c": []}
    return om.run_arm(adapter, TASKS, seq, lambda: om.ScriptedAgent(scripts), conc, PRICE, clock=clock, **kw)


def test_valid_rate_and_cost_per_valid_rollout_follow_from_the_explicit_price():
    clk = Clock()
    ad = FakeAdapter("fake", clk, reset_s=1.0, step_s=0.5, open_s=4.0)
    r = run(ad, ["a", "b"], clk)["summary"]
    # open 4 s + (1 + 2*0.5) + (1 + 1*0.5) = 7.5 s of wall; 2 valid
    assert r["wall_s"] == pytest.approx(7.5) and r["valid"] == 2 and r["rollouts"] == 2
    assert r["valid_per_hour"] == pytest.approx(2 / (7.5 / 3600))
    assert r["cost_usd"] == pytest.approx(7.5) and r["cost_per_valid_usd"] == pytest.approx(3.75)
    assert ad.closed


def test_a_faulted_rollout_is_invalid_but_its_cost_still_counts():
    clk = Clock()
    ad = FakeAdapter("fake", clk, fault_tasks={"b"})
    res = run(ad, ["a", "b", "a"], clk)
    s = res["summary"]
    assert s["valid"] == 2 and s["rollouts"] == 3 and s["faults"] == {"reset_failed": 1}
    assert s["cost_per_valid_usd"] == pytest.approx(s["cost_usd"] / 2)  # three rollouts paid for, two valid
    assert [r["valid"] for r in res["rows"]] == [True, False, True]


def test_no_score_is_invalid():
    clk = Clock()
    s = run(FakeAdapter("fake", clk, noscore=True), ["a"], clk)["summary"]
    assert s["valid"] == 0 and s["faults"] == {"no_score": 1} and s["cost_per_valid_usd"] is None


def test_adapter_bugs_are_counted_not_swallowed():
    clk = Clock()

    class Bad(FakeAdapter):
        def acquire(self, worker):
            raise KeyError("pool empty")
    s = run(Bad("bad", clk), ["a", "b"], clk)["summary"]
    assert s["valid"] == 0 and s["faults"] == {"exception:KeyError": 2}


def test_both_arms_get_the_identical_task_sequence():
    seq1 = om.sample_tasks(["a", "b", "c"], 3, seed=7)
    seq2 = om.sample_tasks(["a", "b", "c"], 3, seed=7)
    assert seq1 == seq2 and sorted(seq1) == sorted(["a", "b", "c"] * 3)
    assert om.sample_tasks(["a", "b", "c"], 3, seed=8) != seq1
    clk = Clock()
    A, B = FakeAdapter("A", clk), FakeAdapter("B", clk)
    run(A, seq1, clk)
    run(B, seq1, clk)
    assert A.seen == B.seen == seq1


def test_prices_are_an_input_not_a_constant(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"host_usd_per_hour": 7.2, "hosts": 2, "fixed_usd": 1.0, "label": "example"}))
    pr = om.Prices.from_json(str(p))
    assert pr.total(0.5) == pytest.approx(2 * 7.2 * 0.5 + 1.0)
    with pytest.raises(TypeError):
        om.Prices()  # no default price exists


def test_memory_sampler_reports_peak_and_per_env_delta():
    vals = iter([1000, 1500, 3000, 2000, 2000, 2000, 2000, 2000])
    s = om.MemorySampler(lambda: next(vals), period=0.01)
    with s:
        import time
        time.sleep(0.08)
    out = s.summary(concurrency=2)
    assert out["baseline_kib"] == 1000 and out["peak_kib"] >= 1500
    assert out["peak_delta_per_env_mib"] == pytest.approx((out["peak_kib"] - 1000) / 1024 / 2)


def test_concurrency_uses_every_worker_and_keeps_all_rows():
    clk = Clock()
    res = run(FakeAdapter("fake", clk), ["a", "b", "c", "a", "b", "c"], clk, conc=3)
    assert len(res["rows"]) == 6 and {r["worker"] for r in res["rows"]} <= {0, 1, 2}


def test_comparison_needs_the_interval_lower_bound_above_one_to_call_a_win():
    def arm(valid, total, cost):
        rows = [{"valid": i < valid} for i in range(total)]
        return {"rows": rows, "summary": {"cost_usd": cost}}
    clear = om.compare(arm(95, 100, 10.0), arm(50, 100, 10.0))
    assert clear["win"] and clear["valid_per_dollar_ratio_A_over_B"] == pytest.approx(1.9)
    close = om.compare(arm(52, 100, 10.0), arm(50, 100, 10.0))
    assert not close["win"], close  # a small difference inside the noise is not a win


class FakeDesktopEnv:
    """Stand-in for the public DesktopEnv surface the adapter touches (reset/step/evaluate/close)."""
    built = []

    def __init__(self, **kw):
        self.kw, self.log, self.closed = kw, [], False
        FakeDesktopEnv.built.append(self)

    def reset(self, task_config=None, seed=None, options=None):
        self.log.append(("reset", task_config["id"]))
        return {"screenshot": b""}

    def step(self, action, pause=2):
        self.log.append(("step", action))
        return {"screenshot": b""}, 0, False, {}

    def evaluate(self):
        return 1.0

    def close(self):
        self.closed = True


class FakeModule:
    DesktopEnv = FakeDesktopEnv


def test_desktop_env_adapter_dry_run_builds_a_pool_once_and_reuses_it():
    """HARNESS VALIDATION of the adapter plumbing against a fake DesktopEnv; no real environment is involved."""
    FakeDesktopEnv.built.clear()
    ad = om.DesktopEnvAdapter("incumbent", om.incumbent_factory("vmware", path_to_vm="/vm/x.vmx", snapshot_name="init_state",
                                                                 desktop_env_module=FakeModule))
    clk = Clock()
    res = om.run_arm(ad, TASKS, ["a", "b", "a", "c"], lambda: om.ScriptedAgent({"a": ["x"]}), 2, PRICE, clock=clk)
    assert len(FakeDesktopEnv.built) == 2, "the pool is built once per slot and REUSED across rollouts"
    assert all(e.closed for e in FakeDesktopEnv.built)
    assert FakeDesktopEnv.built[0].kw["provider_name"] == "vmware" and FakeDesktopEnv.built[0].kw["snapshot_name"] == "init_state"
    assert res["summary"]["valid"] == 4
    assert sum(1 for e in FakeDesktopEnv.built for ev in e.log if ev[0] == "reset") == 4


def test_the_quarantined_provider_is_not_imported_by_the_runner():
    import sys
    assert "envd_linux.scalecua._quarantined_provider" not in sys.modules
