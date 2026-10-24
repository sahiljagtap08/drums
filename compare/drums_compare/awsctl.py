"""Lifecycle and cost-control CLI for ONE authorized AWS bare-metal host (uses the installed `aws` CLI with a NON-ROOT profile). PROTOTYPE, UNEXECUTED: written and unit-tested
against a fake runner; it has never called AWS and this sandbox has no AWS credentials. Nothing runs without `--execute`; `launch` also needs an approval file the owner writes.

    python -m drums_compare.awsctl plan     --approval approval.json                     # offline: bill components, stop margin, refusals
    python -m drums_compare.awsctl watch    --approval approval.json --execute --receipt R   # ARM FIRST, from another machine: waits, heartbeats, tears down
    python -m drums_compare.awsctl launch   --approval approval.json --receipt R [--execute] # needs a fresh receipt from a watcher that CAN terminate
    python -m drums_compare.awsctl status   --approval approval.json [--receipt R]
    python -m drums_compare.awsctl teardown --approval approval.json [--execute]

CONTROLS (each a design property with a stated failure mode in docs/aws-incumbent-runbook.md):
  * Exactly the approved ACCOUNT (463186160490) and never root: every command checks the caller identity's account number, not only that it is non-root.
  * ONE benchmark host at most, across run ids: launch refuses if ANY non-terminated instance exists in the region (unless the approval lists it as a known other instance)
    or any leftover volume or address tagged drums-bench=true exists. A new run id cannot bypass it.
  * DEADLINE MODE (explicit in the approval): "managed" = an AWS-managed one-time EventBridge Scheduler schedule (universal target ec2:terminateInstances, auto-deleted) created and
    READ BACK immediately after the instance exists, with the host's own `shutdown -h +N` in user data as a second stop that only takes effect AFTER the instance has booted and cloud-init has run it (user data runs after
    boot and can fail), so between RunInstances returning and the schedule's readback there is a short interval protected by NOTHING in the cloud; if the schedule cannot be created or
    does not read back exactly, or the process is interrupted by an exception or Ctrl-C, the instance is terminated at once. A kill -9 or power loss in that interval cannot be handled here. "watcher" = the external watcher below. awsctl never creates IAM roles or touches IAM.
  * (watcher mode) An independent WATCHER must be armed first: `watch` waits while nothing is owned (it does not exit), writes a heartbeat receipt, expires if no launch happens within
    `watch_arm_timeout_s`, and keeps watching after launch. `launch --execute` refuses without a fresh receipt from a watcher started with --execute (one that can terminate).
  * The AMI is DESCRIBED and its real root device name is mapped (never a hard-coded one); extra mapped devices, a foreign owner or a Marketplace product code refuse the launch;
    the approved disk is a whole number of GiB within the stage's cap.
  * ONE --tag-specifications flag carries both the instance and the volume specification (a repeated list flag is replaced by the CLI, which would lose the instance tags).
  * The security group is INSPECTED, not trusted: only approved TCP ports, only the approved owner CIDR, no IPv6, prefix list or group sources, and the subnet must be in the same VPC.
  * A runtime CEILING in two independent places: the host shuts itself down (user data `shutdown -h +N`, instance-initiated shutdown behaviour TERMINATE) and the watcher
    terminates at the deadline. A timer is NOT a hard dollar cap: it bounds hours; a stop margin is priced in.
  * Everything created is tagged, root volume DeleteOnTermination=true, no Elastic IP is allocated, IMDSv2 required.
  * A dry run reports its verdict: DryRunOperation = allowed, UnauthorizedOperation = DENIED (never shown as allowed), anything else = UNKNOWN; execute needs "allowed".
  * Every numeric approval field must be a finite number (no NaN, no infinity, no negative, no zero price).
All prices are INPUTS in the approval file; none is hard-coded here and none is verified by this code."""

import argparse
import ipaddress
import json
import math
import os
import re
import subprocess
import sys
import time

APPROVED_ACCOUNT = "463186160490"
# Each stage is a separate, owner-approved experiment with its own instance type, vCPU count and runtime ceiling. Candidates only: selection and budget are the owner's.
STAGES = {"docker_kvm_small": {"types": ("m7i.xlarge",), "vcpu": 4, "max_hours": 2.0, "max_ebs_gb": 400},       # stock OSWorld Docker/KVM cold-reset baseline, nested virtualization
          "vbox_metal": {"types": ("m7i.metal-24xl", "m5.metal"), "vcpu": 96, "max_hours": 12.0, "max_ebs_gb": 500}}  # original VirtualBox live-snapshot goal: needs the 96 vCPU quota
