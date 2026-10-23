# Drums vs tuned Linux: comparison design

Status: design and code only. Nothing here has been run on a real environment.
Anything written as a number below is a parameter to fix before the run, not a result.

## 1. Question

At the same dollar budget, with the same agent doing the same desktop tasks, how many
valid rollouts per hour and what cost per valid rollout does each side deliver?

- Side D: Drums on macOS (sessions per Mac, no VMs, `envd` per session).
- Side L: a tuned Linux desktop baseline on cloud hardware (see
  `docs/linux-comparison-baselines.md`).

The test can lose. Section 7 states the rule that says "Drums does not win" and fixes it
before any data exists.

## 2. What is held equal

| Item | Rule |
|---|---|
| Tasks | One task file (`tasks/linux_comparison/tasks.json`), hashed into `run.json`. Same seed gives the same task order on both sides. |
| Agent | Same agent id, model, prompt, sampling settings, step budget and timeout. The scorer refuses a verdict if the agent ids differ. |
| Observations | PNG screenshots at one fixed size (target 1280x800 logical, 1 px per point; macOS Retina scaling must be turned off or normalised so the agent sees the same pixels). |
| Actions | The `envd` action vocabulary: click, dblclick, type, key, scroll, plus `done`. The Linux guest daemon must implement the same routes. |
| Verification | One verifier, outside the agent and outside the environment under test (`verify-server`). Same checks for both sides. |
| Budget | Equal dollars, not equal machines. Each side gets the same dollar cap for the measured run, and the headline metrics are per dollar. Hourly price per side is declared in `run.json` (`cost_basis`). For a Mac the owner states amortised or rented cost and how it was computed. Per-core and per-GiB figures are reported next to it so a reader can renormalise. |
| Time | Boot, image pull, warm-up and idle time are billed. The steady-state rate is reported next to the end-to-end rate, never instead of it. |

Open decision (needs the owner): what Mac, and what hourly cost to declare for it. The repo's "Studio" is not available now, so the Drums side has no confirmed hardware yet; the Linux side can be prepared and bake-off-tested without it, but the comparison itself needs both. If it
is an owned machine, the amortisation rule must be written down before the run.

## 3. Two phases

- Phase A, infrastructure only: a scripted agent, no model calls. This removes model
  latency and noise, so differences come from the environment layer. Scripts must be
  keyboard-driven (address bar, tab order) so they do not depend on pixel layout that
  differs across operating systems. Cost: no model spend.
- Phase B, real agent: same model on both sides, small N. It shows whether the Phase A
  rates survive realistic agent latency. Model spend is outside this document's cost
  estimate and must be approved separately.

Phase A comes first. If Phase A alone decides the question, Phase B is a confirmation.

## 4. Tasks

The enabled tasks drive a real browser inside the real desktop and are verified by
`verify-server`. They are weak as "desktop" tasks: they exercise window management,
focus, keyboard and mouse, screenshots and background rendering, but not office or editor
apps. The two office-style tasks in `tasks.json` are disabled until both guest daemons
have a file-read route (neither has one). The task specs themselves are unvalidated on a
real environment. Before the first measured run, each task needs a human-checked dry run
on both sides.

## 5. Valid rollout

An attempt is valid only if all hold (implemented in `compare/drums_compare/harness.py`):

1. The reset succeeded and the verifier then saw a clean slot.
2. No infrastructure fault: reset, screenshot or action error; black frame; stale
   generation; three identical frames in a row after actions (frozen display).
3. It ended by agent `done`, step budget or task timeout. `infra_error` is not a valid end.
4. The verifier ran.
5. No state from another attempt appeared in the slot while it held it.
6. No later audit found a stale write that landed in it or came from it.

The agent failing the task does not make an attempt invalid. Success rate is reported
separately. If the same agent has a different success rate on the two sides beyond its
confidence interval, the scorer refuses a verdict: the environments are not equivalent and
that has to be fixed first.

## 6. How failures, resets and interference are counted

- Every attempt is recorded, valid or not, with all its reasons (`attempts.jsonl`).
  Invalid attempts stay in the denominator for cost and time. Nothing is dropped.
- Reset time is part of the attempt's slot time. It is also reported alone (`reset_s_mean`).
- A failed reset is an attempt with `terminated_by = infra_error`.
- Interference has two detectors:
  - Per attempt: state under another generation that appeared after the attempt took
    the slot (`foreign_rows`).
  - Per run: after the run, every verifier row is placed on its slot's timeline of
    attempts. A row whose generation is not the one holding the slot at that moment is a
    stale write. Both the writer's attempt and the victim's attempt are marked invalid,
    and the pair is saved in `audit.json`.
- Limit: the run-level audit assumes the verifier clock and the harness clock agree to
  well under a reset time, which holds when both run on one host. Cross-host runs need a
  clock check first.
- Limit: the detectors only see state that reaches `verify-server`. Leaks through the
  clipboard, files or window state are not detected by the enabled tasks.

## 7. Metrics and the verdict rule

Primary:

- Valid rollouts per hour, end to end (billed time including boot) and steady state.
- Cost per valid rollout = total cost / valid rollouts.

