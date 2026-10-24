#!/usr/bin/env bash
# Prepare ONE authorized Ubuntu 24.04 x86 host for a matched Drums run against a STOCK OSWorld incumbent. PROTOTYPE, UNRUN: written in a sandbox that has no VirtualBox, no hardware
# virtualization and no network authority, so nothing here has executed. Run it ON THE HOST as a normal sudo-capable user (never root), idempotently; it makes NO cloud API call.
#
#   STAGE=docker DRUMS_REF=<commit> DOCKER_DIGEST=sha256:<64 hex> bash deploy/aws_host/bootstrap.sh     # small stage: stock Docker/KVM, cold reset
#   STAGE=vbox   DRUMS_REF=<commit> bash deploy/aws_host/bootstrap.sh                                  # original goal: stock VirtualBox, live snapshots (bare metal)
#
# STAGE and DRUMS_REF have no defaults on purpose: the host runs an exact, recorded commit and one declared stage. Optional: CRIU_DEB_URLS="url1 url2" (vbox stage only: exact .deb files
# for a private CRIU; otherwise the distribution's `criu` is extracted privately and its REAL version is recorded; restore on this kernel is UNTESTED until `host_run gates` passes).
#
# TWO PYTHON ENVIRONMENTS, DO NOT CONFUSE THEM: $SRC/.venv-host runs host_run and the OSWorld `DesktopEnv` (it has librosa so no module is stubbed); /opt/drums-osworld-real/venv is the
# guest server's own environment inside Drums sessions (built by envd_linux.realkit; it needs no librosa). host_run itself must run as ROOT (sudo): Drums needs namespaces and
# mounts, and VirtualBox keeps its VM registry per user, so every VBoxManage call here and in fetch_image.sh runs as root too.
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
case "${STAGE:-}" in docker|vbox) ;; *) die "set STAGE=docker (small Docker/KVM cold-reset stage) or STAGE=vbox (VirtualBox, bare metal)";; esac
[ "$STAGE" != docker ] || [[ "${DOCKER_DIGEST:-}" =~ ^sha256:[0-9a-f]{64}$ ]] || die "set DOCKER_DIGEST=sha256:<64 hex> (the pinned happysixd/osworld-docker digest recorded in docs/aws-small-stage-plan.md)"
. /etc/os-release; [ "$ID" = ubuntu ] && [ "$VERSION_ID" = 24.04 ] || die "Ubuntu 24.04 expected, found $ID $VERSION_ID"
[ "$(uname -m)" = x86_64 ] || die "x86_64 expected"
grep -qE ' (vmx|svm)( |$)' /proc/cpuinfo || die "no vmx/svm flag: hardware virtualization is not exposed to this host"
if [ "$STAGE" = vbox ]; then [ "$(systemd-detect-virt || true)" = none ] || die "STAGE=vbox needs a non-virtualized (bare-metal) host, found $(systemd-detect-virt || true)"
else [ -e /dev/kvm ] || die "STAGE=docker needs /dev/kvm (the stock Docker provider silently falls back to software emulation without it, so this refuses)"; fi
for d in "$STATE" "$SRC" "$OSW" "$CRIU_HOME" /opt/vm-image /opt/drums-osworld-real /opt/pw-browsers; do sudo mkdir -p "$d" && sudo chown "$USER" "$d"; done

step() { local n=$1; shift; if [ -e "$STATE/$n.done" ]; then echo "skip $n (done)"; else say "$n"; "$@"; touch "$STATE/$n.done"; fi; }

