# Phase C: matched short RL run, Drums-Linux vs a tuned incumbent backend

Status: plan only. Phase C is unscheduled, nothing is launched, no cloud resource exists,
no price in this document was fetched, and no spend is approved. Everything below was read
from the owning sources on 2026-10-23 unless it says "derived" (my arithmetic from those
sources) or "unverified".

Labels that apply to everything in this plan:

- The run is a SCALED SUBSET of ScaleCUA's published recipe. It is never an "exact
  reproduction", and its absolute success rates are never compared with published OSWorld
  numbers.
- The Drums claim lives at level L1 (shared pre-warmed runtime forks). A run on L0 (shared
  files, private state, per-session processes) is a precursor, not a Drums result.
- Fakes and spikes are engineering validation, not results.

## 1. Which incumbent recipe: resolved

| Candidate | What is public (verified) | Reproducible as a training recipe? |
|---|---|---|
| ComputerRL (THUDM/ComputerRL, Apache-2.0) | The repo tree is an OSWorld-derived evaluation harness: `desktop_env`, `mm_agents`, `evaluation_examples`, `run_autoglm*.py`, `run_multienv_autoglm*.py`, `show_result.py`. README covers only deploying the released models and running evaluation. Weights on ModelScope (text-only and multimodal). Paper appendix gives the hardware and hyperparameters. | No. The trainer, the behaviour-cloning data (about 8,000 tasks, 180,000 steps), the RL task subset and the Entropulse stage are not in the repo. Its trainer is AgentRL (THUDM/AgentRL, MIT, public, but it ships examples for AgentBench-style tasks, not ComputerRL's data). Paper hardware: 128 H800 (16 nodes x 8) for the SFT stage; RL needs at least 4 GPU nodes, with a 7-node environment cluster (120-core, 1.1 TiB each, 200 VMs per server). |
| EvoCUA and EvoCUA-1.5 (meituan/EvoCUA, Apache-2.0) | README as fetched covers installing, downloading the 8B/32B weights and vLLM serving; updates stop at 2026-03-31, before EvoCUA-1.5. The EvoCUA-1.5 paper describes an asynchronous RL stack but gives no code or weights link, and does not state sandbox count, rollout volume or GPUs. | No. I could not list the repo tree, so "no trainer" rests on the README and the paper, not on a file listing. Treat as not reproducible until a trainer is shown. |
| CUA-Gym (xlang-ai/CUA-Gym) | Task-synthesis pipeline, 32,112 verified tuples, 110 environments, mock web apps (CUA-Gym-Hub). Models "coming soon". Recipe: GSPO on Qwen3.5-35B-A3B and 397B-A17B. | No as a recipe (no trainer, models unreleased, MoE backbones far beyond a short run). Useful later as a task source. Licence wording is inconsistent (GitHub says "Other"; the README says Apache-2.0 code and CC-BY-4.0 data). |
| MAI-Gym | Weights released; framework source "coming soon" per its README. Paper: 512 GPUs, about 96 h, up to 20,000 environments. | No. |
| ScaleCUA (THUDM/SCALE-CUA, arXiv 2607.11185) | Trainer and controller (`scalecua_rl`), environment worker (`osworld_env`), evaluation code, shipped training configs (Qwen3-VL, Qwen3.5, GLM-4.6V), SFT start-point checkpoints and RL checkpoints, 3,212 RL tasks with judge functions, REPRODUCE.md. | Yes, in part: see sections 2 to 5. It is the leading candidate. |

Leading recipe: ScaleCUA, with the trainer unchanged and only the environment backend swapped:
their QEMU-in-Docker/KVM backend (tuned, see 8) against Drums-Linux.

## 2. ScaleCUA, as verified

Sources: github.com/THUDM/SCALE-CUA (README, REPRODUCE.md, `osworld_env/README.md`,
`osworld_env/worker/{config.yaml,src/environment.py,src/session.py,src/task.py}`,
`scalecua_rl/README.md`, `scalecua_rl/LICENSE`, `scalecua_rl/examples/training/configs/qw3vl_train_std.yaml`);
huggingface.co/datasets/extreme1228/ScaleCUA; huggingface.co/extreme1228/ScaleCUA-qwen3.5-osworld-sft;
arXiv 2607.11185 (appendices B.6, D).

- Weights: SFT start points and RL checkpoints exist for GLM-4.6V-Flash, Qwen3-VL-8B and
  Qwen3.5-9B, each for OSWorld and ScienceBoard (model card for the Qwen3.5 SFT model read;
  the Qwen3-VL cards were not opened individually, they are listed in the README table).
- Tasks: the HF dataset has `osworld/rl_tasks` (2,049 tasks in domains chrome, gimp,
  libreoffice_calc, libreoffice_impress, libreoffice_writer, multi_apps, os, thunderbird,
  vlc, vs_code), `scienceboard/rl_tasks` (1,163), 24,655 task-only candidates, and OSWorld
  judge functions. Dataset size 149 MB.
- Trainer: Ray, vLLM rollout, Megatron training, asynchronous GRPO with DAPO-style
  clipping, Frontier Sampling, Visual Context Segmentation.
- Environment layer: Go controller (`:5020`) plus Python workers (`:5021`) that start one
  QEMU-in-Docker VM per rollout (Docker or Kubernetes with KVM pods), Redis for allocation.
- Reset: the public VM image cold-boots on every episode (the README says so); a
  snapshot-capable child image is provided under `osworld_env/vm_image/snapshot/` and needs
  PostgreSQL and a shared snapshot store. The Kubernetes path can use a qcow2 copy-on-write
  overlay from an OCI image volume.
- Missing for training: `initial_pass_rate_file` is not packaged; it must be generated by
  four rollouts per selected task with the chosen start checkpoint.

## 3. Is it a clean drop-in? No. What a backend must be

The environment contract is not a screenshot/click API. It is the OSWorld VM contract:

- `FrameworkVMProvider.start_emulator` asks the controller for a session and gets back a
  container address; `desktop_env.DesktopEnv` then talks to the guest at fixed ports: the
  OSWorld guest server on 5000 (screenshot, command execution, files, accessibility tree,
  setup steps), Chrome DevTools on 9222, VLC on 8080, VNC on 5900/8006, SSH on 22,
  plus 8000 for ScienceBoard apps.
- A backend is an `EnvironmentDelegation` (`get_subtypes`, `is_exclusive`,
  `create_docker_container`, `create_k8s_pod`) and an `EnvironmentController`
  (`sync_start_session(os_type, image_path)` returning session id, container ids and
  addresses; `sync_end_session`). `create_controller(env_driver, delegation, ...)` selects
  the driver. I read how `osworld_env/worker` uses these; I could not open the
  `agentrl.worker.environment` source itself (path not found), so the exact base-class
  signatures are unverified.
- Each VM is 2 vCPU and 4 GiB by default (`vm_cpu: 2`, `vm_ram: 4` in the worker config;
  Kubernetes requests cpu/2 and memory, limits cpu+0.1 and memory+1 GiB; Ubuntu minimum 1
  CPU, 4 GiB). The paper does not state per-environment CPU or RAM.

What this means for Drums-Linux (decisions, mine):

1. Fidelity needs the real OSWorld guest. Decision: each Drums session runs OSWorld's own
   guest server (Apache-2.0, in `osworld_eval/desktop_env/server`), unmodified, on the
   OSWorld Ubuntu root filesystem used as the overlay lower layer. This replaces the earlier
   "no OSWorld shim" decision, because the trainer's tasks and judges run through that API.
   The envd-linux vocabulary stays the interface for Phases A and B.
2. Fixed guest ports (5000, 9222, 8080) collide across sessions in a shared network
   namespace. Decision: per-session network namespace with its own address (what was called
   v1) is required for this run, not optional.
3. Many OSWorld tasks change system state (package installs, `gsettings`, `sudo`). Decision:
   a full-root overlay per session (pivot_root onto overlayfs), not only `$HOME`.
4. Desktop and window manager: decision, fidelity first. Sessions run the image's native
   desktop components. A lighter window manager, or shared runtimes (L1), are introduced
   only after a per-task parity study shows the pass rates match the QEMU backend. Each
   reduction step passes that parity gate or is reverted.
5. Open technical risk (cannot be settled here): whether the image's GNOME session and its
   systemd-dependent services run inside a namespaced session without systemd as PID 1.
   If not, the affected tasks are excluded from the pool for both backends and listed.

Cost of this drop-in on the Drums side: the OSWorld-server-in-session work, a per-session
network namespace, full-root overlay, and a Drums `EnvironmentController`. None exists yet.

## 4. Licence ledger and routes (nothing unresolved is used)

| Asset | Status | Route |
|---|---|---|
| `scalecua_rl` (trainer, controller, worker framework) | MIT (`scalecua_rl/LICENSE` read) | Use. |
| `osworld_eval` | Apache-2.0 (owner-reported check; not independently read here) | Use after confirming the file. |
| Snapshot image assets | MIT (owner-reported) | Use for the tuned baseline's fast reset after confirming. |
| `osworld_env/worker` code | UNRESOLVED (no root LICENSE; the root repo page shows none) | Do not copy, modify or redistribute. I have read it, so I am not a clean-room author. Either obtain written permission from the rightsholder, or have it written by someone who has not read it, against the MIT `agentrl.worker` API and Apache `desktop_env` provider interface. |
| VeriGen code | UNRESOLVED | Not needed: tasks are released, not regenerated. Do not use. |
| HF task pool and judge functions (`extreme1228/ScaleCUA`) | UNRESOLVED (dataset card shows no licence in what I read) | Request written permission before any run. A user's statement that it is fine is not a substitute for the rightsholder's permission. Route around if refused: use OSWorld's own Apache-2.0 task set split into disjoint train and held-out parts (about 350 tasks; a much smaller pool, so a smaller and noisier run; labelled "OSWorld-split", not the published benchmark protocol). |
| Model weights | "other" licence inheriting the base model (Qwen3-VL-8B or Qwen3.5-9B) | Read the exact base terms (research use, publication of results, redistribution) before use. If unsuitable, the fallback is the licensed base model with a higher cost to first signal. |
| OSWorld Ubuntu image | Licence field not visible in what I read | Confirm before use; it is also the proposed overlay lower layer. |

Permission requests (to the repository owners, as an issue or by email) are part of the
plan's critical path and are not something I will assert as granted.

## 5. Recipe versions: paper versus shipped config

| Item | Paper (v1, Appendix D) | Shipped `qw3vl_train_std.yaml` |
|---|---|---|
| Backbone | Qwen3.5-9B headline; also Qwen3-VL-8B-Thinking, GLM-4.6V-Flash | Qwen3-VL (`model_type: qwen3_vl`; Qwen3.5 and GLM have their own configs, not read) |
| Task batch size | 32 (16 for ablations) | 16 |
| Rollouts per task n | 8 | 8 |
| Environment concurrency | 600 (OSWorld), 300 (ScienceBoard) | 256 |
| Max steps | 1,000 | 1,000 |
| Learning rate, KL, clip | 3e-6, 1e-4, 0.2/0.28, cap 3.0 | same |
| Max turns | 50 | 50 |
| Max sequence length | 40,000 | `max_total_len: 30000` |
| Rollout engine | vLLM TP 2 | vLLM TP 2, `gpu_memory_utilization: 0.7`, `max_model_len: 200000` |
| Actor engine | Megatron TP 4 | Megatron TP 2, ViT frozen, bf16, optimizer and HF model in checkpoints |
| Rollout ratio | 0.25 | 0.25 |
| Visual window K | 5 (OSWorld) | `images_to_keep: 5` |
| Frontier sampling | mu 0.5, sigma 0.25, gamma 0.2, alpha 0.2 | same values, initial pass-rate file empty |

Any comparison states which version it follows. This plan follows the shipped config, with
the scaling changes in section 8.

## 6. Hardware and time, from the sources

Published scale (paper Appendix D, Table 11), as reported:

- 8 nodes x 8 H800 80 GB = 64 GPUs; 16 for rollout (vLLM TP 2), 48 for actor and reference
  (Megatron TP 4). Batch 32, n 8, 600 environments. Average step 672.1 s, average rollout
  539.9 s, 1,000 steps.
- Derived: 1,000 x 672.1 s = 186.7 h wall; 64 x 186.7 = about 11,950 GPU-hours; 1,000 x 32 x 8
  = 256,000 rollouts; 256,000 x 539.9 s = about 38,400 environment-hours of use, against
  600 x 186.7 = about 112,000 slot-hours if all 600 slots were provisioned throughout
  (about 34% busy). That busy fraction is arithmetic on published averages; the paper
  does not state how many environments were actually provisioned over time.
- CPU and RAM per environment host: not stated in the paper. From the shipped worker config:
  2 vCPU and 4 GiB per VM, so 600 concurrent VMs nominally means 1,200 vCPU and 2.4 TiB
  of guest RAM before QEMU overhead.

Smallest footprint consistent with the shipped config (DERIVED, not validated):

- Rollout: TP 2 means one engine instance needs 2 GPUs. An 8B model in bf16 is about
  16 GB of weights, 8 GB per GPU at TP 2; the config reserves 70% of GPU memory.
- Actor and reference: Megatron TP 2. Standard mixed-precision Adam accounting is about
  16 bytes per trainable parameter (bf16 weights and gradients, fp32 master copy and two
  moments); with the distributed optimiser sharded over data-parallel replicas, a TP 2 x DP 2
  group (4 GPUs) is the smallest plausible actor group, plus a bf16 reference copy of about
  16 GB.
- Rollout ratio 0.25 on one 8-GPU node gives 2 rollout and 6 actor/reference GPUs. So one
  8 x 80 GB node is the smallest configuration consistent with these parallelism settings.
  Whether it runs without out-of-memory at the shipped sequence lengths is unknown until a
  two-step smoke run. The figures "8 x 80 GB and 64-128 CPUs" that circulate are not
  requirements and are not used as a price basis here; the only sourced figures are the
  paper's 64-GPU cluster and the per-VM 2 vCPU / 4 GiB.

## 7. Contamination

- Paper Appendix B.6: an overlap audit of VeriGen training tasks against benchmark pools.
  OSWorld `traj_verify` subset, N = 1,049: 0 exact JSON duplicates, 0 exact instruction
  duplicates, 2 objective near-duplicates (0.19%, similarity threshold 0.90), 22 exact judge
  reuses (2.10%, described as generic evaluator templates). ScienceBoard N = 825: 0, 0, 3
  (0.36%), 0.
- Gap: the released OSWorld RL pool has 2,049 tasks, the audit covers 1,049, so about half
  of the pool is not covered by the published audit (my reading of the counts; the paper may
  define the subsets differently).
- Unknown: whether the SFT start-point data contain OSWorld evaluation tasks.
- Our rule: run our own audit (normalised instruction equality, similarity at 0.90, judge
  hash) between the chosen training subset and the chosen held-out set before any run, and
  publish the result. Both backends start from the same weights, so start-point
  contamination affects both equally; therefore the headline is gain, not absolute score.

## 8. The scaled matched run (Phase C)

Same initial weights, same task subset, same trainer commit, same hyperparameters, same
seeds and task order; only the environment backend differs. Equal total budget.

| Item | Choice |
|---|---|
| Start policy | ScaleCUA SFT start point for Qwen3-VL-8B (OSWorld), subject to the weights-terms check |
| Trainer | `scalecua_rl` as shipped, commit pinned |
| Config | `qw3vl_train_std.yaml` with: task batch 8 (shipped 16, paper 32), n 8, concurrency 64, steps S (planning value 50, shipped 1,000), everything else unchanged. 64 rollouts per step, 3,200 per run at S = 50. |
| Task subset | A fixed subset of the pool chosen by the signal gate: initial pass rate in [0.1, 0.9] from 4 rollouts each; domains limited to those that run in both backends after the parity study |
| Held-out set | Disjoint from training by our own audit (section 7); fixed seed; the same set before and after, in a neutral harness (section 9, item E) |
| Backend A (Drums-Linux) | Section 3 design, at L1 for the Drums claim; an L0 run is reported separately as a precursor |
| Backend B (tuned incumbent) | ScaleCUA's QEMU-in-Docker/KVM with the snapshot fast-reset variant, qcow2 CoW overlays, the OSGym-style tunings from the baselines doc, same tuning-hours cap |
| Seeds | At least 2 per backend, because RL runs are noisy |
| Equal budget | Primary: same number of steps S, report cost, wall time and held-out gain. Secondary: same dollar cap, report gain at the cap |
| Reported metric | Held-out success-rate gain per dollar of total cost; also rollouts per dollar, GPU idle share, reset p50/p95, invalid-attempt share, interference counts |
| Never reported | Comparison of absolute success with published OSWorld scores |

Planning bounds for sizing only, from published averages and not measured: wall per step
cannot be shorter than one rollout, about 540 s (paper average), so S = 50 is at least
about 7.5 h per run; with 2 rollout GPUs instead of 16 the real figure is higher, and a
planning ceiling of three times that (about 22 h) is used until a smoke run measures it.
At 8 GPUs that is about 60 to 180 GPU-hours per run; four runs (2 backends x 2 seeds) is
about 240 to 720 GPU-hours, plus smoke, signal gate and evaluation. Environment time per
run is about 3,200 x 540 s = 480 environment-hours at the paper's average rollout time.

## 9. Cost components (every line must be priced before any approval)

| # | Component | What drives it |
|---|---|---|
| A | Environment hosts, per backend | Hours x host price; host count from 64 VMs x (2 vCPU, 4 GiB) nominal = 128 vCPU, 256 GiB plus overhead; includes image pull, pool warm-up, idle |
| B | Rollout inference GPUs (vLLM) | GPU-hours; weight-sync overhead; idle time while waiting for environments |
| C | Training GPUs (actor and reference) | GPU-hours; idle time while waiting for rollouts |
| D | Failed runs | Environment and inference spent on invalid or aborted attempts (harness `failed_run_cost_usd` plus tokens), whole-run restarts, discarded training data |
| E | Evaluation | Held-out rollouts before and after, both checkpoints, in the neutral harness, plus the initial pass-rate runs (4 per task) and the parity study |
| F | Storage and transfer | OSWorld image 23.7 GB; checkpoints (an 8B model is about 16 GB in bf16; with optimizer state a kept checkpoint is of order 100 GB, derived); screenshots if recorded (3,200 rollouts x up to 50 frames x about 0.5-1 MB, order 100 GB per run, estimate); results out |
| G | Control plane | Controller, Redis, PostgreSQL and snapshot store (snapshot variant), orchestration node |
| H | Setup and dev on billed hardware | Image build, smoke runs, parity study; one-time costs are reported separately and amortised explicitly, never hidden |
| I | Not priced | People's time; model-weight and dataset access (free if licences allow); any LLM API (none planned, all inference is local vLLM) |

Price inputs, none fetched: the GPU class (80 GB-class; the paper used H800, which is not
sold by AWS, so the real class and its relative speed are a decision; note the AWS default
On-Demand quota for P-family instances is 0 vCPUs, per the AWS quota page), the
environment host class (bare metal or nested KVM for backend B; no KVM needed for
Drums-Linux), and storage. Total = A + B + C + D + E + F + G + H, per run and for the whole
programme, with failed runs included in the denominator of gain per dollar.

An upper bound on what faster environments can buy: if environment cost share is f of total
cost, even free environments improve gain per dollar by at most 1/(1-f). The other channel
is GPU idle time caused by slow or failing environments, which is measured (rollout-worker
wait time, GPU utilisation) before Phase C is approved. If Phase B shows environments are a
small share and GPUs are not waiting on them, Phase C is expected to be inconclusive and the
plan says so up front.

## 10. Gates before any run

1. Written permission or a clean route for each unresolved asset in section 4; weights
   terms read.
2. Phase A and B complete on both backends; Drums at L1; isolation probes pass.
3. Per-task parity study on the chosen subset (same checkpoint, at least 4 rollouts per
   task per backend): no task domain with a significant pass-rate gap, else exclude.
4. Signal gate: initial pass rates give GRPO groups with variance.
5. Two-step smoke run on one GPU node: fits in memory, step time measured, environment
   wait share measured.
6. Contamination audit published.
7. Live prices for every line in section 9, quota confirmed, owner approval of a dollar cap.

Phase C stays unscheduled until all seven hold.

## 11. Reading the result

Pre-registered before any run: per backend, per seed, the held-out gain and the total cost;
bootstrap over tasks and seeds. "Drums better" only if the gain-per-dollar interval lies
above the incumbent's; "incumbent better" if below; otherwise inconclusive. All outcomes
are published, including inconclusive and negative ones.

## 12. Still unknown

Exact `agentrl.worker.environment` signatures; whether a GNOME session runs in a
namespaced session; the real minimum GPU footprint (needs the smoke run); step time at
8 GPUs; environment CPU and RAM actually needed per VM under this workload; the weights'
exact terms; every price; and the licence of the unresolved assets.
