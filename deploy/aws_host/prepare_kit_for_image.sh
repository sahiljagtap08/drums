#!/usr/bin/env bash
# Build the OSWorld server kit for an IMAGE ROOT's own Python (3.10 on the 22.04.3 image), into a writable template overlay, never touching the read-only root and never mapping the host's
# python 3.12. Runs the image's /usr/bin/python3 and pip inside a chroot over overlay(lower=image root, upper=OVERLAY/upper). Run AS ROOT by the owner; it needs network for pip. The dependency
# list is realkit.PIP, UNPINNED (as realkit builds it); the versions actually installed are recorded in the kit as pip-freeze.txt. Licence and PROVENANCE.json come from realkit.write_provenance.
# PROTOTYPE: unrun against a real image. Nothing is installed on the host and the lower root is never written.
# The image has NO ensurepip (python3.10-venv is not installed; a real boot attempt failed there), and nothing may be apt-installed. So the venv is made WITHOUT pip and pip is seeded from ONE
# owner-supplied pip wheel whose sha256 is checked before anything is mounted; the wheel is run from its own zip (no apt, no systemd, no host python 3.12) and its name, size and hash are recorded.
#   sudo bash deploy/aws_host/prepare_kit_for_image.sh /mnt/vm-image-ro /opt/OSWorld-checkout /opt/drums-overlay /opt/seed/pip-X-py3-none-any.whl <sha256-of-that-wheel>
set -euo pipefail
die() { echo "REFUSED: $*" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || die "run as root"
ROOT=${1:-}; SRC=${2:-}; OV=${3:-}; WHL=${4:-}; WSHA=${5:-}
[ -n "$ROOT" ] && [ -n "$SRC" ] && [ -n "$OV" ] && [ -n "$WHL" ] && [ -n "$WSHA" ] || die "usage: prepare_kit_for_image.sh IMAGE_ROOT OSWORLD_CHECKOUT OVERLAY_DIR PIP_WHEEL PIP_WHEEL_SHA256"
for p in "$ROOT" "$SRC" "$OV" "$WHL"; do [[ "$p" =~ ^/[A-Za-z0-9_./-]+$ ]] && [ "$(readlink -f -- "$p")" = "$p" ] || die "paths must be absolute, canonical and use only safe characters ($p)"; done
[[ "$WSHA" =~ ^[0-9a-f]{64}$ ]] || die "the pip wheel sha256 must be 64 lowercase hex characters"
[[ "$WHL" =~ /pip-[0-9][A-Za-z0-9.]*-py3-none-any\.whl$ ]] && [ -f "$WHL" ] || die "the seed must be an existing pip-<version>-py3-none-any.whl"
[ "$(sha256sum "$WHL" | cut -d' ' -f1)" = "$WSHA" ] || die "the pip wheel's sha256 does not match the expected value"
[ -d "$ROOT/etc" ] && [ -x "$ROOT/usr/bin/python3" ] || die "$ROOT has no /usr/bin/python3: not the image root"
[ -f "$ROOT/usr/lib/python3/dist-packages/pyatspi/__init__.py" ] || die "the image has no pyatspi (python3-pyatspi): the kit's accessibility needs it"
[ -f "$SRC/desktop_env/server/main.py" ] || die "no OSWorld checkout at $SRC (desktop_env/server/main.py)"
[ ! -e "$OV/merged" ] || ! mountpoint -q "$OV/merged" || die "$OV/merged is already mounted"
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export PYTHONPATH="$HERE/../../envd-linux"
RK=(python3 -c)
KIT=/opt/drums-osworld-real
M="$OV/merged"
# the resolver pip will use: a REAL one (the systemd stub 127.0.0.53 does not work in a chroot); the image's own /etc/resolv.conf may be a dangling symlink
RES=${DRUMS_RESOLVER_FILE:-/run/systemd/resolve/resolv.conf}; [ -s "$RES" ] || RES=/etc/resolv.conf
[ -s "$RES" ] || die "no resolver file on the host to give the chroot ($RES)"
PIPS=$("${RK[@]}" 'from envd_linux import realkit; print(" ".join(realkit.PIP))') || die "cannot read realkit.PIP"
MOUNTED=0; RESOLV=0; SEEDED=0
cleanup() {
  local rc=$?; trap - EXIT; set +e
  [ "$SEEDED" -eq 1 ] && rm -rf -- "$OV/upper/tmp/drums-seed"   # only the directory this run created
  [ "$RESOLV" -eq 1 ] && rm -f -- "$OV/upper/etc/resolv.conf"   # the upper entry only: the image's own resolv.conf (or its absence) shows through again
  if [ "$MOUNTED" -eq 1 ]; then
    umount "$M"
    if mountpoint -q "$M" 2>/dev/null; then echo "WARNING: $M is STILL MOUNTED (the image root was not written); unmount it with: umount $M" >&2; [ "$rc" -ne 0 ] || rc=1; fi
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
mkdir -p "$OV/upper" "$OV/work" "$M"
MOUNTED=1
mount -t overlay overlay -o "lowerdir=$ROOT,upperdir=$OV/upper,workdir=$OV/work" "$M"
mkdir -p "$M$KIT/server"
COPIED=()
for f in main.py pyxcursor.py screen_recording.py __init__.py; do [ -f "$SRC/desktop_env/server/$f" ] && cp -- "$SRC/desktop_env/server/$f" "$M$KIT/server/$f" && COPIED+=("$f"); done
[ -f "$M$KIT/server/main.py" ] || die "main.py was not copied"
"${RK[@]}" 'import sys; from envd_linux import realkit; realkit.write_provenance(sys.argv[1], sys.argv[2], sys.argv[3:])' "$SRC" "$M$KIT" "${COPIED[@]}"
mkdir -p "$M/etc"; rm -f -- "$M/etc/resolv.conf"; cp -L -- "$RES" "$M/etc/resolv.conf"; RESOLV=1
echo "resolver used for pip: $RES sha256 $(sha256sum "$RES" | cut -c1-16)" > "$M$KIT/resolver-used.txt"
chroot "$M" /usr/bin/python3 -m venv --without-pip --system-site-packages "$KIT/venv"   # no ensurepip needed
W=$(basename "$WHL")
SEEDED=1; mkdir -p "$M/tmp/drums-seed"; cp -- "$WHL" "$M/tmp/drums-seed/$W"
chroot "$M" env PYTHONPATH="/tmp/drums-seed/$W" "$KIT/venv/bin/python" -m pip install -q --no-index --no-deps "/tmp/drums-seed/$W"
echo "pip seeded from $W sha256 $WSHA ($(stat -c %s "$WHL") bytes), run from its own zip" > "$M$KIT/pip-seed.txt"
# shellcheck disable=SC2086
chroot "$M" "$KIT/venv/bin/python" -m pip install -q $PIPS
chroot "$M" "$KIT/venv/bin/python" -m pip freeze > "$M$KIT/pip-freeze.txt"
# `python` and `python3` on the session PATH (realkit's mechanics): WRAPPER scripts, not symlinks, so the venv's pyvenv.cfg is found
mkdir -p "$M$KIT/bin"
for n in python python3; do printf '#!/bin/sh\nexec "%s/venv/bin/python" "$@"\n' "$KIT" > "$M$KIT/bin/$n"; chmod 755 "$M$KIT/bin/$n"; done
# NON-GUI import check only (no DISPLAY exists here): pyautogui imports an X connection, so it is only checked for presence; the real import is checked through the server's /execute once a display runs
chroot "$M" "$KIT/venv/bin/python" -c 'import flask, lxml, PIL, psutil, Xlib, numpy, requests, pyatspi, importlib.util as u; assert u.find_spec("pyautogui"), "pyautogui not installed"' || die "the kit's non-GUI imports fail under the image's python"
V=$(chroot "$M" "$KIT/venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
echo "kit built for the image's own python $V in $OV/upper (use --overlay $OV/upper); nothing on the host was changed"
