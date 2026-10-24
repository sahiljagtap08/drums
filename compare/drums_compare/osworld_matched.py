"""Matched-comparison runner for OSWorld tasks: the SAME task sequence and agent on two environment adapters.

STATUS: BUILT and unit-tested with fake adapters (harness validation). NOT RUN against any real environment.
Protocol and thresholds: docs/matched-comparison-protocol.md (committed before any run).

An arm is an `Adapter`: it hands out leases; a lease resets to a task, steps, evaluates and is released. Two real adapters are
written against the PUBLIC OSWorld `DesktopEnv` API only (Apache-2.0 xlang-ai/OSWorld): `DesktopEnvAdapter` takes a factory that
builds a DesktopEnv, so one class serves both arms:
  * Drums arm: `drums_factory(pool)` installs the Drums provider bridge (osworld_provider) and builds `DesktopEnv(provider_name="docker",
    region="drums")`;
  * Incumbent arm: `incumbent_factory(provider_name, ...)` builds a stock `DesktopEnv` for any provider the public API ships, with
    the incumbent's own reuse features ON (a pre-warmed pool of environments reused across rollouts, snapshot revert between
    uses, which is what `DesktopEnv.reset` does for providers that support it).
The quarantined ScaleCUA-influenced provider is not imported here.

Accounting (explicit, no hard-coded prices): a rollout is VALID iff it completed without an environment fault and produced a score;
invalid rollouts still cost. valid_rollouts_per_hour = valid / arm wall hours (pool warm-up included); cost_per_valid_rollout =
price.total(arm wall hours) / valid. Prices come from a JSON config the caller supplies."""

import json
import random
import uuid
import statistics
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class AdapterFault(RuntimeError):
    """The environment failed (not the agent): counted, the rollout is invalid, its cost still counts."""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail


@dataclass
class Prices:
    """Everything priced is an input. total(hours) = hosts * host_usd_per_hour * hours + fixed_usd."""
    host_usd_per_hour: float
    hosts: int = 1
    fixed_usd: float = 0.0
    label: str = ""

    @classmethod
    def from_json(cls, path):
        with open(path) as f:
            d = json.load(f)
        return cls(d["host_usd_per_hour"], d.get("hosts", 1), d.get("fixed_usd", 0.0), d.get("label", ""))

    def total(self, hours):
        return self.hosts * self.host_usd_per_hour * hours + self.fixed_usd


@dataclass
class Rollout:
    arm: str
    task_id: str
    seq: int
    worker: int
    reset_s: float = 0.0
    wall_s: float = 0.0
    steps: int = 0
    score: float | None = None
    fault: str | None = None
    valid: bool = False
    t_start: float = 0.0
    canary: str = "off"  # off | clean | interference | unsupported
    canary_seen: list = field(default_factory=list)


class Lease(ABC):
    @abstractmethod
    def reset(self, task_config: dict) -> dict: ...

    @abstractmethod
    def step(self, action) -> dict: ...

    @abstractmethod
    def evaluate(self) -> float: ...

    def release(self) -> None:  # noqa: B027
        pass

    # Interference canary (READINESS): rollout k writes a marker into the slot, rollout k+1 on the same slot must not see it.
    def canary_probe(self) -> list:
        """Markers visible in this slot right now (all of them, whoever wrote them). Raise NotImplementedError if the lease cannot run a command."""
        raise NotImplementedError

    def canary_write(self, token: str) -> None:
        raise NotImplementedError


class Adapter(ABC):
    name: str

    @abstractmethod
    def open(self, slots: int) -> None:
        """Build the pool (counted in the arm's wall time)."""

    @abstractmethod
    def acquire(self, worker: int) -> Lease: ...

    def close(self) -> None:  # noqa: B027
        pass


class Agent(ABC):
    """Same code on both arms. Returns the next action, or None when done."""

    @abstractmethod
    def start(self, task_config: dict, obs: dict) -> None: ...

    @abstractmethod
    def next_action(self, obs: dict): ...


class ScriptedAgent(Agent):
    """HARNESS VALIDATION ONLY: replays a fixed action list per task id."""

    def __init__(self, scripts):
        self.scripts, self.i, self.task = scripts, 0, None

    def start(self, task_config, obs):
        self.task, self.i = task_config["id"], 0

    def next_action(self, obs):
        s = self.scripts.get(self.task, [])
        if self.i >= len(s):
            return None
        self.i += 1
        return s[self.i - 1]


def sample_tasks(task_ids, rollouts_per_task, seed):
    """The sampling rule: every task appears `rollouts_per_task` times; the order is a seeded shuffle; both arms get the SAME list."""
    seq = [t for t in task_ids for _ in range(rollouts_per_task)]
    random.Random(seed).shuffle(seq)
    return seq


