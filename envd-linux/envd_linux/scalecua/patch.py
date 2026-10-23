"""Register a "drums" driver with AgentRL's `create_controller`.

The real `create_controller(driver, delegation, **config)` handles only 'manual', 'docker' and
'k8s' and raises for anything else, so a Drums driver needs a patch (or a worker entry point
that builds the controller itself). `install()` wraps the function in
`agentrl.worker.environment`. It must run BEFORE the code that does
`from agentrl.worker.environment import create_controller` (the worker's task module), because
that import binds the name. `worker_entry.py` calls `install()` and then starts
`python -m agentrl.worker`.

Nothing here imports the quarantined provider bridge (see _quarantined_provider.py)."""

import functools

from .controller import DrumsEnvironmentController


BACKEND_OPTIONS = ("state_dir", "template", "root_mode", "netns", "osworld_port", "size", "display_base")


def build(delegation, backend=None, slots=1, **options):
    """backend: an object with create(slot)/destroy(slot). When absent it is a `Supervisor` built from
    the YAML `env_options` keys state_dir (required), template, root_mode, netns, osworld_port, size,
    display_base. Remaining options go to the pool."""
    if backend is None:
        if "state_dir" not in options:
            raise ValueError("driver 'drums' needs env_options.state_dir (or an explicit backend)")
        from ..session import Supervisor
        sup = {k: options.pop(k) for k in BACKEND_OPTIONS if k in options}
        state_dir = sup.pop("state_dir")
        template = sup.pop("template", None)
        backend = Supervisor(state_dir, template, **sup)
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
