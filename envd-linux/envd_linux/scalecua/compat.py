"""Base classes for the controller and delegation.

When the MIT-licensed `agentrl-worker` package (the AgentRL task worker, which ScaleCUA bundles
under `scalecua_rl/worker`) is importable, its real `EnvironmentController` and
`EnvironmentDelegation` are the base classes, so this driver is checked against the real
interface (tested here against agentrl-worker 0.4.0 from PyPI, not against the copy bundled in the
ScaleCUA repo, which may differ). Otherwise minimal local equivalents with the same method names
are used and the contract tests that need the real package are skipped."""

import asyncio

try:  # pragma: no cover - depends on the environment
    from agentrl.worker.environment import EnvironmentController, EnvironmentDelegation
    REAL = True
except ImportError:  # pragma: no cover
    REAL = False

    class EnvironmentDelegation:
        def __init__(self, name):
            self.name = name

        def get_name(self):
            return self.name

        def get_subtypes(self):
            return ["default"]

        def get_service_port(self, subtype):
            return None

        def is_exclusive(self, subtype):
            return False

        def supports_reuse(self, subtype):
            return False

        def get_concurrency_limit(self, subtype):
            return 1 if self.is_exclusive(subtype) else 0

        def get_reuse_limit(self, subtype):
            return 1 if self.is_exclusive(subtype) or not self.supports_reuse(subtype) else 0

        def get_max_execution_time(self, subtype):
            return 4 * 60 * 60

    class EnvironmentController:
        def __init__(self, delegation):
            self.delegation = delegation
            self.delegation.controller = self

        def _sync(self, coro):
            return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

        def sync_start_session(self, *a, **k):
            return self._sync(self.start_session(*a, **k))

        def sync_renew_session(self, *a, **k):
            return self._sync(self.renew_session(*a, **k))

        def sync_end_session(self, *a, **k):
            return self._sync(self.end_session(*a, **k))

        def sync_execute_command(self, *a, **k):
            return self._sync(self.execute_command(*a, **k))

        def sync_execute_shell(self, *a, **k):
            return self._sync(self.execute_shell(*a, **k))

        def sync_get_env_variables(self, *a, **k):
            return self._sync(self.get_env_variables(*a, **k))

        async def background_task(self):
            pass
