#!/usr/bin/env bash
# Prepare ONE authorized Linux x86 bare-metal host (Ubuntu 24.04) for the matched Drums-vs-VirtualBox run. PROTOTYPE, UNRUN: written in a sandbox that has no VirtualBox, no
# hardware virtualization and no network authority, so nothing here has executed. Run it ON THE HOST as a normal sudo-capable user (never root, never from this repo's CI).
# Idempotent (each step checks before acting), fail-closed (any unmet precondition exits non-zero before anything is installed), makes NO cloud API call.
#
#   DRUMS_REF=<exact commit of this repository> bash deploy/aws_host/bootstrap.sh
#
# DRUMS_REF has no default on purpose: the host must run an exact, recorded commit. Optional: CRIU_DEB_URLS="url1 url2" (exact .deb files for a private CRIU 4.x; otherwise
# the distribution's `criu` is extracted privately and its version is only RECORDED: restore on this host's kernel is UNTESTED until `host_run gates` passes).
set -euo pipefail

OSWORLD_PIN=04718eab9327856efe8192cc58161acc7a7627e2
PLAYWRIGHT_VERSION=${PLAYWRIGHT_VERSION:-1.56.0}   # believed to ship chromium-1194 (Chromium 141.0.7390.37); verified below, not assumed
STATE=/var/lib/drums-host
SRC=/opt/drums-src
OSW=/opt/osworld-pinned
CRIU_HOME=/opt/drums-criu
VMNAME=${VMNAME:-Ubuntu0}
say() { printf '\n== %s\n' "$*"; }
die() { printf 'REFUSED: %s\n' "$*" >&2; exit 2; }

say "preconditions (nothing is installed until all hold)"
[ "$(id -u)" -ne 0 ] || die "run as a normal user with sudo, not as root"
sudo -n true 2>/dev/null || die "passwordless sudo is needed for apt, modprobe and /opt"
[ -n "${DRUMS_REF:-}" ] || die "set DRUMS_REF to the exact commit of this repository to run"
. /etc/os-release; [ "$ID" = ubuntu ] && [ "$VERSION_ID" = 24.04 ] || die "Ubuntu 24.04 expected, found $ID $VERSION_ID"
[ "$(uname -m)" = x86_64 ] || die "x86_64 expected"
[ "$(systemd-detect-virt || true)" = none ] || die "host is virtualized ($(systemd-detect-virt || true)): the plan needs a non-virtualized (bare-metal) host"
grep -qE ' (vmx|svm)( |$)' /proc/cpuinfo || die "no vmx/svm flag: hardware virtualization is not exposed"
sudo mkdir -p "$STATE" && sudo chown "$USER" "$STATE"

step() { # step <name> <command...>: skip when the marker exists
  local n=$1; shift
  if [ -e "$STATE/$n.done" ]; then echo "skip $n (done)"; else say "$n"; "$@"; touch "$STATE/$n.done"; fi
}

