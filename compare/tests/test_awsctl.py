"""awsctl guards, against a fake `aws`. Nothing here touches AWS or uses any credential."""
import json
import math

import pytest

from drums_compare import awsctl

ACCT = awsctl.APPROVED_ACCOUNT


def approval(**kw):
    a = {"run_id": "r1", "approved_by": "owner", "account_id": ACCT, "stage": "vbox_metal", "quota_standard_vcpu_asserted": 96, "instance_type": "m7i.metal-24xl", "region": "us-east-1", "ceiling_hours": 4, "stop_margin_hours": 0.5,
         "budget_cap_usd": 40.0, "hourly_usd": 4.8384, "ebs_gb": 200, "ebs_usd_per_gb_month": 0.08, "public_ipv4_usd_per_hour": 0.005, "transfer_out_gb": 20,
         "transfer_usd_per_gb": 0.09, "ssh_cidr": "203.0.113.5/32", "key_name": "k", "subnet_id": "subnet-1", "security_group_id": "sg-1", "image_id": "ami-1", "profile": "drums",
         "approved": True}
    a.update(kw)
    return a


def good_sg(**over):
    sg = {"VpcId": "vpc-1", "IpPermissions": [{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "203.0.113.5/32"}], "Ipv6Ranges": [], "PrefixListIds": [], "UserIdGroupPairs": []}]}
    sg.update(over)
    return sg


class FakeAws:
    def __init__(self, arn=f"arn:aws:iam::{ACCT}:user/sahil", account=ACCT, instances=(), others=(), volumes=(), addresses=(), bench_volumes=(), sg=None, subnet_vpc="vpc-1", dry="DryRunOperation"):
        self.arn, self.account, self.instances, self.others = arn, account, list(instances), list(others)
        self.volumes, self.addresses, self.bench_volumes, self.sg, self.subnet_vpc, self.dry = list(volumes), list(addresses), list(bench_volumes), sg or good_sg(), subnet_vpc, dry
        self.calls, self.terminated = [], False

    def raw(self, *args):
        self.calls.append(args)
        if args[:2] == ("ec2", "run-instances") and "--dry-run" in args:
            return 254, "", f"An error occurred ({self.dry}) when calling the RunInstances operation"
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
        if args[:2] == ("ec2", "describe-subnets"):
            return {"Subnets": [{"VpcId": self.subnet_vpc}]}
        if args[:2] == ("ec2", "create-tags"):
            self.tags = getattr(self, "tags", []) + [args]
            return ""
        if args[:2] == ("ec2", "terminate-instances"):
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
    assert r["executed"] and r["instance_id"] == "i-new" and r["deadline_epoch"] == 1001 + 4 * 3600 and r["watcher"]["state"] == "armed"
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
