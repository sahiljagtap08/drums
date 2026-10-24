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
* What a session actually STARTS after the pivot, read from `session_init.start_real_desktop` (`rootcheck.desktop_programs`, not guessed): `dbus-daemon`, `/usr/libexec/at-spi-bus-launcher` and `openbox`, then the pinned server on the kit's python. `xclip` and `wmctrl` are NOT launched there; the pinned server runs `wmctrl` (and others, derived from its source with `rootcheck.server_programs`) per task.
* **Whether GNOME Shell can run inside a session is unvalidated**, and is not concluded from package names: a session has its own init, with no logind or system D-Bus (an `exec_systemd` experiment exists in `session.py`; it has host side effects, is unvalidated and is NOT used). The root/home plumbing does NOT declare GNOME or the user identity matched.

## 3. The decision: route B first, route A kept as the fallback, both unvalidated

* **Route A, install the VM's userland on a 22.04 Drums host.** Needs a 22.04 host (the kit, CRIU, cgroup and nested-virtualization findings so far are for 24.04), GNOME 42, `google-chrome-stable` 130.0.6723.58 (Google's apt repository serves the CURRENT stable, so an old `.deb` must be fetchable from a pool URL: availability UNVERIFIED), and LibreOffice 7.3.7 from the jammy archive. **It cannot reach per-package equality**: Ubuntu supersedes versions, and old ones may no longer be installable. Kernel still differs.
* **Route B, use the image's own root filesystem, mounted READ-ONLY, as the base layer.** Same bytes: every package and version, Chrome 130 and LibreOffice 7.3.7 with no download (the old-Chrome problem disappears), `/home/user` and its contents. Kernel still differs. Needs: the read-only mount, the base-root parameter, a python that runs the pinned server inside the image root, and possibly a window manager (below). **Recommended as the first thing to validate** because it is the only route that can give package-level identity, and it costs one host session to find out whether it works.
* Neither route changes the uid, ownership, network or privileges: sessions keep running as ROOT with a different HOME, and the veth network stays. The VM user `user` with sudo is a recorded difference, and changing it would be a separate, explicit decision.
* **Correction from the real image manifest (1932 packages).** wmctrl 1.07-7build1 is PRESENT; openbox and xclip are ABSENT; there is no package named `mutter`, but `gir1.2-mutter-10`, `libmutter-10-0`, `mutter-common` 42.9-0ubuntu4 and `gnome-shell` 42.9-0ubuntu2 are present; Chrome 130.0.6723.58-1; LibreOffice 1:7.3.7-0ubuntu0.22.04.4; Python 3.10.6-1~22.04. An earlier version of this plan and of `matchprep` misattributed `xclip` to the session start and treated the missing `mutter` package as meaning no mutter-based option; both are removed. The Drums manifest tracks only 10 packages, 7 common with the VM and all 7 at different versions: that is NOT a full inventory comparison (the report now says so and gives no fraction).

## 4. What was built now (all unit-tested with fakes; none run on a real image)

1. `compare/drums_compare/matchprep.py`: manifest comparison, package inventory (labelled partial unless a full Drums list is given), and a route report whose prerequisites are DERIVED from the code and the real manifest (10 tests; the package arrays are synthetic stand-ins except the packages the independent review read from the real manifest).
2. `Supervisor(home=..., base_root=...)` and `session_init --base-root`: **defaults reproduce today's behaviour exactly** (`/home/drums`, `/`); `base_root` other than `/` needs full mode, an existing directory, no CRIU, and no nesting with the state directory, and every refusal happens before any mount (5 tests). The existing real-session tests (17, including real sessions) still pass with the change.
3. `stages.build_drums` honours `drums_home` and `drums_base_root` from the config; with another base root it skips the host-browser template and lays down only the Desktop directory. `host_run check` adds `drums_base_root_read_only` (the mount must contain `ro`, and only a cold-reset stage may use it).
4. `deploy/aws_host/mount_image_rootfs.sh`: read-only NBD export AND `blockdev --setro` AND an `ro,noload` mount of the largest ext4 partition (printed, never silent), each verified; an EXIT trap with a success flag that unmounts only what this run mounted, disconnects what it attached and removes its state on ANY failure or interrupt; it refuses a busy device (nonzero size, an owning process, or mounted), a non-root run, relative paths, a symlinked image or mount point, `/` or a top-level or system mount point, and a non-empty or already-mounted target. Tested with FAKE qemu-nbd, mount and lsblk (22 tests: success, four failure modes each leaving nothing attached or mounted, busy devices, unsafe arguments, unmount); against a real device it is UNVERIFIED, and the `nbd` module on the AWS kernel is unchecked.
5. `envd_linux/rootcheck.py` and its use in `Supervisor` and `host_run check` (section 6, item 2), with `drums_template_overlay` bound into the stamp by a listing digest of its tree.