class MemorySampler:
    """Host used memory at a fixed period while an arm runs. `reader()` returns used KiB (injectable for tests)."""

    def __init__(self, reader, period=1.0):
        self.reader, self.period, self.samples = reader, period, []
        self._stop = threading.Event()
        self._t = None

    def __enter__(self):
        self.baseline = self.reader()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def _run(self):
        while not self._stop.wait(self.period):
            self.samples.append(self.reader())

    def __exit__(self, *a):
        self._stop.set()
        self._t.join(5)

    def summary(self, concurrency):
        if not self.samples:
            return {"baseline_kib": self.baseline, "samples": 0}
        peak = max(self.samples)
        return {"baseline_kib": self.baseline, "samples": len(self.samples), "peak_kib": peak,
                "mean_kib": statistics.mean(self.samples), "peak_delta_per_env_mib": (peak - self.baseline) / 1024 / max(concurrency, 1)}


def _canary(lease, r):
    try:
        seen = lease.canary_probe()
    except NotImplementedError:
        r.canary = "unsupported"
        return
    except Exception as e:
        raise AdapterFault("canary_failed", str(e)[:200]) from e
    r.canary_seen = list(seen)
    r.canary = "interference" if seen else "clean"
    try:
        lease.canary_write(f"{r.arm}:{r.seq}:{r.worker}:{uuid.uuid4().hex[:8]}")
    except Exception as e:
        raise AdapterFault("canary_failed", str(e)[:200]) from e


def run_arm(adapter, tasks_by_id, sequence, agent_factory, concurrency, prices, max_steps=50, clock=time.time,
            mem_reader=None, mem_period=1.0, canary=True):
    """Run `sequence` (task ids) on one adapter with `concurrency` workers; return the arm's raw rows and summary.

    canary (READINESS): after each reset the slot is probed for markers written by ANY earlier rollout, then this rollout writes its
    own. A marker that survives a reset is INTERFERENCE: the rollout is invalid (fault "interference"), counted, and still costs.
    A lease that cannot run commands is counted as canary "unsupported" (never as clean)."""
    rows, lock, idx = [], threading.Lock(), {"i": 0}
    sampler = MemorySampler(mem_reader, mem_period) if mem_reader else None
    t0 = clock()
    if sampler:
        sampler.__enter__()
    try:
        adapter.open(concurrency)
        t_open = clock()

        def worker(w):
            agent = agent_factory()
            while True:
                with lock:
                    i = idx["i"]
                    idx["i"] += 1
                if i >= len(sequence):
                    return
                tid = sequence[i]
                r = Rollout(adapter.name, tid, i, w, t_start=clock())
                lease = None
                try:
                    lease = adapter.acquire(w)
                    t1 = clock()
                    obs = lease.reset(tasks_by_id[tid])
                    r.reset_s = clock() - t1
                    if canary:
                        _canary(lease, r)
                    agent.start(tasks_by_id[tid], obs)
                    for _ in range(max_steps):
                        a = agent.next_action(obs)
                        if a is None:
                            break
                        obs = lease.step(a)
                        r.steps += 1
                    r.score = lease.evaluate()
                    r.valid = r.score is not None
                    if not r.valid:
                        r.fault = "no_score"
                    if r.canary == "interference":
                        r.valid, r.fault = False, r.fault or "interference"
                except AdapterFault as e:
                    r.fault = e.kind
                except Exception as e:  # an adapter bug or environment error: invalid, counted, reported
                    r.fault = f"exception:{type(e).__name__}"
                finally:
                    if lease is not None:
                        try:
                            lease.release()
                        except Exception as e:
                            r.fault = r.fault or f"release:{type(e).__name__}"
                            r.valid = False
                    r.wall_s = clock() - r.t_start
                    with lock:
                        rows.append(r)
        ts = [threading.Thread(target=worker, args=(w,)) for w in range(concurrency)]
        [t.start() for t in ts]
        [t.join() for t in ts]
    finally:
        try:
            adapter.close()
        finally:
            if sampler:
                sampler.__exit__()
    wall = clock() - t0
    hours = wall / 3600
    valid = sum(1 for r in rows if r.valid)
    faults = {}
    for r in rows:
        if r.fault:
            faults[r.fault] = faults.get(r.fault, 0) + 1
    walls = sorted(r.wall_s for r in rows)

    def pct(q):
        return walls[min(int(q * len(walls)), len(walls) - 1)] if walls else None
    summary = {"arm": adapter.name, "rollouts": len(rows), "valid": valid, "faults": faults, "wall_s": wall,
               "open_s": t_open - t0, "valid_per_hour": valid / hours if hours else None,
               "cost_usd": prices.total(hours),
               "cost_per_valid_usd": prices.total(hours) / valid if valid else None,
               "rollout_wall_p50_s": pct(0.5), "rollout_wall_p95_s": pct(0.95),
               "interference_count": sum(1 for r in rows if r.canary == "interference"),
               "canary_clean": sum(1 for r in rows if r.canary == "clean"),
               "canary_unsupported": sum(1 for r in rows if r.canary == "unsupported"),
               "canary_enabled": canary,
               "reset_mean_s": statistics.mean(r.reset_s for r in rows) if rows else None,
               "memory": sampler.summary(concurrency) if sampler else None, "prices": prices.__dict__}
    return {"summary": summary, "rows": [r.__dict__ for r in sorted(rows, key=lambda r: r.seq)]}


