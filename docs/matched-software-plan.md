# Matched-software preparation: making the Drums arm run the SAME software as the incumbent image (plan, offline tools, bounded code)

**Status: BUILT offline tooling and two bounded code parameters, all unit-tested; NOTHING validated on a live host or against a real image root. No AWS access, no spend. Every route finding below is manifest logic or code reading and is marked as such. No equivalence is claimed: until measured evidence exists every result stays NOT_A_COMPARISON. No training or economics claim.**

## 1. The two targets, as actually observed

| Aspect | Real incumbent guest (qcow2) | Real Drums arm | Kind |
|---|---|---|---|
| OS | Ubuntu 22.04.3 LTS | Ubuntu 24.04 | release |
| Desktop | GNOME Shell 42.9 | openbox | session |
| Browser | Google Chrome 130.0.6723.58 | Chromium 141.0.7390.37 | package |
| LibreOffice | 7.3.7.2 | 24.2.7.2 | package |
| Kernel | 6.5.0-15 (the guest's own) | the host's (`7aws`) | **structural**: Drums shares the host kernel, so no choice of packages can match it |
| User and home | `user`, `/home/user` | root, `/home/drums` | identity and path |
| CPU and memory | 4 CPU, MemTotal 3,994,372 KiB | 4 CPU, cgroup 4096 MiB | budget (the VM's MemTotal is below configured RAM by firmware and QEMU; a small recorded difference) |

`drums_compare.matchprep` reproduces this table from the two manifests and reports package-level differences (`python -m drums_compare.matchprep --image image_manifest.json --drums drums_composition_manifest.json [--drums-dpkg full_dpkg.txt]`). **Input formats are the real ones:** the image manifest's `dpkg` is a LIST of `package=version`, plus `os_release`, `kernel`, `desktop`, `browser`, `libreoffice`, `guest_user_and_home`; `matchprep.drums_inventory` writes the Drums host's full list in the same form. Running it on the real image manifest is the first thing to do: it answers, from data, which of the gaps below exist.

## 2. What the code needs (read from `session.py`, `session_init.py`, `realkit.py`)

* The "full" mode builds `lowerdir=<template>:/` and pivots into it (`session_init.py` ~36-67). Everything the session runs AFTER the pivot comes from that tree: `dbus-daemon`, `at-spi-bus-launcher`, `openbox` and the server's python (`start_real_desktop`). Xvfb is a HOST process and needs nothing from the image.
* The kit's venv is built on `/usr/bin/python3.12` with system site-packages (`realkit.py`: `NEEDED_FILES`, `build`); a 22.04 image has python 3.10, so the kit as built cannot run inside an image root.
* CRIU restore assumes `/` as a lower layer (`session.py` restore path, ~385). An alternative base root is therefore for cold-reset stages only.
* A real desktop session here is: a session D-Bus, AT-SPI, openbox, and the pinned server. **GNOME Shell as a session is not expected to run**: a session has its own init, with no logind or system D-Bus (an `exec_systemd` experiment exists in `session.py`; it has host side effects, is unvalidated and is NOT used).

## 3. The decision: route B first, route A kept as the fallback, both unvalidated

* **Route A, install the VM's userland on a 22.04 Drums host.** Needs a 22.04 host (the kit, CRIU, cgroup and nested-virtualization findings so far are for 24.04), GNOME 42, `google-chrome-stable` 130.0.6723.58 (Google's apt repository serves the CURRENT stable, so an old `.deb` must be fetchable from a pool URL: availability UNVERIFIED), and LibreOffice 7.3.7 from the jammy archive. **It cannot reach per-package equality**: Ubuntu supersedes versions, and old ones may no longer be installable. Kernel still differs.
* **Route B, use the image's own root filesystem, mounted READ-ONLY, as the base layer.** Same bytes: every package and version, Chrome 130 and LibreOffice 7.3.7 with no download (the old-Chrome problem disappears), `/home/user` and its contents. Kernel still differs. Needs: the read-only mount, the base-root parameter, a python that runs the pinned server inside the image root, and possibly a window manager (below). **Recommended as the first thing to validate** because it is the only route that can give package-level identity, and it costs one host session to find out whether it works.
* Neither route changes the uid, ownership, network or privileges: sessions keep running as ROOT with a different HOME, and the veth network stays. The VM user `user` with sudo is a recorded difference, and changing it would be a separate, explicit decision.

## 4. What was built now (all unit-tested with fakes; none run on a real image)

1. `compare/drums_compare/matchprep.py`: manifest comparison, package inventory, route report (7 tests, using the actual VM and Drums facts above).
2. `Supervisor(home=..., base_root=...)` and `session_init --base-root`: **defaults reproduce today's behaviour exactly** (`/home/drums`, `/`); `base_root` other than `/` needs full mode, an existing directory, no CRIU, and no nesting with the state directory, and every refusal happens before any mount (5 tests). The existing real-session tests (17, including real sessions) still pass with the change.
3. `stages.build_drums` honours `drums_home` and `drums_base_root` from the config; with another base root it skips the host-browser template and lays down only the Desktop directory. `host_run check` adds `drums_base_root_read_only` (the mount must contain `ro`, and only a cold-reset stage may use it).
4. `deploy/aws_host/mount_image_rootfs.sh`: read-only NBD export, `ro,noload` mount of the largest ext4 partition (printed, never silent), verified read-only, refusing otherwise; unmount mode. Refusal paths tested; the mount itself UNVERIFIED on the AWS kernel (needs the `nbd` module).

## 5. Rename and Impress controls before any timing

The cold-reset gates in `host_run gates` (0 before, 1 after the correct action, 0 for the wrong one after a fresh reset; state, file and Writer isolation across resets) are config-independent and run unchanged on a matched Drums arm, so the matched arm must pass the same gates first. `drums_home: /home/user` makes the UNMODIFIED Impress task's `/home/user/.config/libreoffice/...` path resolve (the design of `docs/task-audit-13.md` section 3, option A); the GUI action sequence still needs its own `actions_validated` record before the task is allowed, and **no evaluator or target file is ever written to pass**.

## 6. Exact remaining load-bearing blockers

1. **Old Chrome (route A only):** a 130.0.6723.58 `.deb` source is unverified; route B has it in the image.
2. **Server and python (route B):** whether the pinned server files run on the image's python and its installed packages, or the image-shipped server must be used instead (the real image's `/run_bash_script` fails with `_append_event`, so its server differs from the pinned one: using it would be a different, disclosed server). Decided only by one live attempt.
3. **Session software (route B):** the image's dpkg list decides whether `openbox`, `wmctrl`, `xclip`, `dbus-x11`, `at-spi2-core` and `python3-pyatspi` exist in it; adding any is a recorded software addition. A GNOME image has `mutter`, which could replace openbox as the window manager (closer to the VM, no panel or hotkeys): unvalidated.
4. **GUI, session, D-Bus, systemd:** GNOME Shell, the system D-Bus and logind are not available in a session; tasks needing the shell, hotkeys such as Ctrl+Alt+T, or `gsettings` persistence stay out of scope for the Drums arm and must be excluded per task, not discovered at run time.
5. **Host kernel and per-task scope:** the kernel cannot match (structural). The task scope stays the single audited rename task plus, with `drums_home`, an unmodified Impress task only after its GUI sequence is validated.
6. **Image permissions and mounting:** root, the `nbd` module, a read-only ext4 mount without journal replay, and the image's licence (the card's License field is blank: unresolved) on the AWS host.
7. **Identity and ownership:** root with `/home/user` versus uid 1000 `user`: files created by tasks are owned by root in Drums and by `user` in the VM; recorded, not changed.

## 7. Cheapest honest next live step (owner-approved spend only)

On one small host: run `mount_image_rootfs.sh`, then `matchprep` with the real image manifest and the host's full inventory; then one Drums session with `drums_base_root` set, the rename task through the existing cold gates, and the matchprep verdict. If the server or desktop software gap in section 6 appears, record it and stop; do not paper over it.
