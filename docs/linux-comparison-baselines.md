# Incumbent Linux setups: what is runnable end to end

Checked 2026-10-23 from the owning repos, papers and project docs reachable from this
sandbox. Nothing below was installed or run. "Verified" means I read it in the owner's own
source; "unverified" means I could not, and says what I did read instead.

## What the repo has (this checkout, branch `claude/linux-comparison-prep`)

- Exists and tested: `envd/` (Swift, macOS-only), `verify-server/`, and the comparison
  harness under `compare/`.
- Stubs: `Makefile` targets `setup-host`, `probes`, `sweep`, `reproduce` only print
  "see Task N".
- Referenced by `pyproject.toml` but absent from this checkout: `drumsctl`, `metrics`,
  `report`, `envd-linux`. The owner says the setup may exist elsewhere; read this as
  "missing here", not "never built".
- Not in this checkout: `baseline-linux/`, Gate/probe code, task definitions, any
  Studio-based Linux baseline.

## What "runnable" requires

A candidate is runnable for this comparison only if all of these hold:

- R1 Code: obtainable, with a licence cleared for our use.
- R2 Images: every image the task set needs is obtainable and its licence known.
- R3 Hardware: a host type that satisfies its requirements (for KVM sides: `/dev/kvm`,
  see the cost doc for bare metal versus nested virtualization).
- R4 Adapter: an adapter from our `Env` interface (screenshot, click, dblclick, type, key,
  scroll, done) to its native API, with the cost of the translation inside the measurement.
- R5 Task parity: the same tasks run there and are scored by the same verifier (W1), or by
  its native evaluators (W2).
- R6 Fault visibility: its resets, retries and failures can be observed so we count them
  the same way (section 5 of the design doc).
- R7 Resource attribution: its per-environment processes can be found for the sampler
  (a container or a qemu process per env).
- R8 Pinning: versions and digests recorded.

## Assessment

None of the candidates is configured by us yet, here. They are real, published systems, and "not runnable" means only that we have not set them up: R4 (adapters) and R6/R7 (instrumentation) do not exist yet, and no host exists. The table says what else is missing.

| | OSGym-style KVM + CoW | CUA-Lite | Cua SDK Linux sandbox |
|---|---|---|---|
| R1 code | Paper links `github.com/agiopen-org/osgym`. I could not open that repo from this sandbox. I read a fork's README (`invincible-jha/osgym`, via search) for setup and API. So existence and current contents of the owning repo: unverified. | `github.com/cua-lite/cua-lite` exists (README and `pyproject.toml` read from it; created 2026-08-23, 110 stars). | `github.com/trycua/cua` exists (README, LICENSE.md read from it). Package `cua-sandbox` on PyPI (v0.3.5 listing seen; docs mention 0.4.3 and 0.7.0, so the version in use must be pinned). |
| R1 licence | MIT: the paper states it, and the owner's Hugging Face dataset card has `license: mit` (verified from the authors). The `LICENSE` file in the GitHub repo: unverified (not reachable). | UNRESOLVED. No licence shown on the repo page; `LICENSE` and `LICENSE.md` raw paths return not found; a third-party index says no licence file. Default copyright applies, so reuse is not granted. Blocked until the maintainers or a browser check settle it. | SDK and most of the repo: MIT (verified, LICENSE.md and README licensing section). Cua Spaces and Cua Driver Bench: FSL-1.1-MIT (verified in README). Third-party parts carry their own licences (e.g. Ultralytics AGPL-3.0 in an optional extra). Use only the MIT parts. |
| R2 images | OSWorld images: `Ubuntu.qcow2` 23.7 GB (11.4 GB as zip) at `xlangai/ubuntu_osworld` on Hugging Face (verified file sizes), run via `happysixd/osworld-docker`. Image licence: unverified (no licence field in what I read). OSGym's task JSONs are in `agiopen-org/osgym` on Hugging Face (MIT card). | Its `osworld` env builds a `cua-lite/osworld` image `FROM happysixd/osworld-docker` and provisions the VM disk (README for the env, verified); `lite.osworld` is a lighter GNOME container variant whose image I did not inspect. Same OSWorld image licence question. | Built-in Linux images are `ghcr.io/trycua/linux:24.04` (container rootfs, gVisor if `runsc` is installed, else runc) and `24.04-disk` (QEMU VM), per the SDK docs (verified). A Fleet artifact `public.ecr.aws/k5j5w0x5/cua-ubuntu-24.04` also appears in docs. Image content licence: unverified. |
| R3 hardware | `/dev/kvm`; reflink-capable XFS on fast NVMe for the CoW disks; raised kernel limits (aio, inotify, conntrack, file descriptors); RAM sized at about 6 GB per replica (paper). Paper's rig: 88-core, 768 GB. | `lite.osworld`: Docker only, no `/dev/kvm`. Its OSWorld VM env: KVM. Python >=3.12,<3.13. GPUs only for model serving and training, which our Phase A does not use. | Container path: Docker or Podman (+ gVisor optionally). VM path: `qemu-system-x86_64`, KVM if present, otherwise slow TCG. Python 3.11-3.13 depending on the doc page. Cloud path: Cua Cloud ended on 2026-10-18 per the docs; do not use any hosted path. |
| R4 adapter | Its API (read from the fork README): `POST /reset` with task config, `POST /step` with an action string, `POST /shutdown`, `GET /screenshot`. Our actions must be translated into its action strings. Not written. | `lite.gym` reset/step/close with a desktop action space (click, type...) and screenshot observations. Closest to our vocabulary. Adapter not written. | `sb.screenshot()`, shell and input interfaces via `cua-spacesd` in the image. Adapter not written. |
| R5 task parity | W1 needs a browser in the image and a route from the guest to `verify-server` (QEMU user networking exposes the host at a fixed gateway address; to be tested). W2 uses OSWorld evaluators. | Same two questions; the container variant has a different desktop. | Image has Chromium; same network question for W1. W2 not natively supported (Cua Bench is their own evaluation). |
| R6 fault visibility | The paper says steps are retried (default 10) and failed runners reassigned; those paths need logging hooks or wrapper counting. Its health checks and runner pool recycle hide resets. | Not examined. | Sandbox lifecycle errors surface as exceptions; internal retries not examined. |
| R7 attribution | One container wraps one qemu process per replica; the sampler needs the container's pids. | Container or VM per env. | gVisor puts the workload under a `runsc` sandbox process; per-env RSS needs a decision (the gVisor sandbox's own cgroup). |
| Still missing before first run | host, quota, images, adapter, instrumentation, W1 task start inside each image, pinned versions, owning-repo licence check | licence answer, host, images, adapter, instrumentation, pinned versions | host, images, local runtime installed (`cua runtime doctor` reports what a host can run), adapter, instrumentation, pinned version |