def compare(a, b, n_boot=10000, seed=1):
    """Ratio of valid-per-hour-per-dollar of arm A over arm B with a bootstrap interval over rollouts (rollouts resampled per arm).
    `a` and `b` are run_arm results. Returns the point ratio and the 95% interval; a 'win' needs the lower bound above 1."""
    def metric(rows, summ, res):
        v = sum(1 for r in res if r["valid"])
        return v / summ["cost_usd"] if summ["cost_usd"] else None
    ra, rb = a["rows"], b["rows"]
    point = metric(ra, a["summary"], ra) / metric(rb, b["summary"], rb)
    rnd = random.Random(seed)
    vals = []
    for _ in range(n_boot):
        sa = [rnd.choice(ra) for _ in ra]
        sb = [rnd.choice(rb) for _ in rb]
        ma = sum(1 for r in sa if r["valid"]) / a["summary"]["cost_usd"]
        mb = sum(1 for r in sb if r["valid"]) / b["summary"]["cost_usd"]
        if mb:
            vals.append(ma / mb)
    vals.sort()
    return {"valid_per_dollar_ratio_A_over_B": point,
            "ci95": [vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals))]] if vals else None,
            "win": bool(vals) and vals[int(0.025 * len(vals))] > 1.0}


# ---------------- DesktopEnv-based adapters (public API only) ----------------
class _DesktopEnvLease(Lease):
    def __init__(self, env):
        self.env = env

    def reset(self, task_config):
        try:
            return self.env.reset(task_config=task_config)
        except Exception as e:
            raise AdapterFault("reset_failed", str(e)[:200]) from e

    def step(self, action):
        try:
            obs, *_ = self.env.step(action)
            return obs
        except Exception as e:
            raise AdapterFault("step_failed", str(e)[:200]) from e

    def evaluate(self):
        try:
            return self.env.evaluate()
        except Exception as e:
            raise AdapterFault("evaluate_failed", str(e)[:200]) from e

    def canary_probe(self):
        return _desktop_canary_probe(self.env)

    def canary_write(self, token):
        _desktop_canary_write(self.env, token)


CANARY_PATHS = ("/tmp/.drums_canary", "~/.drums_canary")


def _bash(env, script):
    out = env.controller.run_bash_script(script, timeout=30)  # public DesktopEnv controller API
    if isinstance(out, dict):
        if out.get("status") == "error":
            raise RuntimeError(str(out.get("output") or out.get("error"))[:200])
        return str(out.get("output", ""))
    return str(out or "")


def _desktop_canary_probe(env):
    text = _bash(env, "; ".join(f"cat {p} 2>/dev/null" for p in CANARY_PATHS) + "; true")
    return sorted({ln.strip() for ln in text.splitlines() if ln.strip()})


def _desktop_canary_write(env, token):
    _bash(env, "; ".join(f"echo {token} > {p}" for p in CANARY_PATHS))


class DesktopEnvAdapter(Adapter):
    """A pool of `DesktopEnv` instances built up front and REUSED across rollouts (reset between uses). `factory(worker)` builds one.
    Pool reuse and whatever snapshot revert the provider supports are the incumbent's own mechanisms: we only call
    `reset`/`step`/`evaluate`/`close`, the public surface."""

    def __init__(self, name, factory):
        self.name, self.factory, self.envs = name, factory, []

    def open(self, slots):
        self.envs = [self.factory(i) for i in range(slots)]

    def acquire(self, worker):
        return _DesktopEnvLease(self.envs[worker])

    def close(self):
        for e in self.envs:
            try:
                e.close()
            except Exception:
                pass
        self.envs = []


def incumbent_factory(provider_name, region=None, path_to_vm=None, snapshot_name="init_state", screen_size=(1920, 1080),
                      desktop_env_module=None, **kw):
    """The incumbent arm: a stock public-API `DesktopEnv`. `snapshot_name` and `path_to_vm` are the provider's own reuse knobs
    (snapshot revert on reset for providers that support it); the PROVIDER and its settings are part of the run specification."""
    def make(worker):
        mod = desktop_env_module
        if mod is None:
            import desktop_env.desktop_env as mod
        return mod.DesktopEnv(provider_name=provider_name, region=region, path_to_vm=path_to_vm, snapshot_name=snapshot_name,
                              action_space="pyautogui", screen_size=screen_size, **kw)
    return make


def drums_factory(pool, screen_size=(1280, 800), desktop_env_module=None, **kw):
    """The Drums arm: the same public `DesktopEnv`, with the Drums provider bridge installed (`region="drums"`)."""
    from envd_linux.scalecua import osworld_provider as op

    def make(worker):
        mod = desktop_env_module
        if mod is None:
            import desktop_env.desktop_env as mod
        op.install(pool)
        return mod.DesktopEnv(provider_name="docker", region=op.REGION, action_space="pyautogui", screen_size=screen_size, **kw)
    return make
