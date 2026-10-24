import os
import sys

from drums_compare import debclosure as dc

IMAGE = {"xserver-common": "2:21.1.4-2ubuntu1.7~22.04.8", "x11-xkb-utils": "7.7+5build4", "libc6": "2.35-0ubuntu3.6", "xfonts-base": "1:1.0.5"}
XVFB = {"Package": "xvfb", "Version": "2:21.1.4-2ubuntu1.7~22.04.8", "Depends": "libc6 (>= 2.34), x11-xkb-utils, xserver-common (>= 2:21.1.4-2ubuntu1.7~22.04.8), xfonts-base | xfonts-75dpi", "Pre-Depends": ""}


def test_a_deb_whose_depends_the_image_meets_passes_with_version_relations_compared():
    assert dc.check(IMAGE, [XVFB])["problems"] == []
    assert dc.parse_depends("a (>= 1) | b:any, c")[0] == [("a", ">=", "1"), ("b", None, None)]


def test_a_newer_requirement_a_missing_package_and_an_image_replacement_are_problems():
    newer = dict(XVFB, Depends="xserver-common (>= 2:21.1.4-2ubuntu1.7~22.04.16)")
    p = dc.check(IMAGE, [newer])["problems"]
    assert len(p) == 1 and "unresolved" in p[0] and "xserver-common=2:21.1.4-2ubuntu1.7~22.04.8" in p[0]
    assert any("libgl1" in x for x in dc.check(IMAGE, [dict(XVFB, Depends="libgl1")])["problems"])
    assert any("would REPLACE" in x for x in dc.check(IMAGE, [dict(XVFB, Package="libc6", Version="2.39")])["problems"])
    same = dc.check(IMAGE, [dict(XVFB, Package="libc6", Version=IMAGE["libc6"], Depends="")])
    assert same["problems"] == [] and any("already in the image" in n for n in same["notes"])


def test_a_dependency_can_be_met_by_another_deb_of_the_set():
    lib = {"Package": "libnew1", "Version": "1.0", "Depends": ""}
    assert dc.check(IMAGE, [dict(XVFB, Depends="libnew1 (>= 1.0)"), lib])["problems"] == []
    assert dc.check(IMAGE, [dict(XVFB, Depends="libnew1 (>= 2.0)"), lib])["problems"]


def test_the_image_manifest_is_read_from_lines_or_json(tmp_path):
    (tmp_path / "a.txt").write_text("libc6=2.35\nfoo=1\n")
    (tmp_path / "b.json").write_text('["libc6=2.35", "foo=1"]')
    assert dc.read_image(str(tmp_path / "a.txt")) == dc.read_image(str(tmp_path / "b.json")) == {"libc6": "2.35", "foo": "1"}