ALLOWED_TYPES = tuple(t for st in STAGES.values() for t in st["types"])
REGION = "us-east-1"
REQUIRED = ("run_id", "approved_by", "account_id", "stage", "quota_standard_vcpu_asserted", "instance_type", "region", "ceiling_hours", "stop_margin_hours", "budget_cap_usd", "hourly_usd", "ebs_gb", "ebs_usd_per_gb_month",
            "public_ipv4_usd_per_hour", "transfer_out_gb", "transfer_usd_per_gb", "ssh_cidr", "key_name", "subnet_id", "security_group_id", "image_id", "ami_owner_id", "deadline_mode", "profile", "approved")
POSITIVE = ("ceiling_hours", "stop_margin_hours", "budget_cap_usd", "hourly_usd", "ebs_gb", "ebs_usd_per_gb_month", "public_ipv4_usd_per_hour", "transfer_usd_per_gb")
NONNEGATIVE = ("transfer_out_gb",)
LIVE_STATES = "pending,running,stopping,stopped,shutting-down"
TAG_ALL = "drums-bench"


class Refusal(Exception):
    pass


class LaunchAborted(Exception):
    """The instance was created but the managed deadline could not be proven; termination was attempted at once."""


SCHEDULE_PREFIX = "drums-term-"
SCHEDULER_TARGET = "arn:aws:scheduler:::aws-sdk:ec2:terminateInstances"  # the universal target
ROLE_ARN_RE = re.compile(r"^arn:aws:iam::%s:role/[A-Za-z0-9+=,.@_-]{1,64}$" % APPROVED_ACCOUNT)
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,50}$")


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def bill(a):
    """Prospective, itemized, worst case. Hours are billed to the stop margin; storage is prorated by the same hours."""
    hours = a["ceiling_hours"] + a["stop_margin_hours"]
    items = {"ec2_instance_hours": a["hourly_usd"] * hours,
             "ebs_volume": a["ebs_gb"] * a["ebs_usd_per_gb_month"] * hours / 730.0,
             "public_ipv4_address": a["public_ipv4_usd_per_hour"] * hours,
             "data_transfer_out": a["transfer_out_gb"] * a["transfer_usd_per_gb"]}
    if a.get("deadline_mode") == "managed":  # priced at the paid unit rate with NO free-tier deduction (the account's free usage is unknown): never a free-of-charge assumption
        items["eventbridge_scheduler"] = a.get("scheduler_invocations", 1) * a["scheduler_usd_per_million"] / 1e6
    return {"billed_hours_worst_case": hours, "items_usd": items, "worst_case_usd": sum(items.values()),
            "not_included": ["taxes", "AWS Support plan charges", "any resource not tagged to this run", "price changes", "EBS snapshots (none are taken)", "any quota-request or Marketplace charge"],
            "note": "a runtime ceiling is not a hard dollar cap; the figure holds only if the in-host shutdown or the external watcher works"}


