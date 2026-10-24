"""The ledger's read-modify-write must survive many threads recording at once (two session-creating threads
raced on a shared temp file and lost an entry once). No privileges needed."""

import json
import threading

from envd_linux import sweep as sw


def test_concurrent_records_lose_nothing(tmp_path):
    path = sw.register(str(tmp_path / "state"), "abc123", str(tmp_path / "ledger"))
    errors = []

    def go(i):
        try:
            for j in range(20):
                sw.record(path, "mounts", f"/m/{i}/{j}")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert len(json.load(open(path))["mounts"]) == 160


def test_sweep_ignores_unreadable_entries_and_live_ones(tmp_path):
    led = tmp_path / "ledger"
    led.mkdir()
    (led / "sup-1-0.json").write_text("{not json")
    path = sw.register(str(tmp_path / "state"), "live1", str(led))  # this process: alive
    rep = sw.sweep(ledger=str(led))
    assert rep["entries_removed"] == [] and rep["live_supervisors"] == [__import__("os").getpid()]
    assert (led / "sup-1-0.json").exists(), "an unreadable entry must be left in place, never guessed at"
    sw.unregister(path)


def test_sysfs_knobs_are_recorded_first_and_restored_only_if_we_still_own_the_value(tmp_path):
    knob = tmp_path / "knob"
    knob.write_text("0\n")
    other = tmp_path / "other"
    other.write_text("5\n")
    led = sw.register(str(tmp_path / "state"), "k1", str(tmp_path / "ledger"))
    assert sw.set_sysfs(led, str(knob), 1) == "0"
    assert sw.set_sysfs(led, str(other), 9) == "5"
    entry = json.load(open(led))
    assert [r["path"] for r in entry["sysfs"]] == [str(knob), str(other)]
    other.write_text("7\n")  # someone else changed it after us: not ours to restore
    done = sw.restore_sysfs(entry["sysfs"])
    assert done == [str(knob)] and knob.read_text().strip() == "0" and other.read_text().strip() == "7"
    assert sw.restore_sysfs(entry["sysfs"]) == []  # idempotent
    sw.unregister(led)
