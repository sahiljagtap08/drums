"""Interfaces. A side (Drums or the Linux baseline) supplies an Env; the agent and
verifier are the same code on both sides."""

from abc import ABC, abstractmethod

from .types import Action, Observation, Task, VerifyResult


class Env(ABC):
    slot: int

    @abstractmethod
    def reset(self, task: Task, gen: int, token: str) -> Observation:
        """Return the env to a clean state for `task`. Raises EnvFault on failure."""

    @abstractmethod
    def observe(self) -> Observation: ...

    @abstractmethod
    def act(self, action: Action) -> Observation:
        """Perform the action, return the next observation. Raises EnvFault."""

    @abstractmethod
    def pids(self) -> list[int]:
        """Root pids whose process trees count as this env's resource use."""

    def close(self) -> None:  # noqa: B027
        pass


class Agent(ABC):
    @abstractmethod
    def start(self, task: Task, obs: Observation) -> None: ...

    @abstractmethod
    def next_action(self, obs: Observation) -> Action:
        """Return the next action, or DONE."""


class Verifier(ABC):
    """Checks outcomes outside the agent and outside the env under test.

    A row is state written by the page under test, stamped by the verifier's clock with
    the generation the writer believed it was in: {"gen", "tok", "ts"}. Backends supply
    lease, is_clean and rows; the pass and interference rules live here so both sides
    are scored identically.
    """

    @abstractmethod
    def lease(self, slot: int, gen: int) -> None:
        """Record that `slot` now belongs to generation `gen`."""

    @abstractmethod
    def is_clean(self, slot: int, gen: int) -> bool:
        """True if the slot shows no state from any earlier attempt."""

    @abstractmethod
    def rows(self, slot: int) -> list[dict]: ...

    def check(self, task: Task, slot: int, gen: int, token: str, since_ts: float) -> VerifyResult:
        rows = self.rows(slot)
        passed = any(r["gen"] == gen and r["tok"] == token for r in rows)
        # State from another generation that appeared after this attempt took the slot.
        foreign = sum(1 for r in rows if r["gen"] != gen and r["ts"] >= since_ts)
        return VerifyResult(ran=True, passed=passed, foreign_rows=foreign)