apt_base() {
  sudo apt-get update -y
  # the realkit's own list (envd_linux/realkit.py: NEEDED_BINARIES and NEEDED_FILES) plus what Drums and the GUI gate need
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git curl unzip python3-venv python3-pip python3.12 build-essential iproute2 nftables xvfb openbox wmctrl xclip x11-utils util-linux sudo \
    python3-pyatspi at-spi2-core dbus-x11 python3-gi gir1.2-atspi-2.0 python3-tk scrot gnome-screenshot socat gnome-terminal gir1.2-gtk-3.0 python3-xlib python3-pyroute2 libreoffice-writer qemu-utils
}
vbox() {
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y dkms "linux-headers-$(uname -r)" virtualbox   # GPL base package: NO Extension Pack is installed or assumed
  sudo modprobe vboxdrv || die "vboxdrv would not load (DKMS build or Secure Boot?)"
  sudo VBoxManage --version
}
hostonly() {
  # DECISION RECORDED: the stock manager bridges the VM NIC; an EC2 host does not route a second MAC, so the stock provider (which reads the guest IP from Guest Additions) would wait
  # forever. Host-only networking is the smallest change; it is NOT stock and is recorded as a deviation. All VBoxManage calls run as root, like host_run.
  sudo VBoxManage list hostonlyifs | grep -q vboxnet0 || sudo VBoxManage hostonlyif create
  sudo VBoxManage hostonlyif ipconfig vboxnet0 --ip 192.168.56.1 --netmask 255.255.255.0
  sudo VBoxManage list dhcpservers | grep -q vboxnet0 || sudo VBoxManage dhcpserver add --interface vboxnet0 --server-ip 192.168.56.2 --netmask 255.255.255.0 --lower-ip 192.168.56.100 --upper-ip 192.168.56.200 --enable
}
docker_stack() {
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io
  sudo systemctl enable --now docker
  sudo docker pull "happysixd/osworld-docker@$DOCKER_DIGEST"
  sudo docker tag "happysixd/osworld-docker@$DOCKER_DIGEST" happysixd/osworld-docker:latest   # the stock provider creates containers from `happysixd/osworld-docker` (latest)
  sudo docker image inspect happysixd/osworld-docker --format '{{index .RepoDigests 0}}' | tee "$STATE/docker_image.digest"
  grep -q "$DOCKER_DIGEST" "$STATE/docker_image.digest" || die "the local image is not the pinned digest"
}
repos() {
  [ -d "$OSW/.git" ] || git clone https://github.com/xlang-ai/OSWorld "$OSW"
  git -C "$OSW" checkout --detach "$OSWORLD_PIN"
  [ "$(git -C "$OSW" rev-parse HEAD)" = "$OSWORLD_PIN" ] || die "OSWorld is not at the pin"
  [ -z "$(git -C "$OSW" status --porcelain)" ] || die "OSWorld checkout is modified"
  [ -d "$SRC/.git" ] || git clone https://github.com/sahiljagtap08/drums "$SRC"
  git -C "$SRC" fetch origin
  git -C "$SRC" checkout --detach "$DRUMS_REF"
  [ "$(git -C "$SRC" rev-parse HEAD)" = "$(git -C "$SRC" rev-parse "$DRUMS_REF")" ]
  [ -z "$(git -C "$SRC" status --porcelain)" ] || die "the Drums checkout is modified"
}
venv() {
  python3 -m venv "$SRC/.venv-host"
  "$SRC/.venv-host/bin/pip" install -q --upgrade pip
  # OSWorld's own requirements, then librosa so that NO module has to be stubbed (host_run refuses a stub in comparison mode)
  "$SRC/.venv-host/bin/pip" install -q -r "$OSW/requirements.txt" || die "OSWorld requirements did not install; the failing package is the next thing to fix (no stub may hide it)"
  "$SRC/.venv-host/bin/pip" install -q librosa pytest "playwright==$PLAYWRIGHT_VERSION" python-xlib pyroute2 pillow
  "$SRC/.venv-host/bin/python" -c "import librosa, PIL; print('librosa', librosa.__version__, 'Pillow', PIL.__version__)"
}
chromium() {
  PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers "$SRC/.venv-host/bin/python" -m playwright install chromium
  [ -x /opt/pw-browsers/chromium-1194/chrome-linux/chrome ] || die "Playwright did not provide chromium-1194 (set PLAYWRIGHT_VERSION to the release that ships it): the Drums template path is pinned to it"
}
kit() {  # builds /opt/drums-osworld-real (the GUEST SERVER's own venv, with --system-site-packages); it does not and need not contain librosa
  (cd "$SRC/envd-linux" && PYTHONPATH=. python3 -m envd_linux.realkit "$OSW" --kit /opt/drums-osworld-real)
}
criu() {
  cd "$CRIU_HOME" && mkdir -p dl && cd dl
  if [ -n "${CRIU_DEB_URLS:-}" ]; then for u in $CRIU_DEB_URLS; do curl -fsSLO "$u"; done; else apt-get download criu; fi
  for d in *.deb; do dpkg-deb -x "$d" "$CRIU_HOME/x"; done
  mkdir -p "$CRIU_HOME/x-4.2/usr/sbin" && cp "$CRIU_HOME/x/usr/sbin/criu" "$CRIU_HOME/x-4.2/usr/sbin/criu"
  "$CRIU_HOME/x-4.2/usr/sbin/criu" --version | tee "$STATE/criu.version"
  echo "NOTE: the directory is named x-4.2 because Drums looks for that path; the REAL version is in $STATE/criu.version and may not be 4.2" | tee -a "$STATE/criu.version"
}
manifest() {
  (cd "$SRC/envd-linux" && PYTHONPATH=. python3 -m envd_linux.composition --out "$STATE/drums_composition_manifest.json" --kit /opt/drums-osworld-real --osworld-src "$OSW")
  { echo "stage=$STAGE"; echo "drums_ref=$DRUMS_REF"; echo "kernel=$(uname -r)"; echo "virt=$(systemd-detect-virt || true)"; echo "python=$(python3 --version)"; \
    echo "librosa=$("$SRC/.venv-host/bin/python" -c 'import librosa;print(librosa.__version__)' 2>&1)"; \
    [ "$STAGE" = vbox ] && { echo "vbox=$(sudo VBoxManage --version)"; echo "criu=$(head -1 "$STATE/criu.version")"; } || echo "docker=$(sudo docker --version)"; } | tee "$STATE/host_manifest.txt"
}

step apt_base apt_base
if [ "$STAGE" = vbox ]; then step vbox vbox; step hostonly hostonly; else step docker_stack docker_stack; fi
step repos repos
step venv venv
step chromium chromium
step kit kit
[ "$STAGE" != vbox ] || step criu criu
step manifest manifest
say "host prepared (stage $STAGE)."
echo "Next, as root (sudo):"
echo "  fetch the image (deploy/aws_host/fetch_$( [ "$STAGE" = vbox ] && echo image || echo docker_image ).sh), then run host_run with THIS interpreter:"
echo "  sudo env PYTHONPATH=$SRC/compare:$SRC/envd-linux:$OSW DRUMS_CRIU_HOME=$CRIU_HOME PATH=\$PATH $SRC/.venv-host/bin/python -m drums_compare.host_run check --config <cfg.json> --out <run dir>"
