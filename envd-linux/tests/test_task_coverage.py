from envd_linux.l1proto import task_coverage as tc

HAVE = {"chromium", "libreoffice"}


def task(**kw):
    d = {"id": "t", "related_apps": [], "config": [], "evaluator": {"func": "exact_match", "result": {"type": "vm_file"}}, "proxy": False, "fixed_ip": False}
    d.update(kw)
    return d


def test_a_local_task_with_no_downloads_is_a_candidate():
    r = tc.classify(task(config=[{"type": "execute", "parameters": {"command": ["python3", "-c", "pass"]}}]), HAVE)
    assert r["smallest_tier_candidate"] and r["reasons_not_smallest_tier"] == []


def test_downloads_google_chrome_gnome_and_a11y_each_name_their_reason():
    cfg = [{"type": "download", "parameters": {"files": []}}, {"type": "launch", "parameters": {"command": ["google-chrome", "--x"]}},
           {"type": "execute", "parameters": {"command": ["gsettings", "get", "a", "b"]}}]
    r = tc.classify(task(config=cfg, evaluator={"func": "x", "result": {"type": "accessibility_tree"}}), HAVE)
    text = " | ".join(r["reasons_not_smallest_tier"])
    assert not r["smallest_tier_candidate"]
    for needle in ("downloads", "google-chrome", "GNOME", "accessibility"):
        assert needle in text


def test_env_prefix_does_not_hide_the_launched_app():
    r = tc.classify(task(config=[{"type": "launch", "parameters": {"command": "VLC_VERBOSE=-1 vlc x.mp4"}}]), HAVE)
    assert r["launch_apps"] == ["vlc"] and any("vlc" in x for x in r["reasons_not_smallest_tier"])


def test_infeasible_proxy_and_fixed_ip_are_flagged():
    r = tc.classify(task(evaluator={"func": "infeasible"}, proxy=True, fixed_ip=True), HAVE)
    assert len(r["reasons_not_smallest_tier"]) == 3
