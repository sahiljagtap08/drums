# Drums on Linux: architecture and envd-linux scope

Status: design. Nothing in this document is built except what is marked "tested here".
Where I decided something the repo does not state, it says "decision" and gives the
alternative that was rejected, so the owner can overrule it.

## 1. What this repo shows about Drums

From `envd/` (Swift, macOS) and `verify-server/`:

- One host OS runs many numbered sessions ("envs"). Each session has an `env` number, a
  `uid` and its own `envd` daemon on its own port (`/health` reports `env`, `uid`,
  `on_console`). A session is a separate user session on one macOS, not a VM.
- A per-session generation number guards against stale work: every mutating request
  carries `gen`; the check and the effect are one atomic step, so a late action from a
  finished attempt is refused (409 `stale_gen`) and never applied.
- `envd` is local-only (`Host`/`Origin` guard on every route) and exposes screenshot
  (with stale and black-frame headers), action, action log, pasteboard, launch, kill,
  windows and accessibility text.
- `verify-server` records form submits, leases per (env, gen), and audits rows that
  disagree with the newest lease.

The README states the goal: more training attempts on less hardware, without VMs.

The repo does not describe the Linux design. Everything below is my proposal for it.

## 2. What "Drums on Linux" must mean

Many attempts share ONE running OS and the unchanging parts of applications. Each attempt
gets private copy-on-write state: files, browser profile, app state.

Shared by all sessions (one copy):

| Shared | How |
|---|---|
| Kernel, init, system services | One host OS. No per-attempt boot. |
| Operating system and application files (browser, office suite, fonts, libraries) | One read-only tree used as the lower layer of every session's overlayfs; one copy in the page cache. |
| Initialised-but-unchanging application state (browser first-run profile, font and icon caches, GTK/dconf defaults, ld cache) | A "template layer": a second read-only lower layer built once. |

Private per session:

| Private | How |
|---|---|
| Files, `$HOME`, `/tmp`, browser profile, app state | Overlayfs upper directory, private to the session, discarded on reset. |
| Processes | PID namespace and a cgroup per session; killing the cgroup ends everything. |
| Display, keyboard, pointer, clipboard | One X server per session (Xvfb); input and screenshots address that display only. |
| IPC, session bus, runtime dir | IPC namespace; one D-Bus session bus per session; private `/run/user/<uid>`. |
| CPU, memory, pids, io limits and accounting | cgroup v2 leaf per session. |
| Identity | A distinct uid per session, mapped through a user namespace. |

### Levels, and the label rule

- L0: everything above. One kernel and one set of system services, shared files and
  page cache, private state. Sessions each run their own display, window manager,
  session bus and application processes.
- L1 (where the Drums claim lives): L0 plus shared running application runtime: attempt processes are forked from a
  pre-initialised template process (a Chromium-style zygote or fork server), so the
  initialisation work and its memory are shared, not just the files. This is what the
  CUA-Sandbox paper reports sharing, and it is where most of the further memory saving
  would have to come from.
- L2 and beyond (process checkpoint/restore, for example CRIU) is research, not planned.

L0 is a precursor, not a Drums result. Rule: every result must state the level it ran at (`run.json` field `drums_level`).
A result at L0 must not be described as an L1 result.

Honest note, because the owner asked for the real architecture and not a container
baseline: at L0 the mechanism is made of the same kernel primitives Docker uses, and
containers started from one image already share that image's files and page cache. What
separates L0 from a container-per-attempt incumbent is only (a) no per-attempt container
runtime, image setup or init process, (b) shared running system services, (c) a
controlled template layer, and (d) a reset that destroys state without tearing down
anything else. If the benchmark shows that L0 uses about as much RAM per session as a
lean container-per-attempt baseline, that is a finding, not a flaw in the test. L1 is
where the architectural difference should show up, which is why it is the first
research item after L0 works.

## 3. Isolation and reset mechanism (decisions)

