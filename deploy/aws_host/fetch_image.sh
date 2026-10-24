#!/usr/bin/env bash
# Fetch and prepare the OFFICIAL OSWorld VirtualBox image on the authorized host. PROTOTYPE, UNRUN. Run AS ROOT on the host after bootstrap.sh (STAGE=vbox): the VM must be registered
# in the same VirtualBox user registry that host_run uses.
#   sudo bash deploy/aws_host/fetch_image.sh --accept-licence-risk
#
# Source: https://huggingface.co/datasets/xlangai/ubuntu_x86_virtualbox/resolve/main/Ubuntu.zip (the stock manager's URL, OSWorld 04718ea). Its dataset card's License field is
# BLANK: that is an unresolved licence uncertainty, not a ban and not permission, which is why the flag is required. The image's REAL composition is unknown until the manifest this
# script writes exists; nothing here assumes it matches the Drums arm. No mirror, no alternative image, no self-built image.
set -euo pipefail
[ "${1:-}" = "--accept-licence-risk" ] || { echo "REFUSED: pass --accept-licence-risk after reading the comment at the top of this file (the card's License field is blank)"; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "REFUSED: run as root (the VirtualBox registry is per user and host_run runs as root)"; exit 2; }
VM=${VMNAME:-Ubuntu0}; WORK=${IMGDIR:-/opt/vm-image}; OUT=${OUT:-/var/lib/drums-host/image_manifest.json}
URL=https://huggingface.co/datasets/xlangai/ubuntu_x86_virtualbox/resolve/main/Ubuntu.zip
mkdir -p "$WORK"; cd "$WORK"
command -v VBoxManage >/dev/null || { echo "REFUSED: VirtualBox is not installed (run bootstrap.sh)"; exit 2; }
VBoxManage list vms | grep -q "\"$VM\"" && { echo "REFUSED: a VM named $VM already exists; remove it deliberately first (nothing is deleted here)"; exit 2; }

# completeness is CHECKED, never assumed: an existing partial zip must not be treated as done
EXPECT=$(curl -sIL "$URL" | awk 'tolower($1)=="content-length:"{v=$2} END{gsub("\r","",v); print v}')
[ -n "$EXPECT" ] || { echo "REFUSED: could not read the image size from the server (no Content-Length)"; exit 3; }
HAVE=$(stat -c %s Ubuntu.zip 2>/dev/null || echo 0)
[ "$HAVE" -le "$EXPECT" ] || { echo "REFUSED: the local Ubuntu.zip ($HAVE) is larger than the server's ($EXPECT): remove it deliberately"; exit 3; }
if [ "$HAVE" -lt "$EXPECT" ]; then
  AVAIL=$(df --output=avail -B1 "$WORK" | tail -1)
  [ "$AVAIL" -gt $((EXPECT * 3)) ] || { echo "REFUSED: free space $AVAIL is under 3x the download ($EXPECT)"; exit 3; }
  curl -fL -C - --max-filesize $((EXPECT + 1048576)) -o Ubuntu.zip "$URL"
