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