| Concern | Decision | Rejected alternative and why |
|---|---|---|
| Session filesystem | overlayfs per session: lower = [OS tree, template layer] read-only, upper = per-session directory on tmpfs or XFS, workdir beside it | Copying a tree per attempt: slow and large. btrfs/XFS reflink clone of a prepared tree: viable fallback if overlayfs misbehaves with the browser; also gives reflink-based "task-ready" snapshots. Docker image layers: that is the incumbent mechanism. |
| Process isolation | PID namespace + mount namespace + IPC + UTS, one cgroup v2 leaf; reset kills with `cgroup.kill` | A uid-only isolation like the macOS user sessions: leaks processes and shared `/tmp`. |
| Network | v0: shared host network namespace, a port range per session allocated by the supervisor (envd port, any task servers). v1: network namespace per session with a veth pair | v1 is stronger isolation but costs setup time and kernel objects per session, so it is measured as a variant. Under v0, cross-attempt interference through ports is possible and is counted by the probes in section 6. |
| Display | one Xvfb per session. In this sandbox an idle Xvfb at 1280x800 measured about 10.6 MiB PSS (RSS 15.6 MiB), two sessions, one run, before any window manager or browser. | A full desktop session per attempt (GNOME/XFCE): matches the incumbents' look but defeats the sharing. Wayland headless: less tooling for input and capture. |
| Input | XTEST on the session's display | uinput: it is a host-global device, so isolation is lost. |
| Capture | XGetImage (MIT-SHM) on the session's display | Screen-recording protocols: not needed headless. |
| Window manager | one small WM per session; the choice must be recorded, because the incumbents' images use whatever desktop the OSWorld image ships (not verified by me) | Matching it exactly would cost the memory the design tries to save; the success-rate parity check (design doc section 5) catches large behavioural differences. |
| Browser sandbox | Chromium needs user namespaces; some distributions restrict them (for example AppArmor policy on Ubuntu 24.04, not verified here). If blocked, run `--no-sandbox` on EVERY side and state it | Silently disabling it on one side only. |
| Reset | supervisor: kill the session's cgroup, unmount, delete the upper, create a new upper from the template layer, start dbus, Xvfb, WM, task start command, bump `gen`, then answer health | Rebooting a guest (incumbent mechanism). Snapshot-restore of memory (CRIU): research. |
| Pre-warming | pool of ready session skeletons (upper + display up) so reset is "swap", not "build" | None; this is standard. |
| Daemon topology | ONE host-level `envd-linux` process that listens on one port per session and holds per-session state (generation, action log, frame hashes). It reaches the session through its X socket (bind-mounted into a host path) and a tiny session-init process (PID 1 of the namespace) for launch/kill. | A Python daemon per session: about 20-30 MiB each, which would be charged against Drums and is avoidable (estimate, not measured). The one-process design keeps the per-port `Host` guard and the unchanged adapter. Its own RSS is counted as Drums overhead in the report. |

The reset time and everything above is measured by the harness (`reset_s`, RSS/PSS, cgroup
usage); none of it is an assumption in the results.

### Decisions made (no owner input needed)

- Window manager: for fidelity runs, the image's native desktop components; a small EWMH
  window manager (openbox is the candidate; licence to check at install) only after a
  per-task parity study passes. The spike runs with no window manager (none installed here).
- Networking: shared host network namespace (v0) is acceptable only for envd-linux
  spikes and Phase A. Anything that runs the OSWorld guest (fixed ports 5000, 9222, 8080)
  needs a network namespace per session (v1). This is forced by the ScaleCUA candidate; see
  `docs/training-comparison-plan.md` section 3.
- OSWorld shim: run OSWorld's own guest server inside each session; do not reimplement it.
- Root filesystem: full-root overlay (pivot_root onto overlayfs) for any run that executes
  OSWorld tasks; the spike overlays only `$HOME`.

## 4. What envd-linux must implement

