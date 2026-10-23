"""The kit must carry the Apache-2.0 notices and a provenance manifest (no privileges needed)."""

import json
import subprocess

import pytest

from envd_linux import realkit


def fake_checkout(tmp_path, notice=False):
    c = tmp_path / "osworld"
    (c / "desktop_env" / "server").mkdir(parents=True)
    (c / "LICENSE").write_text("Apache License\nVersion 2.0, January 2004\n")
    if notice:
        (c / "NOTICE").write_text("OSWorld\nCopyright the authors\n")
    subprocess.run(["git", "init", "-q", str(c)], check=True)
    subprocess.run(["git", "-C", str(c), "remote", "add", "origin", "https://github.com/xlang-ai/OSWorld"], check=True)
    subprocess.run(["git", "-C", str(c), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    return c


def kit_with_files(tmp_path):
    k = tmp_path / "kit"
    (k / "server").mkdir(parents=True)
    for f in realkit.SERVER_FILES:
        (k / "server" / f).write_text(f"# {f}\n")
    return k


@pytest.mark.parametrize("notice", [False, True])
def test_manifest_and_notices_travel_with_the_copied_files(tmp_path, notice):
    c, k = fake_checkout(tmp_path, notice), kit_with_files(tmp_path)
    m = realkit.write_provenance(str(c), str(k), realkit.SERVER_FILES)
    assert (k / "LICENSE").read_text().startswith("Apache License")
    assert (k / "NOTICE").exists() == notice
    on_disk = json.loads((k / "PROVENANCE.json").read_text())
    assert on_disk == m
    assert m["license"] == "Apache-2.0" and m["source_repo"] == "https://github.com/xlang-ai/OSWorld"
    assert len(m["source_commit"]) == 40 and m["modified"] is False
    assert set(m["files_copied_unmodified"]) == set(realkit.SERVER_FILES)
    assert "BUILD-TIME ONLY" in m["distribution"] and "NOT committed" in m["distribution"]
    assert (m["notice_file"] == "NOTICE") if notice else ("no NOTICE file" in m["notice_note"])


def test_build_refuses_without_a_license(tmp_path):
    c, k = fake_checkout(tmp_path), kit_with_files(tmp_path)
    (c / "LICENSE").unlink()
    with pytest.raises(SystemExit):
        realkit.write_provenance(str(c), str(k), realkit.SERVER_FILES)
