"""Lifecycle and cost-control CLI for ONE authorized AWS bare-metal host (uses the installed `aws` CLI with a NON-ROOT profile). PROTOTYPE, UNEXECUTED: written and unit-tested
against a fake runner; it has never called AWS and this sandbox has no AWS credentials. Nothing runs without `--execute`; `launch` also needs an approval file the owner writes.

    python -m drums_compare.awsctl plan     --approval approval.json                  # offline: bill components, stop margin, refusals
    python -m drums_compare.awsctl launch   --approval approval.json [--execute]      # dry-run (aws --dry-run) unless --execute
    python -m drums_compare.awsctl status   --approval approval.json                  # what exists, elapsed versus ceiling, estimate to date
    python -m drums_compare.awsctl teardown --approval approval.json [--execute]      # terminate, delete owned volumes, release owned IPs, verify
    python -m drums_compare.awsctl watch    --approval approval.json [--execute]      # EXTERNAL watcher: run from the owner's laptop, not the host

CONTROLS (each a design property, each with a stated failure mode in docs/aws-incumbent-runbook.md):
  * ONE host at most: launch refuses if any owned instance exists in any non-terminated state (owned = tag drums-run=<run_id>).
  * Never root: launch and every other command refuse if the caller identity is the account root.
  * A runtime CEILING in two independent places: the host shuts itself down (user data `shutdown -h +N`, with instance-initiated shutdown behaviour TERMINATE) and the external
    watcher terminates at the ceiling. A timer is NOT a hard dollar cap: it bounds hours, and the bill is bounded only if both work, which is why a stop margin is priced in.
  * Everything created is tagged, root volume DeleteOnTermination=true, no Elastic IP is allocated (the auto-assigned public IPv4 is released on termination), IMDSv2 required.
  * SSH is allowed from ONE owner CIDR, never 0.0.0.0/0.
  * The approved budget must cover the WORST CASE (ceiling plus stop margin plus storage, address and transfer): otherwise launch refuses.
All prices are INPUTS in the approval file; none is hard-coded here and none is verified by this code."""

import argparse
import json
import subprocess
import sys
import time

ALLOWED_TYPES = ("m7i.metal-24xl", "m5.metal")  # candidates only: the final selection and budget are the owner's, in the approval file
REGION = "us-east-1"
REQUIRED = ("run_id", "approved_by", "instance_type", "region", "ceiling_hours", "stop_margin_hours", "budget_cap_usd", "hourly_usd", "ebs_gb", "ebs_usd_per_gb_month",
            "public_ipv4_usd_per_hour", "transfer_out_gb", "transfer_usd_per_gb", "ssh_cidr", "key_name", "subnet_id", "security_group_id", "image_id", "profile", "approved")


class Refusal(Exception):
    pass


def bill(a):
    """Prospective, itemized, worst case. Hours are billed to the stop margin; storage is prorated by the same hours."""
    hours = a["ceiling_hours"] + a["stop_margin_hours"]
    items = {"ec2_instance_hours": a["hourly_usd"] * hours,
             "ebs_volume": a["ebs_gb"] * a["ebs_usd_per_gb_month"] * hours / 730.0,
             "public_ipv4_address": a["public_ipv4_usd_per_hour"] * hours,
             "data_transfer_out": a["transfer_out_gb"] * a["transfer_usd_per_gb"]}
    return {"billed_hours_worst_case": hours, "items_usd": items, "worst_case_usd": sum(items.values()),
            "not_included": ["taxes", "AWS Support plan charges", "any resource not tagged to this run", "price changes", "EBS snapshots (none are taken)", "any quota-request or Marketplace charge"],
            "note": "a runtime ceiling is not a hard dollar cap; the figure holds only if the in-host shutdown or the external watcher works"}


