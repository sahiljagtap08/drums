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
