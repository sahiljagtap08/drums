# Incident: host state left by the systemd-as-PID-1 probe

STATUS: closed. Nothing was cleaned by hand; the state was already gone when the cleanup was authorised.

## What happened

`envd_linux/systemd_probe.py` (one run, `Supervisor(exec_systemd=True)`, NO private network namespace) made a session's PID 1
`exec` `/lib/systemd/systemd --system --unit=multi-user.target`. The session root is a copy-on-write view of this host's `/`,
so systemd started the host rootfs's ENABLED units (17 active, 0 failed). `docker.service` among them ran in the sandbox's
host network namespace, because the session had none. Cgroup directories were also written into the host hierarchy (those
empty leftovers were removed with `rmdir`, only empty ones newer than an hour, before this incident was opened).

## State captured while it was present (from the command outputs of that turn)

| Item | Observed |
|---|---|
| Link | `docker0` present in the host netns (links: lo, ifb0, ifb1, eth0, docker0) |
| nftables tables | `ip nat`, `ip filter`, `ip6 nat`, `ip6 filter` (iptables-nft, Docker's DOCKER/DOCKER-FORWARD/DOCKER-USER chains) |
| `net.ipv4.ip_forward` | 1 (it had been 0) |
| FORWARD policy | `iptables -S FORWARD`: `-P FORWARD DROP`, then jumps to DOCKER-USER and DOCKER-FORWARD |
| Processes | none left (the namespace's processes died with the session) |
| Suite | 142 passed, 3 failed |

The 3 failing tests were `tests/test_session_network_policy.py`: NAT outbound (the peer sees the masqueraded address), real
internet from a session, and the firewall-off control. These are exactly the tests that need FORWARDED packets. A DROP policy
in another table's FORWARD chain overrides an `accept` in our `inet drums_*` table (any base-chain drop wins), so forwarded
session traffic never left. They had passed (141 passed, 0 failed) before the probe.

## What was done about it

The owner authorised cleanup in this order: record state; stop only services the experiment started (checking `is-active`
first); delete `docker0`; delete exactly the four tables; `ip_forward` back to 0; FORWARD policy back to ACCEPT; do not restart
docker.

Before running any of it, the state was re-read:

| Item | State when re-read |
|---|---|
| Kernel uptime | about 23 seconds: the sandbox's kernel state had been reset (the filesystem, the apt packages, `/opt` and the scratch directory were intact) |
| Links | lo, ifb0, ifb1, eth0 only; no `docker0` |
| nftables tables | none |
| `ip_forward` | 0 |
| FORWARD policy | `-P FORWARD ACCEPT` (iptables and ip6tables) |
| Docker processes | none; PID 1 is `process_api`, not systemd, so `systemctl is-active` cannot even be asked on the host (`System has not been booted with systemd`). Docker only ever ran inside the experiment's own namespace and died with it. |

So the leftover state did not survive the sandbox reset, and none of the authorised commands was needed or run. Nothing was
stopped, deleted or flushed by hand. The record that they existed rests on the command outputs captured at the time, not on
anything re-observable now.

## What is and is not known about the prior state

Prior state is EVIDENCE, not defaults. What the evidence supports: before the probe the host had no `docker0` and no
netfilter tables (`nft list tables` printed nothing between the earlier suite runs), `ip_forward` was 0 whenever tests were not
running (the sweep tests set and restore it, and the first reads showed 0), and the suite was green (141 passed, including the
real-internet test). What it does NOT show is the FORWARD chain policy before the incident: I never read it before the probe. The
`ACCEPT` seen after the reset is an observation of the reset host, not a recorded prior value, and it is consistent with
(not proof of) the pre-incident baseline, because the forwarding tests passed then. No value was restored by assertion; none had
to be, because nothing was left to restore.

If the state HAD still been present, the plan was: remove only Docker's residue (the `docker0` link and the four tables it created),
restore only what the evidence supports, use the suite as the check, and if the 3 tests stayed red after that, stop and report
instead of stacking further network changes. Any command the permission layer refused would be named and left there.

## Verification

The full suite was re-run after the reset with every optional environment set: 145 passed, 0 failed, including the 3 tests that had failed (NAT outbound, real internet, firewall-off control). The suite was then 145 tests; 7 more (provenance and the real-server tests) were run separately and passed.

## Lessons kept

- A systemd PID 1 over the host rootfs starts the host's enabled units. Required before any further systemd experiment: a
  private network namespace, a cgroup namespace with the session in its own cgroup subtree, and a trimmed unit set. The
  `exec_systemd` option now refuses without `netns=True` and `DRUMS_ALLOW_SYSTEMD_PROBE=1`.
- The sweep's ledger does not yet record cgroup directories; a systemd session is therefore not sweepable. Follow-up.
- On any host where Docker or another firewall sets a FORWARD DROP policy, `nat=True` needs an explicit allow for the
  session subnet. That is a host requirement for the real run.
