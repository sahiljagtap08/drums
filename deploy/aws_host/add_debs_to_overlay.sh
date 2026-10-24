#!/usr/bin/env bash
# Add OWNER-SUPPLIED .deb files (for example the 22.04 `xvfb` and its missing libraries) to the writable template overlay, so the session's X server is a SAME-ROOT program with the image's own
# library versions. Nothing is installed on the host and nothing in the image root changes: a .deb is unpacked with `dpkg-deb -x` into OVERLAY/upper only. A 24.04 host Xvfb is never copied and
# python 3.12 is never mixed in. Every deb must be listed with its sha256 (SHA256SUMS format) and is refused if it would CHANGE any existing path of the image root (identical files and documentation paths are tolerated), is not amd64,
# has an unsafe path, or puts files outside /usr, /lib, /lib64 or /etc/X11. Run `python -m drums_compare.debclosure` first: it checks the deb set's Depends against the image's package list. The additions are recorded in OVERLAY/additions.txt (they change parity: an X server of the image's own distribution,
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
# PASS 1: unpack EVERY deb into a staging directory of this run and check the staged tree against the image root BEFORE anything reaches the overlay.
#   - documentation paths (/usr/share/doc, man, info, lintian, doc-base, bug) are DROPPED, not refused: they are not runtime files and commonly overlap the image
#   - a file that already exists in the image is accepted ONLY if it is byte-identical (a shared library or data file the image already has at the same content); a different one refuses the deb
#   - two debs of the set writing different bytes to one path are refused
STAGE="$OV/.stage.$$"
mkdir -p "$OV"; rm -rf -- "$STAGE"; mkdir "$STAGE"
trap 'rm -rf -- "$STAGE"' EXIT
for f in "${LIST[@]}"; do
  d="$DEBS/$f"; [ -f "$d" ] || die "$f is listed but missing"
  [ "$(dpkg-deb -f "$d" Architecture)" = "amd64" ] || die "$f is not an amd64 package"
  while IFS= read -r name; do
    path=${name#.}
    [[ "$path" == */../* || "$path" == */.. || "$path" == ../* ]] && die "$f has an unsafe path: $path"
    case "$path" in /|"") continue;; /usr|/usr/*|/lib|/lib/*|/lib64|/lib64/*|/etc/X11|/etc/X11/*|/etc|/etc/) ;; *) die "$f puts a file outside /usr, /lib, /lib64, /etc/X11: $path";; esac
  done < <(dpkg-deb --fsys-tarfile "$d" | tar -tf -)
  mkdir "$STAGE/one"; dpkg-deb -x "$d" "$STAGE/one"
  rm -rf -- "$STAGE/one/usr/share/doc" "$STAGE/one/usr/share/man" "$STAGE/one/usr/share/info" "$STAGE/one/usr/share/lintian" "$STAGE/one/usr/share/doc-base" "$STAGE/one/usr/share/bug"
  while IFS= read -r -d '' p; do
    rel=${p#"$STAGE/one"}
    if [ -e "$ROOT$rel" ] || [ -L "$ROOT$rel" ]; then
      if [ -L "$p" ] && [ -L "$ROOT$rel" ] && [ "$(readlink "$p")" = "$(readlink "$ROOT$rel")" ]; then continue; fi
      if [ -f "$p" ] && [ ! -L "$p" ] && [ -f "$ROOT$rel" ] && cmp -s "$p" "$ROOT$rel"; then continue; fi
      die "$f would change $rel of the image root (a different file already exists; the pinned image stays unmodified)"
    fi
    if [ -e "$STAGE/all$rel" ] || [ -L "$STAGE/all$rel" ]; then
      [ -f "$p" ] && cmp -s "$p" "$STAGE/all$rel" || die "$f and an earlier deb write different content to $rel"
    fi
  done < <(find "$STAGE/one" \( -type f -o -type l \) -print0)
  mkdir -p "$STAGE/all"; cp -a --no-clobber "$STAGE/one/." "$STAGE/all/"; rm -rf -- "$STAGE/one"
done
mkdir -p "$OV/upper"
cp -a --no-clobber "$STAGE/all/." "$OV/upper/"
: > "$OV/additions.txt.new"
for f in "${LIST[@]}"; do
  echo "$(dpkg-deb -f "$DEBS/$f" Package) $(dpkg-deb -f "$DEBS/$f" Version) $f $(grep " \*\?$f\$" "$SUMS" | cut -d' ' -f1)" >> "$OV/additions.txt.new"
done
cat "$OV/additions.txt.new" >> "$OV/additions.txt"; rm -f "$OV/additions.txt.new"
echo "unpacked ${#LIST[@]} deb(s) into $OV/upper (documentation paths dropped); recorded in $OV/additions.txt; the image root was not written"