def validate(a):
    miss = [k for k in REQUIRED if k not in a]
    if miss:
        raise Refusal(f"approval file lacks: {miss}")
    if a["approved"] is not True:
        raise Refusal("approval.approved is not true")
    if a["account_id"] != APPROVED_ACCOUNT:
        raise Refusal(f"approval.account_id must be exactly {APPROVED_ACCOUNT}")
    for k in POSITIVE + NONNEGATIVE:
        if not finite(a[k]) or (a[k] <= 0 if k in POSITIVE else a[k] < 0):
            raise Refusal(f"approval.{k}={a[k]!r} must be a finite number " + ("greater than zero" if k in POSITIVE else "not below zero"))
    if not isinstance(a["run_id"], str) or not RUN_ID_RE.match(a["run_id"]):
        raise Refusal("run_id must be 1 to 50 characters from A-Z a-z 0-9 . _ - (it names tags and the schedule)")
    if a["deadline_mode"] not in ("watcher", "managed"):
        raise Refusal("deadline_mode must be 'watcher' or 'managed'")
    if a["deadline_mode"] == "managed":
        if not isinstance(a.get("deadline_role_arn"), str) or not ROLE_ARN_RE.match(a["deadline_role_arn"]):
            raise Refusal(f"deadline_role_arn must be an IAM role ARN in exactly account {APPROVED_ACCOUNT} (the owner creates the role; awsctl never does)")
        if not finite(a.get("scheduler_usd_per_million")) or a["scheduler_usd_per_million"] <= 0:
            raise Refusal("scheduler_usd_per_million must be a finite price above zero (a paid unit rate, no free-tier assumption)")
        if isinstance(a.get("scheduler_invocations", 1), bool) or not isinstance(a.get("scheduler_invocations", 1), int) or a.get("scheduler_invocations", 1) < 1:
            raise Refusal("scheduler_invocations must be a whole number of at least 1")
    st = STAGES.get(a["stage"])
    if st is None:
        raise Refusal(f"stage {a['stage']!r} is not one of {sorted(STAGES)}")
    if isinstance(a["ebs_gb"], bool) or not isinstance(a["ebs_gb"], int) or not 8 <= a["ebs_gb"] <= st["max_ebs_gb"]:
        raise Refusal(f"ebs_gb must be a whole number of GiB from 8 to {st['max_ebs_gb']} for stage {a['stage']} (got {a['ebs_gb']!r})")
    if not (isinstance(a["ami_owner_id"], str) and a["ami_owner_id"].isdigit() and len(a["ami_owner_id"]) == 12):
        raise Refusal("ami_owner_id must be the 12-digit account id of the AMI's owner, stated by the owner")
    if a["instance_type"] not in st["types"]:
        raise Refusal(f"instance_type {a['instance_type']} is not allowed for stage {a['stage']} {st['types']}")
    if not finite(a["quota_standard_vcpu_asserted"]) or a["quota_standard_vcpu_asserted"] < st["vcpu"]:
        raise Refusal(f"the owner-asserted Standard vCPU quota {a['quota_standard_vcpu_asserted']!r} is below the {st['vcpu']} this stage needs (an assertion, not verified here: no API call is made)")
    if a["region"] != REGION:
        raise Refusal(f"region must be {REGION}")
    if not isinstance(a["ssh_cidr"], str) or "/" not in a["ssh_cidr"]:
        raise Refusal("ssh_cidr must carry an explicit prefix such as /32")
    try:
        net = ipaddress.ip_network(a["ssh_cidr"], strict=True)
    except ValueError as e:
        raise Refusal(f"ssh_cidr is not a valid network: {e}")
    if net.version != 4 or net.prefixlen < 24:
        raise Refusal("ssh_cidr must be an IPv4 owner network of /24 or narrower (a /32 is expected), never broad or open to the world")
    if a["ceiling_hours"] > st["max_hours"] or a["stop_margin_hours"] < 0.25:
        raise Refusal(f"ceiling_hours must be at most {st['max_hours']} for stage {a['stage']} and stop_margin_hours at least 0.25")
    ports = a.get("allowed_ingress_ports", [22])
    if not ports or any(not isinstance(p, int) or isinstance(p, bool) or not 0 < p < 65536 for p in ports):
        raise Refusal("allowed_ingress_ports must be a non-empty list of valid ports")
    b = bill(a)
    if not finite(b["worst_case_usd"]) or b["worst_case_usd"] > a["budget_cap_usd"]:
        raise Refusal(f"worst case ${b['worst_case_usd']:.2f} exceeds the approved budget ${a['budget_cap_usd']:.2f}")
    return b


class Aws:
    def __init__(self, profile, region=REGION, runner=subprocess.run):
        self.profile, self.region, self.runner = profile, region, runner

    def raw(self, *args):
        r = self.runner(["aws", "--profile", self.profile, "--region", self.region, "--output", "json", *args], capture_output=True, text=True, timeout=300)
        return r.returncode, r.stdout, r.stderr

    def __call__(self, *args, check=True):
        rc, out, err = self.raw(*args)
        if check and rc != 0:
            raise RuntimeError(f"aws {' '.join(args[:3])} failed: {(err or out)[:300]}")
        return json.loads(out) if out.strip().startswith(("{", "[")) else out.strip()


def verify_account(aws, a):
    ident = aws("sts", "get-caller-identity")
    if ident["Arn"].endswith(":root"):
        raise Refusal("refusing to run as the account root; use a non-root IAM identity")
    if ident.get("Account") != a["account_id"] or ident.get("Account") != APPROVED_ACCOUNT:
        raise Refusal(f"caller account {ident.get('Account')!r} is not the approved account {APPROVED_ACCOUNT}")
    return ident["Arn"]


def owned(aws, run_id):
    f = ["--filters", f"Name=tag:drums-run,Values={run_id}"]
    inst = [i for r in aws("ec2", "describe-instances", *f)["Reservations"] for i in r["Instances"] if i["State"]["Name"] != "terminated"]
    return {"instances": inst, "volumes": aws("ec2", "describe-volumes", *f)["Volumes"], "addresses": aws("ec2", "describe-addresses", *f)["Addresses"]}


def inventory(aws, a):
    """The account-wide guard: every live instance in the region, whatever its tags or run id, plus leftover benchmark-tagged storage or addresses from ANY run id."""
    inst = [i["InstanceId"] for r in aws("ec2", "describe-instances", "--filters", f"Name=instance-state-name,Values={LIVE_STATES}")["Reservations"] for i in r["Instances"]]
    unknown = [i for i in inst if i not in a.get("known_other_instances", [])]
    tag = ["--filters", f"Name=tag:{TAG_ALL},Values=true"]
    leftovers = [v["VolumeId"] for v in aws("ec2", "describe-volumes", *tag)["Volumes"]] + [x.get("AllocationId") for x in aws("ec2", "describe-addresses", *tag)["Addresses"]]
    if unknown:
        raise Refusal(f"one benchmark host maximum, account-wide: live instance(s) {unknown} exist in {REGION}; list them in approval.known_other_instances only if they are deliberate and unrelated")
    if leftovers:
        raise Refusal(f"leftover benchmark-tagged storage or addresses from an earlier run: {leftovers}; tear them down first")
    return {"live_instances_seen": inst}