## 5. Rename and Impress controls before any timing

The cold-reset gates in `host_run gates` (0 before, 1 after the correct action, 0 for the wrong one after a fresh reset; state, file and Writer isolation across resets) are config-independent and run unchanged on a matched Drums arm, so the matched arm must pass the same gates first. `drums_home: /home/user` makes the UNMODIFIED Impress task's `/home/user/.config/libreoffice/...` path resolve (the design of `docs/task-audit-13.md` section 3, option A); the GUI action sequence still needs its own `actions_validated` record before the task is allowed, and **no evaluator or target file is ever written to pass**.

## 6. Exact remaining load-bearing blockers

1. **Old Chrome (route A only):** a 130.0.6723.58 `.deb` source is unverified; route B has it in the image.
2. **Server, kit and python (route B), now protected before first use:** after the pivot the session sees only `overlay(template : base_root)`, so the HOST's kit (`/opt/drums-osworld-real`) and the HOST's python 3.12 (the kit venv's `python` is a symlink to it; its interpreter and libraries differ from the image's 3.10.6) are gone unless the owner places a validated kit and runtime in the template layer (`drums_template_overlay`, copied into the template by the stage). `rootcheck` resolves every path THROUGH the layers (symlinks followed inside the union, never out to the host) and the Supervisor and `host_run check` refuse BEFORE any session mount when the loader, `dbus-daemon`, `at-spi-bus-launcher`, `openbox`, the kit server or the kit python (including its symlink target) are missing or not executable. Whether the pinned server then runs on that runtime, or the image-shipped server (which fails `/run_bash_script` with `_append_event`, so is a different server) would have to be used, is decided only by one live attempt.
3. **Session software (route B):** the real manifest shows `openbox` ABSENT from the image (the session starts it after the pivot), while `dbus`, `at-spi2-core` and `wmctrl` are present: a window manager must be added to the template layer as a recorded software addition, or the session code changed; no alternative is claimed (the image has no standalone `mutter` package, and nothing here concludes how gnome-shell would behave). `xclip` is absent but is not launched by the session; whether a task needs it is derived from the pinned server source per task.
4. **GUI, session, D-Bus, systemd:** gnome-shell is installed in the image, but a session has no logind or system D-Bus and its own init, so whether GNOME Shell (or its hotkeys, or `gsettings` persistence) can work inside a session is UNVALIDATED. Tasks that need them stay out of scope for the Drums arm until shown otherwise, and are excluded per task, not discovered at run time.
5. **Host kernel and per-task scope:** the kernel cannot match (structural). The task scope stays the single audited rename task plus, with `drums_home`, an unmodified Impress task only after its GUI sequence is validated.
6. **Image permissions and mounting:** root, the `nbd` module, a read-only ext4 mount without journal replay, and the image's licence (the card's License field is blank: unresolved) on the AWS host.
7. **Identity and ownership:** root with `/home/user` versus uid 1000 `user`: files created by tasks are owned by root in Drums and by `user` in the VM; recorded, not changed.

## 7. Cheapest honest next live step (owner-approved spend only)

On one small host: run `mount_image_rootfs.sh`, then `matchprep` with the real image manifest and the host's full inventory; then one Drums session with `drums_base_root` set, the rename task through the existing cold gates, and the matchprep verdict. If the server or desktop software gap in section 6 appears, record it and stop; do not paper over it.

## 8. Finite execution plan (answers "can we run the comparison now?")

