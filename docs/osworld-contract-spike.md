# OSWorld-contract spike: guest server, network namespace, full-root overlay, controller skeleton

Status: local, this sandbox only. Nothing launched, no cloud, no credentials. Code:
`envd-linux/envd_linux/{osworld_server,netsetup,session_init,session}.py` and
`envd-linux/envd_linux/scalecua/`. Tests: `envd-linux/tests/test_osworld_*`, `test_scalecua_controller.py`.
Builds on `docs/envd-linux-spike.md` and implements the decisions in
`docs/training-comparison-plan.md` section 3.

Unresolved-licence assets were not used or copied: `osworld_env` (no licence of its own), VeriGen and the
HF task pool and judges. Disclosure: I read `osworld_env/worker` source before its licence status was known,
so `scalecua/provider.py` (shaped like the worker's provider class) should be reviewed or replaced before use
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
| Controller | `scalecua/controller.py`: `DrumsEnvironmentController` and `DrumsEnvironmentDelegation` subclass the REAL `agentrl-worker` 0.4.0 classes (MIT, read from PyPI): async `start_session(subtypes, immutable, **kw) -> (session_id, {subtype: env_id}, {subtype: url})`, `renew_session`, `end_session`, `execute_command` (via the guest `/execute`), `execute_shell`, `get_env_variables`, a reaper `background_task`; `sync_*` wrappers come from the real base. `scalecua/pool.py`: the blocking pool underneath: exclusive sessions, a rebuilt session for every attempt, start failures recorded and another slot tried, every start, reset and fault in an events file. `patch.py` registers a "drums" driver in the real `create_controller` (which otherwise knows only manual/docker/k8s). `provider.py` (OSWorld provider shape, `"ip:5000:9222:8006:8080"`), `client.py` (`GuestClient`). |
| Smoke job | `scalecua/smoke.py`: the bounded validation job (stage (a) of the money path): sessions through the controller, per-attempt guest-contract checks, start-time and fault statistics, memory low-water mark, mean session PSS, hard wall-clock cap, SIGTERM handling, guaranteed teardown; JSON output. |

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
- Session-to-session reachability is pinned by a test: with host `net.ipv4.ip_forward=0` (this sandbox) a
  session cannot reach another session's address. That is a side effect of the sysctl, not a designed
  firewall (see below).

## 3. Stubbed, not proven, or needs a real host

| Item | Status |
|---|---|
| The real OSWorld guest server | NOT run. It needs flask, pyautogui, lxml, python-xlib, pyxcursor and pyatspi (system package). pyautogui did not build here. On the OSWorld image it would run in place of the stub. |
| pyautogui and the OSWorld action path | NOT exercised. DesktopEnv sends pyautogui code through `/execute`; this test used a small XTEST script. Whether real pyautogui behaves identically in a session is unknown. |
| AT-SPI / accessibility tree | Stubbed (`/accessibility` returns an empty tree). Any task or agent that reads it is unsupported. |
| The OSWorld Ubuntu image, GNOME, systemd | NOT used. The root filesystem here is this sandbox's. Whether the image's GNOME session and systemd-dependent services run in a namespaced session without systemd as PID 1 is still the open technical risk. |
| KVM | Absent here. The incumbent QEMU-in-Docker backend cannot run in this sandbox at all. |
| Chrome DevTools on 9222 | Only a generic listener on 9222 was tested. Chrome itself binds DevTools to loopback; the OSWorld image forwards it with a helper (not reproduced here). |
| Internet from a session | None (no NAT configured, none available in this sandbox). Tasks that need real websites cannot run. Egress/NAT and DNS are not implemented. |
| Network isolation between sessions | No firewall. See the sysctl note above; a host with forwarding on (needed for NAT) would let sessions reach each other until a firewall is added. |
| The real ScaleCUA worker | NOT used. The controller is checked against the real `agentrl-worker` 0.4.0 interface and local stubs only; how the worker's session code calls the provider, the bundled (newer) framework copy, and a worker entry point that installs the driver are unverified. |
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
