"""Offline manifest comparison, with the ACTUAL VM and Drums facts from the first real run (the package arrays are small synthetic stand-ins; their format is the real one)."""
import json

from drums_compare import matchprep as mp

# actual VM target, as reported by the first real run
VM = {"os_release": 'PRETTY_NAME="Ubuntu 22.04.3 LTS"\nVERSION_ID="22.04"\nVERSION="22.04.3 LTS (Jammy Jellyfish)"\n', "kernel": "6.5.0-15-generic", "desktop": "ubuntu:GNOME\nGNOME Shell 42.9",
      "browser": "Google Chrome 130.0.6723.58", "libreoffice": "LibreOffice 7.3.7.2 30(Build:2)", "guest_user_and_home": "user\n/home/user", "guest_nproc": "4", "guest_memtotal": "MemTotal: 3994372 kB",
      # The real image has 1932 packages. THIS LIST IS A SYNTHETIC STAND-IN except for what the independent review read from the real manifest: wmctrl 1.07-7build1 PRESENT; openbox and xclip ABSENT;
      # no package named mutter, but gir1.2-mutter-10, libmutter-10-0 and mutter-common 42.9-0ubuntu4 PRESENT; gnome-shell 42.9-0ubuntu2; Chrome 130.0.6723.58-1; LibreOffice 1:7.3.7-0ubuntu0.22.04.4; Python 3.10.6-1~22.04.
      "dpkg": ["python3=3.10.6-1~22.04", "gnome-shell=42.9-0ubuntu2", "gir1.2-mutter-10=42.9-0ubuntu4", "libmutter-10-0=42.9-0ubuntu4", "mutter-common=42.9-0ubuntu4", "wmctrl=1.07-7build1",
               "google-chrome-stable=130.0.6723.58-1", "libreoffice-core=1:7.3.7-0ubuntu0.22.04.4", "dbus=1.12.20-2ubuntu4.1", "dbus-daemon=1.12.20-2ubuntu4.1", "at-spi2-core=2.44.0-3",
               "python3-pyatspi=2.38.2-1", "sudo=1.9.9-1ubuntu2.4", "bash=5.1-6ubuntu1"]}
# actual Drums rootfs: 24.04, openbox, Chromium 141, LibreOffice 24.2, the host kernel; its manifest tracks ONLY 10 packages (this stand-in lists 7 of them)
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


def test_with_only_tracked_drums_packages_the_package_comparison_is_labelled_partial_and_gives_no_fraction():
    p = mp.compare(VM, DRUMS)["packages"]
    assert p["complete_inventory"] is False and "NOT a full inventory comparison" in p["note"] and p["same_fraction_of_vm"] is None
    assert p["vm_count"] == 14 and p["common"] >= 4 and p["common_same_version"] == 0 and p["common_different_version"] >= 4  # every common tracked package differs in version
    assert {x[0] for x in p["different_sample"]} >= {"python3", "dbus", "libreoffice-core"} and mp.parse_dpkg(["a=1", "b=2:3-4", "junk"]) == {"a": "1", "b": "2:3-4"}


def test_with_a_full_drums_list_the_comparison_is_complete_and_has_a_fraction():
    full = mp.compare(VM, DRUMS, ["python3=3.10.6-1~22.04", "bash=5.1-6ubuntu1", "extra=1"])["packages"]
    assert full["complete_inventory"] is True and full["note"] is None and full["common_same_version"] == 2 and full["only_in_drums"] == 1
    assert abs(full["same_fraction_of_vm"] - 2 / 14) < 1e-9


def test_drums_inventory_emits_the_image_manifest_format_from_dpkg_query():
    out = mp.drums_inventory(lambda cmd: "zlib1g=1:1.3\nbash=5.2\n\nnot-a-package\n" if cmd[0] == "dpkg-query" and "${Package}=${Version}" in cmd[2] else "")
    assert out == ["bash=5.2", "zlib1g=1:1.3"]


SERVER = "import subprocess\ndef f():\n    subprocess.run(['wmctrl', '-l'])\n    subprocess.run(['xclip', '-o'])\n"


