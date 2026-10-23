# envd-linux spike: what runs, what is proven, what is stubbed

Status: local spike on this sandbox only. Nothing launched, no cloud, no credentials.
Code: `envd-linux/envd_linux/`, tests `envd-linux/tests/`. Adapter and harness changes in `compare/`.

## What it does

ONE real browser task runs end to end through an envd-linux daemon inside a private
session:

1. `Supervisor.create` starts `unshare --mount --pid --ipc --uts --fork --mount-proc` running
   `session_init` as PID 1 of the new namespace. In it: an overlayfs on `/home/drums`
   (lower: a shared read-only template tree holding a Chromium profile after its first run;
   upper and work: private to the session), a private tmpfs `/tmp` and `/dev/shm`, a private
   X-socket directory, and the session's own `Xvfb` (`-nolisten tcp -nolisten local`).
2. `envd_linux.daemon` (one process, one port per session) speaks the envd protocol:
   health, gen, screenshot, action, log, launch, kill, windows, local guard, generation
   guard. Screenshots are XGetImage frames encoded as PNG with the `X-Drums-*` headers;
   input is XTEST; launch/kill go through `nsenter` into the session's namespaces.
3. The comparison harness (`EnvdHttpEnv` + `run_attempt`) resets the session through a
   `pre_reset` hook (the generation is posted first so a late action from the previous
   attempt is refused while the session is rebuilt), launches Chromium at the form page,
   waits for the display to settle, runs a keyboard-driven agent (click, Tab, type, Enter,
   done) and verifies with the real `verify-server`.

## Choices and why

| Choice | Why | What it cannot prove |
|---|---|---|
| Screenshot: python-xlib `XGetImage` on the session's own Xvfb | In-process, no spawn per frame, gives raw pixels for the hash and black checks. `import`/`scrot` would work but spawn a process per frame. | Capture speed at scale; MIT-SHM was not used. |
| Input: XTEST on the session's display | Per-display input is the isolation primitive; uinput would be host-global. | Real desktop focus behaviour: there is no window manager, so keyboard focus follows the pointer and window placement is whatever Chromium asks for. A task that depends on window-manager behaviour is not exercised. |
| Daemon: Python, stdlib HTTP plus optional python-xlib | `pyproject.toml` already points at `envd-linux`. Runs under the system `python3` here because only that interpreter has python-xlib; the test suite talks to it over HTTP. | Throughput; a threaded stdlib server is not a performance design. |
| One daemon process, a port per session | Keeps the per-port Host guard and the unchanged adapter, no per-session interpreter. | Its own RSS is not yet counted in per-session memory. |
| Overlay on `$HOME` only | Simple, root is untouched. | A full-root overlay (pivot_root): package installs, `sudo`, system settings stay on the real root. Needed for OSWorld tasks. |
| Shared host network namespace | Session needs to reach `verify-server` on loopback. | Port collisions and cross-session network visibility; per-session network namespaces are required for the OSWorld guest ports and are not done. |
| No cgroups | This sandbox exposes cgroup v1 pieces, not a usable v2 tree. | Memory/CPU limits and cgroup-based accounting; resource numbers come from `/proc` over the session's PID namespace. |
| Chromium with `--no-sandbox`, `--disable-gpu` | No unprivileged user namespaces inside the session; software rendering under Xvfb. | Realistic GPU/sandbox behaviour. The browser shows the "unsupported command-line flag" bar; an agent sees it. |

Forced decisions this surfaced (made, not blocking): a `/proc` mount is needed in the
namespace (`--mount-proc`); the state directory must not live under the path that becomes
the session's private `/tmp` (the init opens its files and mounts first); the daemon's
X connection follows a session `epoch` so it reconnects after a reset; the adapter's
reset order changed to "post the generation, then rebuild".

## Proven here (tests that ran and passed)

- The real daemon process passes the full conformance suite (`drums_compare.conformance`,
  about 30 checks including Origin/Host refusal, stale 409, validation, 404/405) over real
  X screenshots and real XTEST input.
- One real Chromium task end to end: the form row reaches `verify-server` with the attempt's
  generation and token; the attempt is valid and passes; the shared template tree is
  byte-for-byte unchanged afterwards (listing of sizes and mtimes).
- Reset: a file written in the session's `$HOME` and `/tmp` is gone after reset; a second
  attempt on the same slot is valid with no foreign rows and no dirty reset.
- Isolation between two simultaneous sessions: files invisible to each other and to the host
  (`/home/drums` and `/tmp` on the host stay clean), the namespace sees only its own
  processes (host pids absent), session 0's X socket is neither on the filesystem nor on an
  abstract socket from session 1, kill-all in session 0 leaves session 1's process alive,
  and typing into session 0's browser changes session 0's frame and not session 1's.
- A late action carrying the previous generation is refused with 409 while the session is
  being rebuilt.
- One sample on this sandbox (not a benchmark): reset plus browser settle about 2.5 s; a
  session of about 12 processes (Xvfb plus Chromium) at about 363 MiB PSS (814 MiB RSS);
  about 9 MB of private copy-on-write state after one attempt. The browser's own shared
  libraries are in the host page cache, not in these figures.

## Stubbed or not proven

- `/pb` returns 501; `/axtext` returns "unsupported"; typing is ASCII only; `cmd` maps to
  Super; scroll direction (positive = down/right) is an assumption not checked against envd.
- Not tested: more than two sessions, concurrency under load, a window manager, GNOME or any
  OSWorld image, KVM (absent here), user namespaces, cgroup limits, a network namespace per
  session, memory sharing between sessions (this is L0: each session runs its own browser;
  nothing is shared at runtime beyond files), failure injection, long runs.
- The fake backend tests prove the protocol layer only. The integration tests skip
  themselves where root, `unshare`/`nsenter`, `Xvfb`, the Playwright Chromium, overlayfs or
  a python-xlib interpreter are missing.
- Flake found and fixed: the end-to-end test failed in about 2 of a dozen early runs because
  the harness's "display settled" check accepted the blank window Chromium paints before the
  page loads, so the first click and Tab went to the wrong place. The adapter now requires
  at least one display change after launch (or a long enough quiet period for apps that paint
  once) before it counts the display as settled. After that fix, 12 consecutive runs of the
  whole integration file passed. That is evidence, not proof; the settle rule is a heuristic
  and the real fix for readiness is an application-level signal.
- Requires `python-xlib` (`pip install python-xlib`) for the daemon and tests. It is not
  added to `pyproject.toml`/`uv.lock`, so `uv run pytest` skips the integration tests unless
  an interpreter with python-xlib exists (the tests find `/usr/bin/python3`).

## Level

This is L0: shared files and page cache, private state, per-session processes. It is an
engineering precursor, not a Drums result. The memory number above is what L1 (shared
pre-warmed runtime forks) would have to beat.


## Claim discipline

This document describes (a): the REAL Chromium end-to-end task through the namespace + overlayfs + Xvfb daemon. It says nothing about (b) the OSWorld guest-server stub contract (see `osworld-contract-spike.md`) and nothing about (c) actual OSWorld app-environment compatibility, which is NOT proven. It is an L0 precursor (shared files, private state, per-session processes); no L1 (shared pre-warmed runtime forks) claim follows.
