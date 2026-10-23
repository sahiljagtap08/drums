"""One attempt: reset, agent loop, independent verification, validity decision.

Validity policy (identical for both sides, written down in docs/linux-comparison-design.md):
an attempt is VALID only if all of these hold. Anything else is counted, with a reason,
and its slot time still counts toward cost and the rate denominator.
  - the reset succeeded and the verifier saw a clean slot afterwards
  - no infrastructure fault happened (reset/screenshot/action error, black frame,
    a long run of identical frames, stale generation)
  - the rollout ended by agent `done`, step budget, or task timeout (not infra_error)
  - the verifier ran
  - no state from another attempt was seen in this slot
The agent failing the task is NOT invalid. Success is reported separately.
"""

import time

from .protocol import Agent, Env, Verifier
from .types import DONE, AttemptRecord, EnvFault, Task

STALE_RUN_FAULT = 3  # consecutive identical frames after actions that count as a frozen display


def run_attempt(*, side, env: Env, agent: Agent, verifier: Verifier, task: Task, attempt_id: str,
                gen: int, token: str, clock=time.time) -> AttemptRecord:
    rec = AttemptRecord(attempt_id=attempt_id, side=side, slot=env.slot, task_id=task.id,
                        gen=gen, token=token, t_start=clock())
    t0 = clock()
    obs = None
    try:
        verifier.lease(env.slot, gen)
        obs = env.reset(task, gen, token)
        rec.reset_s = clock() - t0
        if not verifier.is_clean(env.slot, gen):
            rec.dirty_reset = True
    except EnvFault as e:
        rec.faults.append(e.kind)
        rec.terminated_by = "infra_error"
        rec.reset_s = clock() - t0

    if obs is not None and not rec.faults:
        _rollout(rec, env, agent, task, obs, clock)

    try:
        res = verifier.check(task, env.slot, gen, token, rec.t_start)
        rec.verify_ran, rec.passed, rec.foreign_rows = res.ran, res.passed, res.foreign_rows
    except Exception as e:  # verifier outage is infrastructure, not the agent's problem
        rec.faults.append(f"verifier_error:{type(e).__name__}")

    rec.t_end = clock()
    rec.wall_s = rec.t_end - t0
    _decide(rec)
    return rec


def _rollout(rec, env, agent, task, obs, clock):
    start = clock()
    stale_run = 0
    try:
        agent.start(task, obs)
        while True:
            if obs.black:
                rec.faults.append("black_frame")
                rec.terminated_by = "infra_error"
                return
            if obs.gen != rec.gen:
                rec.faults.append("stale_gen")
                rec.terminated_by = "infra_error"
                return
            if rec.steps >= task.max_steps:
                rec.terminated_by = "step_budget"
                return
            if clock() - start > task.timeout_s:
                rec.terminated_by = "timeout"
                return
            action = agent.next_action(obs)
            if action.type == DONE.type:
                rec.terminated_by = "agent_done"
                return
            obs = env.act(action)
            rec.steps += 1
            if obs.stale:
                rec.stale_frames += 1
                stale_run += 1
                if stale_run >= STALE_RUN_FAULT:
                    rec.faults.append("frozen_display")
                    rec.terminated_by = "infra_error"
                    return
            else:
                stale_run = 0
    except EnvFault as e:
        rec.faults.append(e.kind)
        rec.terminated_by = "infra_error"


def _decide(rec: AttemptRecord):
    reasons = []
    reasons += [f"fault:{f}" for f in rec.faults]
    if rec.dirty_reset:
        reasons.append("dirty_reset")
    if rec.terminated_by not in ("agent_done", "step_budget", "timeout"):
        reasons.append(f"bad_termination:{rec.terminated_by or 'none'}")
    if not rec.verify_ran:
        reasons.append("verifier_did_not_run")
    if rec.foreign_rows:
        reasons.append("interference:foreign_state_in_slot")
    rec.invalid_reasons = sorted(set(reasons))
    rec.valid = not rec.invalid_reasons