def test_route_report_reads_the_launched_programs_from_the_code_and_attributes_nothing_it_did_not_derive():
    r = mp.route_report(VM, DRUMS, session_source=None, server_source=SERVER)
    launched = r["session_launched_programs"]
    assert set(launched) == {"/usr/libexec/at-spi-bus-launcher", "dbus-daemon", "openbox"}  # exactly what start_real_desktop starts
    assert launched["openbox"] == "ABSENT from the image" and launched["dbus-daemon"]["package"] == "dbus-daemon" and launched["/usr/libexec/at-spi-bus-launcher"]["package"] == "at-spi2-core"
    assert "xclip" not in launched and "wmctrl" not in launched  # not launched by the session: no longer attributed to start_real_desktop
    srv = r["pinned_server_programs"]
    assert srv["wmctrl"] == {"package": "wmctrl", "version": "1.07-7build1"} and srv["xclip"] == "ABSENT from the image"  # wmctrl is PRESENT in the real guest; xclip is absent
    gaps = " ".join(r["route_B_read_only_image_rootfs"]["gaps"])
    assert "openbox, which the session starts after the pivot, is ABSENT" in gaps and "(per task) the pinned server runs xclip" in gaps
    assert "wmctrl" not in gaps and "(per task) the pinned server runs wmctrl" not in gaps  # present, so no gap is claimed
    assert "python3 3.10.6" in gaps and "python3.12" in gaps and "drums_template_overlay" in gaps


def test_gnome_is_not_declared_available_or_unavailable_from_the_absence_of_a_mutter_package():
    r = mp.route_report(VM, DRUMS)
    assert r["image_package_facts"]["mutter"] is None and r["image_package_facts"]["gir1.2-mutter-10"] == "42.9-0ubuntu4" and r["image_package_facts"]["mutter-common"] == "42.9-0ubuntu4"
    assert "gnome-shell 42.9-0ubuntu2 is installed" in r["gnome"] and "UNVALIDATED" in r["gnome"] and "not concluded from package names" in r["gnome"]
    assert "mutter could replace openbox" not in json.dumps(r) and "no panel or hotkeys" not in json.dumps(r)


def test_validated_nothing_and_identity_and_privileges_are_not_widened_or_declared_matched():
    r = mp.route_report(VM, DRUMS)
    assert r["route_A_install_on_host"]["validated"] is False and r["route_B_read_only_image_rootfs"]["validated"] is False
    assert any("ROOT" in x and "does NOT declare identity or GNOME matched" in x for x in r["identity_decisions_needed"])
    assert r["inventory_basis"].endswith("this is not a full inventory comparison") and "ONLY the packages composition.py tracks" in r["inventory_basis"]
    assert any("CRIU" in x for x in r["route_B_read_only_image_rootfs"]["incompatible_with"]) and any("superseded" in x for x in r["route_A_install_on_host"]["cannot_match"])


def test_a_python_312_image_with_every_launched_program_reports_no_python_or_program_gap():
    img = dict(VM, dpkg=[x for x in VM["dpkg"] if not x.startswith("python3=")] + ["python3=3.12.3-0ubuntu2", "openbox=3.6.1-10"])
    gaps = " ".join(mp.route_report(img, DRUMS)["route_B_read_only_image_rootfs"]["gaps"])
    assert "python3.12" not in gaps and "ABSENT" not in gaps and "drums_template_overlay" in gaps  # the kit-visibility gap remains: it is about the HOST path, not the python version


def test_the_cli_prints_the_full_report(tmp_path, capsys):
    (tmp_path / "i.json").write_text(json.dumps(VM))
    (tmp_path / "d.json").write_text(json.dumps(DRUMS))
    mp.main(["--image", str(tmp_path / "i.json"), "--drums", str(tmp_path / "d.json")])
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "NOT_A_COMPARISON" and "routes" in out and out["structural_differences"] == ["kernel"] and out["packages"]["complete_inventory"] is False
