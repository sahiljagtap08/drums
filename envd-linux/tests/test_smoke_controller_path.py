"""The smoke job builds its controller through the SAME path the trainer's worker uses (create_controller('drums') after install(), or
patch.build when agentrl-worker is absent), not by constructing the class directly."""

import argparse

from envd_linux.scalecua.controller import DrumsEnvironmentController
from envd_linux.scalecua.smoke import make_controller

from test_scalecua_controller import FakeBackend


def args():
    return argparse.Namespace(slots=2, health_timeout=5, acquire_timeout=5, events=None)


def test_smoke_controller_comes_from_the_trainer_path():
    ctl, how = make_controller(args(), FakeBackend())
    try:
        assert isinstance(ctl, DrumsEnvironmentController)
        assert how.startswith("agentrl.worker.environment.create_controller('drums')") or how.startswith("patch.build")
        assert ctl.pool is not None
    finally:
        ctl.shutdown()
