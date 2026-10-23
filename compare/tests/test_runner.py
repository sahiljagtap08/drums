import json
import os

from drums_compare import runner

TASKS = os.path.join(os.path.dirname(__file__), "..", "..", "tasks", "linux_comparison", "tasks.json")


def run(tmp_path, side, **kw):
    args = ["--side", side, "--out", str(tmp_path / side), "--tasks", TASKS, "--slots", "3",
            "--attempts", "30", "--seed", "7", "--hardware-json", '{"fake": 1}',
            "--cost-json", '{"usd_per_hour_total": 1.0}']
    for k, v in kw.items():
        args += [f"--{k.replace('_', '-')}", str(v)]
    runner.main(args)
    d = tmp_path / side
    return json.load(open(d / "run.json")), [json.loads(x) for x in open(d / "attempts.jsonl")], json.load(open(d / "audit.json"))


def test_clean_fake_run_all_valid_and_flagged_fake(tmp_path):
    run_meta, attempts, audit = run(tmp_path, "fake-drums")
    assert run_meta["fake"] is True
    assert len(attempts) == 30 and all(a["valid"] for a in attempts) and audit == []


def test_same_seed_gives_same_task_order_on_both_sides(tmp_path):
    _, a, _ = run(tmp_path, "fake-drums")
    _, b, _ = run(tmp_path, "fake-linux")
    key = lambda attempts: [(x["attempt_id"].rsplit("-", 1)[1], x["task_id"]) for x in attempts]
    assert key(a) == key(b)


def test_only_enabled_tasks_are_scheduled(tmp_path):
    _, attempts, _ = run(tmp_path, "fake-drums")
    assert {a["task_id"] for a in attempts} <= {"web_form_token", "web_form_new_window", "web_form_after_busy_tab"}


def test_unique_gen_per_slot_and_tokens(tmp_path):
    _, attempts, _ = run(tmp_path, "fake-drums")
    assert len({a["token"] for a in attempts}) == 30
    assert len({(a["slot"], a["gen"]) for a in attempts}) == 30


def test_leak_is_caught_by_audit_and_both_attempts_invalidated(tmp_path):
    _, attempts, audit = run(tmp_path, "fake-linux", fake_p_leak=1.0)
    assert audit, "audit should have found stale-generation rows"
    flagged = [a for a in attempts if "interference:stale_write_after_reset" in a["invalid_reasons"]]
    assert flagged and all(not a["valid"] for a in flagged)
    assert any(a["interference_with"] for a in flagged)
    assert all(e["owner"] and e["victim"] for e in audit)


def test_reset_failures_counted_not_dropped(tmp_path):
    _, attempts, _ = run(tmp_path, "fake-drums", fake_p_reset_fail=1.0)
    assert len(attempts) == 30 and not any(a["valid"] for a in attempts)
