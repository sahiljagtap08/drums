#!/usr/bin/env bash
# Mount the OFFICIAL OSWorld qcow2's root filesystem READ-ONLY, for use as a Drums `drums_base_root` (docs/matched-software-plan.md, route B). PROTOTYPE, UNRUN: never executed against a real
# image. Run AS ROOT on the authorized host. Nothing is written to the image: the NBD export is read-only, the filesystem is mounted `ro,noload` (no journal replay) and the mount is verified
# read-only before it is reported. No download, no install, no cloud call; `qemu-img`/`qemu-nbd` (qemu-utils) and the nbd kernel module are required and checked.
#   sudo bash deploy/aws_host/mount_image_rootfs.sh /opt/vm-image/Ubuntu.qcow2 /mnt/vm-image-ro
#   sudo bash deploy/aws_host/mount_image_rootfs.sh --unmount /mnt/vm-image-ro
set -euo pipefail
die() { echo "REFUSED: $*" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || die "run as root"
STATE=/run/drums-image-rootfs.state
if [ "${1:-}" = "--unmount" ]; then
  MP=${2:-}; [ -n "$MP" ] || die "usage: --unmount MOUNTPOINT"
  umount "$MP" && echo "unmounted $MP"
  [ -f "$STATE" ] && { DEV=$(cut -d' ' -f2 "$STATE"); qemu-nbd --disconnect "$DEV" >/dev/null && echo "disconnected $DEV"; rm -f "$STATE"; }
  exit 0
fi
Q=${1:-}; MP=${2:-}
[ -n "$Q" ] && [ -n "$MP" ] || die "usage: mount_image_rootfs.sh QCOW2 MOUNTPOINT   |   --unmount MOUNTPOINT"
[ -f "$Q" ] || die "no such image: $Q"
command -v qemu-nbd >/dev/null && command -v qemu-img >/dev/null || die "qemu-utils (qemu-nbd, qemu-img) is required"
mountpoint -q "$MP" && die "$MP is already a mount point"
[ ! -f "$STATE" ] || die "a previous image mount is recorded in $STATE: unmount it first"
mkdir -p "$MP"
modprobe nbd max_part=16 || die "the nbd kernel module would not load on this kernel"
DEV=""
for n in $(seq 0 15); do
  if [ "$(cat /sys/block/nbd$n/size 2>/dev/null || echo 1)" = "0" ]; then DEV=/dev/nbd$n; break; fi
done
[ -n "$DEV" ] || die "no free /dev/nbdN device"
qemu-nbd --read-only --connect="$DEV" "$Q"
trap 'qemu-nbd --disconnect "$DEV" >/dev/null 2>&1 || true' ERR
for i in $(seq 1 20); do [ -b "${DEV}p1" ] && break; partprobe "$DEV" 2>/dev/null || true; sleep 0.5; done
# the root filesystem is taken to be the LARGEST ext4 partition; the choice is printed, never silent
PART=$(lsblk -nbo NAME,SIZE,FSTYPE "$DEV" | awk '$3=="ext4"{print $2, $1}' | sort -rn | head -1 | awk '{print $2}' | sed 's/[^[:alnum:]]//g')
[ -n "$PART" ] || die "no ext4 partition found on $DEV (is this the expected image?)"
mount -o ro,noload "/dev/$PART" "$MP"
OPTS=$(findmnt -n -o OPTIONS --target "$MP")
case ",$OPTS," in *,ro,*) ;; *) umount "$MP"; die "the mount is not read-only ($OPTS)";; esac
[ -f "$MP/etc/os-release" ] || { umount "$MP"; die "no /etc/os-release under $MP: not a root filesystem"; }
echo "$MP $DEV" > "$STATE"
echo "mounted /dev/$PART read-only on $MP ($OPTS); os-release: $(grep '^PRETTY_NAME' "$MP/etc/os-release")"
