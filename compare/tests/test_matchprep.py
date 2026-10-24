"""Offline manifest comparison, with the ACTUAL VM and Drums facts from the first real run (the package arrays are small synthetic stand-ins; their format is the real one)."""
import json

from drums_compare import matchprep as mp

# actual VM target, as reported by the first real run
VM = {"os_release": 'PRETTY_NAME="Ubuntu 22.04.3 LTS"\nVERSION_ID="22.04"\nVERSION="22.04.3 LTS (Jammy Jellyfish)"\n', "kernel": "6.5.0-15-generic", "desktop": "ubuntu:GNOME\nGNOME Shell 42.9",
      "browser": "Google Chrome 130.0.6723.58", "libreoffice": "LibreOffice 7.3.7.2 30(Build:2)", "guest_user_and_home": "user\n/home/user", "guest_nproc": "4", "guest_memtotal": "MemTotal: 3994372 kB",
      "dpkg": ["python3=3.10.6-1~22.04", "gnome-shell=42.9-0ubuntu2", "mutter=42.9-0ubuntu1", "google-chrome-stable=130.0.6723.58-1", "libreoffice-core=1:7.3.7-0ubuntu0.22.04.10", "dbus=1.12.20-2ubuntu4.1",
               "at-spi2-core=2.44.0-3", "python3-pyatspi=2.38.2-1", "xclip=0.13-2", "sudo=1.9.9-1ubuntu2.4", "bash=5.1-6ubuntu1"]}
# actual Drums rootfs: 24.04, openbox, Chromium 141, LibreOffice 24.2, the host kernel
DRUMS = {"os_release": {"NAME": "Ubuntu", "VERSION_ID": "24.04", "VERSION": "24.04.3 LTS (Noble Numbat)"}, "kernel": "7aws", "browsers": {"/opt/pw-browsers/chromium-1194/chrome-linux/chrome": "Chromium 141.0.7390.37"},
         "dpkg": {"libreoffice-core": "4:24.2.7-0ubuntu0.24.04.7", "openbox": "3.6.1-12build5", "at-spi2-core": "2.52.0-1build1", "dbus": "1.14.10-4ubuntu4.1", "xclip": "0.13-3", "python3": "3.12.3-0ubuntu2.1",
                  "soffice_version_string": "LibreOffice 24.2.7.2 420(Build:2)"}}


def row(rep, name):
    return [a for a in rep["aspects"] if a["aspect"] == name][0]


def test_every_aspect_that_the_first_run_showed_differs_is_reported_with_both_values_and_none_is_equal():
    rep = mp.compare(VM, DRUMS)
    assert rep["verdict"] == "NOT_A_COMPARISON" and not rep["all_aspects_equal"]
    assert row(rep, "os_version_id")["vm"] == "22.04" and row(rep, "os_version_id")["drums"] == "24.04"
    assert row(rep, "kernel")["vm"] == "6.5.0-15-generic" and row(rep, "kernel")["kind"] == "structural" and rep["structural_differences"] == ["kernel"]
    assert "GNOME Shell 42.9" in row(rep, "desktop")["vm"] and row(rep, "desktop")["drums"] == "openbox"
    assert "130.0.6723.58" in row(rep, "browser")["vm"] and "141.0.7390.37" in row(rep, "browser")["drums"]
    assert row(rep, "libreoffice")["vm"].startswith("LibreOffice 7.3.7.2") and row(rep, "libreoffice")["drums"].startswith("LibreOffice 24.2.7.2")
    assert (row(rep, "guest_user")["vm"], row(rep, "guest_home")["vm"], row(rep, "guest_home")["drums"]) == ("user", "/home/user", "/home/drums")
    assert row(rep, "guest_memory")["vm"] == "MemTotal: 3994372 kB" and row(rep, "guest_memory")["kind"] == "budget"
    assert set(rep["differences"]) >= {"os_release", "os_version_id", "kernel", "desktop", "browser", "libreoffice", "guest_user", "guest_home", "guest_memory"}


