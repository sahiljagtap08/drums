#!/usr/bin/env bash
# PRIVATE CRIU for an Ubuntu 24.04 host: the distribution's own `criu` package and the packages it needs that this host does NOT already have are DOWNLOADED (apt-get download: nothing is
# installed, nothing system-wide changes) and unpacked under DEST in the layout `envd_linux.criu.Criu` reads: DEST/x-<REAL upstream version>/usr/sbin/criu and DEST/libs/{bin,usr/lib/...}. The
# directory is named after the REAL version (the old bootstrap named it x-4.2 whatever it was), Criu() finds the single x-* directory (or DRUMS_CRIU_VERSION), and every .deb's name, version and
# sha256 is recorded in DEST/CRIU_PROVENANCE.txt. The distribution's version may be older than the 4.2 used in earlier probes: its kernel/feature compatibility is UNKNOWN until `criu check` (root)
# and the probe pass. Owner-run, with network and the apt lists present (`apt-get update` is NOT run here).
#   bash deploy/aws_host/install_criu_private.sh /opt/drums-criu && export DRUMS_CRIU_HOME=/opt/drums-criu && sudo -E "$DRUMS_CRIU_HOME"/x-*/usr/sbin/criu check
set -euo pipefail
die() { echo "REFUSED: $*" >&2; exit 2; }
DEST=${1:-}
[ -n "$DEST" ] || die "usage: install_criu_private.sh DEST_DIR"
[[ "$DEST" =~ ^/[A-Za-z0-9_./-]+$ ]] || die "DEST must be an absolute path with safe characters"
for c in apt-get apt-cache dpkg-deb dpkg ip sha256sum; do command -v "$c" >/dev/null || die "$c is required"; done
[ ! -e "$DEST/CRIU_PROVENANCE.txt" ] || die "$DEST already holds a private CRIU (CRIU_PROVENANCE.txt exists); remove it deliberately"
CAND=$(apt-cache policy criu | sed -n 's/^  Candidate: //p')
[ -n "$CAND" ] && [ "$CAND" != "(none)" ] || die "apt has no candidate for criu (are the apt lists present?)"
mapfile -t ALL < <(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances criu | grep -E '^[a-z0-9]' | sort -u)
FETCH=()
for p in "${ALL[@]}"; do if [ "$p" = criu ] || ! dpkg -s "$p" >/dev/null 2>&1; then FETCH+=("$p"); fi; done
[ "${#FETCH[@]}" -gt 0 ] || die "nothing to fetch"
mkdir -p "$DEST/dl" "$DEST/libs/bin"
(cd "$DEST/dl" && apt-get download "${FETCH[@]}") || die "apt-get download failed"
CRIU_DEB=$(ls "$DEST/dl"/criu_*.deb 2>/dev/null | head -1)
[ -n "$CRIU_DEB" ] || die "the criu .deb was not downloaded"
UP=$(dpkg-deb -f "$CRIU_DEB" Version | sed 's/^[0-9]*://; s/-.*//')
[[ "$UP" =~ ^[0-9][0-9A-Za-z.+~]*$ ]] || die "cannot read the upstream version from $CRIU_DEB"
mkdir -p "$DEST/x-$UP"
dpkg-deb -x "$CRIU_DEB" "$DEST/x-$UP"
for d in "$DEST"/dl/*.deb; do [ "$d" = "$CRIU_DEB" ] || dpkg-deb -x "$d" "$DEST/libs"; done
ln -sf "$(command -v ip)" "$DEST/libs/bin/ip"   # CRIU runs `ip`; the host's own, linked, not copied
BIN="$DEST/x-$UP/usr/sbin/criu"
[ -x "$BIN" ] || die "$BIN is missing after unpacking"
export LD_LIBRARY_PATH="$DEST/libs/usr/lib/x86_64-linux-gnu:$DEST/libs/lib/x86_64-linux-gnu"
if ldd "$BIN" 2>&1 | grep -q "not found"; then ldd "$BIN" | grep "not found" >&2; die "criu still has unresolved libraries (see above): fetch the named packages and unpack them under $DEST/libs"; fi
"$BIN" --version
{ echo "criu real upstream version: $UP (apt candidate $CAND)"; echo "host: $(. /etc/os-release; echo "$PRETTY_NAME") kernel $(uname -r)"
  for d in "$DEST"/dl/*.deb; do echo "$(basename "$d") $(dpkg-deb -f "$d" Version) sha256 $(sha256sum "$d" | cut -d' ' -f1)"; done; } > "$DEST/CRIU_PROVENANCE.txt"
echo "private CRIU $UP unpacked under $DEST (export DRUMS_CRIU_HOME=$DEST); kernel compatibility is UNKNOWN until 'sudo criu check' and the probe pass"
