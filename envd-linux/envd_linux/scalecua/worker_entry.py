"""Worker entry point: `python -m envd_linux.scalecua.worker_entry <task> --config <yaml> ...`.

Installs the "drums" driver into `agentrl.worker.environment.create_controller` BEFORE the worker
(and so the task module) imports it, then runs `agentrl.worker` exactly as `python -m agentrl.worker`
would. Arguments are passed through unchanged.

QUARANTINE BOUNDARY: this file and everything it imports must not import
`_quarantined_provider.py` (licence provenance, see its docstring). The bridge from a session address to
OSWorld's `DesktopEnv` provider interface now exists separately as `osworld_provider.py` (written from the
public Apache-2.0 OSWorld API only), but nothing here wires it in: a task that wants it calls
`osworld_provider.install(pool)` itself, and ScaleCUA's `OSWorldTask` (licence-unresolved worker code, not
used here) has never run through this entry point. Only our own fake task has.

Targeted interface: the MIT PyPI `agentrl-worker==0.4.0`. The newer copy bundled in ScaleCUA's
`osworld_env` is licence-unresolved and was not read for this file; known risk: it may add
parameters to `create_docker_container` and may validate `env_options.driver` against a closed set,
so a config with `driver: drums` can be rejected by a task's own pydantic options model."""

import runpy
import sys

from .patch import install


def main():
    install()
    sys.argv[0] = "agentrl.worker"
    runpy.run_module("agentrl.worker", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
