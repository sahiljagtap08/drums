#!/usr/bin/env bash
# Fetch the ONE pinned Xvfb .deb that matches the image's xserver-common (2:21.1.4-2ubuntu1.7~22.04.8), from the Ubuntu SNAPSHOT archive (a fixed point in time), not from the moving
# 22.04 archive (whose current xvfb would force an xserver-common update the pinned image must not get). The owner's metadata check found all of its Depends satisfied by the image inventory, so
# no other deb is needed; `rootcheck` still has to confirm the ELF/runtime paths on the mounted image. Owner-run, on the host, with network; nothing is installed. Output: DEST/xvfb.deb and
# DEST/SHA256SUMS (for add_debs_to_overlay.sh). Refuses on any size or sha256 difference and removes the partial file.
#   bash deploy/aws_host/fetch_xvfb_deb.sh /opt/debs
#   python -m drums_compare.debclosure --image-dpkg image_manifest.txt --debs /opt/debs
#   sudo bash deploy/aws_host/add_debs_to_overlay.sh /mnt/vm-image-ro /opt/drums-overlay /opt/debs /opt/debs/SHA256SUMS
set -euo pipefail
die() { echo "REFUSED: $*" >&2; exit 2; }
URL=https://snapshot.ubuntu.com/ubuntu/20240301T030400Z/pool/universe/x/xorg-server/xvfb_21.1.4-2ubuntu1.7~22.04.8_amd64.deb
SHA=f8c64bc652e3dc1a1041e85025267f574c27f86b00d7965545a8b3ec41a60cd4
SIZE=863344
DEST=${1:-}
[ -n "$DEST" ] || die "usage: fetch_xvfb_deb.sh DEST_DIR"
[[ "$DEST" =~ ^/[A-Za-z0-9_./-]+$ ]] || die "DEST must be an absolute path with safe characters"
[ ! -e "$DEST/xvfb.deb" ] || die "$DEST/xvfb.deb already exists; remove it deliberately"
mkdir -p "$DEST"
TMP=$(mktemp "$DEST/.xvfb.XXXXXX")
trap 'rm -f -- "$TMP"' EXIT
curl --fail --silent --show-error --proto '=https' --tlsv1.2 --max-redirs 0 --max-filesize "$SIZE" --max-time 300 -o "$TMP" "$URL" || die "download failed"
[ "$(stat -c %s "$TMP")" = "$SIZE" ] || die "size is not $SIZE"
[ "$(sha256sum "$TMP" | cut -d' ' -f1)" = "$SHA" ] || die "sha256 does not match the pinned value"
mv -- "$TMP" "$DEST/xvfb.deb"
echo "$SHA  xvfb.deb" > "$DEST/SHA256SUMS"
echo "fetched xvfb 21.1.4-2ubuntu1.7~22.04.8 into $DEST (sha256 verified)"
