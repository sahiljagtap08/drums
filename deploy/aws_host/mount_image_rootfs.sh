#!/usr/bin/env bash
# Mount the OFFICIAL OSWorld qcow2's root filesystem READ-ONLY, for use as a Drums `drums_base_root` (docs/matched-software-plan.md, route B). PROTOTYPE, UNRUN against a real image: its
# behaviour is tested only with fake qemu-nbd/mount/lsblk. Run AS ROOT on the authorized host. Nothing is written to the image: the NBD export is read-only AND the block device is set
# read-only AND the filesystem is mounted `ro,noload` (no journal replay), and each is verified before success is reported. On ANY failure or interrupt the script unmounts what it mounted,
# disconnects what it attached and removes the state it wrote (an EXIT trap with a success flag); it never leaves an attached NBD device or a mount behind. No download, no install, no cloud call.
#   sudo bash deploy/aws_host/mount_image_rootfs.sh /opt/vm-image/Ubuntu.qcow2 /mnt/vm-image-ro
#   sudo bash deploy/aws_host/mount_image_rootfs.sh --unmount /mnt/vm-image-ro
set -euo pipefail
die() { echo "REFUSED: $*" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || die "run as root"
STATE=${DRUMS_IMAGE_STATE:-/run/drums-image-rootfs.state}
SYSBLOCK=${DRUMS_SYS_BLOCK:-/sys/block}
if [ "${1:-}" = "--unmount" ]; then
  MP=${2:-}; [ -n "$MP" ] || die "usage: --unmount MOUNTPOINT"
  [ -f "$STATE" ] || die "no recorded image mount ($STATE)"
  read -r SMP SDEV _ < "$STATE"
  [ "$SMP" = "$MP" ] || die "the recorded mount is $SMP, not $MP"
  if mountpoint -q "$MP"; then umount "$MP"; fi
  qemu-nbd --disconnect "$SDEV" >/dev/null
  rm -f "$STATE"
  echo "unmounted $MP and disconnected $SDEV"
  exit 0
fi
Q=${1:-}; MP=${2:-}
[ -n "$Q" ] && [ -n "$MP" ] || die "usage: mount_image_rootfs.sh QCOW2 MOUNTPOINT   |   --unmount MOUNTPOINT"
case "$Q" in /*) ;; *) die "the image path must be absolute";; esac
case "$MP" in /*) ;; *) die "the mount point must be absolute";; esac
[ ! -L "$Q" ] || die "the image path is a symlink: refusing (give the real file)"
[ -f "$Q" ] || die "no such image file: $Q"
[ "$MP" != "/" ] && [ "$(dirname "$MP")" != "/" ] || die "the mount point must not be / or a top-level directory ($MP)"
case "$MP" in /proc|/proc/*|/sys|/sys/*|/dev|/dev/*|/run|/run/*|/boot|/boot/*|/etc|/etc/*|/usr|/usr/*|/bin|/sbin|/lib|/lib64|/var|/var/*) die "the mount point $MP is a system path";; esac
[ ! -L "$MP" ] || die "the mount point is a symlink: refusing"
command -v qemu-nbd >/dev/null && command -v qemu-img >/dev/null || die "qemu-utils (qemu-nbd, qemu-img) is required"
[ ! -f "$STATE" ] || die "a previous image mount is recorded in $STATE: unmount it first"
if [ -e "$MP" ]; then
  [ -d "$MP" ] || die "$MP exists and is not a directory"
  mountpoint -q "$MP" && die "$MP is already a mount point"
  [ -z "$(ls -A "$MP")" ] || die "$MP is not empty"
  CREATED=0
else
  mkdir "$MP"; CREATED=1
fi
OK=0; ATTACHED=0; MOUNTED=0; DEV=""
cleanup() {
  if [ "$OK" -ne 1 ]; then
    if [ "$MOUNTED" -eq 1 ]; then umount "$MP" 2>/dev/null || true; fi   # only what THIS run mounted (a pre-existing mount point was refused up front)
    if [ "$ATTACHED" -eq 1 ] && [ -n "$DEV" ]; then qemu-nbd --disconnect "$DEV" >/dev/null 2>&1 || true; fi
    rm -f "$STATE"
    [ "$CREATED" -eq 1 ] && rmdir "$MP" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM
modprobe nbd max_part=16 || die "the nbd kernel module would not load on this kernel"
# a device is FREE only if it reports size 0, has no owning process, and is not mounted: a busy device is never touched
for n in $(seq 0 15); do
  c=/dev/nbd$n
  if [ "$(cat "$SYSBLOCK/nbd$n/size" 2>/dev/null || echo 1)" = "0" ] && [ ! -e "$SYSBLOCK/nbd$n/pid" ] && ! grep -q "^$c" /proc/mounts 2>/dev/null; then DEV=$c; break; fi
done
[ -n "$DEV" ] || die "no free /dev/nbdN device (all are busy)"
qemu-nbd --read-only --connect="$DEV" "$Q"
ATTACHED=1
echo "$MP $DEV attaching" > "$STATE"
blockdev --setro "$DEV"
[ "$(blockdev --getro "$DEV")" = "1" ] || die "the block device $DEV is not read-only"
for i in $(seq 1 20); do
  PARTS=$(lsblk -nbo NAME,SIZE,FSTYPE "$DEV" 2>/dev/null | awk '$3=="ext4"{print $2, $1}' | sort -rn | head -1 | awk '{print $2}' | sed 's/[^[:alnum:]]//g')
  [ -n "$PARTS" ] && break
  partprobe "$DEV" 2>/dev/null || true; sleep 0.5
done
# the root filesystem is taken to be the LARGEST ext4 partition; the choice is printed, never silent
PART=${PARTS:-}
[ -n "$PART" ] || die "no ext4 partition found on $DEV (is this the expected image?)"
MOUNTED=1   # set before the mount so that a mount that half-succeeds is still undone; umount of a failed mount is harmless
mount -o ro,noload "/dev/$PART" "$MP"
OPTS=$(findmnt -n -o OPTIONS --target "$MP")
case ",$OPTS," in *,ro,*) ;; *) die "the mount is not read-only ($OPTS)";; esac
[ -f "$MP/etc/os-release" ] || die "no /etc/os-release under $MP: not a root filesystem"
echo "$MP $DEV $PART" > "$STATE"
OK=1
echo "mounted /dev/$PART read-only on $MP ($OPTS); os-release: $(grep '^PRETTY_NAME' "$MP/etc/os-release")"