def inspect_security_group(aws, a):
    sgs = aws("ec2", "describe-security-groups", "--group-ids", a["security_group_id"])["SecurityGroups"]
    if len(sgs) != 1:
        raise Refusal("security group not found")
    sg = sgs[0]
    ports = set(a.get("allowed_ingress_ports", [22]))
    for rule in sg.get("IpPermissions", []):
        where = f"rule {rule.get('IpProtocol')} {rule.get('FromPort')}-{rule.get('ToPort')}"
        if rule.get("IpProtocol") != "tcp" or rule.get("FromPort") != rule.get("ToPort") or rule.get("FromPort") not in ports:
            raise Refusal(f"security group {a['security_group_id']}: {where} is not an approved TCP port {sorted(ports)}")
        if rule.get("Ipv6Ranges") or rule.get("PrefixListIds") or rule.get("UserIdGroupPairs"):
            raise Refusal(f"security group {a['security_group_id']}: {where} has an IPv6, prefix-list or group source, which is not approved")
        bad = [r.get("CidrIp") for r in rule.get("IpRanges", []) if r.get("CidrIp") != a["ssh_cidr"]]
        if bad:
            raise Refusal(f"security group {a['security_group_id']}: {where} allows {bad}, not only the approved {a['ssh_cidr']}")
    sub = aws("ec2", "describe-subnets", "--subnet-ids", a["subnet_id"])["Subnets"]
    if len(sub) != 1 or sub[0].get("VpcId") != sg.get("VpcId"):
        raise Refusal("the subnet is not in the security group's VPC")
    return {"group": a["security_group_id"], "ingress_rules": len(sg.get("IpPermissions", [])), "vpc": sg.get("VpcId")}


