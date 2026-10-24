"""The shared guest probe helper: faults raise, output and return codes are read exactly, there is no retry, and the pgrep probe cannot match its own shell."""
import os
import shutil
import subprocess
import sys
import time
import types

import pytest

from drums_compare import guestexec as ge
from drums_compare import host_run as hr
from drums_compare import osworld_matched as om


class Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code, self._body, self.text = code, body, text

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class Env:
    """A DesktopEnv stand-in whose controller has only what a real PythonController has: http_server."""
    controller = types.SimpleNamespace(http_server="http://guest.test:5000")


@pytest.fixture
def fake_requests(monkeypatch):
    calls = []
    box = {"reply": Resp(200, {"status": "success", "output": "ENDPOINT_OK", "error": "", "returncode": 0})}

    def post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        r = box["reply"]
        if isinstance(r, Exception):
            raise r
        return r
    mod = types.ModuleType("requests")
    mod.post = post
    monkeypatch.setitem(sys.modules, "requests", mod)
    return calls, box


def test_a_success_reads_stdout_exactly_through_execute_with_shell_true_one_attempt_and_a_bounded_timeout(fake_requests):
    calls, _ = fake_requests
    r = ge.guest_run(Env(), "printf ENDPOINT_OK", timeout=7)
    assert r == {"stdout": "ENDPOINT_OK", "stderr": "", "returncode": 0}
    assert calls == [{"url": "http://guest.test:5000/execute", "json": {"command": "printf ENDPOINT_OK", "shell": True}, "timeout": 7}]
    assert ge.sh_out(Env(), "printf ENDPOINT_OK") == "ENDPOINT_OK" and calls[-1]["timeout"] == ge.DEFAULT_TIMEOUT == 30


def test_the_observed_500_from_the_image_server_raises_instead_of_returning_blank_output(fake_requests):
    calls, box = fake_requests
    box["reply"] = Resp(500, text="name '_append_event' is not defined")
    with pytest.raises(ge.GuestExecError, match=r"HTTP 500.*_append_event"):
        ge.sh_out(Env(), "echo hi")
    assert len(calls) == 1  # one attempt: no retry, no delay, no fallthrough


@pytest.mark.parametrize("reply,match", [
    (Resp(200, {"status": "error", "output": "", "error": "boom"}), "status"),
    (Resp(200, {"output": "x", "returncode": 0}), "status"),
    (Resp(200, "not a dict"), "status"),
    (Resp(200, ValueError("bad json")), "request failed"),
    (Resp(200, {"status": "success", "output": "x"}), "returncode"),
    (Resp(200, {"status": "success", "output": "x", "returncode": "0"}), "returncode"),
    (Resp(200, {"status": "success", "output": "x", "returncode": True}), "returncode"),
    (TimeoutError("read timed out"), "request failed"),
    (ConnectionError("refused"), "request failed"),
])
def test_every_kind_of_fault_raises(fake_requests, reply, match):
    calls, box = fake_requests
    box["reply"] = reply
    with pytest.raises(ge.GuestExecError, match=match):
        ge.guest_run(Env(), "echo hi")
    assert len(calls) == 1


def test_a_nonzero_script_return_code_is_data_not_an_error_and_empty_stdout_from_a_success_is_legitimate(fake_requests):
    _, box = fake_requests
    box["reply"] = Resp(200, {"status": "success", "output": "", "error": "no match", "returncode": 1})
    assert ge.guest_run(Env(), "grep -q x /nonexistent") == {"stdout": "", "stderr": "no match", "returncode": 1}
    assert ge.sh_out(Env(), "grep -q x /nonexistent") == ""


def test_the_selftest_passes_on_a_faithful_guest_and_fails_on_one_that_hides_output_or_codes():
    def faithful(script):
        return {"stdout": "ENDPOINT_OK", "stderr": "", "returncode": 0} if script.startswith("printf") else {"stdout": "", "stderr": "oops\\n".replace("\\n", "\n"), "returncode": 3}
    assert ge.selftest(faithful)["pass"]
    assert not ge.selftest(lambda s: {"stdout": "", "stderr": "", "returncode": 0})["pass"]  # blank output everywhere: the false-pass case
    assert not ge.selftest(lambda s: {"stdout": "ENDPOINT_OK", "stderr": "", "returncode": 0})["pass"]  # return codes ignored


def test_the_canary_probe_now_raises_on_a_500_and_becomes_a_counted_canary_fault_not_a_clean_pass():
    class Bad:
        def __init__(self):
            self.controller = types.SimpleNamespace(http_server="http://x", post_execute=lambda payload, timeout: (500, "name '_append_event' is not defined"))
    with pytest.raises(ge.GuestExecError):
        om._desktop_canary_probe(Bad())
    r = types.SimpleNamespace(canary="", canary_seen=None, arm="a", seq=0, worker=0)
    lease = om._DesktopEnvLease(Bad())
    with pytest.raises(om.AdapterFault) as e:
        om._canary(lease, r)
    assert e.value.kind == "canary_failed"


def test_neither_the_gates_nor_the_canary_call_the_controllers_run_bash_script_any_more():
    for f in ("stages.py", "osworld_matched.py", "host_run.py"):
        code = open(os.path.join(os.path.dirname(om.__file__), f)).read()
        assert "run_bash_script(" not in code.replace("`run_bash_script`", ""), f


# ---- the pgrep self-match ----
@pytest.mark.skipif(shutil.which("pgrep") is None, reason="pgrep needed")
def test_pgrep_dash_f_matches_its_own_shell_and_pgrep_dash_x_does_not(tmp_path):
    exe = tmp_path / "soffice.bin"
    shutil.copy(shutil.which("sleep"), exe)

    def probe(cmd):
        return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=10).stdout.strip()
    old = "pgrep -f soffice.bin | head -1"
    new = "pgrep -x soffice.bin | head -1"
    assert probe(old) != ""  # with NO such process running, the old probe still finds its own shell: a false "Writer still running"
    assert probe(new) == ""  # the fixed probe reports none, correctly
    p = subprocess.Popen([str(exe), "30"])
    try:
        time.sleep(0.3)
        assert probe(new) == str(p.pid)  # and it does find a real process of that name
    finally:
        p.kill()
        p.wait()


def test_the_gates_use_the_exact_name_form_of_pgrep_everywhere():
    code = open(hr.__file__).read()
    assert "pgrep -f soffice.bin" not in code and code.count("pgrep -x soffice.bin") == 2 and code.count('live_pids(sh_out, "soffice.bin")') == 2  # the cold gate's before/after use the state-based exact-name scan


def test_skipping_the_image_hash_needs_an_owner_statement_and_binds_the_file_stat(tmp_path):
    sys.path.insert(0, os.path.dirname(__file__))
    import test_host_run as th
    cfg, deps = th.setup(tmp_path, incumbent="docker_kvm", cfg={"verify_sha256": False})
    r = hr.check_all(cfg, deps)
    assert "qcow2_sha256_matches_record" in {c["name"] for c in r["checks"] if not c["ok"]}  # no statement: refused
    cfg["qcow2_sha256_verified_earlier"] = ["14:12 sha256 verified", "14:31 sha256 verified"]
    r = hr.check_all(cfg, deps)
    c = [c for c in r["checks"] if c["name"] == "qcow2_sha256_matches_record"][0]
    assert c["ok"] and "NOT RECOMPUTED" in c["detail"] and r["inputs"]["qcow2_stat"][0] == 4
    cfg["qcow2_bytes"] = 5  # the size check still runs
    assert "qcow2_size_matches_record" in {c["name"] for c in hr.check_all(cfg, deps)["checks"] if not c["ok"]}
