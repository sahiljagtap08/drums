# Weights routes for the matched RL run: facts, no recommendation

STATUS: documentation only. Nothing was downloaded or used. The choice is the project owner's, with these facts in
front of them. Everything here is about the START POLICY (initial weights) and the data and recipe that go
with it; the environment backend (Drums versus the tuned baseline) is the same in every route.

Comparison honesty that applies to all routes: only route (i) keeps the incumbent's recipe up to the start
point. Anything else is OUR recipe on the same environments and must be labelled that way. Whatever route is
chosen, the evaluation is on OUR held-out split of the public OSWorld tasks (`training-comparison-plan.md`
section 13), which is our benchmark and is not comparable to published headlines; grouping by judge signature
reduces contamination but does not prove there is none.

## Route (i): ScaleCUA SFT start point

| | |
|---|---|
| What | `extreme1228/ScaleCUA-qwen3-vl-osworld-sft`, the SFT start point of the released online-RL checkpoint (also published for GLM-4.6V-Flash and Qwen3.5-9B). The RL checkpoint itself (67.7% on OSWorld by the card) is not the start point. |
| Licence status | **UNRESOLVED grant.** The RL checkpoint card says verbatim: "Weights are released for research; the model inherits the license of its base model (Qwen3-VL-8B)." The SFT card was not read separately and may differ. The base `Qwen/Qwen3-VL-8B-Instruct` shows `License: apache-2.0`; the paper names the trained base as Qwen3-VL-8B-**Thinking**, whose licence I did not check. Whether a published cost-per-success comparison is "research" use is an open permission question; only a grant resolves it (section 13). |
| Blocked | Use of the weights, until a grant covers it. The ScaleCUA 2,049-task pool and judges (HF `license=null`, hard gate) stay gated independently of the weights. |
| Buildable now | The trainer `scalecua_rl` (MIT) with only the environment backend swapped; our Drums controller and bridge (tested against `agentrl-worker` 0.4.0 and the real `desktop_env`); the licensed task split (211 training tasks, 150 held out under seed 0). The harness, scorer and cost model do not depend on the route. |
| Cost to first signal | Lowest of the three: the policy already acts on OSWorld. |
| Contamination caveat | Whether the SFT data included trajectories from the public OSWorld tasks is not established in what I read; a held-out score from this start point inherits that uncertainty. |
| Label | "ScaleCUA recipe, scaled subset, our split." Never "reproduction". |

## Route (ii): Apache-2.0 base plus our own or licensed SFT data

| | |
|---|---|
| What | `Qwen/Qwen3-VL-8B-Instruct`, Apache-2.0 on its Hugging Face page (verified). Warm-up (SFT) data would be ours or licensed. |
| Licence status | Weights: Apache-2.0, clear. Data: **none identified yet.** Candidate sources are unresearched and would each need their own licence check: trajectories we generate ourselves (the terms of whatever model produced them matter), or public trajectory datasets (not surveyed). |
| Blocked | The warm-up data. Without computer-use SFT a base VLM may score near zero on these tasks, which starves RL of reward (unmeasured here; the paper reports Qwen3.5-9B going from 41.8% base to 68.7% after its recipe, which says nothing directly about Qwen3-VL-8B). |
| Buildable now | Everything on the environment and RL side (same as route i). A data-generation and SFT pipeline is NOT built and would be new work with its own cost and its own validity question. |
| Cost to first signal | Highest: SFT data and an SFT stage come first, and their cost belongs in the total. |
| Label | "Our recipe on the same environments." Not the incumbent's, so published ScaleCUA numbers do not apply at all. |

## Route (iii): a different incumbent recipe

From the earlier resolution table (`training-comparison-plan.md` section 1). Nothing below was re-verified this session.

| Recipe | Licence status | Reproducible trainer? | Verdict on buildability |
|---|---|---|---|
| ComputerRL (THUDM/ComputerRL, Apache-2.0) | Repo Apache-2.0; weights on ModelScope, licence not checked | No: the repo is an OSWorld-derived evaluation harness; trainer, behaviour-cloning data and RL subset are not in it. Its trainer, AgentRL (THUDM/AgentRL), is MIT but ships examples for other tasks. | A recipe would have to be rebuilt on AgentRL with our own data. |
| EvoCUA / EvoCUA-1.5 (meituan/EvoCUA, Apache-2.0) | Repo Apache-2.0 | No trainer shown (README covers weights and serving; the tree was not listed) | Not reproducible as far as I could establish. |
| CUA-Gym | Licence wording inconsistent | No trainer, models unreleased | Possible later as a task source only. |
| MAI-Gym | Weights released; framework "coming soon" | No | Not buildable. |

So route (iii) today means "AgentRL (MIT) as a generic trainer plus data and recipe we would have to supply", which
is route (ii) with a different label. A route (iii) that is a real, reproducible incumbent recipe was not found.

## What each route leaves open

| Question | (i) | (ii) | (iii) |
|---|---|---|---|
| Weights licence | open grant | clear (Apache-2.0, base Instruct) | not checked |
| Training data licence | task pool gated; public split licensed but small | no SFT data identified | no data in repo |
| Recipe is the incumbent's | up to the start point | no | no |
| Comparable to published numbers | no (our split) | no | no |
| Work to first signal | lowest | highest | highest |
| Needs the owner to resolve | the grant | the data source | the recipe source |
