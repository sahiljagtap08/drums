"""Black-box protocol conformance checks for an envd-style guest daemon.

    python -m drums_compare.conformance PORT

Any daemon that claims to speak the envd vocabulary (envd-linux, and the macOS envd it is specified from)
must pass these before it is used in a comparison, so both sides give the harness the same
observations and refuse the same bad or stale requests. Expectations come from reading the
Swift sources (Routes.swift, ActionHandler.swift, LocalGuard.swift, ActionSpec.swift); they
have been run only against a stub in the tests, never against a real envd.

The checks mutate the daemon's generation (they set it to 9000+ values), so run them on an
idle daemon, not during a measured run.
"""

import http.client
import json
import sys


def _call(port, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": host or f"127.0.0.1:{port}"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=h)
    r = conn.getresponse()
    raw = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    conn.close()
    return r.status, hdrs, raw


def run_conformance(port):
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), "" if ok else detail))

    st, _, raw = _call(port, "GET", "/health")
    health = json.loads(raw) if st == 200 else {}
    check("health 200", st == 200, st)
    for key in ("env", "uid", "gen", "screen", "version", "ax_trusted", "screen_recording"):
        check(f"health has {key}", key in health, sorted(health))
    check("health screen has w,h,scale", all(k in health.get("screen", {}) for k in ("w", "h", "scale")))

    st, _, raw = _call(port, "POST", "/gen", {"gen": 9001})
    check("set gen", st == 200 and json.loads(raw).get("gen") == 9001, (st, raw))
    st, _, _ = _call(port, "POST", "/gen", {"gen": "x"})
    check("gen rejects non-integer", st == 400, st)

    st, hdrs, png = _call(port, "GET", "/screenshot")
    check("screenshot 200 png", st == 200 and png[:8] == b"\x89PNG\r\n\x1a\n", st)
    check("screenshot gen header", hdrs.get("x-drums-gen") == "9001", hdrs.get("x-drums-gen"))
    for h in ("x-drums-captured-at", "x-drums-stale", "x-drums-black"):
        check(f"screenshot header {h}", h in hdrs)
    st, _, _ = _call(port, "GET", "/screenshot?region=0,0,-1,10")
    check("screenshot rejects bad region", st == 400, st)

    click = {"gen": 9001, "seq": 1, "type": "click", "x": 5, "y": 5}
    st, _, raw = _call(port, "POST", "/action", click)
    check("valid action accepted", st == 200 and "applied_at" in json.loads(raw), (st, raw))
    st, _, raw = _call(port, "POST", "/action", {**click, "gen": 9000, "seq": 2})
    check("stale gen action refused with 409", st == 409 and json.loads(raw).get("error") == "stale_gen", (st, raw))
    check("409 names current_gen", st == 409 and json.loads(raw).get("current_gen") == 9001)
    for name, bad in {"unknown type": {"type": "teleport"}, "click without x,y": {"type": "click"},
                      "type without text": {"type": "type"}, "unknown key": {"type": "key", "key": "nope"},
                      "scroll without delta": {"type": "scroll"}}.items():
        st, _, _ = _call(port, "POST", "/action", {"gen": 9001, "seq": 3, **bad})
        check(f"action rejects {name}", st == 400, st)

    st, _, raw = _call(port, "GET", "/health", headers={"Origin": "http://evil.example"})
    check("Origin header refused", st == 403 and json.loads(raw).get("error") == "forbidden_origin", (st, raw))
    st, _, raw = _call(port, "GET", "/health", host="evil.example")
    check("foreign Host refused", st == 403 and json.loads(raw).get("error") == "forbidden_host", (st, raw))
    st, _, raw = _call(port, "GET", "/screenshot", host="evil.example")
    check("guard covers reads", st == 403, st)
    st, _, _ = _call(port, "GET", "/no-such-route")
    check("unknown path 404", st == 404, st)
    st, _, _ = _call(port, "GET", "/action")
    check("wrong method 405", st == 405, st)

    st, _, raw = _call(port, "POST", "/kill", {"gen": 9000, "all": True})
    check("kill with stale gen refused", st == 409, st)
    st, _, raw = _call(port, "POST", "/launch", {"gen": 9001, "path": "relative/path"})
    check("launch rejects relative path", st == 400, st)
    return results


def main(argv=None):
    argv = argv or sys.argv[1:]
    res = run_conformance(int(argv[0]))
    for name, ok, detail in res:
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    return 0 if all(ok for _, ok, _ in res) else 1


if __name__ == "__main__":
    sys.exit(main())
