#!/usr/bin/env bash
# Fetch, verify and describe the official OSWorld Ubuntu qcow2 for the STOCK Docker/KVM provider. PROTOTYPE, UNRUN. Run AS ROOT on the host after `bootstrap.sh` (STAGE=docker).
#   sudo bash deploy/aws_host/fetch_docker_image.sh --accept-licence-risk --zip-bytes N --uncompressed-bytes M --zip-sha256 H
# The pinned artifact (see drums_compare.imagesize.KNOWN_ARTIFACT): N=12273896463, M=24460197888, H=b795b6cd4c69b252c1b4f10150a347795555032501b60fd031751ed09b896712 (the LFS sha256 of the
# zip). They come from `python -m drums_compare.imagesize` (a BOUNDED range read of the zip directory, run before approval). The hash is REQUIRED, not optional. The script REFUSES to download without them, REFUSES a
# download larger than N, REFUSES to extract if the free space cannot hold zip plus extraction plus a margin, and verifies size, zip integrity and (if given) the zip sha256.
#
# Source: the COMMIT-PINNED URL below (the stock Docker manager points at the mutable `main`; this script never uses it): repository commit a5d9c3eaae98eebf6e3a0beb84e7e47cf72ae133.
# Its dataset card's License field is BLANK: an unresolved uncertainty, not a ban and not permission. No mirror, no other image. The guest composition is read from the booted guest.
set -euo pipefail
die() { echo "REFUSED: $*" >&2; exit 2; }
[ "${1:-}" = "--accept-licence-risk" ] || die "pass --accept-licence-risk after reading the comment at the top of this file (the card's License field is blank)"
shift
ZB= UB= ZSHA=
while [ $# -gt 0 ]; do case "$1" in --zip-bytes) ZB=$2; shift 2;; --uncompressed-bytes) UB=$2; shift 2;; --zip-sha256) ZSHA=$2; shift 2;; *) die "unknown option $1";; esac; done
[[ "$ZB" =~ ^[0-9]+$ ]] && [[ "$UB" =~ ^[0-9]+$ ]] || die "--zip-bytes and --uncompressed-bytes are required (from drums_compare.imagesize)"
[[ "$ZSHA" =~ ^[0-9a-f]{64}$ ]] || die "--zip-sha256 is required: the LFS sha256 of the pinned zip"
[ "$(id -u)" -eq 0 ] || die "run as root"
command -v docker >/dev/null && command -v qemu-img >/dev/null || die "docker and qemu-img are needed (bootstrap.sh STAGE=docker)"
WORK=${IMGDIR:-/opt/vm-image}; STATE=/var/lib/drums-host; OUT=${OUT:-$STATE/image_manifest.json}
HF_COMMIT=a5d9c3eaae98eebf6e3a0beb84e7e47cf72ae133
URL=https://huggingface.co/datasets/xlangai/ubuntu_osworld/resolve/$HF_COMMIT/Ubuntu.qcow2.zip
mkdir -p "$WORK" "$STATE"; cd "$WORK"
HAVE=$(stat -c %s Ubuntu.qcow2.zip 2>/dev/null || echo 0)
[ "$HAVE" -le "$ZB" ] || die "the local zip ($HAVE) is larger than the expected $ZB: remove it deliberately"
AVAIL=$(df --output=avail -B1 "$WORK" | tail -1)
[ "$AVAIL" -gt $((ZB - HAVE + UB + 2147483648)) ] || die "free space $AVAIL cannot hold the remaining download plus the extracted image plus 2 GiB"
if [ "$HAVE" -lt "$ZB" ]; then curl -fL -C - --max-filesize $((ZB + 1048576)) -o Ubuntu.qcow2.zip "$URL"; fi
[ "$(stat -c %s Ubuntu.qcow2.zip)" -eq "$ZB" ] || die "the zip is not the expected size ($(stat -c %s Ubuntu.qcow2.zip) of $ZB)"
unzip -tq Ubuntu.qcow2.zip >/dev/null || die "the zip failed its integrity test"
LISTED=$(unzip -l Ubuntu.qcow2.zip | awk 'END{print $1}')
[ "$LISTED" -eq "$UB" ] || die "the archive lists $LISTED uncompressed bytes, expected $UB"
SHA=$(sha256sum Ubuntu.qcow2.zip | cut -d' ' -f1)
[ "$SHA" = "$ZSHA" ] || die "zip sha256 $SHA differs from the expected $ZSHA"
rm -rf extract.partial; mkdir extract.partial; unzip -q Ubuntu.qcow2.zip -d extract.partial
Q=$(find extract.partial -name '*.qcow2' | head -1); [ -n "$Q" ] || die "no .qcow2 in the archive"
rm -f Ubuntu.qcow2; mv "$Q" Ubuntu.qcow2; rm -rf extract.partial
QBYTES=$(stat -c %s Ubuntu.qcow2); QSHA=$(sha256sum Ubuntu.qcow2 | cut -d' ' -f1)
qemu-img info Ubuntu.qcow2 | tee "$STATE/qcow2.info"
rm -f Ubuntu.qcow2.zip   # verified and extracted; the disk is reclaimed (its size, hash and the listing are recorded)

# describe the REAL guest: boot it with the stock provider's own container settings, read it, remove the container and its volumes
DIG=$(docker image inspect happysixd/osworld-docker --format '{{index .RepoDigests 0}}')
CID=$(docker run -d --device /dev/kvm --cap-add NET_ADMIN -e DISK_SIZE=32G -e RAM_SIZE=4G -e CPU_CORES=4 -v "$WORK/Ubuntu.qcow2:/System.qcow2:ro" -p 15000:5000 happysixd/osworld-docker)
trap 'docker rm -f -v "$CID" >/dev/null 2>&1 || true' EXIT
for i in $(seq 1 90); do curl -fs -m 5 http://localhost:15000/screenshot -o /dev/null && break; sleep 5; done
curl -fs -m 5 http://localhost:15000/screenshot -o /dev/null || die "the guest server did not come up on 15000 within 450 s (KVM? image?)"
guest() { python3 - "$1" <<'PY'
import json,sys,urllib.request
r=urllib.request.Request("http://localhost:15000/execute",data=json.dumps({"command":sys.argv[1],"shell":True}).encode(),headers={"Content-Type":"application/json"})
print(json.load(urllib.request.urlopen(r,timeout=60)).get("output",""))
PY
}
export G_OS="$(guest 'cat /etc/os-release')" G_KERNEL="$(guest 'uname -r')" G_DESKTOP="$(guest 'echo $XDG_CURRENT_DESKTOP; gnome-shell --version 2>&1 | head -1')" \
       G_BROWSER="$(guest 'google-chrome --version 2>&1 | head -1; chromium --version 2>&1 | head -1')" G_LO="$(guest 'soffice --version 2>&1 | head -1')" \
       G_USER="$(guest 'id -un; echo $HOME')" G_DPKG="$(guest "dpkg-query -W -f='\${Package}=\${Version}\n' 2>/dev/null | sort")" G_NPROC="$(guest nproc)" G_MEM="$(guest 'grep MemTotal /proc/meminfo')" \
       G_ZB="$ZB" G_ZSHA="$SHA" G_COMMIT="$HF_COMMIT" G_QB="$QBYTES" G_QSHA="$QSHA" G_DIG="$DIG" G_OUT="$OUT" G_URL="$URL" G_QINFO="$(cat "$STATE/qcow2.info")"
python3 - <<'PY'
import json,os
e=os.environ
m={"source_url":e["G_URL"],"source_commit":e["G_COMMIT"],"zip_bytes":int(e["G_ZB"]),"zip_sha256":e["G_ZSHA"],"qcow2_bytes":int(e["G_QB"]),"qcow2_sha256":e["G_QSHA"],"qemu_img_info":e["G_QINFO"].splitlines(),
   "docker_image":e["G_DIG"],"license_field":"BLANK on the dataset card (unresolved)","composition_known":True,"os_release":e["G_OS"],"kernel":e["G_KERNEL"],"desktop":e["G_DESKTOP"],
   "browser":e["G_BROWSER"],"libreoffice":e["G_LO"],"guest_user_and_home":e["G_USER"],"dpkg":e["G_DPKG"].splitlines(),"guest_nproc":e["G_NPROC"],"guest_memtotal":e["G_MEM"]}
json.dump(m,open(e["G_OUT"],"w"),indent=1)
print("wrote",e["G_OUT"])
PY
echo "image ready: $WORK/Ubuntu.qcow2 ($QBYTES bytes, sha256 $QSHA). Put qcow2, qcow2_bytes, qcow2_sha256 and docker_image_digest into the host_run config."
