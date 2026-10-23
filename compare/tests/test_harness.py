from drums_compare import fakes
from drums_compare.harness import run_attempt
from drums_compare.types import Task

TASK = Task(id="t", instruction="type this token: TOK1", verifier={})


def go(env_kw=None, agent_kw=None, world=None):
    world = world or fakes.FakeWorld()
    env = fakes.FakeEnv(0, world, **(env_kw or {}))
    return run_attempt(side="fake", env=env, agent=fakes.FakeAgent(**(agent_kw or {})),
                       verifier=fakes.FakeVerifier(world), task=TASK, attempt_id="a1",
                       gen=1, token="TOK1")


def test_clean_attempt_is_valid_and_passes():
    r = go()
    assert r.valid and r.passed and r.terminated_by == "agent_done" and r.steps == 2


def test_agent_failure_is_still_valid():
    r = go(agent_kw={"p_wrong": 1.0})
    assert r.valid and not r.passed


def test_reset_failure_is_invalid_infra():
    r = go({"p_reset_fail": 1.0})
    assert not r.valid and "fault:reset_failed" in r.invalid_reasons
    assert r.terminated_by == "infra_error"


def test_action_failure_invalid():
    r = go({"p_action_fail": 1.0})
    assert not r.valid and "fault:action_failed" in r.invalid_reasons


def test_black_frame_invalid():
    r = go({"p_black": 1.0})
    assert not r.valid and "fault:black_frame" in r.invalid_reasons


def test_frozen_display_after_three_stale_frames():
    class Long(fakes.FakeAgent):
        def start(self, task, obs):
            from drums_compare.types import Action
            self.queue = [Action("click", x=1, y=1)] * 5

    world = fakes.FakeWorld()
    env = fakes.FakeEnv(0, world, p_stale=1.0)
    r = run_attempt(side="fake", env=env, agent=Long(), verifier=fakes.FakeVerifier(world),
                    task=TASK, attempt_id="a", gen=1, token="TOK1")
    assert "fault:frozen_display" in r.invalid_reasons and not r.valid


def test_two_stale_frames_alone_are_not_a_fault():
    world = fakes.FakeWorld()
    env = fakes.FakeEnv(0, world, p_stale=1.0)
    r = run_attempt(side="fake", env=env, agent=fakes.FakeAgent(), verifier=fakes.FakeVerifier(world),
                    task=TASK, attempt_id="a", gen=1, token="TOK1")
    assert r.valid and r.stale_frames == 2


def test_dirty_reset_invalid():
    world = fakes.FakeWorld()
    env = fakes.FakeEnv(0, world, p_dirty=1.0)
    env.reset(TASK, 1, "old")  # establish a previous attempt so a leftover can be planted
    r = run_attempt(side="fake", env=env, agent=fakes.FakeAgent(), verifier=fakes.FakeVerifier(world),
                    task=TASK, attempt_id="a", gen=2, token="TOK1")
    assert r.dirty_reset and "dirty_reset" in r.invalid_reasons and not r.valid


def test_step_budget_counts_as_valid_termination():
    from drums_compare.types import Action

    class Forever(fakes.FakeAgent):
        def next_action(self, obs):
            return Action("click", x=1, y=1)

    world = fakes.FakeWorld()
    t = Task(id="t", instruction="x", verifier={}, max_steps=4)
    r = run_attempt(side="fake", env=fakes.FakeEnv(0, world), agent=Forever(),
                    verifier=fakes.FakeVerifier(world), task=t, attempt_id="a", gen=1, token="T")
    assert r.terminated_by == "step_budget" and r.valid and not r.passed


def test_foreign_state_arriving_during_attempt_invalidates():
    class Leaky(fakes.FakeEnv):
        def act(self, action):
            self.world.write(self.slot, 99, "someone-elses")  # lands under another gen mid-attempt
            return super().act(action)

    world = fakes.FakeWorld()
    r = run_attempt(side="fake", env=Leaky(0, world), agent=fakes.FakeAgent(),
                    verifier=fakes.FakeVerifier(world), task=TASK, attempt_id="a", gen=1, token="TOK1")
    assert not r.valid and "interference:foreign_state_in_slot" in r.invalid_reasons


def test_old_history_before_the_attempt_is_not_interference():
    world = fakes.FakeWorld()
    world.write(0, 99, "yesterday")
    import time
    time.sleep(0.01)
    assert go(world=world).valid
