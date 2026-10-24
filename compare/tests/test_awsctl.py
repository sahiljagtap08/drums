"""awsctl guards, against a fake `aws`. Nothing here touches AWS or uses any credential."""
import json
import math

import pytest

from drums_compare import awsctl

ACCT = awsctl.APPROVED_ACCOUNT


def approval(**kw):
    a = {"run_id": "r1", "approved_by": "owner", "account_id": ACCT, "stage": "vbox_metal", "quota_standard_vcpu_asserted": 96, "instance_type": "m7i.metal-24xl", "region": "us-east-1", "ceiling_hours": 4, "stop_margin_hours": 0.5,
         "budget_cap_usd": 40.0, "hourly_usd": 4.8384, "ebs_gb": 200, "ebs_usd_per_gb_month": 0.08, "public_ipv4_usd_per_hour": 0.005, "transfer_out_gb": 20,
         "transfer_usd_per_gb": 0.09, "ssh_cidr": "203.0.113.5/32", "key_name": "k", "subnet_id": "subnet-1", "security_group_id": "sg-1", "image_id": "ami-1", "ami_owner_id": "099720109477", "deadline_mode": "watcher", "profile": "drums",
         "approved": True}
    a.update(kw)
    return a


def good_sg(**over):
    sg = {"VpcId": "vpc-1", "IpPermissions": [{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "203.0.113.5/32"}], "Ipv6Ranges": [], "PrefixListIds": [], "UserIdGroupPairs": []}]}
    sg.update(over)
    return sg


def good_image(**over):
    im = {"ImageId": "ami-1", "OwnerId": "099720109477", "State": "available", "Architecture": "x86_64", "RootDeviceType": "ebs", "VirtualizationType": "hvm", "RootDeviceName": "/dev/sda1",
          "BlockDeviceMappings": [{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 8, "SnapshotId": "snap-1"}}], "ProductCodes": [], "Name": "ubuntu-24.04"}
    im.update(over)
    return im


class FakeAws:
    def __init__(self, arn=f"arn:aws:iam::{ACCT}:user/sahil", account=ACCT, instances=(), others=(), volumes=(), addresses=(), bench_volumes=(), sg=None, subnet_vpc="vpc-1", dry="DryRunOperation", image="default", scheduler_fail=False, schedule_override=None, terminate_fail=False, delete_fails=False):
        self.arn, self.account, self.instances, self.others = arn, account, list(instances), list(others)
        self.volumes, self.addresses, self.bench_volumes, self.sg, self.subnet_vpc, self.dry = list(volumes), list(addresses), list(bench_volumes), sg or good_sg(), subnet_vpc, dry
        self.calls, self.terminated, self.image = [], False, (good_image() if image == 'default' else image)
        self.schedule, self.scheduler_fail, self.schedule_override, self.terminate_fail, self.delete_fails = None, scheduler_fail, schedule_override or {}, terminate_fail, delete_fails

    def raw(self, *args):
        self.calls.append(args)
        if args[:2] == ("ec2", "run-instances") and "--dry-run" in args:
            return 254, "", f"An error occurred ({self.dry}) when calling the RunInstances operation"
        if args[:2] == ("scheduler", "get-schedule") and self.schedule is None:
            return 254, "", "An error occurred (ResourceNotFoundException) when calling the GetSchedule operation"
        return 0, json.dumps(self._do(*args)), ""

    def __call__(self, *args, check=True):
        self.calls.append(args)
        return self._do(*args)

    def _do(self, *args):
        if args[:2] == ("sts", "get-caller-identity"):
            return {"Arn": self.arn, "Account": self.account}
        if args[:2] == ("ec2", "describe-instances"):
            flt = args[3]
            if "tag:drums-run" in flt:
                return {"Reservations": [{"Instances": [] if self.terminated else self.instances}]}
            return {"Reservations": [{"Instances": [{"InstanceId": i} for i in ([] if self.terminated else [x["InstanceId"] for x in self.instances]) + self.others]}]}
        if args[:2] == ("ec2", "describe-volumes"):
            return {"Volumes": self.bench_volumes if "tag:drums-bench" in args[3] else self.volumes}
        if args[:2] == ("ec2", "describe-addresses"):
            return {"Addresses": self.addresses if "tag:drums-run" in args[3] else []}
        if args[:2] == ("ec2", "describe-security-groups"):
            return {"SecurityGroups": [self.sg]}
        if args[:2] == ("ec2", "describe-images"):
            return {"Images": [self.image] if self.image else []}
        if args[:2] == ("ec2", "describe-subnets"):
            return {"Subnets": [{"VpcId": self.subnet_vpc}]}
        if args[:2] == ("ec2", "create-tags"):
            self.tags = getattr(self, "tags", []) + [args]
            return ""
        if args[0] == "scheduler":
            return self._scheduler(*args)
        if args[:2] == ("ec2", "terminate-instances"):
            if self.terminate_fail:
                raise RuntimeError("terminate refused")
            self.terminated = True
            return {}
        if args[:2] == ("ec2", "delete-volume"):
            self.volumes = [v for v in self.volumes if v["VolumeId"] != args[3]]
            return ""
        if args[:2] == ("ec2", "release-address"):
            self.addresses = []
            return ""
        if args[:2] == ("ec2", "run-instances"):
            return {"Instances": [{"InstanceId": "i-new"}]}
        raise AssertionError(args)

    def _scheduler(self, *args):
        if args[1] == "create-schedule":
            if self.scheduler_fail:
                raise RuntimeError("AccessDeniedException: not authorized to scheduler:CreateSchedule")
            o = dict(zip(args[2::2], args[3::2]))
            self.schedule = {"Name": o["--name"], "State": o["--state"], "ScheduleExpression": o["--schedule-expression"], "ScheduleExpressionTimezone": o["--schedule-expression-timezone"],
                             "FlexibleTimeWindow": {"Mode": o["--flexible-time-window"].split("=")[1]}, "ActionAfterCompletion": o["--action-after-completion"], "Target": json.loads(o["--target"])}
            self.schedule.update(self.schedule_override)
            return {"ScheduleArn": "arn:aws:scheduler:us-east-1:%s:schedule/default/%s" % (ACCT, o["--name"])}
        if args[1] == "get-schedule":
            if self.schedule is None:
                raise RuntimeError("ResourceNotFoundException")
            return self.schedule
        if args[1] == "delete-schedule":
            if not self.delete_fails:
                self.schedule = None
            return ""
        raise AssertionError(args)

    def made(self, name):
        return [c for c in self.calls if c[:2] == ("ec2", name)]


