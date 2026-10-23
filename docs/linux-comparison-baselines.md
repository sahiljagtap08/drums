# Linux baseline candidates

Checked on 2026-10-23 from the owning repos and project pages where reachable from this
sandbox. Nothing below was installed or run. "Not verified" means exactly that.

## What the repo has today (branch `exp1-sessions-per-studio`)

- Exists and has tests: `envd/` (Swift, macOS-only daemon: screenshot, action, pasteboard,
  launch/kill, windows, axtext, generation guard) and `verify-server/` (Python; form
  rows, visibility counter, leases, audit).
- Stubs: `Makefile` targets `setup-host`, `probes`, `sweep`, `reproduce` only print
  "see Task N". `README.md` says "nothing measured yet".
- Referenced by `pyproject.toml` but absent from the repo: `drumsctl`, `metrics`,
  `report`, `envd-linux`.
- Not in the repo at all: `baseline-linux/`, any Gate or probe code, any task definitions
  (`.gitignore` mentions `tasks/golden/work/` only), any Studio-based Linux VM baseline or
  trace of one. I searched the tree and history of the branch; the Studio-based baseline
  is not here. The word "Studio" appears only in the README ("sessions per Studio") and the
  branch name; I read it as a Mac Studio but the repo does not say. Per the owner the
  Studio is not available now, so this comparison does not depend on it. It also means the
  Drums-side hardware is undecided (see the design doc, section 2).

## Candidates

| Candidate | Owner / repo | License (source) | Runs without a VM? | What I could confirm |
|---|---|---|---|---|
| OSGym | agiopen-org/osgym (code link from the paper) | MIT, stated in the paper (arXiv 2511.11672). I could not open the owning repo or its LICENSE file from here; a fork's README also says MIT, which is not authoritative. Verify before use. | No. KVM (`/dev/kvm`) in Docker, qcow2 per replica with reflink copy-on-write on XFS, pre-warmed runner pool, per-container 6 GB limit. | Design and numbers are from the paper only (88% disk saving, 37x faster provisioning, 1420 trajectories/min at 1024 replicas, USD 0.2-0.3 per replica-day on 88-core / 768 GB machines). Not reproduced by us. Setup per README of the fork: Python 3.10, Docker, OSWorld images. |
| CUA-Lite | cua-lite/cua-lite (Berkeley RDI), created 2026-08-23 | Not verified: the repo page fetched showed no license field and `LICENSE` raw returned not found. | Yes for its own sandboxes (Docker, no `/dev/kvm`); it can also wrap OSWorld VMs. | README: `uv sync --all-extras`, Gym-style reset/step/close, screenshot observations, click/type tool calls, 30k+ tasks in containers. The project page itself notes Docker is the trade-off against a VM. Young project (110 stars). This is the closest competitor to Drums' "no VM" claim. |
| Cua SDK and sandboxes | trycua/cua | MIT (LICENSE.md fetched from the owning repo) | Local Linux sandbox defaults to a gVisor container; QEMU Linux VMs also supported (from project docs, via a search result, not the repo). Python 3.12/3.13. | Active repo (30k stars). The repo's focus is macOS VMs (Lume) and Cua Bench; its cloud sandboxes are metered and must not be used for this test. Fit for a tuned Linux baseline not confirmed. |
| BrowserGym | ServiceNow/BrowserGym | Apache-2.0 (LICENSE fetched from the owning repo) | Yes, Playwright Chromium. | Browser only, not a desktop. Cannot run desktop tasks, so it is not a baseline for this comparison. It would only suit a browser-only variant. |

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
