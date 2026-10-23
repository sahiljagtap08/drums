"""FAKES. Everything here simulates an environment so the harness, runner and scorer can be
tested without a desktop. Nothing here measures a real system. Every run that uses these
sets `"fake": true` in run.json and the scorer refuses to give a verdict on it.
"""

import random
import threading
import time

from .protocol import Agent, Env, Verifier
from .types import DONE, Action, EnvFault, Observation, Task, VerifyResult

FAKE = True


class FakeWorld:
    """Shared state standing in for verify-server's tables."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rows = []  # (slot, gen, tok)
        self.leases = {}  # slot -> gen

    def lease(self, slot, gen):
        with self.lock:
            self.leases[slot] = gen

    def write(self, slot, gen, tok):
        with self.lock:
            self.rows.append({"slot": slot, "gen": gen, "tok": tok, "ts": time.time()})


class FakeEnv(Env):
    """Fault rates are per call. `p_leak`: a late write from the previous attempt lands
    after the next reset, the failure mode the generation guard exists to stop."""

    fake = True

    def __init__(self, slot, world, seed=0, latency_s=0.0, p_reset_fail=0.0, p_action_fail=0.0,
                 p_black=0.0, p_stale=0.0, p_leak=0.0, p_dirty=0.0):
        self.slot, self.world = slot, world
        self.rng = random.Random(f"{seed}-{slot}")
        self.latency_s = latency_s
        self.p = dict(reset_fail=p_reset_fail, action_fail=p_action_fail, black=p_black,
                      stale=p_stale, leak=p_leak, dirty=p_dirty)
        self.gen = -1
        self.token = ""

    def _obs(self):
        black = self.rng.random() < self.p["black"]
        stale = self.rng.random() < self.p["stale"]
        return Observation(png=b"\x89PNG-fake", width=1280, height=800, gen=self.gen,
                           stale=stale, black=black)

    def _sleep(self):
        if self.latency_s:
            time.sleep(self.latency_s)

    def reset(self, task, gen, token):
        self._sleep()
        if self.rng.random() < self.p["reset_fail"]:
            raise EnvFault("reset_failed", "fake")
        prev = (self.gen, self.token)
        self.gen, self.token = gen, token
        if prev[0] >= 0:
            if self.rng.random() < self.p["leak"]:
                self.world.write(self.slot, prev[0], prev[1])
            if self.rng.random() < self.p["dirty"]:
                self.world.write(self.slot, gen, "leftover-from-previous")
        return self._obs()

    def observe(self):
        return self._obs()

    def act(self, action):
        self._sleep()
        if self.rng.random() < self.p["action_fail"]:
            raise EnvFault("action_failed", "fake")
        if action.type == "type" and action.text:
            self.world.write(self.slot, self.gen, action.text)
        return self._obs()

    def pids(self):
        return []


class FakeVerifier(Verifier):
    fake = True

    def __init__(self, world):
        self.w = world

    def lease(self, slot, gen):
        self.w.lease(slot, gen)

    def is_clean(self, slot, gen):
        return not any(r["gen"] == gen for r in self.rows(slot))

    def rows(self, slot):
        with self.w.lock:
            return [r for r in self.w.rows if r["slot"] == slot]


class FakeAgent(Agent):
    """Types the token it was given. `p_wrong` makes it type the wrong text: a task
    failure, which must NOT make the attempt invalid."""

    fake = True

    def __init__(self, seed=0, p_wrong=0.0):
        self.rng = random.Random(seed)
        self.p_wrong = p_wrong
        self.queue = []

    def start(self, task, obs):
        tok = task.instruction.rsplit(" ", 1)[-1]
        if self.rng.random() < self.p_wrong:
            tok = "wrong"
        self.queue = [Action("click", x=100, y=100), Action("type", text=tok), DONE]

    def next_action(self, obs):
        return self.queue.pop(0)