def inst(i="i-1", deadline=None):
    return {"InstanceId": i, "State": {"Name": "running"}, "Tags": [{"Key": "drums-deadline", "Value": str(deadline)}] if deadline else []}


def arm(tmp_path, f, a=None, **kw):
    """Arm a watcher that can terminate, for exactly one heartbeat, and return the receipt path."""
    p = str(tmp_path / "receipt.json")
    awsctl.watch(f, a or approval(), execute=True, receipt=p, max_iters=1, now=kw.get("now", lambda: 1000.0), sleep=lambda s: None)
    return p


# ---- approval validation ----
@pytest.mark.parametrize("over", [{"approved": False}, {"instance_type": "c5.large"}, {"region": "eu-west-1"}, {"ssh_cidr": "0.0.0.0/0"}, {"ssh_cidr": "203.0.113.5"}, {"ssh_cidr": "10.0.0.0/16"},
                                  {"ssh_cidr": "::/0"}, {"ssh_cidr": "203.0.113.5/24x"}, {"ceiling_hours": 13}, {"stop_margin_hours": 0.0}, {"budget_cap_usd": 5.0},
                                  {"account_id": "111111111111"}, {"allowed_ingress_ports": []}, {"allowed_ingress_ports": [0]}])
def test_unapproved_or_unsafe_approvals_are_refused(over):
    with pytest.raises(awsctl.Refusal):
        awsctl.validate(approval(**over))


@pytest.mark.parametrize("field", ["hourly_usd", "ebs_usd_per_gb_month", "public_ipv4_usd_per_hour", "transfer_usd_per_gb", "budget_cap_usd", "ceiling_hours", "ebs_gb"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, 0, "4.0", True])
def test_nan_infinite_negative_zero_string_and_bool_fields_are_refused(field, bad):
    with pytest.raises(awsctl.Refusal):
        awsctl.validate(approval(**{field: bad}))


def test_zero_transfer_volume_is_fine_but_negative_or_nan_is_not():
    awsctl.validate(approval(transfer_out_gb=0))
    for bad in (-1, float("nan")):
        with pytest.raises(awsctl.Refusal):
            awsctl.validate(approval(transfer_out_gb=bad))


def test_missing_field_and_the_example_file_are_refused():
    a = approval()
    del a["budget_cap_usd"]
    with pytest.raises(awsctl.Refusal):
        awsctl.validate(a)
    with pytest.raises(awsctl.Refusal):
        awsctl.validate(json.load(open(__file__.replace("compare/tests/test_awsctl.py", "deploy/aws_host/approval.example.json"))))


def test_bill_is_itemized_prices_the_stop_margin_and_says_it_is_not_a_hard_cap():
    b = awsctl.bill(approval())
    assert b["billed_hours_worst_case"] == 4.5 and abs(b["items_usd"]["ec2_instance_hours"] - 4.8384 * 4.5) < 1e-9
    assert set(b["items_usd"]) == {"ec2_instance_hours", "ebs_volume", "public_ipv4_address", "data_transfer_out"}
    assert math.isfinite(b["worst_case_usd"]) and "not a hard dollar cap" in b["note"]


