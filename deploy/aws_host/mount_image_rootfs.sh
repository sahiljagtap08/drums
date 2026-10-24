#!/usr/bin/env bash
# Mount the OFFICIAL OSWorld qcow2's root filesystem READ-ONLY, for use as a Drums `drums_base_root` (docs/matched-software-plan.md, route B). PROTOTYPE, UNRUN against a real image: its
# behaviour is tested only with fake qemu-nbd/mount/lsblk/findmnt and injected failures. Run AS ROOT on the authorized host. No download, no install, no cloud call, no real image needed to test.
#
# OWNERSHIP RULES. The script touches ONLY what it created: an EXIT trap is armed BEFORE the first attach, with flags for what this run attached and mounted. On ANY exit (success excepted) it
# unmounts the mount it made, THEN disconnects the device it attached, verifies each step, and removes its per-run state file ONLY after both are verified; if a step cannot be verified the
# state is KEPT and the exit is non-zero with the recovery command. It never disconnects a pre-existing device and never unmounts anything it did not mount. The state file is per mount point
# (named by a hash of the canonical path), holds the exact source and target, and `--unmount` refuses unless the mount point, the recorded source and the live `findmnt` SOURCE all agree.
# Read-only is enforced three ways (qemu-nbd --read-only, blockdev --setro, mount -o ro,noload) and each is verified. Nothing here removes any path except its own state file and a mount-point
# directory it created.
#   sudo bash deploy/aws_host/mount_image_rootfs.sh /opt/vm-image/Ubuntu.qcow2 /mnt/vm-image-ro
#   sudo bash deploy/aws_host/mount_image_rootfs.sh --unmount /mnt/vm-image-ro
set -euo pipefail
die() { echo "REFUSED: $*" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || die "run as root"
STATE_DIR=${DRUMS_IMAGE_STATE_DIR:-/run}
SYSBLOCK=${DRUMS_SYS_BLOCK:-/sys/block}
SAFE='^[A-Za-z0-9_./-]+$'
# ONE lock covers device selection, attach, state operations and --unmount: a second invocation is refused before it touches anything. The lock fd is NOT inherited by qemu-nbd (9>&-).
mkdir -p "$STATE_DIR" && exec 9>"$STATE_DIR/drums-image-rootfs.lock" && flock -n 9 || die "another image-mount invocation holds the lock (or $STATE_DIR is unusable)"
statefile() { echo "$STATE_DIR/drums-image-rootfs.$(printf '%s' "$1" | sha256sum | cut -c1-16).state"; }
canonical_ok() {  # absolute, only safe characters, already canonical (no .., no //, no symlink in ANY component)
  [[ "$1" =~ $SAFE ]] || return 1
  case "$1" in /*) ;; *) return 1;; esac
  [ "$(readlink -f -- "$1")" = "$1" ]
}
if [ "${1:-}" = "--unmount" ]; then
  MP=${2:-}; [ -n "$MP" ] || die "usage: --unmount MOUNTPOINT"
  [[ "$MP" =~ $SAFE ]] && case "$MP" in /*) ;; *) false;; esac || die "the mount point must be absolute and use only safe characters"
  S=$(statefile "$MP"); [ -f "$S" ] || die "no recorded image mount for $MP"
  read -r SMP SDEV SPART SSRC < "$S"
  [ "$SMP" = "$MP" ] || die "the record names $SMP, not $MP"
  case "$SPART" in "$(basename "$SDEV")"p*) ;; *) die "the record is inconsistent ($SPART does not belong to $SDEV)";; esac
  LIVE=$(findmnt -n -o SOURCE --target "$MP" 2>/dev/null || true)
  [ "$LIVE" = "$SSRC" ] && [ "$SSRC" = "/dev/$SPART" ] || die "the live mount source '$LIVE' does not match the recorded '/dev/$SPART': refusing to touch it"
  umount "$MP" || die "could not unmount $MP; nothing else was touched"
  mountpoint -q "$MP" && die "$MP is still mounted; the device was NOT disconnected"
  qemu-nbd --disconnect "$SDEV" >/dev/null || die "could not disconnect $SDEV; the state record is kept"
  rm -f -- "$S"
  echo "unmounted $MP and disconnected $SDEV"
  exit 0
fi
Q=${1:-}; MP=${2:-}
[ -n "$Q" ] && [ -n "$MP" ] || die "usage: mount_image_rootfs.sh QCOW2 MOUNTPOINT   |   --unmount MOUNTPOINT"
canonical_ok "$Q" || die "the image path must be absolute, canonical (no symlink, .., //) and use only safe characters"
canonical_ok "$MP" || die "the mount point must be absolute, canonical (no symlink in any component) and use only safe characters ($MP)"
[ -f "$Q" ] || die "no such image file: $Q"
[ "$MP" != "/" ] && [ "$(dirname "$MP")" != "/" ] || die "the mount point must not be / or a top-level directory ($MP)"
case "$MP" in /proc|/proc/*|/sys|/sys/*|/dev|/dev/*|/run|/run/*|/boot|/boot/*|/etc|/etc/*|/usr|/usr/*|/bin|/sbin|/lib|/lib64|/var|/var/*) die "the mount point $MP is a system path";; esac
command -v qemu-nbd >/dev/null && command -v qemu-img >/dev/null || die "qemu-utils (qemu-nbd, qemu-img) is required"
S=$(statefile "$MP")
[ ! -f "$S" ] || die "a previous image mount for $MP is recorded in $S: unmount it first"
mkdir -p "$STATE_DIR" 2>/dev/null || true
if [ -e "$MP" ]; then
  [ -d "$MP" ] || die "$MP exists and is not a directory"
  mountpoint -q "$MP" && die "$MP is already a mount point"
  [ -z "$(ls -A -- "$MP")" ] || die "$MP is not empty"
  CREATED=0
else
  CREATED=1
fi
OK=0; ATTACHED=0; MOUNTED=0; DEV=""; CLEAN_FAILED=0
cleanup() {
  local rc=$?
  [ "$OK" -eq 0 ] || return 0
  trap - EXIT
  set +e
  # 1. unmount ONLY what this run mounted, 2. disconnect ONLY what this run attached (never while still mounted), 3. clear state ONLY once both are verified
  if [ "$MOUNTED" -eq 1 ]; then
    umount "$MP" 2>/dev/null
    if mountpoint -q "$MP" 2>/dev/null; then CLEAN_FAILED=1; echo "WARNING: $MP is STILL MOUNTED and $DEV was NOT disconnected; state kept in $S; recover with: $0 --unmount $MP" >&2; fi
  fi
  if [ "$CLEAN_FAILED" -eq 0 ] && [ "$ATTACHED" -eq 1 ] && [ -n "$DEV" ]; then
    qemu-nbd --disconnect "$DEV" >/dev/null 2>&1
    sz=$(cat "$SYSBLOCK/${DEV#/dev/}/size" 2>/dev/null || echo 0)
    if [ "$sz" != "0" ]; then CLEAN_FAILED=1; echo "WARNING: $DEV did not report disconnected; state kept in $S" >&2; fi
  fi
  if [ "$CLEAN_FAILED" -eq 0 ]; then
    rm -f -- "$S"
    if [ "$CREATED" -eq 1 ] && [ -d "$MP" ] && [ -z "$(ls -A -- "$MP" 2>/dev/null)" ]; then rmdir -- "$MP" 2>/dev/null; fi
  fi
  [ "$rc" -ne 0 ] || rc=1
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
[ "$CREATED" -eq 0 ] || mkdir -- "$MP"
modprobe nbd max_part=16 || die "the nbd kernel module would not load on this kernel"
# a device is FREE only if it reports size 0, has no owning process, and is not mounted: a busy device is never touched
for n in $(seq 0 15); do
  c=/dev/nbd$n
  if [ "$(cat "$SYSBLOCK/nbd$n/size" 2>/dev/null || echo 1)" = "0" ] && [ ! -e "$SYSBLOCK/nbd$n/pid" ] && ! grep -q "^$c" /proc/mounts 2>/dev/null; then DEV=$c; break; fi
done
[ -n "$DEV" ] || die "no free /dev/nbdN device (all are busy)"
ATTACHED=1   # set BEFORE the attach: if qemu-nbd half-succeeds, the cleanup still disconnects exactly this device
qemu-nbd --read-only --connect="$DEV" "$Q" 9>&-
echo "$MP $DEV attaching -" > "$S"
blockdev --setro "$DEV"
[ "$(blockdev --getro "$DEV")" = "1" ] || die "the block device $DEV is not read-only"
for i in $(seq 1 20); do
  PART=$(lsblk -nbo NAME,SIZE,FSTYPE "$DEV" 2>/dev/null | awk '$3=="ext4"{print $2, $1}' | sort -rn | head -1 | awk '{print $2}' | sed 's/[^[:alnum:]]//g')
  [ -n "$PART" ] && break
  partprobe "$DEV" 2>/dev/null || true; sleep 0.5
done
# the root filesystem is taken to be the LARGEST ext4 partition; the choice is printed, never silent
[ -n "${PART:-}" ] || die "no ext4 partition found on $DEV (is this the expected image?)"
case "$PART" in "$(basename "$DEV")"p*) ;; *) die "partition $PART does not belong to $DEV";; esac
MOUNTED=1   # set before the mount so that a mount that half-succeeds is still undone; umount of a failed mount is harmless
mount -o ro,noload "/dev/$PART" "$MP"
OPTS=$(findmnt -n -o OPTIONS --target "$MP")
case ",$OPTS," in *,ro,*) ;; *) die "the mount is not read-only ($OPTS)";; esac
SRC=$(findmnt -n -o SOURCE --target "$MP")
[ "$SRC" = "/dev/$PART" ] || die "the mount's source '$SRC' is not the partition this run mounted (/dev/$PART)"
[ -f "$MP/etc/os-release" ] || die "no /etc/os-release under $MP: not a root filesystem"
echo "$MP $DEV $PART $SRC" > "$S"   # the final, exact record: target, device, partition, source
OK=1
echo "mounted /dev/$PART read-only on $MP ($OPTS); os-release: $(grep '^PRETTY_NAME' "$MP/etc/os-release")"