Same vocabulary, same routes, same refusals as envd. Source of truth is the Swift code;
`compare/drums_compare/conformance.py` is the black-box check a candidate must pass.

| Route | Required for first run | Notes |
|---|---|---|
| `GET /health` | yes | same fields as envd (`env, uid, gen, on_console, screen{w,h,scale}, version, ax_trusted, screen_recording`) plus `backend: "linux"` and `level`. Report `ax_trusted`/`screen_recording` true only if X access works. |
| `POST /gen` | yes | sets the generation; per-session. |
| `GET /screenshot[?region=x,y,w,h]` | yes | PNG, 1 pixel per point, size fixed per run; headers `X-Drums-Gen`, `X-Drums-Captured-At`, `X-Drums-Stale`, `X-Drums-Black`; region validation; FNV-1a hash per region for stale detection; black-frame detection. Port the logic from `FrameHash.swift`. |
| `POST /action` | yes | types click, dblclick, type, key, scroll (and the harness-level `done`); same validation as `ActionSpec` (text up to 2000 chars, button left/right, modifiers cmd/shift/alt/ctrl: on Linux `cmd` maps to Super or is rejected, decision to record); key names from `KeyCodes.swift` mapped to X keysyms; atomic check-and-apply against the generation; 409 on stale; action log entry. |
| `GET /log` | yes | ndjson of action and screenshot events (latency, status). |
| `POST /launch`, `POST /kill` | yes | launch inside the session namespace; kill by pid or all (never the session init, daemon helper or protected names; the Linux equivalent of the ancestor-chain protection). |
| `GET /windows` | yes | via EWMH (`_NET_CLIENT_LIST`) on the session display. |
| `GET/POST/DELETE /pb` | clear at least | X selection owner per session; clearing on reset is required, read/write can follow. |
| `GET /axtext` | no | AT-SPI over the session bus; not needed by the first workload. Return `text: null, reason: unsupported`. |
| Local guard | yes | `Origin` header refused; `Host` must be `127.0.0.1:<session port>` or `localhost:<port>`; applied to every route including reads and unknown paths. |
| 404/405/400 behaviour | yes | as in `Routes.swift`. |

Not part of envd, added for Linux: the supervisor (create, reset, destroy sessions;
allocate ports and display numbers; write cgroup limits) with a small CLI.

### Build versus reuse

| Item | Decision |
|---|---|
| Namespaces, overlayfs, cgroup v2, `cgroup.kill` | Reuse kernel features. All worked in this sandbox except cgroup v2 and KVM, which were not available or not tested: PID + mount namespace and an overlayfs mount were created and used by two sessions without touching the lower layer (tested here, root, kernel 6.18). |
| Session setup tool | Candidate: `bubblewrap` (bwrap; licence LGPL-2.1-or-later in v0.12.0, earlier files say 2.0-or-later). It supports `--overlay-src/--overlay/--tmp-overlay`, PID/IPC/UTS/net/user namespaces, `--die-with-parent`; overlay options need v0.11.0 or later (released 2024-10-30) and do not work when installed setuid. It does no cgroups. Not installed here, so untested. Alternative: own `unshare`/`mount` code. Decision after a spike. |
| X server and WM | Reuse Xvfb (present here) and an off-the-shelf lightweight WM (not installed here; licence to check when chosen). |
| Input and capture libraries | Reuse: python-xlib (XTEST) or `xdotool`, `mss` or direct Xlib. Not installed here. Licences to check when chosen. |
| Daemon | Build in Python. Reason: `pyproject.toml` already lists `envd-linux/tests` and `envd-linux` on the Python path, X11/AT-SPI bindings are Python-friendly, and the daemon is not on the CPU-critical path. Alternative: port `EnvdCore` to Swift on Linux; it is only about 885 lines but is macOS-only today (`Package.swift` pins macOS 14; `FrameHash` imports CoreGraphics, the pasteboard hash uses CryptoKit, `KillSpec` decoding uses a CoreFoundation type check). Rejected for the first version. |
| Behaviour spec | Reuse the Swift sources and their tests as the specification; reimplement; check with the conformance suite. |
| Supervisor | Build. |
| Template layer builder | Build (a script that installs the application set into the lower tree and runs each application once to create its first-run state). |
| `verify-server`, harness, scorer | Reuse unchanged. `EnvdHttpEnv` gained a `pre_reset(slot, gen)` hook and a `pids_fn` so the supervisor can recreate the sandbox and report the session's cgroup pids. |