# ---- identity, inventory, security group ----
def test_account_must_match_exactly_and_root_is_refused(tmp_path):
    for f in (FakeAws(arn=f"arn:aws:iam::{ACCT}:root"), FakeAws(account="111111111111", arn="arn:aws:iam::111111111111:user/x")):
        with pytest.raises(awsctl.Refusal):
            awsctl.launch(f, approval())


def test_a_new_run_id_cannot_bypass_the_one_host_rule():
    with pytest.raises(awsctl.Refusal, match="account-wide"):
        awsctl.launch(FakeAws(others=["i-someoneelse"]), approval(run_id="r2"))  # an untagged or other-run instance still counts
    awsctl.launch(FakeAws(others=["i-someoneelse"]), approval(known_other_instances=["i-someoneelse"]))  # only when the owner lists it
    with pytest.raises(awsctl.Refusal, match="leftover"):
        awsctl.launch(FakeAws(bench_volumes=[{"VolumeId": "vol-old"}]), approval(run_id="r9"))


@pytest.mark.parametrize("sg", [
    good_sg(IpPermissions=[{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]),
    good_sg(IpPermissions=[{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "203.0.113.5/32"}, {"CidrIp": "198.51.100.0/24"}]}]),
    good_sg(IpPermissions=[{"IpProtocol": "tcp", "FromPort": 3389, "ToPort": 3389, "IpRanges": [{"CidrIp": "203.0.113.5/32"}]}]),
    good_sg(IpPermissions=[{"IpProtocol": "tcp", "FromPort": 0, "ToPort": 65535, "IpRanges": [{"CidrIp": "203.0.113.5/32"}]}]),
    good_sg(IpPermissions=[{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "203.0.113.5/32"}]}]),
    good_sg(IpPermissions=[{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [], "Ipv6Ranges": [{"CidrIpv6": "::/0"}]}]),
    good_sg(IpPermissions=[{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [], "UserIdGroupPairs": [{"GroupId": "sg-other"}]}]),
])
def test_security_group_ingress_is_inspected(sg):
    with pytest.raises(awsctl.Refusal):
        awsctl.launch(FakeAws(sg=sg), approval())


def test_subnet_must_share_the_security_groups_vpc_and_a_clean_group_passes():
    with pytest.raises(awsctl.Refusal):
        awsctl.launch(FakeAws(subnet_vpc="vpc-2"), approval())
    r = awsctl.launch(FakeAws(), approval())
    assert r["security_group"]["ingress_rules"] == 1


# ---- dry run, receipt, execute ----
def test_a_dry_run_that_is_unauthorized_is_not_shown_as_allowed_and_blocks_execute(tmp_path):
    f = FakeAws(dry="UnauthorizedOperation")
    r = awsctl.launch(f, approval())
    assert r["executed"] is False and r["dry_run_verdict"] == "DENIED" and "DENIED" in r["note"]
    f2 = FakeAws(dry="UnauthorizedOperation")
    with pytest.raises(awsctl.Refusal, match="DENIED"):
        awsctl.launch(f2, approval(), execute=True, receipt=arm(tmp_path, f2), now=lambda: 1010.0)
    assert f2.made("run-instances") and all("--dry-run" in c for c in f2.made("run-instances"))  # nothing but the dry run was ever sent
    assert awsctl.launch(FakeAws(dry="Throttling"), approval())["dry_run_verdict"] == "UNKNOWN"


def _expired_receipt(tmp_path):
    """A receipt with a fresh heartbeat whose arm window (100 s) has already passed."""
    p = str(tmp_path / "exp.json")
    awsctl.watch(FakeAws(), approval(), execute=True, receipt=p, interval=60, arm_timeout_s=100, max_iters=1, now=lambda: 1000.0, sleep=lambda s: None)
    r = json.load(open(p))
    r["last_beat"] = 1140.0
    json.dump(r, open(p, "w"))
    return p


def test_execute_needs_a_fresh_receipt_from_a_watcher_that_can_terminate(tmp_path):
    a = approval()
    with pytest.raises(awsctl.Refusal, match="no watcher receipt"):
        awsctl.launch(FakeAws(), a, execute=True, receipt=None)
    f = FakeAws()
    p = str(tmp_path / "r.json")
    awsctl.watch(f, a, execute=False, receipt=p, max_iters=1, now=lambda: 1000.0, sleep=lambda s: None)  # observe-only watcher
    with pytest.raises(awsctl.Refusal, match="without --execute"):
        awsctl.launch(FakeAws(), a, execute=True, receipt=p, now=lambda: 1001.0)
    p2 = arm(tmp_path, f)
    with pytest.raises(awsctl.Refusal, match="heartbeat"):
        awsctl.launch(FakeAws(), a, execute=True, receipt=p2, now=lambda: 1000.0 + 3 * 60 + 1)  # the watcher stopped beating
    with pytest.raises(awsctl.Refusal, match="expired"):
        awsctl.check_receipt(_expired_receipt(tmp_path), a, now=lambda: 1150.0)
    with pytest.raises(awsctl.Refusal, match="different run"):
        awsctl.launch(FakeAws(), approval(run_id="r2"), execute=True, receipt=p2, now=lambda: 1001.0)


