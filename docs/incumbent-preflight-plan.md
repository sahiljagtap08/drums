# Incumbent arm: minimal preflight and decision package

**Status: PLAN. HYPOTHESIS-UNTESTED. Nothing here is approved, provisioned, authenticated, priced or run. Engineering validation only: no headline win, no training claim, no benchmark claim.** Current host availability is UNKNOWN (no host or image has been confirmed). No outreach to anyone has been made or is proposed. No AWS re-authentication is requested.

## 1. What remains: internal work vs owner decisions (kept apart)

| # | Item | Who | Needs a host? |
|---|---|---|---|
| I1 | **BUILT** (`envd-linux/envd_linux/composition.py`, test `tests/test_composition.py`; the Drums arm's manifest is `docs/data/l1-criu/drums_composition_manifest.json`). A composition manifest generator: pinned versions of OS packages, browser build, LibreOffice, Python packages, the guest-server and `desktop_env` commits, for the Drums rootfs, plus the same manifest format for the VM to be filled in and diffed | internal | no |
| I2 | **BUILT** (`envd-linux/envd_linux/preflight.py`, test `tests/test_preflight.py`). A host preflight checker that keeps three kinds of result apart: `static` (file, tool and flag presence: NOT permission to create a namespace), `runtime_probe` (run only with `--probe`: it briefly creates and removes a namespace set, an overlay mount with `redirect_dir=on`, a cgroup limit directory, and asks VirtualBox for hardware virtualization) and `gate` (the first-pass gates of section 7, always `untested` until a run exists). Each result is ok / unsupported / untested. The Drums limits probe is part of `go_joint_run_on_this_host`, which stays False while any limit probe, the hardware/enabled state, or an application-snapshot or evaluator gate is untested. The Extension Pack is "not assumed", co-residence and nested-host checks are always untested, memory and disk are reported and never judged, and an empty `VBoxManage list extpacks` is handled. | internal | no (its first run here reports NO-GO for the missing virtualization) |
| I3 | **BUILT; the Drums side SMOKED on real sessions here (section 8b); the incumbent arm is wired but cannot run here** (`compare/drums_compare/first_check.py`). The first-pass matched-check driver: alternating blocks, concurrency 1, same local task and scripted agent, both arms with baseline reuse ON, raw faults, pins. Accounting: anything done before the driver clock (supervisor, template, pool, image) is passed in as `external_prep` and the arm-factory time is measured, both reported and added in the *_with_prep figures; the run-arm-only figures are kept beside them. Host-wide memory and CPU are recorded; **per-environment capture is MISSING and resource parity is NOT claimed** | internal | the Drums arm yes (here); the incumbent arm only on the real host |
| O1 | An existing owner-authorized host, or a rental (decided later, section 8) | owner | n/a |
| O2 | The VM image: the official one (licence field blank) or a self-built one (licence composition unaudited) | owner | n/a |
| O3 | Hypervisor: VirtualBox base package (GPLv3) is the proposed default; VMware Workstation Pro is the alternative (free, Broadcom account needed) | owner | n/a |
| O4 | Price inputs for the cost accounting (the owner's own price sheet; none is assumed here) | owner | n/a |

I1 to I3 proceed without any decision; O1 to O4 gate only the real incumbent run. Main will raise the host-or-rental question at a convenient moment, not overnight.

## 2. Candidate host: requirements (stated as requirements, not as availability)

A **non-virtualized x86-64 Linux machine with administrator rights, authorized by its owner**, because the documented-supported way to run VirtualBox or VMware is a non-virtualized host (OSWorld README, "Installation: VMware/VirtualBox (Desktop, Laptop, Bare Metal Machine)"; AWS lists only KVM and Hyper-V as nested L1 hypervisors, GCE only KVM: see `l1-shared-runtime-design.md` section 23.4). Needed on it:
- **CPU virtualization** exposed (`vmx` or `svm` in `/proc/cpuinfo`; the sandbox has none and no `/dev/kvm`).
- **VirtualBox base package** (GPLv3; Guest Additions GPLv3). **The Extension Pack is NOT ASSUMED**: it is under a separate licence (PUEL, personal and educational use; commercial use for a fee) per `https://www.virtualbox.org/wiki/Licensing_FAQ` (accurate for 7.1 and later, last updated 12 September 2024). Whether the first pass needs a feature that lives in the Extension Pack is UNTESTED (the preflight lists it as `untested`); "not assumed" is not "not needed".
- **For the Drums arm on the same host:** root; PID, mount, IPC, UTS and network namespaces; overlayfs with `redirect_dir`; `pivot_root`; veth and `nft`; cgroups (v1 or v2: the v2 path of `limits.py` has never run on a real v2 kernel); Xvfb; and a private CRIU 4.2 install (4.1.1 fails on kernel 6.18; the kernel needs `CHECKPOINT_RESTORE`; see section 15 of the design doc). Nothing is installed system-wide by Drums.
- **Co-residence is unverified:** that VirtualBox and the Drums arm run correctly on the same host at the same time (Oracle's manual warns against running other hypervisors alongside VirtualBox; Drums uses namespaces, not KVM, so no conflict is expected, but it is expected, not tested). The first pass runs the arms in alternating blocks, never simultaneously, which sidesteps interference.

**Preflight result vocabulary (I2).** Every check is `ok` (checked, present), `unsupported` (checked, absent or failing: a capability the host lacks) or `untested` (not checked: unknown, never to be read as either). A `go_*` answer needs every needed check `ok`; an `untested` item keeps it at no-go. **Run on the current sandbox it answers NO-GO for the incumbent** (`unsupported`: no `vmx`/`svm`, no `VBoxManage`; `untested`: the Extension Pack question, VirtualBox beside Drums on one host, VirtualBox on a nested or cloud host, and the CRIU install when no path is given) and no-go for a joint run. It asserts no minimum RAM or disk.

## 3. Capacity: establish before claiming

**2 vCPU and 4 GiB per environment is the PARITY setting taken from the shipped ScaleCUA worker config, not a host minimum.** Before any capacity statement the following are measured on the candidate host, and reported as measured: image size on disk after extraction and after first boot; per-VM resident memory at idle and under the task; per-VM disk growth with a snapshot taken (a running-VM snapshot stores the memory state, which "can be as large as the memory size of the virtual machine"); free RAM and disk headroom with the Drums arm's own footprint (about 235 MiB per added session in our measurements, a different number on a different host); and how many VMs can coexist without swapping. The first pass uses concurrency 1, so these numbers inform the next step and nothing else.

## 4. Common software composition (authorized, pinned, and not the same disk)

Both arms must run the SAME pinned software, but they are different artefacts: the incumbent VM boots its own kernel and disk; the Drums session shares the host kernel and uses an overlay root. **It is not claimed that the VM disk runs inside a namespace, nor that the two environments are byte-identical.** What is made equal and recorded: OS release and package versions (from a manifest, I1), the browser build, LibreOffice and other applications, the public guest-server files at the pinned `desktop_env` commit (`04718ea` is what Drums runs now), the evaluator code and Python package versions, screen size, and the task files. Differences that cannot be removed (kernel, init system, GNOME vs the session's openbox desktop, display driver, clock source) are listed as known non-equivalences in the run record. Any application added to the Drums rootfs must be one the owner is authorized to use; the manifest, not memory, is the evidence.

### 4a. Currently NOT the same (recorded; "same software" is a TARGET, not a state)

| Aspect | Drums arm today (measured: `docs/data/l1-criu/drums_composition_manifest.json`) | Incumbent VM (the official image is not obtained; nothing is known from a run) |
|---|---|---|
| OS | Ubuntu 24.04 (noble), the host's kernel 6.18.44 | the image's own release and kernel; the server README mentions both Ubuntu 20.04 and 22.04: the actual release is UNVERIFIED |
| Desktop | **openbox 3.6.1, no GNOME shell** (a window manager started by the session) | GNOME (the OSWorld setup instructions install `ubuntu-desktop` with GNOME) |
| Browser | Playwright Chromium 141.0.7390.37; **no `google-chrome`** | tasks launch `google-chrome` (79 public tasks do, each with a `socat` helper): not runnable on the Drums arm as it stands |
| Office | LibreOffice 24.2.7.2 (the `gen` VCL plugin only: no accessibility tree) | unknown; the real image is built for an accessibility tree, which the benchmark's evaluators and observations use |
| Guest server | unmodified public files at OSWorld commit `04718eab9327856efe8192cc58161acc7a7627e2` in a Python 3.12.3 venv | the image's own copy and Python: identity UNVERIFIED |
| Package identities | recorded in the manifest (`dpkg` versions) | not available until the image exists and its manifest is generated |
Consequence: these are **flags that preclude any broad benchmark claim** and any statement that the two arms ran "the same environment". The first pass compares the REUSE paths on one local task only; it must carry this table in its run record, and any later widening has to remove each difference or justify it.

## 5. Licence position (unresolved items stay labelled unresolved)

- The official image's Hugging Face card has a blank License field: that is **uncertainty, not a ban on local research use**, and it is not treated as permission either. No outreach is made. The decision between using it as published and building an image is the owner's (O2).
- A self-built image is **not automatically licence-clean**: the OSWorld server README is explicitly incomplete, the application set includes components with their own terms (a browser, an editor, media and mail clients), and upstream setup steps download files. Its licence composition is **UNVERIFIED until audited**, component by component.
- OSWorld code is Apache-2.0. VirtualBox base GPLv3. VMware Workstation Pro: free for all uses, Broadcom account required for download (not done).

## 6. Route fact: VirtualBox running-VM snapshots keep memory

From Oracle's own documentation (the manual's snapshot chapter, Snapshot Contents; the VirtualBox source docs `snapshots-take-restore-delete`; and the current manual page you named, `https://www.virtualbox.org/manual/topics/working-with-vms.html`, whose fetched text in this session was truncated before the snapshot section, so the wording below is taken from the 6.0 manual `https://docs.oracle.com/en/virtualization/virtualbox/6.0/user/snapshots.html` and the upstream DITA source): a snapshot taken while the machine is running also saves its memory state, and restoring it resumes execution at exactly the point the snapshot was taken; `VBoxManage snapshot <vm> take|restore` and `--live` exist. **How the public OSWorld provider uses it** (`desktop_env/providers/virtualbox/provider.py`, as shipped): `save_state` = `VBoxManage snapshot <uuid> take <name>` then a fixed 3 s sleep; `revert_to_snapshot` = `controlvm savestate`, 3 s, `snapshot restore <name>`, 3 s; `start_emulator` = `startvm` with further 3 s waits. **So the as-shipped incumbent revert carries fixed sleeps of roughly 9 s or more per revert.** The first pass uses the provider AS SHIPPED and records this; shortening the sleeps is a tuning of the incumbent that, if done, must be disclosed and run as a separate labelled arm.

## 7. First-pass gate (all must hold before any timing is read as meaningful)

1. The real public `DesktopEnv` boots the incumbent VM through the VirtualBox provider on the chosen host.
2. An application, an unsaved token typed into it and a live background process survive a snapshot revert (the same three things checked on the Drums arm).
3. The real local evaluator scores the correct continuation 1.0 and the wrong one 0.0, from the reverted state.
4. The interference canary is clean on both arms.
5. Resource parity is applied and recorded on both arms (2 vCPU, 4 GiB: the VM settings on one side, the cgroup on the other) and the measured CPU and memory of each are reported.
6. Costs are counted on both arms with preparation (supervisor, template, pool or image build) reported as external prep and included in the with-prep figures, and first setup, save and every revert inside the run clock, from the owner's price inputs (O4), and the first-rollout work is shown apart and kept in the totals.

## 8. First matched check (engineering validation)

Concurrency 1; the same local task (`e0df059f`) and the same scripted agent on both arms; alternating blocks (ABAB...) so host drift hits both; baseline reuse ON for both (post-setup snapshot via the public snapshot API); every fault, pin (versions, commits, settings) and raw row kept. It validates that the comparison path works end to end and what each side's reuse costs on one trivial task. **It supports no headline win, no throughput claim, no training claim and no benchmark claim.**

## 8b. Drums-arm driver smoke on real sessions (MEASURED as a driver run; raw data `docs/data/l1-criu/first_check_smoke/`)

**What it is.** `envd-linux/envd_linux/l1proto/first_check_smoke.py` ran the I3 driver on this host with real Drums sessions and the real public `DesktopEnv`: two ABBA blocks, concurrency 1, task `e0df059f`, a scripted agent, both arms Drums (`post_setup` reuse against no reuse). **It is a driver smoke, not a comparison**: no incumbent, one task, one host, placeholder prices (3600 USD per hour, so cost equals seconds; not a price).

**Result.** 12 rollouts, 12 valid, no fault, no interference (`summary.json`, `block*_*.json`). The driver recorded: first-rollout work for the reuse arm (3.6 s of setup plus save, inside its wall time), per-block host memory and CPU, adapter build time, and the external preparation. Stored beside it: `pins.json`, `composition_manifest_at_smoke.json` (the Drums arm's manifest at the time of the run), `preflight_probe.json` (the preflight with `--probe` on this host) and the raw stdout and stderr.

**Accounting caveat (read before any number).** The recorded external prep is only 0.45 s because the pool creates sessions lazily: the real cost of the first session creation falls INSIDE the first block's clock (and is therefore in the first-rollout work and wall time), not in `external_prep`. Prep is therefore reported on both sides of the clock, and no statement is made that it is complete. The `raw_ratio...NOT_A_CLAIM` key in the summary is a harness output on a placeholder price and means nothing.

**Telemetry caveat.** Memory is host-wide `MemTotal - MemAvailable` sampled once a second, including everything else running on the host; peak deltas can be negative (page cache and other processes). CPU is host-wide busy seconds. There is no per-environment capture; resource parity is NOT claimed.

**Preflight on this host (`preflight_probe.json`).** `go_incumbent` False (no vmx/svm flag, no `/dev/kvm`, no VirtualBox), `go_drums` True, `go_drums_restore` False (no `ip` on PATH; a private `ip` exists in the CRIU directory but the static check looks at PATH), `go_joint_run_on_this_host` False. The three Drums runtime probes (namespaces, overlay, cgroup limits) succeeded; the VirtualBox hardware probe is unsupported; the four gates are untested.

## 9. If no suitable existing host or image exists

A rental proposal is written LATER, only after the owner confirms what is already available and after access, quota and price checks have been done by the owner. It would be a bounded, priced proposal (host type, duration, shutdown, cost ceiling). **It is not approved, not priced and not requested here.**