def write_receipt(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def check_receipt(path, a, now=time.time):
    """A launch needs proof that an independent watcher is armed NOW, started with --execute (so it can terminate), for this run and account, with a fresh heartbeat and
    an arm window that has not expired."""
    if not path or not os.path.exists(path):
        raise Refusal("no watcher receipt: arm `watch --execute --receipt R` from another machine first (a watcher that does not exist is not an independent stop)")
    r = json.load(open(path))
    if r.get("run_id") != a["run_id"] or r.get("account") != a["account_id"]:
        raise Refusal("watcher receipt is for a different run or account")
    if not r.get("can_terminate"):
        raise Refusal("the watcher was started without --execute: it can only observe, so it is not an independent stop")
    if r.get("state") not in ("armed", "running"):
        raise Refusal(f"watcher state is {r.get('state')!r}")
    stale = now() - r.get("last_beat", 0)
    if stale > 3 * r.get("interval_s", 60):
        raise Refusal(f"watcher heartbeat is {int(stale)}s old (more than 3 intervals): it is not running")
    if r.get("state") == "armed" and now() - r.get("armed_at", 0) >= r.get("arm_timeout_s", 0):
        raise Refusal("the watcher's arm window has expired before this launch")
    return r


DEADMAN_S = 600


def user_data(a):
    """In-host stops. ALWAYS (1) the ceiling: `shutdown -h +N`, run by cloud-init from user data: it takes effect only AFTER the host has booted and user data has executed, and user data can fail, so it does NOT cover the interval right after RunInstances. In WATCHER mode also (2) a DEAD-MAN's
    switch: the external watcher tags the instance `drums-watch-beat=<epoch>`; this script reads that tag from the instance metadata service (no credentials needed,
    `InstanceMetadataTags=enabled`) every minute and shuts the host down if the beat is older than DEADMAN_S. In MANAGED mode no watcher exists, so the dead-man is NOT installed
    (it would shut the host down for want of a beat); the AWS-managed schedule is the second stop. Instance-initiated shutdown behaviour is TERMINATE. Failure modes: in watcher mode a tag
    API throttle or an IMDS fault shuts the host down early; a root user on the host can cancel the timers."""
    ceiling = f"#!/bin/bash\nshutdown -h +{int(a['ceiling_hours'] * 60)}\n"
    if a.get("deadline_mode") == "managed":
        return ceiling
    return ceiling + f"""cat >/usr/local/bin/drums-deadman <<'DEADMAN'
#!/bin/bash
T=$(curl -s -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 60" http://169.254.169.254/latest/api/token)
B=$(curl -s -H "X-aws-ec2-metadata-token: $T" http://169.254.169.254/latest/meta-data/tags/instance/drums-watch-beat)
now=$(date +%s)
up=$(cut -d. -f1 /proc/uptime)
if [[ "$B" =~ ^[0-9]+$ ]]; then [ $((now-B)) -gt {DEADMAN_S} ] && shutdown -h now; else [ "$up" -gt {DEADMAN_S} ] && shutdown -h now; fi
DEADMAN
chmod +x /usr/local/bin/drums-deadman
echo '* * * * * root /usr/local/bin/drums-deadman' >/etc/cron.d/drums-deadman
"""


def dry_run_verdict(rc, out, err):
    t = (out or "") + (err or "")
    if "DryRunOperation" in t:
        return "allowed"
    if "UnauthorizedOperation" in t or "AccessDenied" in t:
        return "DENIED"
    return "UNKNOWN"


def schedule_name(a):
    return SCHEDULE_PREFIX + a["run_id"]


def schedule_expression(deadline):
    return time.strftime("at(%Y-%m-%dT%H:%M:%S)", time.gmtime(deadline))


def scheduler_role_documents(a):
    """The exact least-privilege documents the OWNER would create (awsctl never calls IAM). The TRUST policy follows the AWS-documented confused-deputy shape for EventBridge Scheduler:
    aws:SourceAccount is this account and aws:SourceArn is EXACTLY this account's schedule GROUP ARN (`...:schedule-group/default`) with StringEquals. AWS states that the value must be the
    group ARN and must NOT be narrowed to a schedule or a schedule-name prefix (a prefix there would make the role impossible to assume). The narrowing by schedule-name prefix belongs
    to the LAUNCHING identity's permissions below, not to the trust policy. The permission policy is of ONE action on instances in this account and region that carry the benchmark tag. Plus what the LAUNCHING identity needs."""
    acct, region = APPROVED_ACCOUNT, REGION
    group_arn = f"arn:aws:scheduler:{region}:{acct}:schedule-group/default"
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "scheduler.amazonaws.com"}, "Action": "sts:AssumeRole",
                                                     "Condition": {"StringEquals": {"aws:SourceAccount": acct, "aws:SourceArn": group_arn}}}]}
    perm = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "ec2:TerminateInstances", "Resource": f"arn:aws:ec2:{region}:{acct}:instance/*",
                                                    "Condition": {"StringEquals": {f"ec2:ResourceTag/{TAG_ALL}": "true"}}}]}
    launcher = {"scheduler": [f"scheduler:CreateSchedule", "scheduler:GetSchedule", "scheduler:DeleteSchedule"], "scheduler_resource": f"arn:aws:scheduler:{region}:{acct}:schedule/default/{SCHEDULE_PREFIX}*",
                "iam:PassRole": {"resource": a.get("deadline_role_arn", f"arn:aws:iam::{acct}:role/<the role>"), "condition": {"StringEquals": {"iam:PassedToService": "scheduler.amazonaws.com"}}},
                "note": "documentation of what the launching identity would need; nothing here grants or assumes it, and its absence makes create-schedule fail, which terminates the instance"}
    return {"role_trust_policy": trust, "role_permission_policy": perm, "launching_identity_needs": launcher}


def schedule_create_args(a, instance_id, deadline):
    target = {"Arn": SCHEDULER_TARGET, "RoleArn": a["deadline_role_arn"], "Input": json.dumps({"InstanceIds": [instance_id]}, separators=(",", ":")),
              "RetryPolicy": {"MaximumRetryAttempts": 3, "MaximumEventAgeInSeconds": 3600}}
    return ["scheduler", "create-schedule", "--name", schedule_name(a), "--group-name", "default", "--schedule-expression", schedule_expression(deadline),
            "--schedule-expression-timezone", "UTC", "--flexible-time-window", "Mode=OFF", "--action-after-completion", "DELETE", "--state", "ENABLED",
            "--description", f"drums run {a['run_id']}: terminate the benchmark host at its deadline", "--target", json.dumps(target, separators=(",", ":"))]