def test_execute_with_an_armed_watcher_sets_every_control_and_allocates_no_address(tmp_path):
    f = FakeAws()
    p = arm(tmp_path, f)
    r = awsctl.launch(f, approval(), execute=True, now=lambda: 1001.0, receipt=p)
    args = [c for c in f.made("run-instances") if "--dry-run" not in c][0]
    s = " ".join(map(str, args))
    assert r["executed"] and r["instance_id"] == "i-new" and r["deadline_epoch"] == 1001 + 4 * 3600 and r["deadline"]["watcher"]["state"] == "armed" and r["deadline"]["mode"] == "watcher"
    assert "--instance-initiated-shutdown-behavior terminate" in s and "HttpTokens=required" in s and "DeleteOnTermination" in s
    assert "Key=drums-run,Value=r1" in s and "Key=drums-bench,Value=true" in s
    assert "shutdown -h +240" in args[args.index("--user-data") + 1] and "--associate-public-ip-address" in args
    assert f.made("allocate-address") == []


# ---- the watcher ----
def test_the_watcher_waits_armed_before_launch_expires_without_one_and_keeps_watching_after(tmp_path, capsys):
    p = str(tmp_path / "r.json")
    t = {"now": 1000.0}
    f = FakeAws()
    out = awsctl.watch(f, approval(), execute=True, receipt=p, interval=60, arm_timeout_s=300, now=lambda: t["now"], sleep=lambda s: t.__setitem__("now", t["now"] + s), max_iters=3)
    assert out["result"].startswith("stopped watching") and out["state"] == "armed"  # nothing owned, yet it did NOT exit as clean
    r = json.load(open(p))
    assert r["state"] == "armed" and r["can_terminate"] is True and r["interval_s"] == 60 and r["last_beat"] == 1120.0
    out = awsctl.watch(FakeAws(), approval(), execute=True, receipt=p, interval=60, arm_timeout_s=300, now=lambda: t.__setitem__("now", t["now"] + 100) or t["now"],
                       sleep=lambda s: None, max_iters=20)
    assert out["state"] == "expired" and "nothing was created" in out["result"] and json.load(open(p))["state"] == "expired"
    # a launch appears: the SAME watcher (same loop) moves to running and then tears down at the deadline
    f2 = FakeAws()
    calls = {"n": 0}

    def now2():
        calls["n"] += 1
        if calls["n"] == 3:
            f2.instances.append(inst("i-1", deadline=1500))
        return 1000.0 + calls["n"] * 100

    out = awsctl.watch(f2, approval(), execute=True, receipt=p, interval=60, arm_timeout_s=10 ** 6, now=now2, sleep=lambda s: None, max_iters=50)
    assert out["result"] == "teardown triggered" and out["state"] == "done" and len(f2.made("terminate-instances")) == 1
    assert json.load(open(p))["state"] == "done"


def test_a_second_host_triggers_teardown_and_status_reports_a_stale_watcher(tmp_path):
    f = FakeAws(instances=[inst("i-1", deadline=10 ** 12), inst("i-2", deadline=10 ** 12)])
    out = awsctl.watch(f, approval(), execute=True, now=lambda: 1.0, sleep=lambda s: None)
    assert out["result"] == "teardown triggered"
    p = arm(tmp_path, FakeAws())
    assert awsctl.status(FakeAws(), approval(), now=lambda: 1000.0 + 10 ** 4, receipt=p)["watcher"]["stale"] is True


def test_teardown_terminates_deletes_owned_leftovers_releases_and_verifies():
    f = FakeAws(instances=[inst()], volumes=[{"VolumeId": "vol-1", "Attachments": []}], addresses=[{"AllocationId": "eip-1"}])
    assert awsctl.teardown(f, approval(), execute=False)["executed"] is False and f.made("terminate-instances") == []
    r = awsctl.teardown(f, approval(), execute=True, sleep=lambda s: None)
    assert r["clean"] and len(f.made("terminate-instances")) == 1 and len(f.made("delete-volume")) == 1 and len(f.made("release-address")) == 1


def test_teardown_reports_what_remains_when_a_volume_is_still_attached():
    f = FakeAws(instances=[inst()], volumes=[{"VolumeId": "vol-2", "Attachments": [{"InstanceId": "i-1"}]}])
    r = awsctl.teardown(f, approval(), execute=True, sleep=lambda s: None)
    assert not r["clean"] and r["remaining"]["volumes"] == ["vol-2"]


@pytest.mark.parametrize("over", [{"stage": "nope"}, {"stage": "docker_kvm_small"}, {"instance_type": "m7i.xlarge"}, {"quota_standard_vcpu_asserted": 32}, {"quota_standard_vcpu_asserted": "96"}])
def test_stage_instance_type_and_asserted_quota_must_agree(over):
    with pytest.raises(awsctl.Refusal):
        awsctl.validate(approval(**over))


