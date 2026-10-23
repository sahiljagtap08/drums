import json
import os

from drums_compare.resources import LinuxProcReader, Sampler, tree


def test_linux_reader_sees_this_process():
    r = LinuxProcReader()
    rss, pss, cpu = r.env_usage([os.getpid()])
    assert rss > 0 and cpu >= 0


def test_linux_reader_host_totals():
    h = LinuxProcReader().host()
    assert h["mem_total_kib"] > 0 and h["cpu_total_ticks"] > 0


def test_tree_follows_children():
    assert sorted(tree(1, {1: [2, 3], 3: [4]})) == [1, 2, 3, 4]


class FakeReader:
    clk_tck = 100

    def env_usage(self, roots):
        return 1000, None, 50

    def host(self):
        return {}


class E:
    slot = 3

    def pids(self):
        return [1]


def test_sampler_writes_one_line_per_env(tmp_path):
    p = tmp_path / "r.jsonl"
    s = Sampler(str(p), [E()], FakeReader())
    with open(p, "w") as fh:
        s.sample_once(fh)
    row = json.loads(open(p).read())
    assert row["slot"] == 3 and row["rss_kib"] == 1000