fi
[ "$(stat -c %s Ubuntu.zip)" -eq "$EXPECT" ] || { echo "REFUSED: Ubuntu.zip is not complete ($(stat -c %s Ubuntu.zip) of $EXPECT bytes)"; exit 3; }
unzip -tq Ubuntu.zip >/dev/null || { echo "REFUSED: Ubuntu.zip failed its integrity test"; exit 3; }
SHA=$(sha256sum Ubuntu.zip | cut -d' ' -f1); echo "Ubuntu.zip bytes=$EXPECT sha256=$SHA (no expected hash exists to compare: RECORDED ONLY)"
rm -rf Ubuntu.partial; mkdir Ubuntu.partial; unzip -q Ubuntu.zip -d Ubuntu.partial
[ -f Ubuntu.partial/Ubuntu/Ubuntu.ovf ] || { echo "REFUSED: the archive does not contain Ubuntu/Ubuntu.ovf"; exit 3; }
rm -rf Ubuntu; mv Ubuntu.partial/Ubuntu Ubuntu; rmdir Ubuntu.partial
# same invocation as the stock manager (desktop_env/providers/virtualbox/manager.py `import_vm`), plus the parity settings
VBoxManage import "$WORK/Ubuntu/Ubuntu.ovf" --vsys 0 --vmname "$VM" --settingsfile "$WORK/$VM/$VM.vbox" --basefolder "$WORK" --unit 14 --disk "$WORK/$VM/${VM}_disk1.vmdk"
VBoxManage modifyvm "$VM" --cpus "${GUEST_CPUS:-2}" --memory "${GUEST_MEM_MIB:-4096}" --nic1 hostonly --hostonlyadapter1 vboxnet0   # network is a RECORDED deviation from the stock bridged NIC
VBoxManage startvm "$VM" --type headless
for i in $(seq 1 120); do
  IP=$(VBoxManage guestproperty get "$VM" /VirtualBox/GuestInfo/Net/0/V4/IP | awk '/Value:/{print $2}')
  [ -n "${IP:-}" ] && curl -fs -m 3 "http://$IP:5000/platform" >/dev/null && break
  sleep 5
done
[ -n "${IP:-}" ] && curl -fs -m 3 "http://$IP:5000/platform" >/dev/null || { echo "REFUSED: the guest server did not answer on ${IP:-?}:5000 (Guest Additions? the image's own service?)"; exit 3; }
guest() { python3 - "$IP" "$1" <<'PY'
import json,sys,urllib.request
ip,cmd=sys.argv[1],sys.argv[2]
r=urllib.request.Request(f"http://{ip}:5000/execute",data=json.dumps({"command":cmd,"shell":True}).encode(),headers={"Content-Type":"application/json"})
print(json.load(urllib.request.urlopen(r,timeout=60)).get("output",""))
PY
}
export G_OS="$(guest 'cat /etc/os-release')" G_KERNEL="$(guest 'uname -r')" G_DESKTOP="$(guest 'echo $XDG_CURRENT_DESKTOP; gnome-shell --version 2>&1 | head -1')" \
       G_BROWSER="$(guest 'google-chrome --version 2>&1 | head -1; chromium --version 2>&1 | head -1')" G_LO="$(guest 'soffice --version 2>&1 | head -1')" \
       G_USER="$(guest 'id -un; echo $HOME')" G_DPKG="$(guest "dpkg-query -W -f='\${Package}=\${Version}\n' 2>/dev/null | sort")" \
       G_VMINFO="$(VBoxManage showvminfo "$VM" --machinereadable | grep -E '^(cpus|memory|nic1|hostonlyadapter1|VMState)=')" G_SHA="$SHA" G_BYTES="$EXPECT" G_VM="$VM" G_OUT="$OUT" G_URL="$URL"
python3 - <<'PY'
import json,os
e=os.environ
m={"source_url":e["G_URL"],"zip_bytes":int(e["G_BYTES"]),"zip_sha256":e["G_SHA"],"license_field":"BLANK on the dataset card (unresolved)","vm":e["G_VM"],"composition_known":True,"os_release":e["G_OS"],
   "kernel":e["G_KERNEL"],"desktop":e["G_DESKTOP"],"browser":e["G_BROWSER"],"libreoffice":e["G_LO"],"guest_user_and_home":e["G_USER"],"dpkg":e["G_DPKG"].splitlines(),"vm_info":e["G_VMINFO"].splitlines()}
json.dump(m,open(e["G_OUT"],"w"),indent=1)
print("wrote",e["G_OUT"])
PY
# the stock provider reverts to the snapshot named init_state on every reset: take it LIVE now (memory included), then leave the VM in the saved state like the stock stop does
VBoxManage snapshot "$VM" take init_state
VBoxManage controlvm "$VM" savestate
echo "image ready: VM=$VM, snapshot init_state, manifest $OUT."
