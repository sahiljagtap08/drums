"""Validation of POST /action, /launch and /kill bodies. Ported from ActionSpec.swift and
Processes.swift; the rules and messages follow the Swift sources."""

TYPES = {"click", "dblclick", "type", "key", "scroll"}
BUTTONS = {"left", "right"}
MODIFIERS = {"cmd", "shift", "alt", "ctrl"}
MAX_TEXT = 2000

KEY_NAMES = (
    {"return", "tab", "space", "delete", "escape", "left", "right", "down", "up"}
    | set("abcdefghijklmnopqrstuvwxyz0123456789")
    | {f"f{i}" for i in range(1, 13)}
)


class SpecError(ValueError):
    pass


def _finite(v):
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf"))


def decode_action(obj):
    if not isinstance(obj, dict):
        raise SpecError("body must be a JSON object")
    for k in ("gen", "seq"):
        if not isinstance(obj.get(k), int) or isinstance(obj.get(k), bool):
            raise SpecError(f"{k} is required and must be an integer")
    t = obj.get("type")
    if t not in TYPES:
        raise SpecError(f"unknown type {t}")
    for k in ("x", "y", "dx", "dy"):
        if not _finite(obj.get(k)):
            raise SpecError("numbers must be finite")
    if obj.get("button") is not None and obj["button"] not in BUTTONS:
        raise SpecError("button must be left or right")
    for m in obj.get("modifiers") or []:
        if m not in MODIFIERS:
            raise SpecError(f"unknown modifier {m}")
    if t in ("click", "dblclick"):
        x, y = obj.get("x"), obj.get("y")
        if x is None or y is None or x < 0 or y < 0:
            raise SpecError(f"{t} needs x and y, both at least 0")
    elif t == "type":
        text = obj.get("text")
        if not isinstance(text, str) or not text:
            raise SpecError("type needs non-empty text")
        if len(text) > MAX_TEXT:
            raise SpecError(f"text is longer than {MAX_TEXT} characters")
    elif t == "key":
        key = obj.get("key")
        if not isinstance(key, str):
            raise SpecError("key needs key")
        if key.lower() not in KEY_NAMES:
            raise SpecError(f"unknown key {key}")
    elif t == "scroll":
        if obj.get("dx") is None and obj.get("dy") is None:
            raise SpecError("scroll needs dx or dy")
    return obj


def decode_launch(obj):
    if not isinstance(obj, dict):
        raise SpecError("body must be a JSON object")
    if not isinstance(obj.get("gen"), int) or isinstance(obj.get("gen"), bool):
        raise SpecError("gen is required and must be an integer")
    path = obj.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise SpecError("path is required and must be an absolute path")
    args = obj.get("args") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise SpecError("args must be a list of strings")
    env = obj.get("env") or {}
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise SpecError("env must be an object of strings")
    return {"gen": obj["gen"], "path": path, "args": args, "env": env}


def decode_kill(obj):
    if not isinstance(obj, dict):
        raise SpecError("body must be a JSON object")
    if not isinstance(obj.get("gen"), int) or isinstance(obj.get("gen"), bool):
        raise SpecError("gen is required and must be an integer")
    allv = obj.get("all")
    if allv is not None and not isinstance(allv, bool):
        raise SpecError("all must be true or false")
    allv = bool(allv)
    pid = obj.get("pid")
    if pid is not None:
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1 or pid > 2**31 - 1:
            raise SpecError("pid must be an integer above 1")
    if allv and pid is not None:
        raise SpecError("send pid or all, not both")
    if not allv and pid is None:
        raise SpecError("send pid, or all:true")
    return {"gen": obj["gen"], "pid": pid, "all": allv}
