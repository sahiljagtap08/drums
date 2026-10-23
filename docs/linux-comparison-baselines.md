# Linux baseline candidates

Checked on 2026-10-23 (updated after a second licence pass) from the owning repos and project pages where reachable from this
sandbox. Nothing below was installed or run. "Not verified" means exactly that.

## What the repo has today (branch `exp1-sessions-per-studio`)

- Exists and has tests: `envd/` (Swift, macOS-only daemon: screenshot, action, pasteboard,
  launch/kill, windows, axtext, generation guard) and `verify-server/` (Python; form
  rows, visibility counter, leases, audit).
- Stubs: `Makefile` targets `setup-host`, `probes`, `sweep`, `reproduce` only print
  "see Task N". `README.md` says "nothing measured yet".
- Referenced by `pyproject.toml` but absent from the repo: `drumsctl`, `metrics`,
  `report`, `envd-linux`.
- Not in this checkout (the owner says the repo is correct, so read this as "missing here", not "never built elsewhere"; it may exist on another machine or branch I cannot see): `baseline-linux/`, any Gate or probe code, any task definitions
  (`.gitignore` mentions `tasks/golden/work/` only), any Studio-based Linux VM baseline or
  trace of one. I searched the tree and history of the branch; the Studio-based baseline
  is not here. The word "Studio" appears only in the README ("sessions per Studio") and the
  branch name; I read it as a Mac Studio but the repo does not say. Per the owner the
  Studio is not available now, so this comparison does not depend on it. It also means the
  Drums-side hardware is undecided (see the design doc, section 2).

## Candidates

| Candidate | Owner / repo | License (source) | Runs without a VM? | What I could confirm |
|---|---|---|---|---|
| OSGym | agiopen-org/osgym | MIT per the authors: the paper (v2/v3) says the code is released under MIT, and the owner's Hugging Face dataset card (agiopen-org/osgym) has `license: mit`. The GitHub `LICENSE` file itself still could not be read from this sandbox (the crawler returns not found for the repo). Treat as MIT-stated, file unconfirmed. | No. KVM (`/dev/kvm`) in Docker, qcow2 per replica with reflink copy-on-write on XFS, pre-warmed runner pool, 6 GB per container. | Design and numbers are from the paper only, not reproduced. Setup per the README text seen in a fork: Python 3.10, Docker, OSWorld images. Not run. |
| CUA-Lite | cua-lite/cua-lite (Berkeley RDI), created 2026-08-23 | UNRESOLVED. The repo page shows no licence, `LICENSE`, `LICENSE.md` and `main` raw paths are not found, and a third-party index lists "License file: Not found". Without a licence, default copyright applies, so reuse and redistribution are not granted. Its sibling forks under the same org (OSWorld, android_world: Apache-2.0; cua: MIT) are forks of other projects and say nothing about CUA-Lite's own code. Action: ask the maintainers (contact is on the project blog) or re-check the repo in a browser. | Its own sandboxes are VM-free Docker containers (no `/dev/kvm`); its `osworld` env is VM-in-Docker with QEMU/KVM, and `lite.osworld` is the lighter GNOME container variant. Requires Python >=3.12,<3.13 per `pyproject.toml`. | Gym-style reset/step/close, screenshots plus click/type tool calls. Young project (110 stars). Not run. Closest to Drums' "no VM" claim, but cannot be used until the licence question is answered. |
| Cua SDK and sandboxes | trycua/cua | MIT for the SDK and most of the repo (LICENSE.md and the README licensing section, read from the owning repo). Not everything: Cua Spaces and Cua Driver Bench are source-available under FSL-1.1-MIT (competing hosted service not allowed; becomes MIT after two years); third-party parts carry their own licences (for example Ultralytics AGPL-3.0 in an optional extra). Keep to the MIT parts. | Local Linux sandbox defaults to a gVisor container, QEMU VMs also supported (project docs). Python 3.12/3.13. | Cloud sandboxes are metered and are not used here. Name clash: the "CUA-Sandbox" research paper (arXiv 2609.32750) is a different project from the trycua `cua-sandbox` package. Not run. |
| BrowserGym | ServiceNow/BrowserGym | Apache-2.0 (LICENSE from the owning repo) | Yes, Playwright Chromium. | Browser only, not a desktop. Cannot run desktop tasks. |

Other incumbents with open code (for reference, not proposed as baselines): ComputerRL
(THUDM/ComputerRL, Apache-2.0, OSWorld-derived, qemu-in-docker, needs KVM), EvoCUA
(meituan/EvoCUA, Apache-2.0, a model and eval repo rather than the training sandbox
fleet), CUA-Gym (xlang-ai/CUA-Gym, Apache-2.0 code, CC-BY-4.0 data). MAI-Gym's README
says framework source is "coming soon", so it is not runnable now.

## No candidate is called the strongest

Choosing by reputation or by paper numbers would weaken the test. Proposed selection:

1. Put the same fixed three tasks and one fixed hardware class in front of each runnable
   candidate (OSGym-style KVM, CUA-Lite container, Cua SDK Linux sandbox).
2. Give each the same capped tuning effort (hours, recorded).
3. Measure valid rollouts per hour, cost per valid rollout and RAM per env at 1, 8, 16 and
   32 concurrent environments, with the same harness and scorer used for the final run.
4. The best measured candidate on cost per valid rollout is the baseline. Runners-up stay
   in the report if they were run.

If a candidate cannot be made to run, it is reported as "did not run" with the reason; it
is not silently dropped.

## Tuning the baseline must not be weakened

Applied to the KVM candidate (taken from the OSGym paper's design plus standard practice,
each to be verified on the real box):

- Reflink copy-on-write disks (XFS on NVMe or fast EBS, `cp --reflink=always` of a qcow2
  base), `noatime`, large `allocsize`.
- KVM with `-cpu host`, virtio devices, a fixed small guest RAM size, KSM or page sharing
  where it helps.
- Pre-warmed pool of booted guests recycled between tasks; fast reset by snapshot restore
  instead of reboot if it works.
- Kernel limits raised (aio, inotify, conntrack, file descriptors) as the paper describes.
- Memory-bound packing: choose guests per host by RAM, since the paper reports RAM, not CPU,
  becomes the limit at high density.
- Same screen size and the same in-guest daemon protocol as Drums.

For container candidates: same pool/reset ideas, resource limits matched to what the
tasks need, shared image layers.

## Gaps that must be closed before a real run

- A Linux guest daemon (`envd-linux`) speaking the same routes as `envd`, or adapters
  from each candidate's own API to this repo's `Env` interface. Only the `envd` HTTP
  adapter exists (`compare/drums_compare/envd_http.py`).
- A base desktop image with the browser and the task start commands.
- A decision on whether `verify-server` runs on the host or beside each side.
