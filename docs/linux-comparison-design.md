# Drums-on-Linux vs tuned Linux incumbents: comparison design

**Epistemic status: DESIGN with BUILT local code (the harness and scorer, fake-tested). No real comparison has been run.** The runner for the OSWorld tasks is specified in `matched-comparison-protocol.md`.

Status: design and local code only. Nothing here has been run on a real environment, no
cloud resource exists, and no spend is approved. This replaces the earlier macOS-based
plan, which is withdrawn (the Mac, its cost plan and the questions about it are dropped).
Anything written as a number below is a parameter to fix before the run, not a result.

## 1. Question

On the same Linux host, with the same agent doing the same tasks, how many valid
rollouts per hour, at what cost per valid rollout and at what RAM and CPU per environment,
does each of these deliver, with every failure, reset and cross-attempt interference counted?

- D: Drums-Linux, the Drums shared-runtime architecture on Linux: many attempts share one
  running OS and the unchanging parts of applications, each with private copy-on-write
  state, driven by `envd-linux`. See `docs/drums-linux-architecture.md`. Not a
  container-per-attempt setup.
- I1: OSGym-style KVM with copy-on-write disks.
- I2: CUA-Lite (container-per-attempt desktops; its OSWorld env is VM-in-Docker).
- I3: Cua SDK local Linux sandbox (container or QEMU VM, depending on image kind and version).

Which incumbents actually run is assessed in `docs/linux-comparison-baselines.md`; any that
cannot be run are reported as "did not run" with the reason.

The end goal the owner stated is a better score at lower TOTAL training cost
(environments + inference + training + failed runs). The first runs here measure the
environment-side parts of that and the failed-run share. They cannot show a score
improvement, and nothing here promises one (section 10).

The test can lose. Section 7 states the rule that says "Drums does not win" and fixes it
before any data exists.

## 2. What is held equal

| Item | Rule |
|---|---|
| Host | The same instance type and the same disk configuration for every side, one side on the host at a time (no co-running), run in alternating order (for example D, I1, I2, I3, I3, I2, I1, D) so drift or noisy neighbours do not favour one side. Host kernel, sysctls and filesystem are recorded in `run.json`. Equal hardware makes equal hourly cost by construction. |
| Tasks | One task file hashed into `run.json`. Same seed gives the same task order on every side (workload W1 below). |
| Agent | Same agent id, model, prompt, sampling settings, step budget and timeout. The scorer refuses a verdict if the agent ids differ. |
| Observations | PNG screenshots at one fixed size (target 1280x800) on every side. |
| Actions | The envd vocabulary: click, dblclick, type, key, scroll, plus `done`. Each side gets a thin adapter that translates it into the side's native input (envd on D; the guest server API on I1/I2/I3). The adapter's cost is part of the side's measured cost. |
| Verification | One verifier outside the agent and the environment under test (`verify-server`), reachable from every side. |
| Image content | Same applications and versions where possible (section 3). Where a side must use different bits, application list and image digests are recorded and the success-rate parity check applies. |
| Concurrency | The same set of concurrency levels on every side (1, 8, 16, 32, then the largest level the host sustains), so results can be read against published concurrency figures (section 10). |
| Time | Boot, image pull, warm-up, pool pre-creation and idle time are billed. Steady-state rate is reported next to end-to-end rate, never instead of it. |

## 3. Workloads and agent

W1 (first run, all sides): browser-in-desktop tasks, verified by `verify-server`
(`tasks/linux_comparison/tasks.json`). They exercise window management, focus, keyboard,
mouse, screenshots and background rendering, not office applications, so they are weak as
"real desktop tasks". They are the only workload verifiable identically on every side
without extra work.

