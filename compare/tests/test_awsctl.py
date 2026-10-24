"""awsctl guards, against a fake `aws`. Nothing here touches AWS."""
import json

import pytest

from drums_compare import awsctl


def approval(**kw):
    a = {"run_id": "r1", "approved_by": "owner", "instance_type": "m7i.metal-24xl", "region": "us-east-1", "ceiling_hours": 4, "stop_margin_hours": 0.5, "budget_cap_usd": 40.0,
         "hourly_usd": 4.8384, "ebs_gb": 200, "ebs_usd_per_gb_month": 0.08, "public_ipv4_usd_per_hour": 0.005, "transfer_out_gb": 20, "transfer_usd_per_gb": 0.09,
         "ssh_cidr": "203.0.113.5/32", "key_name": "k", "subnet_id": "subnet-1", "security_group_id": "sg-1", "image_id": "ami-1", "profile": "drums", "approved": True}
    a.update(kw)
    return a


class FakeAws:
    def __init__(self, arn="arn:aws:iam::1:user/sahil", instances=(), volumes=(), addresses=()):
        self.arn, self.instances, self.volumes, self.addresses, self.calls = arn, list(instances), list(volumes), list(addresses), []
        self.terminated = False

    def __call__(self, *args, check=True):
        self.calls.append(args)
        if args[:2] == ("sts", "get-caller-identity"):
            return {"Arn": self.arn}
        if args[:2] == ("ec2", "describe-instances"):
            return {"Reservations": [{"Instances": [] if self.terminated else self.instances}]}
        if args[:2] == ("ec2", "describe-volumes"):
            return {"Volumes": self.volumes}
        if args[:2] == ("ec2", "describe-addresses"):
            return {"Addresses": self.addresses}
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


@pytest.mark.parametrize("over", [{"approved": False}, {"instance_type": "c5.large"}, {"region": "eu-west-1"}, {"ssh_cidr": "0.0.0.0/0"}, {"ssh_cidr": "203.0.113.5"},
                                  {"ceiling_hours": 13}, {"stop_margin_hours": 0.0}, {"budget_cap_usd": 5.0}])
def test_unapproved_or_unsafe_approvals_are_refused(over):
    with pytest.raises(awsctl.Refusal):
        awsctl.validate(approval(**over))


def test_missing_field_is_refused():
    a = approval()
    del a["budget_cap_usd"]
    with pytest.raises(awsctl.Refusal):
        awsctl.validate(a)


def test_bill_is_itemized_prices_the_stop_margin_and_says_it_is_not_a_hard_cap():
    b = awsctl.bill(approval())
    assert b["billed_hours_worst_case"] == 4.5
    assert abs(b["items_usd"]["ec2_instance_hours"] - 4.8384 * 4.5) < 1e-9
    assert set(b["items_usd"]) == {"ec2_instance_hours", "ebs_volume", "public_ipv4_address", "data_transfer_out"}
    assert abs(b["worst_case_usd"] - sum(b["items_usd"].values())) < 1e-9 and "not a hard dollar cap" in b["note"]


def test_launch_refuses_root_and_a_second_host():
    with pytest.raises(awsctl.Refusal):
        awsctl.launch(FakeAws(arn="arn:aws:iam::1:root"), approval(), execute=True)
    with pytest.raises(awsctl.Refusal):
        awsctl.launch(FakeAws(instances=[inst()]), approval(), execute=True)


def test_dry_run_creates_nothing_and_execute_sets_every_control():
    f = FakeAws()
    r = awsctl.launch(f, approval())
    assert r["executed"] is False and "--dry-run" in f.made("run-instances")[0]
    f2 = FakeAws()
    r2 = awsctl.launch(f2, approval(), execute=True, now=lambda: 1000)
    args = f2.made("run-instances")[0]
    s = " ".join(map(str, args))
    assert r2["executed"] and r2["deadline_epoch"] == 1000 + 4 * 3600
    assert "--instance-initiated-shutdown-behavior terminate" in s and "HttpTokens=required" in s and "DeleteOnTermination" in s and "true" in s.lower()
    assert "drums-run,Value=r1" in s.replace("=", ",").replace(",,", ",") or "Key=drums-run,Value=r1" in s
    assert "shutdown -h +240" in args[args.index("--user-data") + 1] and "--associate-public-ip-address" in args and "--dry-run" not in args
    assert f2.made("allocate-address") == []  # no Elastic IP is ever allocated


def test_teardown_terminates_deletes_owned_leftovers_releases_and_verifies():
    f = FakeAws(instances=[inst()], volumes=[{"VolumeId": "vol-1", "Attachments": []}], addresses=[{"AllocationId": "eip-1"}])
    plan = awsctl.teardown(f, approval(), execute=False)
    assert plan["executed"] is False and f.made("terminate-instances") == []
    r = awsctl.teardown(f, approval(), execute=True, sleep=lambda s: None)
    assert r["clean"] and r["remaining"] == {"instances": [], "volumes": [], "addresses": []}
    assert len(f.made("terminate-instances")) == 1 and len(f.made("delete-volume")) == 1 and len(f.made("release-address")) == 1


def test_teardown_reports_what_remains_when_a_volume_is_still_attached():
    f = FakeAws(instances=[inst()], volumes=[{"VolumeId": "vol-2", "Attachments": [{"InstanceId": "i-1"}]}])
    r = awsctl.teardown(f, approval(), execute=True, sleep=lambda s: None)
    assert not r["clean"] and r["remaining"]["volumes"] == ["vol-2"]  # never silently reported clean


def test_watch_tears_down_at_the_deadline_or_on_a_second_host_and_exits_when_empty(capsys):
    f = FakeAws(instances=[inst("i-1", deadline=500)])
    r = awsctl.watch(f, approval(), execute=True, now=lambda: 501, sleep=lambda s: None)
    assert r["result"] == "teardown triggered" and len(f.made("terminate-instances")) == 1
    f2 = FakeAws(instances=[inst("i-1", deadline=10 ** 12), inst("i-2", deadline=10 ** 12)])
    assert awsctl.watch(f2, approval(), execute=True, now=lambda: 1, sleep=lambda s: None)["result"] == "teardown triggered"
    assert awsctl.watch(FakeAws(), approval(), now=lambda: 1)["result"] == "nothing owned remains"
    f3 = FakeAws(instances=[inst("i-1", deadline=10 ** 12)])
    assert awsctl.watch(f3, approval(), now=lambda: 1, sleep=lambda s: None, max_iters=2)["result"].startswith("stopped watching") and f3.made("terminate-instances") == []