def test_the_small_docker_kvm_stage_is_capped_at_two_hours_and_four_vcpu():
    small = approval(stage="docker_kvm_small", instance_type="m7i.xlarge", hourly_usd=0.2016, quota_standard_vcpu_asserted=32, ceiling_hours=2, budget_cap_usd=5)
    assert awsctl.validate(small)["billed_hours_worst_case"] == 2.5
    with pytest.raises(awsctl.Refusal):
        awsctl.validate(dict(small, ceiling_hours=2.5))
    with pytest.raises(awsctl.Refusal):
        awsctl.validate(dict(small, quota_standard_vcpu_asserted=3))


def test_user_data_has_the_ceiling_and_a_deadman_that_reads_the_watchers_beat_tag():
    u = awsctl.user_data(approval())
    assert "shutdown -h +240" in u and "drums-watch-beat" in u and "169.254.169.254" in u and str(awsctl.DEADMAN_S) in u and "/etc/cron.d/drums-deadman" in u


def test_launch_requests_metadata_tags_and_passes_an_owner_supplied_cpu_option_only_when_given(tmp_path):
    f = FakeAws()
    awsctl.launch(f, approval(cpu_options="NestedVirtualization=enabled"), execute=True, receipt=arm(tmp_path, f), now=lambda: 1001.0)
    args = [c for c in f.made("run-instances") if "--dry-run" not in c][0]
    assert "InstanceMetadataTags=enabled" in " ".join(args) and args[args.index("--cpu-options") + 1] == "NestedVirtualization=enabled"
    f2 = FakeAws()
    awsctl.launch(f2, approval(), execute=True, receipt=arm(tmp_path, f2), now=lambda: 1001.0)
    assert "--cpu-options" not in [c for c in f2.made("run-instances") if "--dry-run" not in c][0]


def test_the_watcher_tags_each_live_instance_with_its_beat_and_never_a_missing_one():
    f = FakeAws(instances=[inst("i-1", deadline=10 ** 12)])
    awsctl.watch(f, approval(), execute=True, now=lambda: 5.0, sleep=lambda s: None, max_iters=2)
    assert len(f.tags) == 2 and "Key=drums-watch-beat,Value=5" in f.tags[0]
    f2 = FakeAws()
    awsctl.watch(f2, approval(), execute=True, now=lambda: 5.0, sleep=lambda s: None, max_iters=2)
    assert getattr(f2, "tags", []) == []


# ---- launch tracking and cost: tags, AMI root mapping, bounded disk ----
def _run_args(f, **kw):
    return [c for c in f.made("run-instances") if "--dry-run" not in c][0]


def test_exactly_one_tag_specifications_flag_carries_both_specifications(tmp_path):
    f = FakeAws()
    awsctl.launch(f, approval(), execute=True, receipt=arm(tmp_path, f), now=lambda: 1001.0)
    args = _run_args(f)
    assert args.count("--tag-specifications") == 1  # a repeated list flag is replaced by the AWS CLI: the instance tags would be lost
    i = args.index("--tag-specifications")
    inst, vol, nxt = args[i + 1], args[i + 2], args[i + 3]
    assert inst.startswith("ResourceType=instance,") and "Key=drums-run,Value=r1" in inst and "Key=drums-bench,Value=true" in inst and "Key=drums-deadline,Value=" in inst
    assert vol.startswith("ResourceType=volume,") and "Key=drums-run,Value=r1" in vol
    assert nxt.startswith("--")  # exactly two values, then the next option


def test_the_amis_real_root_device_is_mapped_and_nothing_else(tmp_path):
    f = FakeAws(image=good_image(RootDeviceName="/dev/xvda", BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 8}}]))
    awsctl.launch(f, approval(), execute=True, receipt=arm(tmp_path, f), now=lambda: 1001.0)
    m = json.loads(_run_args(f)[_run_args(f).index("--block-device-mappings") + 1])
    assert [x["DeviceName"] for x in m] == ["/dev/xvda"] and m[0]["Ebs"]["DeleteOnTermination"] is True and m[0]["Ebs"]["VolumeSize"] == 200 and len(m) == 1


@pytest.mark.parametrize("image,why", [
    (None, "not found"), (good_image(OwnerId="111111111111"), "owner"), (good_image(ProductCodes=[{"ProductCodeId": "x", "ProductCodeType": "marketplace"}]), "Marketplace"),
    (good_image(ImageOwnerAlias="aws-marketplace"), "Marketplace"), (good_image(Architecture="arm64"), "x86_64"), (good_image(RootDeviceType="instance-store"), "EBS-rooted"),
    (good_image(State="pending"), "available"), (good_image(BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 8}}, {"DeviceName": "/dev/sdb", "Ebs": {"VolumeSize": 100}}]), "additional EBS devices"),
    (good_image(BlockDeviceMappings=[{"DeviceName": "/dev/sdz", "Ebs": {"VolumeSize": 8}}]), "no EBS mapping"),
    (good_image(BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 500}}]), "below the AMI"),
])
def test_ami_is_described_and_refused_when_it_is_not_what_was_approved(image, why):
    with pytest.raises(awsctl.Refusal, match=why):
        awsctl.launch(FakeAws(image=image), approval())