def verify_schedule(aws, a, instance_id, deadline):
    """Read the schedule back and require EXACTLY what was asked: enabled, the one-time expression in UTC, the universal terminate target, the approved role, one instance id, no flexible
    window, auto-delete. Anything else is a failed deadline."""
    g = aws("scheduler", "get-schedule", "--name", schedule_name(a), "--group-name", "default")
    t = g.get("Target", {})
    try:
        ids = json.loads(t.get("Input", "{}")).get("InstanceIds")
    except ValueError:
        ids = None
    bad = []
    if g.get("State") != "ENABLED":
        bad.append(f"state {g.get('State')!r}")
    if g.get("ScheduleExpression") != schedule_expression(deadline):
        bad.append(f"expression {g.get('ScheduleExpression')!r}")
    if g.get("ScheduleExpressionTimezone", "UTC") != "UTC":
        bad.append("timezone")
    if t.get("Arn") != SCHEDULER_TARGET:
        bad.append(f"target {t.get('Arn')!r}")
    if t.get("RoleArn") != a["deadline_role_arn"]:
        bad.append(f"role {t.get('RoleArn')!r}")
    if ids != [instance_id]:
        bad.append(f"instance ids {ids!r}")
    if g.get("FlexibleTimeWindow", {}).get("Mode") != "OFF":
        bad.append("flexible window")
    if g.get("ActionAfterCompletion") != "DELETE":
        bad.append(f"action after completion {g.get('ActionAfterCompletion')!r}")
    if bad:
        raise RuntimeError("schedule readback differs: " + "; ".join(bad))
    return {"name": schedule_name(a), "expression": g["ScheduleExpression"], "target": t["Arn"], "role": t["RoleArn"], "instance_ids": ids}


def abort_launch(aws, instance_id, reason):
    ok = True
    try:
        aws("ec2", "terminate-instances", "--instance-ids", instance_id)
    except Exception as e:  # noqa: BLE001 - reported, never swallowed
        ok = False
        reason += f"; ALSO the immediate terminate-instances failed ({str(e)[:120]}): the host's own shutdown timer is then the only remaining stop"
    return LaunchAborted(f"instance {instance_id} was created but its deadline could not be proven ({reason}); terminate-instances was {'issued' if ok else 'NOT accepted'}")


def schedule_exists(aws, a):
    rc, out, err = aws.raw("scheduler", "get-schedule", "--name", schedule_name(a), "--group-name", "default")
    if rc == 0:
        return True
    return False if "ResourceNotFoundException" in (err or "") else None  # None = could not tell


def inspect_image(aws, a):
    """The AMI is DESCRIBED before launch, never assumed: owner as stated, x86_64 HVM Linux, EBS-rooted, available, no Marketplace product code, and its block device mappings are exactly
    the root device plus any device the owner approved BY NAME. Returns the real root device name (the launch maps THAT name, never a hard-coded /dev/sda1) and its snapshot size. Non-EBS (ephemeral) mappings, which the official Canonical AMI carries as
    /dev/sdb and /dev/sdc, are suppressed with NoDevice in the launch; an extra EBS device always refuses."""
    imgs = aws("ec2", "describe-images", "--image-ids", a["image_id"])["Images"]
    if len(imgs) != 1:
        raise Refusal(f"AMI {a['image_id']} was not found in {REGION}")
    im = imgs[0]
    if im.get("OwnerId") != a["ami_owner_id"]:
        raise Refusal(f"AMI owner {im.get('OwnerId')!r} is not the approved ami_owner_id {a['ami_owner_id']!r}")
    if im.get("ProductCodes") or im.get("ImageOwnerAlias") == "aws-marketplace":
        raise Refusal("the AMI carries a Marketplace product code or is a Marketplace image: refused (no Marketplace charge is approved)")
    if im.get("State") != "available" or im.get("Architecture") != "x86_64" or im.get("RootDeviceType") != "ebs" or im.get("VirtualizationType") != "hvm":
        raise Refusal("the AMI must be available, x86_64, hvm and EBS-rooted")
    root = im.get("RootDeviceName")
    maps = {m["DeviceName"]: m for m in im.get("BlockDeviceMappings", [])}
    if root not in maps or "Ebs" not in maps[root]:
        raise Refusal(f"the AMI's root device {root!r} has no EBS mapping")
    others = {n: m for n, m in maps.items() if n != root}
    extra_ebs = sorted(n for n, m in others.items() if "Ebs" in m)
    if extra_ebs:
        raise Refusal(f"the AMI maps additional EBS devices {extra_ebs}: they would be extra volumes and bill, and none is approvable; use another AMI")
    suppress = sorted(n for n, m in others.items() if "Ebs" not in m)  # instance-store (ephemeral) mappings: the instance types here are EBS-only, so each is suppressed with NoDevice
    snap_gb = maps[root]["Ebs"].get("VolumeSize") or 0
    if a["ebs_gb"] < snap_gb:
        raise Refusal(f"approved ebs_gb {a['ebs_gb']} is below the AMI's own root size {snap_gb}")
    return {"root_device": root, "ami_root_gb": snap_gb, "owner": im.get("OwnerId"), "name": im.get("Name"), "suppress_devices": suppress}


