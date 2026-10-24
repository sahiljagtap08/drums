#!/usr/bin/env bash
# Build the OSWorld server kit for an IMAGE ROOT's own Python (3.10 on the 22.04.3 image), into a writable template overlay, never touching the read-only root and never mapping the host's
# python 3.12. Runs the image's /usr/bin/python3 and pip inside a chroot over overlay(lower=image root, upper=OVERLAY/upper). Run AS ROOT by the owner; it needs network for pip (pinned list in
# realkit.PIP) and the unmodified OSWorld checkout. PROTOTYPE: unrun against a real image. Nothing is installed on the host.
#   sudo bash deploy/aws_host/prepare_kit_for_image.sh /mnt/vm-image-ro /opt/OSWorld-checkout /opt/drums-overlay
set -euo pipefail
die() { echo "REFUSED: $*" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || die "run as root"
ROOT=${1:-}; SRC=${2:-}; OV=${3:-}
[ -n "$ROOT" ] && [ -n "$SRC" ] && [ -n "$OV" ] || die "usage: prepare_kit_for_image.sh IMAGE_ROOT OSWORLD_CHECKOUT OVERLAY_DIR"
for p in "$ROOT" "$SRC" "$OV"; do [[ "$p" =~ ^/[A-Za-z0-9_./-]+$ ]] && [ "$(readlink -f -- "$p")" = "$p" ] || die "paths must be absolute, canonical and use only safe characters ($p)"; done
[ -d "$ROOT/etc" ] && [ -x "$ROOT/usr/bin/python3" ] || die "$ROOT has no /usr/bin/python3: not the image root"
[ -f "$ROOT/usr/lib/python3/dist-packages/pyatspi/__init__.py" ] || die "the image has no pyatspi (python3-pyatspi): the kit's accessibility needs it"
[ -f "$SRC/desktop_env/server/main.py" ] || die "no OSWorld checkout at $SRC (desktop_env/server/main.py)"
[ ! -e "$OV/merged" ] || ! mountpoint -q "$OV/merged" || die "$OV/merged is already mounted"
KIT=/opt/drums-osworld-real
MOUNTED=0
cleanup() { local rc=$?; trap - EXIT; set +e; if [ "$MOUNTED" -eq 1 ]; then umount "$OV/merged" || echo "WARNING: $OV/merged still mounted" >&2; fi; exit "$rc"; }
trap cleanup EXIT
mkdir -p "$OV/upper" "$OV/work" "$OV/merged"
MOUNTED=1
mount -t overlay overlay -o "lowerdir=$ROOT,upperdir=$OV/upper,workdir=$OV/work" "$OV/merged"
mkdir -p "$OV/merged$KIT/server"
for f in main.py pyxcursor.py screen_recording.py __init__.py; do [ -f "$SRC/desktop_env/server/$f" ] && cp -- "$SRC/desktop_env/server/$f" "$OV/merged$KIT/server/$f"; done
for f in LICENSE NOTICE; do [ -f "$SRC/$f" ] && cp -- "$SRC/$f" "$OV/merged$KIT/$f"; done
cp /etc/resolv.conf "$OV/merged/etc/resolv.conf.drums" 2>/dev/null || true
chroot "$OV/merged" /usr/bin/python3 -m venv --system-site-packages "$KIT/venv"
chroot "$OV/merged" "$KIT/venv/bin/python" -m pip install flask lxml pyautogui requests pillow numpy python-xlib psutil
V=$(chroot "$OV/merged" "$KIT/venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
chroot "$OV/merged" "$KIT/venv/bin/python" -c 'import flask, pyautogui, Xlib, lxml, PIL, psutil, pyatspi' || die "the kit's imports fail under the image's python $V"
rm -f "$OV/merged/etc/resolv.conf.drums"
echo "kit built for the image's own python $V in $OV/upper (use --overlay $OV/upper); nothing on the host was changed"
