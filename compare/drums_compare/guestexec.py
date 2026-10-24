"""ONE probe helper for BOTH arms: run a shell script in the guest through the public `/execute` endpoint and raise on ANY fault. The stock guest server's `/run_bash_script` route
can answer 500 (the image-shipped server on the real qcow2 reported `name '_append_event' is not defined`) while `/execute` works; the controller's `run_bash_script` then retried three
times and returned blank output, which a probe could mistake for a clean result. This helper makes a single bounded attempt (no retries, no delay), checks the HTTP status, the JSON
`status` and an integer `returncode`, and never converts a fault into empty output. A non-zero script return code is DATA (several probes rely on `grep`/`kill -0` failing), returned
to the caller, not an error. The endpoint is the pinned OSWorld server's own `/execute` (`getters/general.py:8` posts to it too); no server, checkout or task is modified."""

DEFAULT_TIMEOUT = 30


class GuestExecError(RuntimeError):
    pass


def guest_run(env, script, timeout=DEFAULT_TIMEOUT):
    """Returns {"stdout", "stderr", "returncode"}; raises GuestExecError on a transport error, a non-200, a non-JSON body, a status other than "success" or a missing returncode."""
    ctl = env.controller
    payload = {"command": script, "shell": True}
    try:
        override = getattr(ctl, "post_execute", None)  # a test seam only: a real PythonController has no such attribute
        if override is not None:
            code, body = override(payload, timeout)
        else:
            import requests  # imported here: the OSWorld host venv has it, and a test environment without it must not break on import
            r = requests.post(ctl.http_server + "/execute", json=payload, timeout=timeout)
            code, body = r.status_code, None
            if code == 200:
                body = r.json()
            else:
                body = r.text[:300]
    except GuestExecError:
        raise
    except Exception as e:
        raise GuestExecError(f"/execute request failed: {type(e).__name__}: {str(e)[:200]}") from e
    if code != 200:
        raise GuestExecError(f"/execute answered HTTP {code}: {str(body)[:300]}")
    if not isinstance(body, dict) or body.get("status") != "success":
        raise GuestExecError(f"/execute reported status {body.get('status') if isinstance(body, dict) else type(body).__name__!r}: {str(body)[:300]}")
    rc = body.get("returncode")
    if isinstance(rc, bool) or not isinstance(rc, int):
        raise GuestExecError(f"/execute returned no integer returncode: {str(body)[:300]}")
    return {"stdout": body.get("output") or "", "stderr": body.get("error") or "", "returncode": rc}


def sh_out(env, script, timeout=DEFAULT_TIMEOUT):
    return guest_run(env, script, timeout)["stdout"]


def selftest(run):
    """Proof, per guest, that the helper reads output AND return codes correctly before any gate relies on it. `run(script) -> dict` is `guest_run` bound to one env."""
    res = {}
    a = run("printf ENDPOINT_OK")
    res["stdout_exact"] = a["stdout"] == "ENDPOINT_OK" and a["returncode"] == 0
    b = run("echo oops 1>&2; exit 3")
    res["nonzero_returncode_is_read"] = b["returncode"] == 3 and "oops" in b["stderr"]
    res["pass"] = all(res.values())
    return res
