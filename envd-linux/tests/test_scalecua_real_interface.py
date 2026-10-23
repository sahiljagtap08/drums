"""Checks against the REAL agentrl-worker package (MIT, PyPI 0.4.0), skipped when it is not
installed. They prove interface compatibility only: the controller subclasses the real base, the
real sync wrappers drive it, and the "drums" driver can be registered. They do not exercise the
real ScaleCUA worker."""

import pytest

pytest.importorskip("agentrl.worker.environment")

from agentrl.worker.environment import EnvironmentController, EnvironmentDelegation  # noqa: E402

from envd_linux.scalecua import compat, patch  # noqa: E402
from envd_linux.scalecua.controller import DrumsEnvironmentController, DrumsEnvironmentDelegation  # noqa: E402
from test_scalecua_controller import FakeBackend  # noqa: E402


def make(**kw):
    c = DrumsEnvironmentController(DrumsEnvironmentDelegation(), FakeBackend(), 2,
                                   health=lambda ip, **k: True, acquire_timeout=2, eager_reset=False, **kw)
    c.run_loop_in_thread()
    return c


def test_bases_are_the_real_agentrl_classes():
    assert compat.REAL
    assert issubclass(DrumsEnvironmentController, EnvironmentController)
    assert issubclass(DrumsEnvironmentDelegation, EnvironmentDelegation)


def test_real_sync_wrappers_drive_the_drums_controller():
    c = make()
    assert c.delegation.controller is c  # the real base wires the delegation back to the controller
    sid, envs, urls = c.sync_start_session("Ubuntu-modified")
    c.sync_renew_session(sid)
    rc = c.sync_execute_command  # exists on the real base
    assert callable(rc)
    c.sync_end_session(sid)
    c.shutdown()


def test_drums_driver_can_be_registered_into_create_controller():
    import agentrl.worker.environment as env
    original = env.create_controller
    try:
        patch.install()
        d = DrumsEnvironmentDelegation()
        c = env.create_controller("drums", d, backend=FakeBackend(), slots=1, health=lambda ip, **k: True)
        assert isinstance(c, DrumsEnvironmentController)
        with pytest.raises(ValueError):  # the original behaviour for unknown drivers is untouched
            env.create_controller("nope", d)
        assert patch.install() is env.create_controller  # idempotent
    finally:
        env.create_controller = original