def run_instances_args(a, deadline, root_device, suppress=()):
    """ONE --tag-specifications flag with BOTH specifications as its values: the AWS CLI replaces an earlier occurrence of a list option with a later one, so repeating the flag
    would silently drop the instance tags (and leave the watcher blind). The root mapping uses the AMI's real device name and nothing else."""
    inst_tags = "ResourceType=instance,Tags=[{Key=drums-run,Value=%s},{Key=%s,Value=true},{Key=drums-deadline,Value=%d}]" % (a["run_id"], TAG_ALL, deadline)
    vol_tags = "ResourceType=volume,Tags=[{Key=drums-run,Value=%s},{Key=%s,Value=true}]" % (a["run_id"], TAG_ALL)
    args = ["ec2", "run-instances", "--image-id", a["image_id"], "--instance-type", a["instance_type"], "--count", "1", "--key-name", a["key_name"],
            "--subnet-id", a["subnet_id"], "--security-group-ids", a["security_group_id"], "--associate-public-ip-address",
            "--instance-initiated-shutdown-behavior", "terminate", "--metadata-options", "HttpTokens=required,HttpEndpoint=enabled,InstanceMetadataTags=enabled",
            "--block-device-mappings", json.dumps([{"DeviceName": root_device, "Ebs": {"VolumeSize": a["ebs_gb"], "VolumeType": "gp3", "DeleteOnTermination": True}}] + [{"DeviceName": n, "NoDevice": ""} for n in suppress]),
            "--tag-specifications", inst_tags, vol_tags, "--user-data", user_data(a)]
    if a.get("cpu_options"):  # e.g. a nested-virtualization option, copied by the owner from the live describe-instance-types result; never assumed here
        args += ["--cpu-options", a["cpu_options"]]
    return args


def launch(aws, a, execute=False, now=time.time, receipt=None):
    b = validate(a)
    verify_account(aws, a)
    inv = inventory(aws, a)
    owned_now = owned(aws, a["run_id"])
    if owned_now["instances"]:
        raise Refusal(f"one host maximum: {[i['InstanceId'] for i in owned_now['instances']]} already exists for run {a['run_id']}")
    sg = inspect_security_group(aws, a)
    rc_ = check_receipt(receipt, a, now) if (execute and a["deadline_mode"] == "watcher") else None
    deadline = int(now() + a["ceiling_hours"] * 3600)
    image = inspect_image(aws, a)
    args = run_instances_args(a, deadline, image["root_device"], image["suppress_devices"])
    verdict = dry_run_verdict(*aws.raw(*args, "--dry-run"))
    base = {"bill": b, "deadline_epoch": deadline, "dry_run_verdict": verdict, "security_group": sg, "inventory": inv, "image": image}
    if not execute:
        return {"executed": False, **base, "note": "dry run only: nothing was created" + ("" if verdict == "allowed" else f"; the dry run says {verdict}, so --execute would be refused")}
    if verdict != "allowed":
        raise Refusal(f"the dry run verdict is {verdict}: launch is not authorized for this identity (nothing was created)")
    out = aws(*args)
    iid = out["Instances"][0]["InstanceId"]
    deadline_info = {"mode": a["deadline_mode"], "in_host_ceiling_minutes": int(a["ceiling_hours"] * 60)}
    if a["deadline_mode"] == "managed":
        try:
            aws(*schedule_create_args(a, iid, deadline))
            deadline_info["schedule"] = verify_schedule(aws, a, iid, deadline)
        except BaseException as e:  # noqa: BLE001 - ANY failure to create or prove the deadline, including an interrupt (Ctrl-C), terminates the instance now
            raise abort_launch(aws, iid, f"{type(e).__name__}: {str(e)[:200]}") from e
    else:
        deadline_info["watcher"] = {k: rc_.get(k) for k in ("state", "last_beat", "armed_at")}
    return {"executed": True, "instance_id": iid, "deadline": deadline_info, **base}


def status(aws, a, now=time.time, receipt=None):
    o = owned(aws, a["run_id"])
    rows = []
    for i in o["instances"]:
        dl = next((int(t["Value"]) for t in i.get("Tags", []) if t["Key"] == "drums-deadline"), None)
        rows.append({"id": i["InstanceId"], "state": i["State"]["Name"], "past_deadline": bool(dl and now() > dl), "deadline_epoch": dl, "launch_time": i.get("LaunchTime")})
    out = {"instances": rows, "volumes": [v["VolumeId"] for v in o["volumes"]], "addresses": [x.get("AllocationId") for x in o["addresses"]], "more_than_one_host": len(rows) > 1}
    if a.get("deadline_mode") == "managed":
        out["deadline_schedule_exists"] = schedule_exists(aws, a)
    if receipt and os.path.exists(receipt):
        r = json.load(open(receipt))
        out["watcher"] = {"state": r.get("state"), "heartbeat_age_s": int(now() - r.get("last_beat", 0)), "stale": now() - r.get("last_beat", 0) > 3 * r.get("interval_s", 60)}
    return out


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
    sched = None
    if a.get("deadline_mode") == "managed":  # the one-time schedule deletes itself after it fires; if the teardown came first it is deleted here, and its absence is verified
        if schedule_exists(aws, a) is not False:
            aws("scheduler", "delete-schedule", "--name", schedule_name(a), "--group-name", "default", check=False)
        sched = schedule_exists(aws, a)
    after = owned(aws, a["run_id"])
    for v in after["volumes"]:
        if not v.get("Attachments"):
            aws("ec2", "delete-volume", "--volume-id", v["VolumeId"], check=False)
    for x in after["addresses"]:
        aws("ec2", "release-address", "--allocation-id", x["AllocationId"], check=False)
    final = owned(aws, a["run_id"])
    resources_gone = not (final["instances"] or final["volumes"] or final["addresses"])
    schedule_ok = a.get("deadline_mode") != "managed" or sched is False  # an unknown schedule state (None) is never reported clean
    return {"executed": True, "plan": plan, "remaining": {"instances": [i["InstanceId"] for i in final["instances"]], "volumes": [v["VolumeId"] for v in final["volumes"]],
                                                         "addresses": [x["AllocationId"] for x in final["addresses"]], "deadline_schedule_exists": sched},
            "clean": resources_gone and schedule_ok}


