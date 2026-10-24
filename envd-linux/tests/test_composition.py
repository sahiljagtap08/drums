from envd_linux import composition


def test_manifest_has_the_pinned_sections_and_the_diff_lists_every_difference():
    m = composition.build()
    assert m["format"] == 1 and {"os_release", "kernel", "dpkg", "browsers", "python", "guest_server", "desktop_env"} <= set(m)
    other = {**m, "kernel": "other", "dpkg": {**m["dpkg"], "xvfb": "0.0"}, "browsers": {"/x/chrome": "1"}}
    d = composition.diff(m, other)
    assert any(x.startswith("/kernel:") for x in d) and any(x.startswith("/dpkg/xvfb:") for x in d) and any("/x/chrome" in x for x in d)
    assert composition.diff(m, m) == []


def test_diff_treats_lists_as_sets_and_a_missing_key_as_a_difference():
    assert composition.diff({"p": ["a==1", "b==2"]}, {"p": ["b==2", "a==1"]}) == []
    assert composition.diff({"p": ["a==1"]}, {"p": ["a==2"]}) == ['/p: only in A: "a==1"', '/p: only in B: "a==2"']
    assert composition.diff({"k": 1}, {}) == ["/k: 1 != None"]
