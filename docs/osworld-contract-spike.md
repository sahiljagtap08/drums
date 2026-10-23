# OSWorld-contract spike: guest server, network namespace, full-root overlay, controller skeleton

Status: local, this sandbox only. Nothing launched, no cloud, no credentials. Code:
`envd-linux/envd_linux/{osworld_server,netsetup,session_init,session}.py` and
`envd-linux/envd_linux/scalecua/`. Tests: `envd-linux/tests/test_osworld_*`, `test_scalecua_controller.py`.
Builds on `docs/envd-linux-spike.md` and implements the decisions in
`docs/training-comparison-plan.md` section 3.

Unresolved-licence assets were not used or copied: `osworld_env` (no licence of its own), VeriGen and the
HF task pool and judges. Disclosure: I read `osworld_env/worker` source before its licence status was known,
so `scalecua/provider.py` was QUARANTINED (renamed `_quarantined_provider.py`, import-guarded, imported by nothing, enforced by a test; resolution is an independent implementation or written permission)
outside this branch; see `docs/training-comparison-plan.md` section 4. The OSWorld guest-server shapes were
taken from the Apache-2.0 upstream OSWorld repository (xlang-ai/OSWorld, `desktop_env/server/main.py`),
read for response shapes only; the stub is our own code.

## 1. What was built

| Piece | What it is |
|---|---|
| Full-root copy-on-write view | `session_init.setup_full_root`: a tmpfs stage holds upper, work and merged; overlayfs with lower layers `template:/` (template on top, the host `/` below); new `/proc`, a minimal `/dev`, `/sys` bound read-only, private `/tmp`, `/run`, `/dev/shm`; then `pivot_root`. Writes anywhere in the root (`/etc`, `/usr`, `/opt`) land in the session's upper and vanish with it. The upper is RAM-backed. |
| Template layer | Copied once into a tmpfs mounted on the host and shared by all sessions (overlayfs refuses a layer that sits inside another layer, and `/` is a lower layer). One RAM copy, shared page cache. |
| Network namespace per session | `unshare --net` plus a veth pair (`netsetup.py`, uses pyroute2): host `10.200.<slot>.1/30`, session `10.200.<slot>.2/30`, default route via the host side. The kernel removes the veth when the namespace dies. |
| OSWorld guest-server STUB | `osworld_server.py`: stdlib server on port 5000 inside each session with the routes and response shapes environment layers use (`/platform`, `/screenshot`, `/screen_size`, `/cursor_position`, `/execute`, `/setup/launch`, `/file`, `/list_directory`, `/desktop_path`, `/run_python`, `/window_size`, plus stubbed `/accessibility` and `/terminal`). `GET /__contract` lists what is implemented. Download, upload, open-file, wallpaper, recording, activate/close return 501. |
| Controller | `scalecua/controller.py`: `DrumsEnvironmentController` and `DrumsEnvironmentDelegation` subclass the REAL `agentrl-worker` 0.4.0 classes (MIT, read from PyPI): async `start_session(subtypes, immutable, **kw) -> (session_id, {subtype: env_id}, {subtype: url})`, `renew_session`, `end_session`, `execute_command` (via the guest `/execute`), `execute_shell`, `get_env_variables`, a reaper `background_task`; `sync_*` wrappers come from the real base. `scalecua/pool.py`: the blocking pool underneath: exclusive sessions, a rebuilt session for every attempt, start failures recorded and another slot tried, every start, reset and fault in an events file. `patch.py` registers a "drums" driver in the real `create_controller` (which otherwise knows only manual/docker/k8s). `client.py` (`GuestClient`). |
| Smoke job | `scalecua/smoke.py`: the bounded validation job (stage (a) of the money path): sessions through the controller, per-attempt guest-contract checks, start-time and fault statistics, memory low-water mark, mean session PSS, hard wall-clock cap, SIGTERM handling, teardown (verified only by the local 4 s-cap test; nothing about AWS is guaranteed-capped); JSON output. `worker_entry.py` installs the driver then runs `agentrl.worker` (tested against real `agentrl-worker==0.4.0` with our own fake task; the provider bridge is missing by design). |

## 2. Proven here (tests that ran and passed)

- Interface: the controller and delegation subclass the real `agentrl-worker` 0.4.0 base classes; the
  real sync wrappers drive them; a "drums" driver registers into the real `create_controller` without
  disturbing the original drivers (tests skip if the package is absent). Not checked against the copy
  bundled in the ScaleCUA repo.
