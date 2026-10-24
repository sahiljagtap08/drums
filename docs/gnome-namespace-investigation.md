# Can the OSWorld image's GNOME session run in a namespaced session without systemd as PID 1?

**Epistemic status: HYPOTHESIS-UNTESTED for GNOME under our namespaces; one systemd-as-PID-1 experiment was RUN and MEASURED once (results and an incident in this page).**

STATUS: desk investigation PLUS one local experiment (section 'Experiment: systemd as PID 1', below). The OSWorld image's GNOME session itself is still **not established**. This sandbox has no
`gnome-session`, `gnome-shell`, `mutter` or `at-spi-bus-launcher`, no OSWorld image and no `/dev/kvm`
(PID 1 here is `process_api`; `dbus-daemon` and `dbus-run-session` exist). Anything below that is not a
quoted fact is an inference and says so.

Claim levels (see `training-comparison-plan.md`): this note is about (c), actual OSWorld app-environment
compatibility, which is **not proven**. Our sessions today are an L0 precursor and run Xvfb with no desktop
shell; nothing here bears on L1 (shared pre-warmed runtime forks).

## What the public OSWorld documentation says the guest assumes (Apache-2.0 repo, `desktop_env/server/README.md`)

- Ubuntu with the `ubuntu-desktop` package (GNOME) and `systemctl set-default graphical.target`.
- Xorg, not Wayland (checked with `echo $XDG_SESSION_TYPE` = `x11`); a dummy Xorg video driver at 1920x1080 in the documented setup.
- The guest server runs as a **system systemd unit** (`osworld_server.service`) with `DISPLAY=:0`,
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus` and `XDG_RUNTIME_DIR=/run/user/1000`. The session bus
  is needed for the accessibility tree (AT-SPI) and for changing the wallpaper.
- x11vnc and noVNC are **user systemd units** (`systemctl --user`), after `display-manager.service`.
- Ports 5000 server, 9222 Chromium, 8006 VNC, 8080 VLC, 5910 noVNC. Optional: disable `unattended-upgrades`.
- A third-party reading of the server (a teardown note on GitHub, not authoritative) says the Linux paths use
  pyautogui, `pyatspi`, `wmctrl`, `xclip`, `gnome-screenshot`, and hardcode `DISPLAY=:0`, uid 1000, user `user`.

So the documented image is built around two systemd instances (system and user) and a logind-managed uid-1000 session.
I did not inspect the actual image, which may differ from the setup instructions.

## What GNOME upstream says about running without systemd

- GNOME "strongly depends on logind" since 3.x (ConsoleKit support removed 2015). `elogind` can supply logind
  without systemd. (Adrian Vovk, blogs.gnome.org, 2025-06-10.)
- Since GNOME 3.34 `gnome-session` starts session services through the systemd **user** instance, and falls back to a
  builtin `.desktop`-file service manager when systemd is unavailable. The same post says that builtin manager is
  being **removed** (it targets GNOME 49 and later), that upstream does not test non-systemd paths, and that
  non-systemd setups must supply their own service manager, session leader and `gnome-session-ctl` replacement.
- Reported fallback behaviour on GNOME of that era (a forum log, not an upstream guarantee): with no
  `org.freedesktop.systemd1` on the bus, `gnome-session-binary` logs "Falling back to non-systemd startup
  procedure", warns it cannot get a session id from logind, and carries on.
- Containers that run a GNOME session in practice either run systemd inside (x11docker `--init=systemd`) or bind in
  the host's logind/system bus.

## What this implies for Drums (inference, unverified)

1. The image's GNOME version is not recorded here. If it is the GNOME 42 era implied by Ubuntu 22.04, the builtin
   fallback should still exist, but whether `gnome-shell` under it comes up usably with no logind (needed for
   session/seat state and idle/lock handling) is untested. That test needs the image.
2. The unknown that decides it is whether `org.gnome.Shell`, `gnome-settings-daemon`, `at-spi2` and the guest server
   all start in the order and environment the image's boot sequence gives them. The image's real boot sequence is
   systemd units; reproducing it by hand is the work, and **any hand-built substitute is a different environment
   from the one the incumbents run**, which weakens "same tasks, same environment".
3. Three candidate routes, none tried:
   - **A. systemd as PID 1 inside each session** (the `--init=systemd` pattern). Closest to the image. Costs a
     cgroup namespace and writable cgroup subtree, more mounts and a heavier per-session boot, which eats into
     the density argument. Whether it fits inside our `unshare` + overlayfs `pivot_root` session is untested.
   - **B. no GNOME**: Xvfb + a window manager + `dbus-run-session` + `at-spi-bus-launcher` + the guest server.
     What we run now minus the AT-SPI pieces. Cheap and dense, but tasks that rely on GNOME features (gsettings,
     Nautilus, gnome-terminal, the wallpaper route) would differ from the incumbents and cannot be called the same
     environment.
   - **C. elogind + a non-systemd service manager** replacing the session leader. Upstream says this is the
     unsupported path and the effort is not bounded.
4. A cheap way to learn which route is feasible, when it is time (needs a Linux host with the image or a stock Ubuntu
   22.04 desktop, still no cloud needed beyond a local box): in a private PID+mount+UTS namespace with a fresh
   `dbus-daemon --session`, start `gnome-session --session=ubuntu` and record what comes up (`org.gnome.Shell` on the
   bus, windows mapped under Xvfb or Xorg+dummy, `gdbus` AT-SPI registry present), what logs "falling back", and what
   exits. That is a bounded experiment, not run here.

## Networking notes recorded alongside (read-only)

- The guest ports are fixed, so a per-session network namespace is required (already built in `netsetup.py`).
- Our veth/NAT story gives sessions no egress yet, and no firewall between sessions exists. Isolation between them
  is only a side effect of `ip_forward=0` (already pinned by a test). The documented guest also expects noVNC on
  5910 and the system `unattended-upgrades` disabled, which an in-session network must not rely on.

## Not established

- Whether the OSWorld image's GNOME session runs without systemd as PID 1: **unknown**.
- Whether route A fits in our namespace stack, what it costs per session, or whether it changes the density story: **unknown**.
- Which GNOME version and which session type the image really uses: **unknown**, not inspected.


## Experiment: systemd as PID 1 in our namespace (no OSWorld image needed)

`envd_linux/systemd_probe.py` and `Supervisor(exec_systemd=True)`: the session's PID 1 `exec`s `/lib/systemd/systemd
--system --unit=multi-user.target` after the full-root overlay setup (the session root is this host's Ubuntu 24.04, which
has systemd 255). One run, no network namespace, one 12 s wait.

Observed, from outside with `nsenter`:
- systemd stayed PID 1 (`/proc/1/comm` = `systemd`), `systemctl is-system-running` = `running`, 17 active units, 0 failed,
  `systemd-detect-virt --container` = `container-other` (it did not refuse to run in the namespace).
- The sandbox host is cgroup v1/hybrid (`/sys/fs/cgroup/<controller>` plus `unified`); systemd ran with that hierarchy
  inherited from the host (`name=systemd:/init.scope`).

THIS RUN HAD HOST SIDE EFFECTS, which are the main finding:
1. Systemd started the HOST ROOTFS's enabled units (the session root is a copy of the host `/`). Among them, `docker.service`
   ran in the host's network namespace (no private netns in this run) and created the `docker0` bridge, Docker's `ip nat`,
   `ip filter`, `ip6 nat` and `ip6 filter` netfilter tables, and turned `net.ipv4.ip_forward` to 1.
2. Without a cgroup namespace, systemd wrote units into the HOST's cgroup hierarchy (`init.scope`, `system.slice`, `*.mount`
   under every controller). The empty leftover directories were removed afterwards with `rmdir` (only empty ones, newer than
   an hour), which was a hand cleanup with no ownership record.
3. After the run, `docker0`, those four netfilter tables and `ip_forward=1` are STILL PRESENT in this sandbox. My attempt to
   delete them was refused by the permission layer, and I did not try another way. Removing them is the owner's decision.
   They do not collide with the sweep's own `inet drums_*` tables or `dvh*` links, and the test suite restores `ip_forward` to
   whatever it found, but a run that assumes a clean netfilter ruleset or `ip_forward=0` as the starting value will see
   different values. No further systemd experiment was run for that reason.

What this says about running systemd inside a session (inference from one run, not a tested design):
- Capability requirements seen: PID/mount/UTS/IPC namespaces (have), the ability to mount `/run`, `/sys`, `/dev` (have), a
  writable cgroup hierarchy (host-wide here, which is the problem), `CAP_SYS_ADMIN` (root).
- NOT satisfied and required before anything real: a PRIVATE network namespace (else services bind host ports and change
  host netfilter); a CGROUP NAMESPACE with the session placed in its own cgroup subtree first (so systemd's cgroups are
  inside a subtree we own and the sweep can `rmdir` by ownership record); and a TRIMMED unit set (a clean image, or
  `systemd.mask` for everything not needed) so the host rootfs's services do not start. None of these three was tried.
- The recording-update probe boundary found earlier does not need systemd when the guest server is a current build
  (osworld-contract-spike.md section 8); systemd matters for an older guest image and for GNOME/session services.
- Still unknown: GNOME Shell or `gnome-session` under that systemd, a logind session, the user systemd instance
  (`systemctl --user`) that GNOME 3.34+ expects, and the cost per session in memory and start time.

Ownership consequence for the sweep: a systemd session adds cgroup directories to what a run owns. The sweep does not know about
them yet; until it does, an exec-systemd run is not sweepable.