def validate(a):
    miss = [k for k in REQUIRED if k not in a]
    if miss:
        raise Refusal(f"approval file lacks: {miss}")
    if a["approved"] is not True:
        raise Refusal("approval.approved is not true")
    if a["instance_type"] not in ALLOWED_TYPES:
        raise Refusal(f"instance_type {a['instance_type']} is not an allowed candidate {ALLOWED_TYPES}")
    if a["region"] != REGION:
        raise Refusal(f"region must be {REGION}")
    if a["ssh_cidr"] in ("0.0.0.0/0", "::/0") or "/" not in a["ssh_cidr"]:
        raise Refusal("ssh_cidr must be a specific owner CIDR, never open to the world")
    if not (0 < a["ceiling_hours"] <= 12) or a["stop_margin_hours"] < 0.25:
        raise Refusal("ceiling_hours must be in (0, 12] and stop_margin_hours at least 0.25")
    b = bill(a)
    if b["worst_case_usd"] > a["budget_cap_usd"]:
        raise Refusal(f"worst case ${b['worst_case_usd']:.2f} exceeds the approved budget ${a['budget_cap_usd']:.2f}")
    return b


class Aws:
    def __init__(self, profile, region=REGION, runner=subprocess.run):
        self.profile, self.region, self.runner = profile, region, runner

    def __call__(self, *args, check=True):
        r = self.runner(["aws", "--profile", self.profile, "--region", self.region, "--output", "json", *args], capture_output=True, text=True, timeout=300)
        if check and r.returncode != 0:
            raise RuntimeError(f"aws {' '.join(args[:3])} failed: {(r.stderr or r.stdout)[:300]}")
        return json.loads(r.stdout) if r.stdout.strip().startswith(("{", "[")) else r.stdout.strip()


def not_root(aws):
    arn = aws("sts", "get-caller-identity")["Arn"]
    if arn.endswith(":root"):
        raise Refusal("refusing to run as the account root; use a non-root IAM identity")
    return arn


def owned(aws, run_id):
    f = ["--filters", f"Name=tag:drums-run,Values={run_id}"]
    inst = [i for r in aws("ec2", "describe-instances", *f)["Reservations"] for i in r["Instances"] if i["State"]["Name"] != "terminated"]
    vols = aws("ec2", "describe-volumes", *f)["Volumes"]
    addrs = aws("ec2", "describe-addresses", *f)["Addresses"]
    return {"instances": inst, "volumes": vols, "addresses": addrs}


def user_data(a):
    return f"#!/bin/bash\n# in-host ceiling: independent of the external watcher; instance-initiated shutdown terminates the instance\nshutdown -h +{int(a['ceiling_hours'] * 60)}\n"