- Smoke job: run as a subprocess in real sessions, 4 attempts at concurrency 2 all pass the guest checks
  and leave no Xvfb or veth behind; a 4-second wall cap stops a 200-attempt job, writes
  `wall_cap_reached`, exits 3 and leaves nothing behind. One manual run (4 slots, 8 attempts, empty
  sessions): session ready in about 0.8 s (p50 and p95), mean session PSS about 38 MiB (Xvfb, guest stub,
  init; no browser), 0 faults. Observations from one run in a 4-CPU, 16 GiB sandbox, not a benchmark.
- Unit tests (fake backend, fake health check): exclusivity under 12 threads on 4 slots, rebuild before
  reuse, eager rebuild, fault accounting and retry on another slot, unhealthy guest, pool exhaustion
  timeout, unknown session end, parallel warm-up, provider shape, registry stub.
- Guest-server stub contract tests in-process for the non-display routes.
- Integration, real sessions (full root + network namespace + guest stub + controller):
  - each session answers `/platform`, reports a 1280x800 screen and a real PNG screenshot;
  - two sessions serve port 5000 AND a second listener on port 9222 at the same time, on different
    addresses (this is why a per-session network namespace is required);
  - files written under `/etc`, `/opt`, `/usr/local/bin`, `/tmp` and `/home/drums` in one session are
    absent from another session and from the host; the next session on the same slot is rebuilt and has
    none of it; the shared template (the Chromium profile) is visible in every session;
  - a browser task runs end to end through the guest contract only: launch via `/setup/launch`, display
    settles, input through `/execute` (a python-xlib XTEST script standing in for pyautogui), the form row
    reaches the real `verify-server` through the session's gateway address with the right generation;
  - controller events record every start, reset (with `reset_s`) and end; no faults in the run.
- One sample, this sandbox, not a benchmark: a session of 14 processes (Xvfb, Chromium, guest stub) at
  about 394 MiB PSS (908 MiB RSS); host tmpfs growth of about 35 MiB for the private upper layers after one
  attempt; creating a session with the full-root overlay and network namespace took under a second
  (0.69 s for the first, measured by hand, not asserted).
- Session-to-session reachability is now refused by POLICY (section 4), including with host forwarding forced on.

## 3. Stubbed, not proven, or needs a real host

| Item | Status |
|---|---|
| The real OSWorld guest server | NOT run. It needs flask, pyautogui, lxml, python-xlib, pyxcursor and pyatspi (system package). pyautogui did not build here. On the OSWorld image it would run in place of the stub. |
| pyautogui and the OSWorld action path | NOT exercised. DesktopEnv sends pyautogui code through `/execute`; this test used a small XTEST script. Whether real pyautogui behaves identically in a session is unknown. |
| AT-SPI / accessibility tree | Stubbed (`/accessibility` returns an empty tree). Any task or agent that reads it is unsupported. |
| The OSWorld Ubuntu image, GNOME, systemd | NOT used. The root filesystem here is this sandbox's. Whether the image's GNOME session and systemd-dependent services run in a namespaced session without systemd as PID 1 is still the open technical risk. |
| KVM | Absent here. The incumbent QEMU-in-Docker backend cannot run in this sandbox at all. |
| Chrome DevTools on 9222 | Only a generic listener on 9222 was tested. Chrome itself binds DevTools to loopback; the OSWorld image forwards it with a helper (not reproduced here). |
| Internet from a session | Optional masquerade NAT exists (section 4) and was tested against a local stand-in uplink and, where the host itself has it, a real TCP connection to 1.1.1.1:443. DNS inside sessions is NOT handled (the session's resolv.conf is the host's, which may point at a loopback stub). No HTTP-level task has used it. |
| Network isolation between sessions | nftables policy, tested with forwarding on (section 4). Not covered: IPv6 (sessions get none), bandwidth, per-session egress allow-lists, one Supervisor per host (the table is global). |
| The real ScaleCUA worker | NOT used. The controller is checked against the real `agentrl-worker` 0.4.0 interface and local stubs only; how the real ScaleCUA worker's session code calls a provider, and the bundled (newer) framework copy, are unverified. The entry point exists but only a fake task of ours has run through it. The OSWorld provider bridge now exists (section 5) but has only met stand-ins of the public API, not the real `desktop_env` package. |
| Memory and cgroups | Upper layers are RAM-backed tmpfs and are not limited or accounted per session. No cgroup limits. |
| Scale | Two to four sessions at most in any test. No load, no soak, no failure injection into real sessions beyond a fake backend. |
| Reset speed claims | A rebuild per attempt took about a second here for an empty session; the cost with a real GNOME desktop is unknown. |