def canonical_image():
    """The shape of the official Canonical Ubuntu 24.04 amd64 AMI read live for us-east-1: root /dev/sda1 EBS 8 GiB gp3 plus two instance-store template mappings."""
    return good_image(ImageId="ami-0d6dd9e8265d48906", Name="ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-20261023", OwnerId="099720109477", ProductCodes=[],
                      BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {"DeleteOnTermination": True, "SnapshotId": "snap-0abc", "VolumeSize": 8, "VolumeType": "gp3", "Encrypted": False}},
                                           {"DeviceName": "/dev/sdb", "VirtualName": "ephemeral0"}, {"DeviceName": "/dev/sdc", "VirtualName": "ephemeral1"}])


def test_the_official_canonical_ami_is_accepted_with_its_ephemeral_mappings_suppressed_by_nodevice(tmp_path):
    f = FakeAws(image=canonical_image())
    r = awsctl.launch(f, approval(ebs_gb=120), execute=True, receipt=arm(tmp_path, f), now=lambda: 1001.0)
    assert r["image"]["suppress_devices"] == ["/dev/sdb", "/dev/sdc"] and r["image"]["root_device"] == "/dev/sda1"
    args = _run_args(f)
    m = json.loads(args[args.index("--block-device-mappings") + 1])
    assert m == [{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 120, "VolumeType": "gp3", "DeleteOnTermination": True}}, {"DeviceName": "/dev/sdb", "NoDevice": ""}, {"DeviceName": "/dev/sdc", "NoDevice": ""}]
    assert args.count("--block-device-mappings") == 1


def test_an_extra_ebs_device_is_always_refused_even_next_to_ephemeral_ones_and_no_approval_field_overrides_it():
    im = canonical_image()
    im["BlockDeviceMappings"].append({"DeviceName": "/dev/sdd", "Ebs": {"VolumeSize": 50, "DeleteOnTermination": False}})
    with pytest.raises(awsctl.Refusal, match="additional EBS devices"):
        awsctl.launch(FakeAws(image=im), approval(ebs_gb=120, approved_extra_devices=["/dev/sdd"]))  # the old override field is ignored


@pytest.mark.parametrize("bad", [7, 401, 200.5, "200", True, None])
def test_the_approved_disk_is_a_bounded_whole_number(bad):
    with pytest.raises(awsctl.Refusal):
        awsctl.validate(approval(stage="docker_kvm_small", instance_type="m7i.xlarge", quota_standard_vcpu_asserted=32, ceiling_hours=1.5, budget_cap_usd=50, ebs_gb=bad))


def test_the_amis_owner_must_be_stated_as_twelve_digits():
    for bad in ("", "abc", "12345", 99):
        with pytest.raises(awsctl.Refusal):
            awsctl.validate(approval(ami_owner_id=bad))


# ---- managed deadline (EventBridge Scheduler), prices, role documents ----
ROLE = f"arn:aws:iam::{ACCT}:role/drums-terminate"


def managed(**kw):
    a = approval(deadline_mode="managed", deadline_role_arn=ROLE, scheduler_usd_per_million=1.0, stage="docker_kvm_small", instance_type="m7i.xlarge", hourly_usd=0.2016,
                 quota_standard_vcpu_asserted=32, ceiling_hours=1.5, stop_margin_hours=0.5, budget_cap_usd=1.0, ebs_gb=120, ebs_usd_per_gb_month=0.08, public_ipv4_usd_per_hour=0.005,
                 transfer_out_gb=1, transfer_usd_per_gb=0.09)
    a.update(kw)
    return a


def test_the_verified_small_stage_prices_total_0_529502_and_fit_the_one_dollar_ceiling():
    b = awsctl.validate(managed())
    it = b["items_usd"]
    assert abs(it["ec2_instance_hours"] - 0.4032) < 1e-9 and abs(it["ebs_volume"] - 0.026301) < 1e-6 and abs(it["public_ipv4_address"] - 0.01) < 1e-9
    assert abs(it["data_transfer_out"] - 0.09) < 1e-9 and abs(it["eventbridge_scheduler"] - 1e-6) < 1e-12 and abs(b["worst_case_usd"] - 0.529502) < 1e-6
    with pytest.raises(awsctl.Refusal):
        awsctl.validate(managed(budget_cap_usd=0.5))
    assert "eventbridge_scheduler" not in awsctl.bill(approval())["items_usd"]  # watcher mode has no scheduler line


