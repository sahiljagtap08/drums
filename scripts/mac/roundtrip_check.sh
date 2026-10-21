#!/usr/bin/env bash
# Manual check of the envd routes that need a real desktop session.
#
#   bash scripts/mac/roundtrip_check.sh PORT
#
# envd must already be running on 127.0.0.1:PORT. Two checks run:
#   1. Typing: launch TextEdit on a temp file, find its window, click the
#      middle, type the token alphabet, read the text back through /axtext.
#      This needs Accessibility (and Screen Recording for window titles).
#   2. Pasteboard: set "PB-test", read it, clear it, read again.
# Each check prints PASS or FAIL. The script exits 1 if any check failed.
#
# curl sends "Host: 127.0.0.1:PORT" and no Origin header, which is what
# envd's local-only guard accepts.

set -euo pipefail

if [ $# -ne 1 ] || ! [[ "$1" =~ ^[0-9]+$ ]]; then
  echo "usage: $0 PORT" >&2
  exit 2
fi
PORT="$1"
BASE="http://127.0.0.1:${PORT}"
# Apple apps such as TextEdit cannot be started by running their binary: macOS
# kills them (launch constraints). So /launch runs /usr/bin/open, which starts
# TextEdit through Launch Services and then exits. The pid /launch returns is
# open's, so TextEdit's own pid is read from its window in /windows.
OPEN="/usr/bin/open"
# Every character a rollout token can contain: D, G, R, digits, '-' and a-f.
ALPHABET="D07-G12-R0345-0123456789abcdef"

FAILED=0
HTTP_CODE=000
BODY=""
SEQ=0
GEN=0
WORK="$(mktemp -d "${TMPDIR:-/tmp}/envd-roundtrip.XXXXXX")"
APP_PID=""

fail() { echo "FAIL $*"; FAILED=1; }
pass() { echo "PASS $*"; }

# call METHOD PATH [JSON_BODY]: sets HTTP_CODE and BODY. A refused connection
# gives HTTP_CODE 000 instead of ending the script.
call() {
  local out
  out="$(mktemp "$WORK/resp.XXXXXX")"
  if [ $# -ge 3 ]; then
    HTTP_CODE="$(curl -sS --max-time 15 -o "$out" -w '%{http_code}' -X "$1" \
      -H 'Content-Type: application/json' --data "$3" "${BASE}$2" 2>/dev/null)" || HTTP_CODE=000
  else
    HTTP_CODE="$(curl -sS --max-time 15 -o "$out" -w '%{http_code}' -X "$1" "${BASE}$2" 2>/dev/null)" || HTTP_CODE=000
  fi
  BODY="$(cat "$out")"
  rm -f "$out"
}

# jfield KEY: print one top-level field of $BODY. Strings print raw; null,
# numbers and booleans print as JSON. A bad body prints "?".
jfield() {
  printf '%s' "$BODY" | python3 -c '
import json, sys
try:
    v = json.load(sys.stdin)[sys.argv[1]]
except Exception:
    print("?"); sys.exit(0)
print(v if isinstance(v, str) else json.dumps(v))
' "$1"
}

# json_str VALUE: VALUE as a JSON string literal.
json_str() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }

cleanup() {
  if [ -n "$APP_PID" ]; then
    call POST /kill "{\"gen\":${GEN},\"pid\":${APP_PID}}" || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

action() { # JSON fields after the seq, e.g. '"type":"click","x":1,"y":2'
  SEQ=$((SEQ + 1))
  call POST /action "{\"gen\":${GEN},\"seq\":${SEQ},$1}"
}

# --- setup ---------------------------------------------------------------

call GET /health
if [ "$HTTP_CODE" != "200" ]; then
  echo "FAIL cannot reach envd on port ${PORT} (GET /health gave HTTP ${HTTP_CODE})"
  exit 1
fi
GEN="$(jfield gen)"
echo "envd up: gen=${GEN} ax_trusted=$(jfield ax_trusted) screen_recording=$(jfield screen_recording)"

# --- check 1: typing round trip in TextEdit ------------------------------

typing_check() {
  local file="$WORK/roundtrip.txt"
  : > "$file"

  call POST /launch "{\"gen\":${GEN},\"path\":$(json_str "$OPEN"),\"args\":[\"-a\",\"TextEdit\",$(json_str "$file")]}"
  if [ "$HTTP_CODE" != "200" ]; then fail "typing: POST /launch gave HTTP ${HTTP_CODE}: ${BODY}"; return; fi

  # Wait up to 20 s for a real TextEdit window. Its pid is TextEdit's own.
  local center="" i
  for i in $(seq 1 40); do
    call GET /windows
    if [ "$HTTP_CODE" != "200" ]; then fail "typing: GET /windows gave HTTP ${HTTP_CODE}: ${BODY}"; return; fi
    center="$(printf '%s' "$BODY" | python3 -c '
import json, sys
try:
    wins = json.load(sys.stdin)
except Exception:
    sys.exit(0)
best = None
for w in wins:
    b = w["bounds"]
    if w["app"] == "TextEdit" and w.get("layer", 0) == 0 and b["w"] > 50 and b["h"] > 50:
        if best is None or b["w"] * b["h"] > best[0]["w"] * best[0]["h"]:
            best = (b, w["pid"])
if best:
    b, pid = best
    print(int(b["x"] + b["w"] / 2), int(b["y"] + b["h"] / 2), pid)
')"
    [ -n "$center" ] && break
    sleep 0.5
  done
  if [ -z "$center" ]; then fail "typing: no TextEdit window after 20 s"; return; fi
  local cx cy
  read -r cx cy APP_PID <<< "$center"
  echo "TextEdit pid=${APP_PID} window center=(${cx},${cy})"

  action "\"type\":\"click\",\"x\":${cx},\"y\":${cy}"
  if [ "$HTTP_CODE" != "200" ]; then fail "typing: click gave HTTP ${HTTP_CODE}: ${BODY}"; return; fi
  sleep 0.5
  action "\"type\":\"type\",\"text\":$(json_str "$ALPHABET")"
  if [ "$HTTP_CODE" != "200" ]; then fail "typing: type gave HTTP ${HTTP_CODE}: ${BODY}"; return; fi

  # Wait up to 10 s for the text to show up.
  local got="" reason=""
  for i in $(seq 1 20); do
    call GET "/axtext?pid=${APP_PID}"
    if [ "$HTTP_CODE" != "200" ]; then fail "typing: GET /axtext gave HTTP ${HTTP_CODE}: ${BODY}"; return; fi
    got="$(jfield text)"
    reason="$(jfield reason)"
    [ "$got" = "$ALPHABET" ] && break
    sleep 0.5
  done
  if [ "$got" = "$ALPHABET" ]; then
    pass "typing round trip: AX text matches exactly (${ALPHABET})"
  else
    fail "typing round trip: expected '${ALPHABET}' but AX text was '${got}' (reason: ${reason}). Accessibility must be granted to the app that runs envd."
  fi
}

# --- check 2: pasteboard set / read / clear / read -----------------------

pasteboard_check() {
  call POST /pb "{\"gen\":${GEN},\"text\":\"PB-test\"}"
  if [ "$HTTP_CODE" != "200" ]; then fail "pasteboard: POST /pb gave HTTP ${HTTP_CODE}: ${BODY}"; return; fi
  call GET /pb
  if [ "$HTTP_CODE" != "200" ]; then fail "pasteboard: GET /pb gave HTTP ${HTTP_CODE}: ${BODY}"; return; fi
  local first_text first_hash
  first_text="$(jfield text)"
  first_hash="$(jfield hash)"

  call DELETE "/pb?gen=${GEN}"
  if [ "$HTTP_CODE" != "200" ]; then fail "pasteboard: DELETE /pb gave HTTP ${HTTP_CODE}: ${BODY}"; return; fi
  call GET /pb
  if [ "$HTTP_CODE" != "200" ]; then fail "pasteboard: second GET /pb gave HTTP ${HTTP_CODE}: ${BODY}"; return; fi
  local second_text second_hash
  second_text="$(jfield text)"
  second_hash="$(jfield hash)"

  # SHA-256 of "PB-test", worked out here and not by envd.
  local want_hash
  want_hash="$(printf 'PB-test' | shasum -a 256 | cut -d' ' -f1)"
  if [ "$first_text" = "PB-test" ] && [ "$first_hash" = "$want_hash" ] \
     && [ "$second_hash" = "empty" ] && [ "$second_text" = "null" ]; then
    pass "pasteboard: set -> 'PB-test', clear -> 'empty'"
  else
    fail "pasteboard: got (${first_text}, ${first_hash}) then (${second_text}, ${second_hash}); wanted (PB-test, ${want_hash}) then (null, empty)"
  fi
}

typing_check
pasteboard_check

if [ "$FAILED" -eq 0 ]; then echo "ALL PASS"; else echo "SOME CHECKS FAILED"; fi
exit "$FAILED"
