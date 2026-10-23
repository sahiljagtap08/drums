"""Render the L1-F prototype results against the pre-registered thresholds (docs/l1-shared-runtime-design.md section 8).

    python -m envd_linux.l1proto.report bench.json [hygiene.json] [hazard.json]"""

import json
import statistics
import sys


def fmt(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def main(argv=None):
    argv = argv or sys.argv[1:]
    b = json.load(open(argv[0]))
    L, M = b["latency_summary"], b["memory_summary"]
    out = []
    out.append(f"host: kernel {b['host']['kernel']}, {b['host']['cpus']} cpus; zygote preload {fmt(b['zygote']['preload_s'])} s, "
               f"RSS {fmt((b['zygote']['rss_kib'] or 0) / 1024, 0)} MiB, PSS {fmt((b['zygote']['pss_kib'] or 0) / 1024, 0)} MiB, "
               f"preload failures: {b['zygote']['failed']}")
    out.append("\n| latency (s) | n | C0 p50 | C0 p95 | C1 p50 | C1 p95 | F p50 | F p95 |\n|---|---|---|---|---|---|---|---|")
    for k, name in (("server_ready_s", "b: server ready"), ("chromium_ready_s", "c: Chromium ready"),
                    ("init_to_server_ready_s", "session init to server ready"), ("create_s", "create() returns")):
        out.append(f"| {name} | {L['F'][k]['n']} | " + " | ".join(f"{fmt(L[a][k][q])}" for a in ("C0", "C1", "F") for q in ("p50", "p95")) + " |")
    f, c1 = L["F"], L["C1"]
    pass_b = f["server_ready_s"]["p95"] < c1["server_ready_s"]["p95"] and f["server_ready_s"]["p50"] <= 0.7 * c1["server_ready_s"]["p50"]
    pass_c = f["chromium_ready_s"]["p95"] < c1["chromium_ready_s"]["p95"]
    out.append(f"\nb (server ready): F p95 {fmt(f['server_ready_s']['p95'])} < C1 p95 {fmt(c1['server_ready_s']['p95'])} and "
               f"F p50 {fmt(f['server_ready_s']['p50'])} <= 0.7 x C1 p50 {fmt(0.7 * c1['server_ready_s']['p50'])}: **{'PASS' if pass_b else 'FAIL'}**")
    out.append(f"c (Chromium ready): F p95 {fmt(f['chromium_ready_s']['p95'])} < C1 p95 {fmt(c1['chromium_ready_s']['p95'])}: **{'PASS' if pass_c else 'FAIL'}**")
    out.append("\n| memory | slopes MiB/session per trial | mean |\n|---|---|---|")
    for a in ("C0", "C1", "F"):
        out.append(f"| {a} | {', '.join(fmt(s, 1) for s in M[a]['slopes'])} | {fmt(M[a]['mean'], 1)} |")
    d = M.get("C1_minus_F")
    if d:
        ratio = M["F"]["mean"] / M["C1"]["mean"]
        pass_a = ratio <= 0.85 and d["ci95"][0] > 0
        out.append(f"\na (memory): F/C1 slope ratio {fmt(ratio, 3)} (needs <= 0.85); C1-F = {fmt(d['mean'], 1)} MiB/session, bootstrap 95% "
                   f"[{fmt(d['ci95'][0], 1)}, {fmt(d['ci95'][1], 1)}] (needs to exclude 0): **{'PASS' if pass_a else 'FAIL'}**")
        out.append(f"F vs C0: ratio {fmt(M['F']['mean'] / M['C0']['mean'], 3)}")
    out.append("\nf (share bound, one session at the top N, trial 0; KiB):")
    for arm, rows in b["breakdown"].items():
        tot = sum(r["private_kib"] for r in rows.values())
        out.append(f"- {arm}: " + ", ".join(f"{k} {r['private_kib'] // 1024} MiB private (n={r['n']}, pss {r['pss_kib'] // 1024})" for k, r in sorted(rows.items()))
                   + f"; total private {tot // 1024} MiB; Chromium share {100 * rows.get('chromium', {}).get('private_kib', 0) / max(tot, 1):.0f}%")
    print("\n".join(out))
    for extra in argv[1:]:
        print(f"\n--- {extra}\n" + json.dumps(json.load(open(extra)), indent=1))


if __name__ == "__main__":
    main()