@pytest.mark.parametrize("over", [{"deadline_role_arn": "arn:aws:iam::111111111111:role/x"}, {"deadline_role_arn": f"arn:aws:iam::{ACCT}:user/x"}, {"deadline_role_arn": ""},
                                  {"deadline_role_arn": f"arn:aws:iam::{ACCT}:role/x/../y"}, {"scheduler_usd_per_million": 0}, {"scheduler_usd_per_million": float("nan")},
                                  {"scheduler_invocations": 0}, {"deadline_mode": "magic"}, {"run_id": "bad id!"}])
def test_managed_mode_needs_an_exact_account_role_and_a_paid_unit_rate(over):
    a = managed()
    a.update(over)
    with pytest.raises(awsctl.Refusal):
        awsctl.validate(a)


def test_managed_launch_creates_then_reads_back_the_schedule_with_no_watcher_receipt_and_no_deadman():
    f = FakeAws()
    r = awsctl.launch(f, managed(), execute=True, now=lambda: 1_761_300_000)  # no receipt: the deadline is AWS-managed
    order = [c[:2] for c in f.calls if c[:2] in (("ec2", "run-instances"), ("scheduler", "create-schedule"), ("scheduler", "get-schedule"))]
    assert order == [("ec2", "run-instances"), ("ec2", "run-instances"), ("scheduler", "create-schedule"), ("scheduler", "get-schedule")]  # dry run, real run, create, readback
    create = f.made("create-schedule") or [c for c in f.calls if c[:2] == ("scheduler", "create-schedule")]
    o = dict(zip(create[0][2::2], create[0][3::2]))
    tgt = json.loads(o["--target"])
    assert o["--name"] == "drums-term-r1" and o["--schedule-expression"] == "at(2025-10-24T11:30:00)" and o["--schedule-expression-timezone"] == "UTC"
    assert o["--flexible-time-window"] == "Mode=OFF" and o["--action-after-completion"] == "DELETE" and o["--group-name"] == "default" and o["--state"] == "ENABLED"
    assert tgt["Arn"] == "arn:aws:scheduler:::aws-sdk:ec2:terminateInstances" and tgt["RoleArn"] == ROLE and json.loads(tgt["Input"]) == {"InstanceIds": ["i-new"]}
    ud = _run_args(f)[_run_args(f).index("--user-data") + 1]
    assert "shutdown -h +90" in ud and "drums-deadman" not in ud and "drums-watch-beat" not in ud  # the host timer stays; the dead-man needs a watcher that does not exist here
    assert r["deadline"]["mode"] == "managed" and r["deadline"]["schedule"]["instance_ids"] == ["i-new"] and r["deadline"]["in_host_ceiling_minutes"] == 90
    assert "drums-deadman" in awsctl.user_data(approval())  # watcher mode keeps its dead-man


def test_watcher_mode_still_needs_a_receipt_and_managed_mode_does_not_create_a_schedule_in_a_dry_run():
    with pytest.raises(awsctl.Refusal, match="no watcher receipt"):
        awsctl.launch(FakeAws(), approval(), execute=True)
    f = FakeAws()
    awsctl.launch(f, managed())
    assert [c for c in f.calls if c[0] == "scheduler"] == [] and f.schedule is None


def test_if_the_schedule_cannot_be_created_the_instance_is_terminated_at_once():
    f = FakeAws(scheduler_fail=True)
    with pytest.raises(awsctl.LaunchAborted, match="deadline could not be proven.*terminate-instances was issued"):
        awsctl.launch(f, managed(), execute=True, now=lambda: 1_761_300_000)
    assert f.terminated and f.made("terminate-instances")[0][3:] == ("i-new",)


@pytest.mark.parametrize("override", [{"State": "DISABLED"}, {"ScheduleExpression": "at(2099-01-01T00:00:00)"}, {"ScheduleExpressionTimezone": "America/New_York"},
                                      {"FlexibleTimeWindow": {"Mode": "FLEXIBLE"}}, {"ActionAfterCompletion": "NONE"},
                                      {"Target": {"Arn": "arn:aws:scheduler:::aws-sdk:ec2:stopInstances", "RoleArn": ROLE, "Input": '{"InstanceIds":["i-new"]}'}},
                                      {"Target": {"Arn": "arn:aws:scheduler:::aws-sdk:ec2:terminateInstances", "RoleArn": f"arn:aws:iam::{ACCT}:role/other", "Input": '{"InstanceIds":["i-new"]}'}},
                                      {"Target": {"Arn": "arn:aws:scheduler:::aws-sdk:ec2:terminateInstances", "RoleArn": ROLE, "Input": '{"InstanceIds":["i-new","i-other"]}'}},
                                      {"Target": {"Arn": "arn:aws:scheduler:::aws-sdk:ec2:terminateInstances", "RoleArn": ROLE, "Input": "not json"}}])
def test_a_schedule_that_does_not_read_back_exactly_terminates_the_instance(override):
    f = FakeAws(schedule_override=override)
    with pytest.raises(awsctl.LaunchAborted, match="readback differs"):
        awsctl.launch(f, managed(), execute=True, now=lambda: 1_761_300_000)
    assert f.terminated


