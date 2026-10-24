#!/usr/bin/env bash
# Add OWNER-SUPPLIED .deb files (for example the 22.04 `xvfb` and its missing libraries) to the writable template overlay, so the session's X server is a SAME-ROOT program with the image's own
# library versions. Nothing is installed on the host and nothing in the image root changes: a .deb is unpacked with `dpkg-deb -x` into OVERLAY/upper only. A 24.04 host Xvfb is never copied and
# python 3.12 is never mixed in. Every deb must be listed with its sha256 (SHA256SUMS format) and is refused if it would overwrite ANY existing non-directory path of the image root, is not amd64,
# has an unsafe path, or puts files outside /usr, /lib, /lib64 or /etc/X11. The additions are recorded in OVERLAY/additions.txt (they change parity: an X server of the image's own distribution,
# which is Xvfb here, versus the incumbent's Xorg, a structural difference).
#   sudo bash deploy/aws_host/add_debs_to_overlay.sh /mnt/vm-image-ro /opt/drums-overlay /opt/debs /opt/debs/SHA256SUMS
set -euo pipefail
die() { echo "REFUSED: $*" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || die "run as root"
ROOT=${1:-}; OV=${2:-}; DEBS=${3:-}; SUMS=${4:-}
[ -n "$ROOT" ] && [ -n "$OV" ] && [ -n "$DEBS" ] && [ -n "$SUMS" ] || die "usage: add_debs_to_overlay.sh IMAGE_ROOT OVERLAY_DIR DEB_DIR SHA256SUMS"
for p in "$ROOT" "$OV" "$DEBS" "$SUMS"; do [[ "$p" =~ ^/[A-Za-z0-9_./+~-]+$ ]] && [ "$(readlink -f -- "$p")" = "$p" ] || die "paths must be absolute, canonical and use only safe characters ($p)"; done
[ -d "$ROOT/etc" ] && [ -d "$DEBS" ] && [ -s "$SUMS" ] || die "need the image root, a deb directory and a non-empty SHA256SUMS"
command -v dpkg-deb >/dev/null || die "dpkg-deb is required on the host (it only unpacks; nothing is installed)"
mapfile -t LIST < <(awk '{print $2}' "$SUMS" | sed 's/^\*//')
[ "${#LIST[@]}" -gt 0 ] || die "SHA256SUMS lists no file"
(cd "$DEBS" && sha256sum -c --quiet "$SUMS") || die "a deb does not match its sha256 (nothing was unpacked)"
# PASS 1: check every deb completely BEFORE unpacking any of them
for f in "${LIST[@]}"; do
  d="$DEBS/$f"; [ -f "$d" ] || die "$f is listed but missing"
  [ "$(dpkg-deb -f "$d" Architecture)" = "amd64" ] || die "$f is not an amd64 package"
  while IFS= read -r name; do
    path=${name#.}
    [[ "$path" == */../* || "$path" == */.. || "$path" == ../* ]] && die "$f has an unsafe path: $path"
    case "$path" in /|"") continue;; /usr|/usr/*|/lib|/lib/*|/lib64|/lib64/*|/etc/X11|/etc/X11/*|/etc|/etc/) ;; *) die "$f puts a file outside /usr, /lib, /lib64, /etc/X11: $path";; esac
    case "$path" in */) continue;; esac   # directories may already exist; only files and links must not
    if [ -e "$ROOT$path" ] || [ -L "$ROOT$path" ]; then die "$f would overwrite $path of the image root (the pinned image stays unmodified)"; fi
  done < <(dpkg-deb --fsys-tarfile "$d" | tar -tf -)
done
mkdir -p "$OV/upper"
: > "$OV/additions.txt.new"
for f in "${LIST[@]}"; do
  dpkg-deb -x "$DEBS/$f" "$OV/upper"
  echo "$(dpkg-deb -f "$DEBS/$f" Package) $(dpkg-deb -f "$DEBS/$f" Version) $f $(grep " \*\?$f\$" "$SUMS" | cut -d' ' -f1)" >> "$OV/additions.txt.new"
done
cat "$OV/additions.txt.new" >> "$OV/additions.txt"; rm -f "$OV/additions.txt.new"
echo "unpacked ${#LIST[@]} deb(s) into $OV/upper; recorded in $OV/additions.txt; the image root was not written"
