"""home and base_root parameters of the Supervisor: defaults unchanged, refusals before any side effect, and the lower-layer composition. No mounts, no image."""
import os

import pytest

from envd_linux import session as S
from envd_linux import session_init as SI


def sup(tmp_path, **kw):
    kw.setdefault("sweep_on_start", False)
    return S.Supervisor(str(tmp_path / "state"), str(tmp_path / "tpl"), **kw)


def test_defaults_are_exactly_the_old_behaviour(tmp_path):
    s = sup(tmp_path)
    assert (s.home, s.base_root) == ("/home/drums", "/") and S.HOME == "/home/drums"
    assert SI.compose_lower("/t", "/") == "/t:/" and SI.compose_lower("", "/") == "/" and SI.compose_lower("", "/img") == "/img"


def test_a_home_is_accepted_when_normalized_and_absolute_and_refused_otherwise(tmp_path):
    assert sup(tmp_path, home="/home/user").home == "/home/user"
    for bad in ("home/user", "/", "/home/../root", "/home//user", "", None, 5):
        with pytest.raises(ValueError, match="home must be"):
            sup(tmp_path, home=bad)


def test_the_composition_puts_the_template_over_the_chosen_base_root():
    assert SI.compose_lower("/tplfs", "/mnt/image-ro") == "/tplfs:/mnt/image-ro"


def test_another_base_root_needs_full_mode_an_existing_directory_and_no_crio_or_nesting(tmp_path):
    img = tmp_path / "image-root"
    img.mkdir()
    # constructing full mode would mount the template; every refusal below must stop BEFORE that
    with pytest.raises(ValueError, match="needs root_mode='full'"):
        sup(tmp_path, root_mode="home", base_root=str(img))
    with pytest.raises(ValueError, match="incompatible with CRIU"):
        sup(tmp_path, root_mode="full", base_root=str(img), criu_friendly=True)
    with pytest.raises(ValueError, match="incompatible with CRIU"):
        sup(tmp_path, root_mode="full", base_root=str(img), restore_from=object())
    with pytest.raises(ValueError, match="not a directory"):
        sup(tmp_path, root_mode="full", base_root=str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="must not contain one another"):
        S.Supervisor(str(img / "state"), str(tmp_path / "tpl"), root_mode="full", base_root=str(img), sweep_on_start=False)
    with pytest.raises(ValueError, match="normalized absolute"):
        sup(tmp_path, root_mode="full", base_root="relative/path")
    with pytest.raises(ValueError, match="normalized absolute"):
        sup(tmp_path, root_mode="full", base_root=str(img) + "/../image-root")
    assert not os.path.exists(tmp_path / "state" / "tplfs")  # nothing was mounted or created by any refused construction


def test_the_session_command_line_passes_the_home_and_the_base_root_to_the_init():
    src = open(S.__file__).read()
    assert '"--home", self.home, "--base-root", self.base_root' in src
    assert 'info = {"epoch": self._epochs[slot], "display": n, "init_pid": kids[0], "home": self.home' in src
    assert 'if self.base_root == "/":' in src and "os.makedirs(self.home, exist_ok=True)" in src
    assert '"--base-root"' in open(SI.__file__).read() and "compose_lower(a.lower, a.base_root)" in open(SI.__file__).read()
