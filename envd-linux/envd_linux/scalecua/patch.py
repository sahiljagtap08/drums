"""Register a "drums" driver with AgentRL's `create_controller`.

The real `create_controller(driver, delegation, **config)` handles only 'manual', 'docker' and
'k8s' and raises for anything else, so a Drums driver needs a patch (or a worker entry point
that builds the controller itself). `install()` wraps the function in
`agentrl.worker.environment`. It must run BEFORE the code that does
`from agentrl.worker.environment import create_controller` (the worker's task module), because
that import binds the name. A worker entry point that calls `install()` and then starts
`python -m agentrl.worker` is the intended use; that entry point is not written here."""

import functools

from .controller import DrumsEnvironmentController


def build(delegation, backend=None, slots=1, **options):
    return DrumsEnvironmentController(delegation, backend, slots, **options)


def install():
    import agentrl.worker.environment as env
    if getattr(env.create_controller, "_drums", False):
        return env.create_controller
    original = env.create_controller

    @functools.wraps(original)
    def create_controller(driver, delegation, **config):
        if driver == "drums":
            return build(delegation, **config)
        return original(driver, delegation, **config)

    create_controller._drums = True
    env.create_controller = create_controller
    return create_controller