def watch(aws, a, execute=False, interval=60, now=time.time, sleep=time.sleep, max_iters=None, receipt=None, arm_timeout_s=None):
    """External watcher, ARMED BEFORE the launch: while nothing is owned it WAITS (heartbeating) until `arm_timeout_s` passes without a launch, then exits non-clean; once something
    exists it keeps watching and tears down at the deadline, or at once if more than one owned host exists; it exits when everything it watched is gone. Every beat rewrites the
    receipt that `launch --execute` demands. Without execute it can only observe, and says so in the receipt."""
    arm_timeout_s = arm_timeout_s or a.get("watch_arm_timeout_s", 1800)
    armed_at, state, n = now(), "armed", 0

    def beat(extra=None):
        if receipt:
            write_receipt(receipt, {"run_id": a["run_id"], "account": a["account_id"], "state": state, "armed_at": armed_at, "last_beat": now(), "interval_s": interval,
                                    "arm_timeout_s": arm_timeout_s, "can_terminate": bool(execute), "pid": os.getpid(), **(extra or {})})
    while True:
        n += 1
        verify_account(aws, a)
        s = status(aws, a, now)
        present = bool(s["instances"] or s["volumes"] or s["addresses"])
        if present and state == "armed":
            state = "running"
        beat()
        for i in s["instances"]:  # the dead-man heartbeat the host reads from its own metadata tags
            if i["state"] in ("pending", "running"):
                aws("ec2", "create-tags", "--resources", i["id"], "--tags", f"Key=drums-watch-beat,Value={int(now())}", check=False)
        print(json.dumps({"t": int(now()), "watcher_state": state, **s}))
        if not present:
            if state == "running":
                state = "done"
                beat()
                return {"result": "everything watched is gone", "state": state}
            if now() - armed_at >= arm_timeout_s:
                state = "expired"
                beat()
                return {"result": "arm window expired with no launch: nothing was created", "state": state}
        else:
            if s["more_than_one_host"] or any(r["past_deadline"] for r in s["instances"]):
                r = teardown(aws, a, execute)
                state = "done" if r.get("clean") else "teardown_incomplete"
                beat()
                return {"result": "teardown triggered", "teardown": r, "state": state}
        if max_iters and n >= max_iters:
            return {"result": "stopped watching (max_iters)", "state": state, "last": s}
        sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="awsctl")
    ap.add_argument("cmd", choices=["plan", "launch", "status", "teardown", "watch"])
    ap.add_argument("--approval", required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--receipt")
    a = ap.parse_args(argv)
    ap_ = json.load(open(a.approval))
    try:
        if a.cmd == "plan":
            out = {"bill": validate(ap_)}
            if ap_["deadline_mode"] == "managed":
                out["managed_deadline"] = {"schedule_name": schedule_name(ap_), "schedule_target": SCHEDULER_TARGET, "documents_for_the_owner_to_create": scheduler_role_documents(ap_),
                                           "note": "awsctl creates no role; the role, its policies and the launching identity's permissions are separate owner approvals"}
            print(json.dumps(out, indent=1))
            return 0
        validate(ap_)
        aws = Aws(ap_["profile"], ap_["region"])
        verify_account(aws, ap_)
        r = {"launch": lambda: launch(aws, ap_, a.execute, receipt=a.receipt), "status": lambda: status(aws, ap_, receipt=a.receipt), "teardown": lambda: teardown(aws, ap_, a.execute),
             "watch": lambda: watch(aws, ap_, a.execute, a.interval, receipt=a.receipt)}[a.cmd]()
        print(json.dumps(r, indent=1, default=str))
        return 0
    except Refusal as e:
        print(f"REFUSED: {e}")
        return 2
    except LaunchAborted as e:
        print(f"LAUNCH ABORTED: {e}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