Total-cost view (the owner's end goal): total cost = environments + inference + training +
failed runs. The harness measures environment cost and the cost share of invalid attempts
(`failed_run_cost_usd`). Inference and training cost are supplied by the operator in
`run.json` under `cost_basis.other_usd` and reported as `total_cost_all_per_valid_usd`. Phase A
has no inference or training cost. The verdict below still uses environment cost only;
extending it to total cost needs Phase B data and a rule fixed beforehand.

Secondary: RSS (and PSS where readable) per environment, CPU cores per environment,
host memory used, reset time, invalid reasons, interference attempts, success rate.

Per-env resources come from `/proc` on Linux (process tree of the guest, so a KVM guest is
its qemu process and the numbers include QEMU overhead) and from `ps` on macOS. These are
host-side numbers: memory the guest kernel holds is counted for L and not separable for D.
That asymmetry is stated in the report, not hidden.

Raw results saved per side: `run.json`, `attempts.jsonl`, `audit.json`,
`resources.jsonl`. The scorer only reads these files.

Confidence intervals: 2000 bootstrap resamples of attempts, 95%.

Verdict (fixed before any real run; `scorer.verdict`):

- No verdict if either run is fake, has fewer than 50 valid rollouts, has missing
  hardware or cost inputs, used a different task file, seed or agent, or if success
  rates differ beyond their intervals.
- `baseline_wins` if the Linux cost-per-valid interval lies entirely below Drums'.
- `drums_wins` only if the Drums cost-per-valid interval lies entirely below Linux',
  valid-per-hour is not significantly worse, and Drums' interference rate is not more
  than 2 points higher.
- `mixed` if Drums wins cost but loses on rate or interference. `inconclusive` otherwise.

The 50-valid minimum and the 2-point margin are placeholders; the owner should confirm or
change them before the run, and they must not change afterwards.

## 8. Threats to validity

- macOS and Linux render the same page differently (fonts, scale, window chrome). The same
  agent may behave differently for reasons unrelated to infrastructure. The success-rate
  check catches large gaps only.
- A tuned baseline can always be tuned further. The tuning budget (hours) is fixed per
  candidate and written into `run.json` notes so it is the same effort on each.
- Cloud price and Mac cost are not the same kind of number (rented vs owned).
- First-run N is small. The intervals will be wide, and "inconclusive" is a likely and
  acceptable first outcome.
- Phase A scripts that depend on pixel positions would break across OSes. Keep them
  keyboard-driven.

## 9. What is not decided

Mac model and its declared hourly cost; baseline winner (chosen by a measured bake-off, see
baselines doc); Phase B model and spend; whether disabled office tasks get a file route
first; guest daemon for Linux (`envd-linux` does not exist yet).

## 10. Reading our results against incumbent setups

Goal: let a reader place our numbers next to what others published. This is not a promise
of a benchmark-score win, and our first runs are not comparable to any score below: we use
simple browser-in-desktop tasks, a small host, and (Phase A) no model. Only the
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
| MAI-Gym (arXiv 2610.20939, Oct 2026) | Up to 20,000 parallel environments; 1,000 desktop instances GUI-ready in 88.9 s; desktop reset p50 37.2 s / p95 100.1 s; 1.9% unscored aborts on desktop; RL run on 512 GPUs, peak 20,000 environments, about 96 h; MAI-Gym-9B 76.2% OSWorld-Verified. Framework source is "coming soon" per its repo, so it cannot be run yet. | `reset_s` (we should report median and p95, add p95 to the scorer), invalid fraction against their unscored-abort rate, interference attempts (they report concurrent-attempt interference as the main desktop failure) | Their aborts exclude failures they chose to retry; ours count every fault. Their training cost is 512 GPUs for about 96 h, but no price is given. |
| CUA-Sandbox (arXiv 2609.32750; unrelated to the `cua-sandbox` PyPI package from trycua, same name) | Shares initialised application runtimes across envs with private state; OSWorld at 8 concurrent envs: memory per env 2653.8 to 837.3 MiB, throughput 0.291 to 0.895 env/s, incremental storage 62.7 to 29.1 MiB, up to 6.20x throughput (VisualWebArena) and 3.08x (OSWorld) at 8 envs, 5.14x OSWorld at 16; host with 8 GPUs, 192 cores, 1.5 TiB; they state maximum density is unmeasured | RAM per env, valid per hour, reset time, all at concurrency 8 and 16 (the same two levels); this is the closest published method to Drums' shared-session idea and the most direct comparison | Their throughput unit is envs/s of a measured batch, not valid rollouts per hour with failures counted. Our hardware is much smaller. |
| CUA-Lite (cua-lite.github.io, Berkeley RDI blog Sep 2026) | VM-free Docker sandboxes with no `/dev/kvm`; 30k+ verifiable tasks; 15+ benchmarks; Lite.OSWorld runs OSWorld tasks and evaluators unchanged. No environment count, hardware, throughput or cost figures found in the blog or README. | Nothing published to read against; it is a runnable baseline candidate (licence unresolved, see baselines doc), so we would produce the numbers ourselves. | The blog's own caveat: Docker isolates less than a VM. |

Mapping from our metrics to their claims, in one line each:

- `valid_per_hour` and `cost_per_valid_usd`: OSGym throughput and cost claims; CUA-Sandbox throughput.
- RSS/PSS and CPU per env: OSGym 6 GB limit, ComputerRL 2 cores / 4 GB, CUA-Sandbox memory per env.
- `reset_s_mean` (add p50/p95): MAI-Gym reset medians, CUA-Sandbox reset operation speedups.
- invalid reasons and interference attempts: MAI-Gym unscored aborts and concurrent-attempt interference; OSGym retries; ComputerRL stability complaints about vanilla OSWorld.
- `failed_run_cost_usd`: no incumbent publishes it. This is where a "counted failures" comparison can differ from theirs.

Where a reader would want a benchmark score (OSWorld-Verified and similar) we do not
claim one. A score claim would need a trained model on a recognised benchmark, which is
outside this plan.

