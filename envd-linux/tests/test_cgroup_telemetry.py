import os

from envd_linux.cgroup_telemetry import CgroupProbe


def _v1(tmp, name, pids, mem, peak):
    d = {}
    for c in ("cpu", "memory", "pids"):
        d[c] = os.path.join(tmp, c, "drums", "run", name)
        os.makedirs(d[c])
        open(os.path.join(d[c], "cgroup.procs"), "w").write("\n".join(map(str, pids)))
    open(os.path.join(d["cpu"], "cpu.cfs_quota_us"), "w").write("200000")
    open(os.path.join(d["cpu"], "cpu.cfs_period_us"), "w").write("100000")
    open(os.path.join(d["memory"], "memory.usage_in_bytes"), "w").write(str(mem))
    open(os.path.join(d["memory"], "memory.max_usage_in_bytes"), "w").write(str(peak))
    open(os.path.join(d["memory"], "memory.limit_in_bytes"), "w").write(str(4 << 30))
    return list(d.values())


def test_v1_session_directories_are_merged_by_session_and_limits_are_read_back(tmp_path):
    dirs = _v1(str(tmp_path), "s0", [os.getpid()], 100 << 20, 300 << 20)
    p = CgroupProbe(lambda: dirs, period=0.05)
    p.begin()
    out = p.end()
    (k, v), = out["sessions"].items()
    assert k == "run/s0" and v["cgroup_kind"] == "v1" and v["procs_max"] == 1 and v["empty_samples"] == 0
    assert v["mem_peak_mib_sampled"] == 100 and v["mem_peak_mib_kernel"] == 300
    assert v["limits_as_read_by_kernel"]["cpu.cfs_quota_us"] == "200000" and v["limits_as_read_by_kernel"]["memory.limit_in_bytes"] == str(4 << 30)
    assert v["cpu_s_lower_bound"] >= 0


def test_an_empty_cgroup_is_counted_not_hidden(tmp_path):
    dirs = _v1(str(tmp_path), "s1", [], 0, 0)
    p = CgroupProbe(lambda: dirs, period=0.05)
    p.begin()
    out = p.end()
    assert out["sessions"]["run/s1"]["empty_samples"] >= 1 and out["sessions"]["run/s1"]["procs_max"] == 0