apt_base() {
  sudo apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git curl unzip python3-venv python3-pip python3.12 build-essential dkms "linux-headers-$(uname -r)" \
    iproute2 nftables xvfb openbox wmctrl xclip x11-utils util-linux sudo
}
vbox() {
  # Ubuntu's `virtualbox` package is the GPL base package: NO Extension Pack is installed or assumed (it is separately licensed). VRDE/USB2/3 are therefore not available.
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y virtualbox
  sudo modprobe vboxdrv || die "vboxdrv would not load (DKMS build or Secure Boot?)"
  VBoxManage --version
}
hostonly() {
  # DECISION RECORDED: the stock manager bridges the VM NIC. On an EC2 host a second MAC on the ENI is not routed by the VPC, so a bridged VM gets no address and the stock
  # provider (which reads the guest IP from Guest Additions) would wait forever. Host-only networking is the smallest change; it is NOT stock and is recorded as a deviation.
  VBoxManage list hostonlyifs | grep -q vboxnet0 || VBoxManage hostonlyif create
  VBoxManage hostonlyif ipconfig vboxnet0 --ip 192.168.56.1 --netmask 255.255.255.0
  VBoxManage list dhcpservers | grep -q vboxnet0 || VBoxManage dhcpserver add --interface vboxnet0 --server-ip 192.168.56.2 --netmask 255.255.255.0 --lower-ip 192.168.56.100 --upper-ip 192.168.56.200 --enable
}
repos() {
  [ -d "$OSW/.git" ] || sudo git clone https://github.com/xlang-ai/OSWorld "$OSW"
  sudo chown -R "$USER" "$OSW"
  git -C "$OSW" checkout --detach "$OSWORLD_PIN"
  [ "$(git -C "$OSW" rev-parse HEAD)" = "$OSWORLD_PIN" ] || die "OSWorld is not at the pin"
  [ -z "$(git -C "$OSW" status --porcelain)" ] || die "OSWorld checkout is modified"
  if [ ! -d "$SRC/.git" ]; then sudo mkdir -p "$SRC" && sudo chown "$USER" "$SRC"; git clone https://github.com/sahiljagtap08/drums "$SRC"; fi
  git -C "$SRC" fetch origin
  git -C "$SRC" checkout --detach "$DRUMS_REF"
  [ "$(git -C "$SRC" rev-parse HEAD)" = "$(git -C "$SRC" rev-parse "$DRUMS_REF")" ]
}
venv() {
  python3 -m venv "$SRC/.venv-host"
  "$SRC/.venv-host/bin/pip" install -q --upgrade pip
  # OSWorld's own requirements, then librosa so that NO module has to be stubbed (host_run refuses a stub unless the config allows it)
  "$SRC/.venv-host/bin/pip" install -q -r "$OSW/requirements.txt" || die "OSWorld requirements did not install; the failing package is the next thing to fix (no stub is allowed to hide it)"
  "$SRC/.venv-host/bin/pip" install -q librosa pytest "playwright==$PLAYWRIGHT_VERSION" python-xlib pyroute2
}
chromium() {
  sudo mkdir -p /opt/pw-browsers && sudo chown "$USER" /opt/pw-browsers
  PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers "$SRC/.venv-host/bin/python" -m playwright install chromium
  [ -x /opt/pw-browsers/chromium-1194/chrome-linux/chrome ] || die "Playwright did not provide chromium-1194 (set PLAYWRIGHT_VERSION to the release that ships it): the Drums template path is pinned to it"
}
kit() { sudo mkdir -p /opt/drums-osworld-real && sudo chown "$USER" /opt/drums-osworld-real; (cd "$SRC/envd-linux" && PYTHONPATH=. python3 -m envd_linux.realkit "$OSW"); }
criu() {
  mkdir -p "$CRIU_HOME/dl" && cd "$CRIU_HOME/dl"
  if [ -n "${CRIU_DEB_URLS:-}" ]; then for u in $CRIU_DEB_URLS; do curl -fsSLO "$u"; done; else apt-get download criu; fi
  for d in *.deb; do dpkg-deb -x "$d" "$CRIU_HOME/x"; done
  mkdir -p "$CRIU_HOME/x-4.2/usr/sbin" && cp "$CRIU_HOME/x/usr/sbin/criu" "$CRIU_HOME/x-4.2/usr/sbin/criu"
  "$CRIU_HOME/x-4.2/usr/sbin/criu" --version | tee "$STATE/criu.version"
  echo "NOTE: the directory is named x-4.2 because Drums looks for that path; the REAL version is in $STATE/criu.version and may not be 4.2" | tee -a "$STATE/criu.version"
}
manifest() {
  (cd "$SRC/envd-linux" && PYTHONPATH=. python3 -m envd_linux.composition --out "$STATE/drums_composition_manifest.json" --kit /opt/drums-osworld-real --osworld-src "$OSW")
  { echo "drums_ref=$DRUMS_REF"; echo "kernel=$(uname -r)"; echo "vbox=$(VBoxManage --version)"; echo "criu=$(head -1 "$STATE/criu.version")"; echo "python=$(python3 --version)"; \
    echo "librosa=$("$SRC/.venv-host/bin/python" -c 'import librosa;print(librosa.__version__)' 2>&1)"; } | tee "$STATE/host_manifest.txt"
}

step apt_base apt_base
step vbox vbox
step hostonly hostonly
step repos repos
step venv venv
step chromium chromium
step kit kit
step criu criu
step manifest manifest
say "host prepared. Next: deploy/aws_host/fetch_image.sh --accept-licence-risk, then host_run check / gates / run (docs/aws-incumbent-runbook.md)."