def launch(aws, a, execute=False, now=time.time):
    b = validate(a)
    not_root(aws)
    o = owned(aws, a["run_id"])
    if o["instances"]:
        raise Refusal(f"one host maximum: {[i['InstanceId'] for i in o['instances']]} already exists for run {a['run_id']}")
    deadline = int(now() + a["ceiling_hours"] * 3600)
    args = ["ec2", "run-instances", "--image-id", a["image_id"], "--instance-type", a["instance_type"], "--count", "1", "--key-name", a["key_name"],
            "--subnet-id", a["subnet_id"], "--security-group-ids", a["security_group_id"], "--associate-public-ip-address",
            "--instance-initiated-shutdown-behavior", "terminate", "--metadata-options", "HttpTokens=required,HttpEndpoint=enabled",
            "--block-device-mappings", json.dumps([{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": a["ebs_gb"], "VolumeType": "gp3", "DeleteOnTermination": True}}]),
            "--tag-specifications", "ResourceType=instance,Tags=[{Key=drums-run,Value=%s},{Key=drums-deadline,Value=%d}]" % (a["run_id"], deadline),
            "--tag-specifications", "ResourceType=volume,Tags=[{Key=drums-run,Value=%s}]" % a["run_id"], "--user-data", user_data(a)]
    if not execute:
        aws(*args, "--dry-run", check=False)  # AWS answers DryRunOperation (allowed) or UnauthorizedOperation: either is informational
        return {"executed": False, "bill": b, "deadline_epoch": deadline, "note": "dry run only: nothing was created"}
    out = aws(*args)
    return {"executed": True, "instance_id": out["Instances"][0]["InstanceId"], "deadline_epoch": deadline, "bill": b}


def status(aws, a, now=time.time):
    o = owned(aws, a["run_id"])
    rows = []
    for i in o["instances"]:
        dl = next((int(t["Value"]) for t in i.get("Tags", []) if t["Key"] == "drums-deadline"), None)
        rows.append({"id": i["InstanceId"], "state": i["State"]["Name"], "past_deadline": bool(dl and now() > dl), "deadline_epoch": dl, "launch_time": i.get("LaunchTime")})
    return {"instances": rows, "volumes": [v["VolumeId"] for v in o["volumes"]], "addresses": [x.get("AllocationId") for x in o["addresses"]], "more_than_one_host": len(rows) > 1}


def teardown(aws, a, execute=False, sleep=time.sleep, tries=60):
    """Terminate owned instances, wait, then delete owned volumes that survived and release owned addresses; finally verify nothing owned remains."""
    o = owned(aws, a["run_id"])
    plan = {"terminate": [i["InstanceId"] for i in o["instances"]], "volumes": [v["VolumeId"] for v in o["volumes"]], "addresses": [x["AllocationId"] for x in o["addresses"]]}
    if not execute:
        return {"executed": False, "plan": plan}
    if plan["terminate"]:
        aws("ec2", "terminate-instances", "--instance-ids", *plan["terminate"])
        for _ in range(tries):
            if not owned(aws, a["run_id"])["instances"]:
                break
            sleep(10)
    after = owned(aws, a["run_id"])
    for v in after["volumes"]:
        if not v.get("Attachments"):
            aws("ec2", "delete-volume", "--volume-id", v["VolumeId"], check=False)
    for x in after["addresses"]:
        aws("ec2", "release-address", "--allocation-id", x["AllocationId"], check=False)
    final = owned(aws, a["run_id"])
    return {"executed": True, "plan": plan, "remaining": {"instances": [i["InstanceId"] for i in final["instances"]], "volumes": [v["VolumeId"] for v in final["volumes"]],
                                                         "addresses": [x["AllocationId"] for x in final["addresses"]]}, "clean": not (final["instances"] or final["volumes"] or final["addresses"])}


def watch(aws, a, execute=False, interval=60, now=time.time, sleep=time.sleep, max_iters=None):
    """External watcher. Tears down at the deadline, or at once if more than one owned host exists; exits when nothing owned remains. Run from a machine other than the host."""
    n = 0
    while True:
        n += 1
        s = status(aws, a, now)
        print(json.dumps({"t": int(now()), **s}))
        if not s["instances"] and not s["volumes"] and not s["addresses"]:
            return {"result": "nothing owned remains"}
        if s["more_than_one_host"] or any(r["past_deadline"] for r in s["instances"]):
            return {"result": "teardown triggered", "teardown": teardown(aws, a, execute)}
        if max_iters and n >= max_iters:
            return {"result": "stopped watching (max_iters)", "last": s}
        sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="awsctl")
    ap.add_argument("cmd", choices=["plan", "launch", "status", "teardown", "watch"])
    ap.add_argument("--approval", required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    a = ap.parse_args(argv)
    ap_ = json.load(open(a.approval))
    try:
        if a.cmd == "plan":
            print(json.dumps({"bill": validate(ap_)}, indent=1))
            return 0
        aws = Aws(ap_["profile"], ap_["region"])
        not_root(aws)
        r = {"launch": lambda: launch(aws, ap_, a.execute), "status": lambda: status(aws, ap_), "teardown": lambda: teardown(aws, ap_, a.execute),
             "watch": lambda: watch(aws, ap_, a.execute, a.interval)}[a.cmd]()
        print(json.dumps(r, indent=1, default=str))
        return 0
    except Refusal as e:
        print(f"REFUSED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