## 4. Decisions this forced (made, documented)

- Template as a shared tmpfs (above), because overlayfs rejects nested layers.
- The state directory must not sit under a path that gets replaced inside the session; the init opens
  its files and mounts before pivoting.
- The AgentRL base class `EnvironmentController.sync_*` helpers need `controller.loop`; the worker's task
  sets it, tests and the smoke job use `run_loop_in_thread()`.
- Controller shutdown must stop and join background rebuilds before destroying sessions, or a rebuild can
  recreate a session during teardown (found as a leak in testing, fixed).
- `/execute` and `/setup/launch` run arbitrary commands by design; the stub listens on all interfaces of
  the session's private namespace only, reachable from the host and (when forwarding is on) other sessions.
- `python -m http.server` hangs in a session without DNS (its bind does a reverse lookup); tests use a
  plain `socketserver.TCPServer`. The same trap applies to any task that starts a stdlib HTTP server.

## 5. Dependencies added to the sandbox (not to the repo)

`python-xlib` and `pyroute2` for the system `python3` (pyroute2 is dual GPL-2.0-or-later / Apache-2.0).
Neither is in `pyproject.toml` or `uv.lock`; the integration tests skip when they are missing.

## 6. Level

Still L0. Sessions share files and page cache and now also a read-only template tmpfs, but each runs its
own Xvfb, browser and guest server. It is an engineering precursor, not a Drums result.


## Claim discipline

(a) The REAL Chromium end-to-end task through the daemon is in `envd-linux-spike.md`. (b) Everything in this document is the OSWorld guest-server STUB contract: accessibility tree and terminal are empty, pyautogui is absent, many routes return 501. (c) Actual OSWorld app-environment compatibility is NOT proven; (b) is not (c). This is an L0 precursor; no L1 (shared pre-warmed runtime forks) claim follows from it.


## 4. Session network policy (added; L0 precursor, claim (b)-adjacent plumbing)

`envd_linux/firewall.py` installs an nftables table `inet drums` (removed by `Supervisor.close()`). It needs the
`nft` binary and CAP_NET_ADMIN in the host network namespace (nf_tables, NAT and masquerade in the kernel);
this sandbox has all of them, so it ran here. A host without them cannot run it, and that is a host
requirement for the real run.

- Session to session: dropped in the forward chain. Inbound to a session: dropped (only replies to its own
  connections). A session may talk to the host only at its own gateway address (per-slot input chain); the
  host's other addresses and other sessions' gateways are dropped.
- Egress is policy: with `nat=False` every session-originated forward is dropped; with `nat=True` the host
  masquerades session traffic (and sets `net.ipv4.ip_forward=1`, restored on close) and private, link-local
  (cloud metadata 169.254.169.254) and CGNAT destinations stay dropped (`block_private_egress`).
- Tests (`tests/test_session_network_policy.py`): the peer behind a stand-in uplink sees the host address, not the
  session's (NAT); private and metadata targets refused; session-to-session refused with forwarding on in both
  directions; only the own gateway reachable on the host; firewall holds with forwarding forced on and no NAT;
  teardown removes the table and restores `ip_forward`; a CONTROL run with the firewall off and forwarding on
  shows the probe does reach, so the refusals are the policy's doing.
- Not done: DNS, IPv6, bandwidth limits, per-session egress allow-lists, more than one Supervisor per host,
  nftables rules persistence or recovery after a crash (a stale table survives until the next Supervisor
  replaces it, or `nft delete table inet drums`).

## 5. The OSWorld provider bridge (added; claim (b), not (c))

`envd_linux/scalecua/osworld_provider.py`: `DrumsProvider` and `DrumsVMManager` for OSWorld's public
`desktop_env.providers.base` API. Written solely from the public Apache-2.0 xlang-ai/OSWorld sources (base
classes, factory, `DesktopEnv`); the quarantined module and ScaleCUA's `osworld_env` were not opened. Findings
from the public code that shape it: the factory is a fixed if/elif and `DesktopEnv` rejects unknown
`provider_name`s, so `install()` wraps the factory and selects Drums by `region="drums"` with
`provider_name="docker"`; the ip string is `host:server:chromium:vnc:vlc`, parsed with `rsplit(':', 4)`;
`revert_to_snapshot` may return a new path. Tested against a replica of those parts of `DesktopEnv` and against
real sessions (private state per attempt, the next attempt starts clean). NOT tested against the real
`desktop_env` package, which is not installed here, and the session behind it answers with the guest STUB.