def test_an_aspect_is_equal_only_when_both_values_exist_and_match():
    same = {"os_release": 'VERSION="22.04.3 LTS"\nVERSION_ID="22.04"', "kernel": "k", "desktop": "openbox", "browser": "B", "libreoffice": "L", "guest_user_and_home": "root\n/home/drums", "dpkg": []}
    d = {"os_release": {"VERSION": "22.04.3 LTS", "VERSION_ID": "22.04"}, "kernel": "k", "browsers": {"x": "B"}, "dpkg": {"soffice_version_string": "L"}}
    rep = mp.compare(same, d)
    assert row(rep, "kernel")["equal"] and row(rep, "browser")["equal"] and row(rep, "libreoffice")["equal"] and row(rep, "desktop")["equal"]
    assert not row(rep, "guest_user")["equal"]  # the Drums side is root by design: "root (sessions run as root)" is a description, not a match
    assert mp.aspects({}, {})[0]["equal"] is False  # missing on both sides is not equal


def test_package_inventory_counts_same_and_different_versions_and_uses_the_real_array_format():
    rep = mp.compare(VM, DRUMS)
    p = rep["packages"]
    assert p["vm_count"] == 11 and p["common"] >= 4 and p["common_different_version"] >= 4 and p["common_same_version"] == 0
    assert {x[0] for x in p["different_sample"]} >= {"python3", "dbus", "xclip"}
    assert mp.parse_dpkg(["a=1", "b=2:3-4", "junk"]) == {"a": "1", "b": "2:3-4"}
    full = mp.compare(VM, DRUMS, ["python3=3.10.6-1~22.04", "bash=5.1-6ubuntu1", "extra=1"])["packages"]
    assert full["common_same_version"] == 2 and full["only_in_drums"] == 1 and full["drums_count"] == 3 and abs(full["same_fraction_of_vm"] - 2 / 11) < 1e-9


def test_drums_inventory_emits_the_image_manifest_format_from_dpkg_query():
    out = mp.drums_inventory(lambda cmd: "zlib1g=1:1.3\nbash=5.2\n\nnot-a-package\n" if cmd[0] == "dpkg-query" and "${Package}=${Version}" in cmd[2] else "")
    assert out == ["bash=5.2", "zlib1g=1:1.3"]


def test_route_report_names_the_real_blockers_from_the_manifests_and_validates_nothing():
    r = mp.route_report(VM, DRUMS)
    b, a = r["route_B_read_only_image_rootfs"], r["route_A_install_on_host"]
    assert b["validated"] is False and a["validated"] is False
    gaps = " ".join(b["gaps"])
    assert "python3.12" in gaps and "3.10.6" in gaps  # the kit venv is built on python 3.12; the image has 3.10
    assert "openbox is NOT in the image" in gaps and "wmctrl is NOT in the image" in gaps and "mutter" in gaps  # the image has no openbox but has mutter
    assert "dbus is NOT in the image" not in gaps and "xclip is NOT in the image" not in gaps  # present in the manifest
    assert any("CRIU" in x for x in b["incompatible_with"]) and any("kernel" in x for x in a["cannot_match"]) and any("kernel" in x for x in b["cannot_match"])
    assert any("old .deb" in x or "UNVERIFIED" in x for x in a["needs"]) and any("superseded" in x for x in a["cannot_match"])
    assert any("ROOT" in x for x in r["identity_decisions_needed"]) and any("network" in x for x in r["identity_decisions_needed"])
    assert r["inventory_basis"].startswith("image dpkg array; Drums side: only the packages composition.py tracks")


def test_a_python_312_image_would_not_raise_the_kit_gap():
    img = dict(VM, dpkg=[x for x in VM["dpkg"] if not x.startswith("python3=")] + ["python3=3.12.3-0ubuntu2"])
    assert not any("python3.12" in g for g in mp.route_report(img, DRUMS)["route_B_read_only_image_rootfs"]["gaps"])


def test_the_cli_prints_the_full_report(tmp_path, capsys):
    (tmp_path / "i.json").write_text(json.dumps(VM))
    (tmp_path / "d.json").write_text(json.dumps(DRUMS))
    mp.main(["--image", str(tmp_path / "i.json"), "--drums", str(tmp_path / "d.json")])
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "NOT_A_COMPARISON" and "routes" in out and out["structural_differences"] == ["kernel"]