def test_when_even_the_abort_terminate_fails_the_error_says_only_the_host_timer_remains():
    f = FakeAws(scheduler_fail=True, terminate_fail=True)
    with pytest.raises(awsctl.LaunchAborted, match="host's own shutdown timer is then the only remaining stop"):
        awsctl.launch(f, managed(), execute=True, now=lambda: 1_761_300_000)


def test_teardown_deletes_the_schedule_and_reports_clean_only_when_it_is_verified_gone():
    f = FakeAws(instances=[inst()])
    awsctl.launch(FakeAws(), managed(), execute=True)  # (separate fake; just proves the path runs)
    f.schedule = {"Name": "drums-term-r1"}
    r = awsctl.teardown(f, managed(), execute=True, sleep=lambda s: None)
    assert r["clean"] and r["remaining"]["deadline_schedule_exists"] is False and f.schedule is None
    f2 = FakeAws(instances=[inst()], delete_fails=True)
    f2.schedule = {"Name": "drums-term-r1"}
    r2 = awsctl.teardown(f2, managed(), execute=True, sleep=lambda s: None)
    assert not r2["clean"] and r2["remaining"]["deadline_schedule_exists"] is True  # a schedule that remains is never called clean
    assert awsctl.teardown(FakeAws(instances=[inst()]), approval(), execute=True, sleep=lambda s: None)["clean"]  # watcher mode: no schedule involved


def test_the_role_documents_are_least_privilege_and_scoped_to_this_account_region_tag_and_schedule_prefix():
    d = awsctl.scheduler_role_documents(managed())
    trust, perm = d["role_trust_policy"]["Statement"][0], d["role_permission_policy"]["Statement"]
    assert trust["Principal"] == {"Service": "scheduler.amazonaws.com"} and trust["Action"] == "sts:AssumeRole"
    # the AWS-documented confused-deputy shape for EventBridge Scheduler: StringEquals on the account AND on the schedule GROUP ARN, never a schedule or a name prefix
    assert trust["Condition"] == {"StringEquals": {"aws:SourceAccount": ACCT, "aws:SourceArn": f"arn:aws:scheduler:us-east-1:{ACCT}:schedule-group/default"}}
    assert "ArnLike" not in json.dumps(d["role_trust_policy"]) and "*" not in json.dumps(d["role_trust_policy"]) and "schedule/default" not in json.dumps(d["role_trust_policy"])
    assert len(perm) == 1 and perm[0]["Action"] == "ec2:TerminateInstances" and perm[0]["Resource"] == f"arn:aws:ec2:us-east-1:{ACCT}:instance/*"
    assert perm[0]["Condition"] == {"StringEquals": {"ec2:ResourceTag/drums-bench": "true"}}
    assert "*" not in perm[0]["Action"] and "iam" not in json.dumps(perm) and "scheduler:" not in json.dumps(perm)
    need = d["launching_identity_needs"]
    assert need["scheduler_resource"] == f"arn:aws:scheduler:us-east-1:{ACCT}:schedule/default/drums-term-*"  # the name-prefix narrowing lives on the LAUNCHER, where it is valid
    assert need["iam:PassRole"]["resource"] == ROLE and need["iam:PassRole"]["condition"] == {"StringEquals": {"iam:PassedToService": "scheduler.amazonaws.com"}} and "nothing here grants" in need["note"]


def test_awsctl_never_calls_iam_or_creates_a_role():
    src = open(awsctl.__file__).read()
    assert '"iam"' not in src and "create-role" not in src and "put-role-policy" not in src and "attach-role-policy" not in src


def test_plan_prints_the_role_documents_for_managed_mode(tmp_path, capsys):
    p = tmp_path / "a.json"
    p.write_text(json.dumps(managed()))
    assert awsctl.main(["plan", "--approval", str(p)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["managed_deadline"]["schedule_target"] == "arn:aws:scheduler:::aws-sdk:ec2:terminateInstances" and "role_trust_policy" in out["managed_deadline"]["documents_for_the_owner_to_create"]


def test_an_interrupt_right_after_run_instances_also_terminates_the_instance():
    class Interrupted(FakeAws):
        def _scheduler(self, *args):
            raise KeyboardInterrupt()
    f = Interrupted()
    with pytest.raises(awsctl.LaunchAborted, match="KeyboardInterrupt.*terminate-instances was issued"):
        awsctl.launch(f, managed(), execute=True, now=lambda: 1_761_300_000)
    assert f.terminated


def test_the_launch_docs_do_not_claim_the_host_timer_covers_the_interval_after_run_instances():
    src = open(awsctl.__file__).read()
    assert "NOTHING in the cloud" in src and "user data runs after" in src and "can fail" in src
    assert "never a window without a stop" not in src and "so covers the gap before any API-side deadline exists" not in src