W2 (needed to read against published results): a subset of OSWorld tasks, with OSWorld's
own evaluators, in the OSWorld Ubuntu image. Every incumbent above is built around
OSWorld. For D this needs an OSWorld-controller shim over `envd-linux` (the evaluators
read files, browser state and accessibility data through the environment's controller),
and a file-read route that neither guest daemon has. W2 is not in Phase A. Decision (revised, see `docs/training-comparison-plan.md` section 3): the "shim" is OSWorld's own unmodified guest server (Apache-2.0) running inside each Drums session on the OSWorld root filesystem, not a reimplementation; the per-session network namespace and full-root overlay it forces are required for W2 and for Phase C.

Phases:

- Phase A: infrastructure only, scripted keyboard-driven agent, no model calls. Isolates
  the environment layer. W1 only.
- Phase B: one real agent on all sides, small N, W1 then W2 when available. The same
  model on every side, served from one inference endpoint. The model is an open decision
  (candidates in the incumbent papers include EvoCUA's open models, licence of the weights
  unverified). Group rollouts (G attempts per task, as in GRPO-style sampling) so the
  sampling pattern matches training. Inference cost is measured and entered as
  `cost_basis.other_usd.inference`.
- Phase C (not planned here): a real training run on each environment backend. This is
  the only phase that can say anything about a score at a given total cost, because only
  there can environment choice change the data (volume, faults, staleness) a policy trains
  on. It needs a trainer, GPUs and a budget, none of which exist in this plan.

## 4. What a "total cost" comparison needs

Total cost = environments + inference + training + failed runs.

- Environments: host cost per hour times billed hours (measured by the harness).
- Failed runs: environment cost share of attempts that were not valid
  (`failed_run_cost_usd`), plus inference spent on them (Phase B).
- Inference: operator-supplied, from the endpoint's own accounting.
- Training: for a fixed number of trajectories the training cost is the same on every
  backend, so for Phases A and B it is a shared constant and is reported but not compared.
  It becomes backend-dependent only in Phase C, through faults and staleness.

`total_cost_all_per_valid_usd` in the scorer is this sum per valid rollout. The verdict in
section 7 still uses environment cost only until Phase B data exists and the extended rule
is fixed in advance.

## 5. Valid rollout

An attempt is valid only if all hold (implemented in `compare/drums_compare/harness.py`):

1. The reset succeeded and the verifier then saw a clean slot.
2. No infrastructure fault: reset, screenshot or action error; black frame; stale
   generation; three identical frames in a row after actions (frozen display).
3. It ended by agent `done`, step budget or task timeout. `infra_error` is not a valid end.
4. The verifier ran.
5. No state from another attempt appeared in the slot while it held it.
6. No later audit found a stale write that landed in it or came from it.

The agent failing the task does not make an attempt invalid. If the same agent has a
different success rate on two sides beyond its confidence interval, the scorer refuses a
verdict: the environments are not equivalent and that has to be fixed first.

Retries: an incumbent that retries a failed step internally (OSGym's default is 10 step
retries) must have its retries logged as faults by the adapter, so that retried steps are
counted rather than hidden. Where an incumbent offers no hook to see a retry, that is
recorded as a limit of the measurement.

## 6. How failures, resets and interference are counted

- Every attempt is recorded, valid or not, with all its reasons (`attempts.jsonl`).
  Invalid attempts stay in the denominator for cost and time. Nothing is dropped.
- Reset time is part of the attempt's slot time. It is also reported alone (mean, p50, p95).
- A failed reset is an attempt with `terminated_by = infra_error`.
- Interference has three detectors:
  - Per attempt: state under another generation that appeared after the attempt took the slot.
  - Per run: after the run, every verifier row is placed on its slot's timeline; a row whose
    generation is not the one holding the slot at that moment is a stale write. Writer and
    victim attempts are both marked invalid; the pair is saved in `audit.json`.
  - Per side, outside the attempts: the isolation probes in `docs/drums-linux-architecture.md`
    section 6 (files, cookies, clipboard, input, kill-all, ports, residue after reset), run
    on D, and the equivalent checks on each incumbent where its isolation model allows.
- Limits: the run-level audit assumes the verifier clock and the harness clock agree to well
  under a reset time (one host, which the design requires). The detectors see only state
  that reaches `verify-server` or the probes; other leakage routes are not detected.

## 7. Metrics and the verdict rule

Primary:

- Valid rollouts per hour, end to end (billed time including boot) and steady state.
- Cost per valid rollout = total cost / valid rollouts.

RAM per environment is measured two ways, and both are reported:

- Marginal host memory per additional environment: the slope of host used memory
  (MemTotal minus MemAvailable) against concurrency over the sweep (1, 8, 16, 32, ...).
  This is the fair cross-side figure, because it includes everything an environment makes
  the host hold (a guest kernel and page cache on KVM sides, shared pages once on D).
- Per-environment RSS and PSS from `/proc` over the environment's processes (for a KVM guest,
  its qemu process; for D, the session's cgroup pids). PSS divides shared pages fairly,
  RSS double-counts them, cgroup `memory.current` charges shared page cache to whichever
  session touched it first, so RSS and cgroup numbers are not used as the headline figure.
- D's own daemon and supervisor memory is added to D's per-environment figure as overhead.

CPU per environment: cores used per env from process CPU time, plus host CPU busy fraction.

Secondary: reset time, invalid reasons, interference attempts, success rate.

Raw results saved per side: `run.json`, `attempts.jsonl`, `audit.json`, `resources.jsonl`.
The scorer only reads these files. Confidence intervals: 2000 bootstrap resamples of
attempts, 95%.

Verdict (fixed before any real run; `scorer.verdict`), D against each incumbent in turn:

- No verdict if either run is fake, has fewer than 50 valid rollouts, has missing hardware
  or cost inputs, used a different task file, seed or agent, or if success rates differ
  beyond their intervals.
- `baseline_wins` if the incumbent's cost-per-valid interval lies entirely below D's.
- `drums_wins` only if D's cost-per-valid interval lies entirely below the incumbent's,
  valid-per-hour is not significantly worse, and D's interference rate is not more than 2
  points higher.
- `mixed` if D wins cost but loses on rate or interference. `inconclusive` otherwise.

D's result is only comparable at its stated level (`drums_level`, L0 or L1). L0 is a precursor, not a Drums result: the Drums claim lives at L1 (shared pre-warmed runtime forks), so the scorer never reports `drums_wins` for an L0 run (`precursor_only` instead). The 50-valid
minimum and the 2-point margin are placeholders for the owner to confirm before the run.

## 8. Threats to validity

- Image parity: bits differ between I1/I2/I3 and D unless the OSWorld root filesystem is
  reused; the parity check catches only large behavioural gaps.
- Window manager and desktop shell differ across sides, so the same agent sees different
  pixels.
- A tuned baseline can always be tuned further. The tuning budget (hours) is fixed per
  candidate and recorded.
- Incumbent papers measured different hardware, tasks, and concurrency; section 10 says
  which of their claims our runs can be read against and which they cannot.
- First-run N is small; the intervals will be wide and "inconclusive" is a likely outcome.
- Nested-virtualization KVM on non-metal hosts would handicap I1; the primary run uses
  bare metal (see the cost doc).

## 9. What is not decided

The host instance type and quota; which incumbents turn out to be runnable; the Phase B
model; whether W2 (OSWorld controller shim and file route) is built before the first
comparison; D's level for the first run; the WM choice; v0 or v1 networking.

## 10. Reading our results against incumbent setups

Goal: let a reader place our numbers next to what others published, now with both sides
on Linux. This is not a promise of a benchmark-score win, and our first runs are not comparable to any score below: we use
simple browser-in-desktop tasks, one host, and (Phase A) no model. Only the
infrastructure-side claims can be read against ours, and only if hardware, task and
concurrency are stated beside them. Fake runs are engineering validation and never appear
in any such table.

None of these papers publishes a dollar figure for total training cost except OSGym (per
replica-day). For the others a reader can only compare what was published (GPU count,
hours, throughput, reset time, memory). Any total-cost comparison therefore needs our own
costing of an incumbent's published hardware, labelled as an estimate.

| Incumbent (source) | What it published | Our metric that maps onto it | What does not map |
|---|---|---|---|
| OSGym (arXiv 2511.11672, v3 Mar 2026) | 1000+ OS replicas; 1420 multi-turn trajectories/min at 1024 replicas (about 15 turns each); USD 0.2-0.3 per replica per day, 0.23 on an 88-core / 768 GB server; 88% less disk and 37x faster disk provisioning from reflink copy-on-write; each container limited to 6 GB; step retry default 10 | `cost_per_valid_usd` and `valid_per_hour` (convert: replica-day cost to cost per valid rollout needs their trajectories/hour per replica, which the paper gives only in aggregate); RSS/PSS per env against the 6 GB limit; reset time | Their trajectory counts are not filtered by our validity rule; their retries hide faults that we count. Disk saving: we do not measure it in Phase A. Scale (1000+) is far beyond our first run. |
| ComputerRL (arXiv 2508.14040, ICLR 2026) | Thousands of parallel desktops (qemu-in-docker Ubuntu VMs); 7-node environment cluster, 200 envs per environment server, each VM 2 cores / 4 GB; about 80 rollouts per GPU; training needs at least 4 GPU nodes; AutoGLM-OS 9B 48.9% on OSWorld | RAM and CPU per env (compare 2 cores / 4 GB); envs per host; cost per valid rollout once we cost their hardware ourselves | Their OSWorld score depends on model and RL recipe, not on environment infrastructure. We report no OSWorld score. |
| EvoCUA and EvoCUA-1.5 (arXiv 2601.15876, 2607.09773) | QEMU-KVM in Docker; claims 100,000+ concurrent sandboxes and tens of thousands bootstrapped in under a minute; EvoCUA-32B 56.7% OSWorld, EvoCUA-1.5 63.2% OSWorld-Verified; asynchronous RL with staleness-controlled buffer. A reviewer-style summary notes the concurrent sandbox count, rollout volume and GPU hours for 1.5 are not disclosed. | Only the idea that environment interaction is the bottleneck maps: our reset time, valid fraction and valid per hour. | Their fleet size and boot-burst claim cannot be tested at our scale. No hardware or cost disclosed to compare cost per rollout. |
| MAI-Gym (arXiv 2610.20939, Oct 2026) | Up to 20,000 parallel environments; 1,000 desktop instances GUI-ready in 88.9 s; desktop reset p50 37.2 s / p95 100.1 s; 1.9% unscored aborts on desktop; RL run on 512 GPUs, peak 20,000 environments, about 96 h; MAI-Gym-9B 76.2% OSWorld-Verified. Framework source is "coming soon" per its repo, so it cannot be run yet. | `reset_s_p50` / `reset_s_p95`, invalid fraction against their unscored-abort rate, interference attempts (they report concurrent-attempt interference as the main desktop failure) | Their aborts exclude failures they chose to retry; ours count every fault. Their training cost is 512 GPUs for about 96 h, but no price is given. |
| CUA-Sandbox (arXiv 2609.32750; unrelated to the `cua-sandbox` PyPI package from trycua, same name) | Shares initialised application runtimes across envs with private state; OSWorld at 8 concurrent envs: memory per env 2653.8 to 837.3 MiB, throughput 0.291 to 0.895 env/s, incremental storage 62.7 to 29.1 MiB, up to 6.20x throughput (VisualWebArena) and 3.08x (OSWorld) at 8 envs, 5.14x OSWorld at 16; host with 8 GPUs, 192 cores, 1.5 TiB; they state maximum density is unmeasured | RAM per env, valid per hour, reset time, all at concurrency 8 and 16 (the same two levels); this is the closest published method to Drums' shared-session idea and the most direct comparison | Their throughput unit is envs/s of a measured batch, not valid rollouts per hour with failures counted. Our hardware is much smaller. |
| CUA-Lite (cua-lite.github.io, Berkeley RDI blog Sep 2026) | VM-free Docker sandboxes with no `/dev/kvm`; 30k+ verifiable tasks; 15+ benchmarks; Lite.OSWorld runs OSWorld tasks and evaluators unchanged. No environment count, hardware, throughput or cost figures found in the blog or README. | Nothing published to read against; it is a runnable baseline candidate (licence unresolved, see baselines doc), so we would produce the numbers ourselves. | The blog's own caveat: Docker isolates less than a VM. |

Which of our configurations to run so a published figure has a counterpart:

- Concurrency 8 and 16 on every side: CUA-Sandbox's two reported levels (memory per env,
  throughput, reset).