**No comparison is runnable today.** The mount script, the base-root plumbing and the Python-minor refusal are unit-tested with fakes and synthetic trees only: **no real image, device or mount has ever been exercised.** The 10/10 scripted rename run stays a harness/validity result; it is not a comparison and not training evidence.

**Decision (choice 1 first, choice 2 only after it):**

1. *Matched cold-reset on the small host* (stock Docker/KVM provider vs Drums, same task e0df059f). Worst case is the already-verified ≈0.53 USD (m7i.xlarge, one attempt, ≤2 h provision-to-terminate, 90+30 min), inside the original 100 credits + 10 tax. The earlier actual m7i.xlarge run did use KVM for the stock Docker arm (4 CPU / 4 GiB), so small-host KVM is measured evidence, not a blocker; the provider's own KVM check is still asserted at run time. It tests cold reset only and says nothing about live snapshots.
2. *Original live-snapshot route* needs VirtualBox/VMware on bare metal (quota 96 allows one 96-vCPU metal instance; it is not a capacity guarantee). Run it only after (1) has produced a valid, labelled result. Worst case = hourly price READ from the Pricing API at launch time x the stage ceiling (3 h); that product is written into the stage file before launch, not assumed here.

**"Boot the official VM as the host and run Drums root-full inside it"** is possible wherever KVM is available (the small host showed it is; no metal is needed for that), as a guest; an AMI import would need new IAM/S3 resources, which this plan does not create. A snapshot hypervisor (VirtualBox/VMware) is a separate question and must be validated on its own. It would make GNOME, user `user` and the 6.5 kernel of the VM native, but session_init hard-codes `openbox` and starts the desktop under a per-session Xvfb, so GNOME per session was NOT done until the optional `desktop="gnome"` / `uid` plumbing (section 9). It is unvalidated live. A mutter library or package is never a substitute for GNOME and is never described as equivalent.

**Finite real validation, before any timing (small host, one attempt):**
- Inputs: the pinned image mounted read-only by `mount_image_rootfs.sh`; a kit rebuilt for the image's Python 3.10 inside the owner-prepared `drums_template_overlay`; `rootcheck` clean (programs, loader, kit python, pyvenv minor, site-packages).
- Checks, in order, each a hard stop on failure: (1) mount verified read-only (script's own checks); (2) Supervisor constructs; (3) one session boots to a screenshot; (4) identity: `gnome-shell --version` binary present in the image (42.9 expected) AND the live session's actual window manager/shell name recorded from the running session. If the running desktop is not GNOME, record `desktop: not matched` and stop timing claims.
- Stop conditions: any failed check, ≥90 min elapsed, ≥0.80 USD accrued, or two consecutive boot failures. Raw failures are kept; a failed validation is published.
- Unvalidated until then: the new mount fix against a real nbd device, overlay behaviour on an ext4 `noload` image root, and the kit under 3.10.

## 9. Built now: minimum boot-ready path (all UNVALIDATED live)

- `Supervisor(desktop="gnome", uid=1000, gid=1000, user="user")` (defaults `openbox`/no uid, unchanged): `session_init` starts the image's `gnome-session --builtin --session=ubuntu-xorg` instead of `openbox`; the desktop, dbus, AT-SPI and server children drop supplementary groups, gid, then uid BEFORE exec (the init parent stays root for setup) and get `USER`/`LOGNAME`/`HOME`/`XDG_RUNTIME_DIR=/run/user/<uid>`. No `exec_systemd`. `server_mode="fork"` with a uid is refused.
- The session records an `identity` mark: the window manager's NAME read from X (`xprop`), desktop process uids and server uids.
- `deploy/aws_host/prepare_kit_for_image.sh`: builds the kit with the image's own `/usr/bin/python3` in a chroot over an overlay (no host 3.12 mapping); `rootcheck` refuses a kit whose Python minor differs.
- `python -m drums_compare.boot_validate`: boot-only; aborts before any session when programs/kit are missing; otherwise records screenshot, uid, WM name and server imports; `timing_allowed` only if all hold.
- Known open risk: whether `gnome-session --builtin` and `gnome-shell` start under per-session Xvfb without systemd/logind is not known. If the boot validation shows a different WM, the report records `desktop: not matched`; nothing is claimed.