Size and schedule are not estimated here; I have no basis for them.

## 5. Image: what both sides must run

The workload needs the same software on every side. The proposal is to take the root
filesystem of the OSWorld Ubuntu image (the image the incumbents' docker providers boot)
as the shared lower tree for Drums-Linux, so applications and versions are identical to
the KVM-based incumbents. Facts I could confirm: OSWorld's docker provider runs
`Ubuntu.qcow2` read-only inside a QEMU/KVM container and resets by destroying the
container; the file is 23.7 GB (11.4 GB zipped) on Hugging Face under `xlangai/ubuntu_osworld`.
Not confirmed: the image's licence (the dataset card shows no licence field in what I
could read), which desktop shell and application versions it ships, and whether its
root filesystem can be used as an overlay lower without its init and VM-specific
configuration getting in the way. The container-based incumbents use different bits
(Ubuntu 24.04 desktop images for Cua; a GNOME container for `lite.osworld`), so image
parity across sides is approximate and is recorded per run (application list and image
digests in `run.json`).

## 6. Isolation probes (the "Gate")

`Makefile` has `probes` as a stub. These probes define what private means and are run on
Drums-Linux before any measured run and again after it. A failed probe is a counted
interference finding, not a reason to drop data.

1. A file written under `$HOME` and `/tmp` in session A is invisible in session B.
2. A browser cookie and localStorage value set in A is absent in B.
3. Clipboard text set in A is not readable in B.
4. Pointer and key events sent to A's display never change B's screenshot.
5. `kill all` in A leaves B's processes alive; killing A's cgroup leaves nothing of A.
6. A server bound to the same port in A and B (under v0 networking) is reported as a
   collision and counted.
7. After reset, a scan of the new upper finds no files from the previous attempt; a
   token written by attempt n is not present in attempt n+1.
8. A late action carrying the old generation is refused (this is also in the
   conformance suite).
9. Memory limit: a session that exceeds `memory.max` is killed alone.

## 7. What was and was not tested

A local spike now exists (`docs/envd-linux-spike.md`): a real daemon process, real X11 screenshots and
XTEST input, and a real Chromium task in a PID+mount+IPC+UTS namespace with an overlayfs on `$HOME`,
run end to end through the comparison harness. The notes below predate it; where they say
"not tested", the spike doc is the current record.


Tested here: PID + mount namespace with overlayfs for two sessions (isolation of writes,
lower untouched); two Xvfb displays started (idle memory measured). The conformance
suite runs against a stub written to the spec, and also against two deliberately
non-conforming stubs to prove it can fail.

Not tested here: cgroups, bwrap, XTEST input, a window manager, a browser, anything
with `/dev/kvm` (absent), an overlay on top of the OSWorld image, and any real envd. A
screenshot of an Xvfb display with a terminal came back blank (459 bytes, identical for
two displays), so capture of a drawn window was not demonstrated.


## Claim discipline

Keep three things apart: (a) REAL Chromium end-to-end through the daemon; (b) the OSWorld guest-server STUB contract (accessibility tree and terminal empty, pyautogui absent); (c) actual OSWorld app-environment compatibility, NOT proven. Keep L1 (shared pre-warmed runtime forks, the real Drums claim) separate from L0 (what is built so far). The OSWorld provider bridge is QUARANTINED (licence provenance); see `training-comparison-plan.md` section 4.
