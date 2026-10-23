"""Worker entry point: `python -m envd_linux.scalecua.worker_entry <task> --config <yaml> ...`.

Installs the "drums" driver into `agentrl.worker.environment.create_controller` BEFORE the worker
(and so the task module) imports it, then runs `agentrl.worker` exactly as `python -m agentrl.worker`
would. Arguments are passed through unchanged.

QUARANTINE BOUNDARY: this file and everything it imports must not import
`_quarantined_provider.py` (licence provenance, see its docstring). What is therefore MISSING is the
bridge from a session address to OSWorld's `DesktopEnv` provider interface. A task that needs that
bridge (ScaleCUA's `OSWorldTask`, whose worker code is itself licence-unresolved and not used here)
cannot run through this entry point until the bridge is written independently or permission is
granted. Only tasks that talk to the guest port directly (for example `GuestClient`) work.

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
