"""Plain data types shared by the harness, runner and scorer."""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Action:
    """Same vocabulary as envd's POST /action, plus `done` (agent finished)."""

    type: str  # click, dblclick, type, key, scroll, done
    x: float | None = None
    y: float | None = None
    button: str | None = None
    text: str | None = None
    key: str | None = None
    modifiers: tuple[str, ...] | None = None
    dx: float | None = None
    dy: float | None = None

    def to_json(self):
        return {k: v for k, v in asdict(self).items() if v is not None}


DONE = Action(type="done")


@dataclass(frozen=True)
class Observation:
    png: bytes
    width: int
    height: int
    gen: int
    stale: bool = False
    black: bool = False


@dataclass(frozen=True)
class Task:
    id: str
    instruction: str
    verifier: dict
    max_steps: int = 30
    timeout_s: float = 300.0


class EnvFault(Exception):
    """Infrastructure failure. Never the agent's fault. `kind` is the counted reason."""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass
class VerifyResult:
    ran: bool
    passed: bool = False
    foreign_rows: int = 0  # state in this env that belongs to another attempt
    detail: str = ""


@dataclass
class AttemptRecord:
    attempt_id: str
    side: str
    slot: int
    task_id: str
    gen: int
    token: str
    t_start: float = 0.0  # wall clock, seconds since epoch
    t_end: float = 0.0
    reset_s: float = 0.0
    wall_s: float = 0.0  # reset + rollout + verify; this is the slot time billed to the attempt
    steps: int = 0
    terminated_by: str = ""  # agent_done, step_budget, timeout, infra_error
    faults: list = field(default_factory=list)
    stale_frames: int = 0
    verify_ran: bool = False
    passed: bool = False
    foreign_rows: int = 0
    dirty_reset: bool = False
    valid: bool = False
    invalid_reasons: list = field(default_factory=list)
    interference_with: list = field(default_factory=list)  # filled by the run-level audit

    def to_json(self):
        return asdict(self)