Everything about incumbents' performance is from their papers and docs, not from runs here.

## No incumbent is called the strongest

Selection is by measurement, not reputation: run each runnable candidate on the same host
class, with the same fixed workload and the same capped tuning effort (hours, recorded),
over the same concurrency sweep, using the harness and scorer of the final run. The best on
cost per valid rollout is the headline incumbent; the others stay in the report. If a
candidate cannot be made to run it is reported as "did not run" with the reason, never
dropped silently.

## Tuning an incumbent must not be weakened

KVM + CoW candidate (from the OSGym paper's design, to be verified on the real box):
reflink CoW qcow2 copies on XFS with `noatime` and a large `allocsize`; `-cpu host`, virtio
devices, small fixed guest RAM, page-sharing where it helps; pre-warmed runner pool recycled
between tasks, snapshot-restore reset if it works; raised kernel limits; memory-bound
packing (the paper finds RAM, not CPU, limits density at scale); the same screen size.

Container candidates: pre-warmed pool, image layers shared, per-container limits matched to
what the tasks need, runc versus gVisor tested and recorded.

## Matched workload and agent (what every side runs)

- W1 task set from `tasks/linux_comparison/tasks.json` (three enabled browser-in-desktop
  tasks; the specs are unvalidated on any real environment); W2 (OSWorld subset with native
  evaluators) when the controller shim exists.
- Same seed and task order; same group size G per task in Phase B.
- One agent: Phase A scripted and keyboard-driven; Phase B one open VLM from a single
  inference endpoint, same prompt, sampling and step budget on every side. Model choice is
  open.
- Observation 1280x800 PNG; actions in the envd vocabulary through each side's adapter.
- Verification by `verify-server` (W1) or OSWorld evaluators (W2).

## Other incumbents with open code (reference only)

- ComputerRL (`THUDM/ComputerRL`, Apache-2.0 verified): OSWorld-derived, qemu-in-docker
  Ubuntu VMs, KVM recommended; it is the environment layer behind a 9B model at 48.9% OSWorld.
  Could be a fourth baseline if its environment layer can be run without its trainer.
- EvoCUA (`meituan/EvoCUA`, Apache-2.0 verified): model and evaluation code, not the
  sandbox fleet.
- CUA-Gym (`xlang-ai/CUA-Gym`, Apache-2.0 code, CC-BY-4.0 data): task synthesis pipeline.
- MAI-Gym (`Tongyi-MAI/MAI-Gym`): README says framework source is "coming soon"; not runnable.
- CUA-Sandbox (arXiv 2609.32750): research paper; I found no released code. Not the same as
  the trycua `cua-sandbox` package.
- BrowserGym (`ServiceNow/BrowserGym`, Apache-2.0 verified): browser only; cannot run
  desktop tasks.
