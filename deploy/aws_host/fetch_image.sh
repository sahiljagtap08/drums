#!/usr/bin/env bash
# Fetch and prepare the OFFICIAL OSWorld VirtualBox image on the authorized host. PROTOTYPE, UNRUN. Owner-run, on the host, after bootstrap.sh.
#   bash deploy/aws_host/fetch_image.sh --accept-licence-risk
#
# Source: https://huggingface.co/datasets/xlangai/ubuntu_x86_virtualbox/resolve/main/Ubuntu.zip (the stock manager's URL, OSWorld 04718ea). Its dataset card's License field is
# BLANK: that is an unresolved licence uncertainty, not a ban and not permission, which is why the flag is required. The image's REAL composition is unknown until the manifest
# this script writes exists; nothing here assumes it matches the Drums arm. No third-party mirror, no alternative image, no self-built image.
set -euo pipefail
[ "${1:-}" = "--accept-licence-risk" ] || { echo "REFUSED: pass --accept-licence-risk after reading the comment at the top of this file (the card's License field is blank)"; exit 2; }
VM=${VMNAME:-Ubuntu0}; WORK=${IMGDIR:-/opt/vm-image}; OUT=${OUT:-/var/lib/drums-host/image_manifest.json}
URL=https://huggingface.co/datasets/xlangai/ubuntu_x86_virtualbox/resolve/main/Ubuntu.zip
mkdir -p "$WORK"; cd "$WORK"
command -v VBoxManage >/dev/null || { echo "REFUSED: VirtualBox is not installed (run bootstrap.sh)"; exit 2; }
VBoxManage list vms | grep -q "\"$VM\"" && { echo "REFUSED: a VM named $VM already exists; remove it deliberately first (nothing is deleted here)"; exit 2; }

[ -f Ubuntu.zip ] || curl -fL -C - -o Ubuntu.zip "$URL"
SHA=$(sha256sum Ubuntu.zip | cut -d' ' -f1); echo "Ubuntu.zip sha256=$SHA (no expected hash exists to compare: RECORDED ONLY)"
[ -d Ubuntu ] || unzip -q Ubuntu.zip
# same invocation as the stock manager (desktop_env/providers/virtualbox/manager.py `import_vm`), plus the 2 vCPU / 4096 MiB parity settings
VBoxManage import "$WORK/Ubuntu/Ubuntu.ovf" --vsys 0 --vmname "$VM" --settingsfile "$WORK/$VM/$VM.vbox" --basefolder "$WORK" --unit 14 --disk "$WORK/$VM/${VM}_disk1.vmdk"
VBoxManage modifyvm "$VM" --cpus 2 --memory 4096 --nic1 hostonly --hostonlyadapter1 vboxnet0   # network is a RECORDED deviation from the stock bridged NIC (see bootstrap.sh)
VBoxManage startvm "$VM" --type headless
for i in $(seq 1 120); do
  IP=$(VBoxManage guestproperty get "$VM" /VirtualBox/GuestInfo/Net/0/V4/IP | awk '/Value:/{print $2}')
  [ -n "${IP:-}" ] && curl -fs -m 3 "http://$IP:5000/platform" >/dev/null && break
  sleep 5
done
[ -n "${IP:-}" ] && curl -fs -m 3 "http://$IP:5000/platform" >/dev/null || { echo "REFUSED: the guest server did not answer on $IP:5000 (Guest Additions? the image's own service?)"; exit 3; }
guest() { python3 - "$IP" "$1" <<'PY'
import json,sys,urllib.request
ip,cmd=sys.argv[1],sys.argv[2]
r=urllib.request.Request(f"http://{ip}:5000/execute",data=json.dumps({"command":cmd,"shell":True}).encode(),headers={"Content-Type":"application/json"})
print(json.load(urllib.request.urlopen(r,timeout=60)).get("output",""))
PY
}
# composition of the REAL image: what it actually contains, to be diffed against the Drums arm's manifest (docs/incumbent-preflight-plan.md section 4a)
export G_OS="$(guest 'cat /etc/os-release')" G_KERNEL="$(guest 'uname -r')" G_DESKTOP="$(guest 'echo $XDG_CURRENT_DESKTOP; gnome-shell --version 2>&1 | head -1')" \
       G_BROWSER="$(guest 'google-chrome --version 2>&1 | head -1; chromium --version 2>&1 | head -1')" G_LO="$(guest 'soffice --version 2>&1 | head -1')" \
       G_USER="$(guest 'id -un; echo $HOME')" G_DPKG="$(guest "dpkg-query -W -f='\${Package}=\${Version}\n' 2>/dev/null | sort")" \
       G_VMINFO="$(VBoxManage showvminfo "$VM" --machinereadable | grep -E '^(cpus|memory|nic1|hostonlyadapter1|VMState)=')" G_SHA="$SHA" G_VM="$VM" G_OUT="$OUT" G_URL="$URL"
python3 - <<'PY'
import json,os
e=os.environ
m={"source_url":e["G_URL"],"zip_sha256":e["G_SHA"],"license_field":"BLANK on the dataset card (unresolved)","vm":e["G_VM"],"composition_known":True,"os_release":e["G_OS"],
   "kernel":e["G_KERNEL"],"desktop":e["G_DESKTOP"],"browser":e["G_BROWSER"],"libreoffice":e["G_LO"],"guest_user_and_home":e["G_USER"],"dpkg":e["G_DPKG"].splitlines(),"vm_info":e["G_VMINFO"].splitlines()}
json.dump(m,open(e["G_OUT"],"w"),indent=1)
print("wrote",e["G_OUT"])
PY
# the stock provider reverts to the snapshot named init_state on every reset: take it LIVE now (memory included), then leave the VM in the saved state like the stock stop does
VBoxManage snapshot "$VM" take init_state
VBoxManage controlvm "$VM" savestate
echo "image ready: VM=$VM, snapshot init_state, manifest $OUT. Next: python -m drums_compare.host_run check ..."