- Per-env limits of 2 cores / 4 GB on the KVM side and the density at which one host
  holds about 200 of them: ComputerRL's configuration (200 envs per environment server).
- 6 GB per env cap and a 128-runner pre-warmed pool: OSGym's configuration.
- A 1,000-environment bring-up test is out of reach on one host; bring-up time is
  reported only for the largest N the host sustains, never extrapolated to 1,000.
- Reset time as median and p95 over all attempts: MAI-Gym's reporting form.

Mapping from our metrics to their claims, in one line each:

- `valid_per_hour` and `cost_per_valid_usd`: OSGym throughput and cost claims; CUA-Sandbox throughput.
- RSS/PSS and CPU per env: OSGym 6 GB limit, ComputerRL 2 cores / 4 GB, CUA-Sandbox memory per env.
- `reset_s_p50` and `reset_s_p95`: MAI-Gym reset medians, CUA-Sandbox reset operation speedups.
- invalid reasons and interference attempts: MAI-Gym unscored aborts and concurrent-attempt interference; OSGym retries; ComputerRL stability complaints about vanilla OSWorld.
- `failed_run_cost_usd`: no incumbent publishes it. This is where a "counted failures" comparison can differ from theirs.

Where a reader would want a benchmark score (OSWorld-Verified and similar) we do not
claim one. A score claim would need a trained model on a recognised benchmark, which is
outside this plan.



## Claim discipline

L0 results (shared files, private state, per-session processes) are reported as `precursor_only`, never as a Drums win; only L1 (shared pre-warmed runtime forks) can support the Drums claim. Fakes are engineering validation, not results. OSWorld-stub compatibility is not OSWorld compatibility. Teardown guarantees are only what the local 4 s-cap test exercises; nothing about AWS is guaranteed-capped yet.
