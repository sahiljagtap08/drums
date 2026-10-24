# Small stage plan: stock OSWorld Docker/KVM cold-reset baseline versus Drums, on one m7i.xlarge (max 2 h provision-to-terminate)

**Status: BUILT (code, scripts and this plan, corrected after independent review) and UNRUN. No instance was launched, no AWS credential or API call was used, nothing was spent, no image or package was downloaded or installed here, nothing was merged to main. The full cost approval is handled outside this session. The first result this produces is an ARCHITECTURE MEASUREMENT of cold-reset task execution on one task: not a comparison (software composition is not shown equivalent), not a snapshot or live-state result, no throughput, cost-advantage, training or benchmark claim. It is separate from, and does not replace, the original VirtualBox live-snapshot goal (`docs/aws-incumbent-runbook.md`), which stays unrun and needs the 96 vCPU quota.**

## 1. What the stock Docker provider does (read from OSWorld `04718ea`, `desktop_env/providers/docker/provider.py`)

* It creates a container from `happysixd/osworld-docker` (the floating tag `latest`) with environment `DISK_SIZE=32G RAM_SIZE=4G CPU_CORES=4`, the qcow2 bind-mounted read-only at `/System.qcow2`, port forwards for 5000/9222/8006/8080, and `/dev/kvm` **only if it exists**; otherwise it sets `KVM=N` and silently runs without hardware acceleration. The runner therefore refuses unless `/dev/kvm` exists and the container actually has it.
* `save_state` raises `NotImplementedError`; `revert_to_snapshot` is `stop_emulator` (stop and remove the container), after which `DesktopEnv.reset` starts a new container: **every reset after the first is a full cold boot of a new VM**. No VM snapshot exists in this provider and none is claimed. Both arms run with reuse OFF (the Drums arm destroys and recreates its session per rollout).
* Guest budget is hard-coded in the provider at 4 CPU and 4 GiB; `host_run check` reads that from the provider source and refuses unless the config's budget equals it. The Drums arm gets cgroup limits at the same 4 CPU and 4 GiB.

## 2. Facts about the artifacts (metadata reads only, nothing pulled or installed)

| Item | Value | Basis |
|---|---|---|
| Docker image `happysixd/osworld-docker` | digest `sha256:0e6497a9295647cf05bf2b2af522fdd79bdeba2737595259cab310a3bcf6baa9`, linux/amd64, created 2024-10-17, 6 layers, 84,258,814 compressed bytes (32,359,025 + 49,993,725 + 16,045 + 3,392 + 758 + 1,885,869) | Docker Hub registry manifest and config read with an anonymous pull token; no layer was downloaded |
| Its own defaults | `CPU_CORES=1 RAM_SIZE=1G DISK_SIZE=16G`, entrypoint tini, ports 22, 5900, 8006; the provider overrides the three sizes | image config |
| The tag | floating: `latest` can move. The run pins by digest; `bootstrap.sh` pulls that digest and retags it `latest`; `host_run check` requires the local image to carry the digest | registry |
| qcow2 archive, pinned | repository commit `a5d9c3eaae98eebf6e3a0beb84e7e47cf72ae133` (the `X-Repo-Commit` of `resolve/main` at the independent read), URL `https://huggingface.co/datasets/xlangai/ubuntu_osworld/resolve/a5d9c3eaae98eebf6e3a0beb84e7e47cf72ae133/Ubuntu.qcow2.zip`: **never the mutable `main`** (the stock manager uses `main`; the run passes the qcow2 path so the manager never downloads) | independent review plus `KNOWN_ARTIFACT` in `drums_compare/imagesize.py` |
| Zip bytes and hash | **12,273,896,463 bytes**, LFS sha256 `b795b6cd4c69b252c1b4f10150a347795555032501b60fd031751ed09b896712` (required, not optional, in `fetch_docker_image.sh`). Two reads agree: the independent range read, and this session's text fetch of the LFS pointer file (`raw/main/Ubuntu.qcow2.zip`: the same `oid sha256` and `size`). The hash is the repository's claim, verified only against the downloaded bytes on the host | pointer file and range read |
| Zip layout | 1 member `Ubuntu.qcow2`, compressed 12,273,896,173, **uncompressed 24,460,197,888 bytes**; central directory at offset 12,273,896,263, 102 bytes, 1 entry (the end record covers the rest); read with a 131,072-byte tail range and a 102-byte directory range, both answered 206 | independent review; this session could not reach Hugging Face directly (proxy 403) |
| Range reads are bounded | `imagesize._fetch` now requires status 206 and a `Content-Range` naming exactly the requested bytes, reads at most length+1 bytes, and refuses any range over 16 MiB; a server that ignores `Range` and answers 200 is refused before its body is read (a 12 GB body would otherwise be read) | tests with a counting fake response |
| Licence | the dataset card's License field is BLANK: unresolved uncertainty, neither a ban nor permission | card |
| Guest composition | **STILL UNKNOWN until `fetch_docker_image.sh` boots the guest and writes the manifest**; nothing is assumed to match the Drums arm (openbox vs GNOME, Chromium vs Chrome, packages). Existing software differences stay **NOT_A_COMPARISON** until genuine parity is shown with measured, digest-bound evidence; this stage is not renamed a comparison | not yet read |

## 3. Disk: 120 GiB, from measured bytes plus named allowances

`imagesize.disk_budget` with the pinned numbers: **measured** zip 12.273896463 GB plus extracted 24.460197888 GB (both resident while extracting; the zip is deleted after verification), plus **allowances** (named ceilings, not measurements): OS plus apt packages 20, extracted Docker image 2, the container's writable ceiling 32 (the provider's `DISK_SIZE`), Drums template and session state 5 (total 59). Subtotal 95.734 GB, times 1.15 = 110.09 GB, **rounded up to 120**. (An earlier illustrative figure of 110 was an assumption and is superseded: 110.09 does not fit in 110.) `ebs_gb` in the approval is 120, a whole GiB within 8 to 400 for this stage; the launch refuses anything else and refuses a size below the AMI's own root. Storage at the owner-verified $0.08 per GB-month for 120 GiB over 2 h is $0.026301.

## 4. Bytes, limits and refusals in the image scripts

`fetch_docker_image.sh` refuses to download without `--zip-bytes`, `--uncompressed-bytes` and `--zip-sha256` (12,273,896,463; 24,460,197,888; the hash above, all from `imagesize`); refuses if free space cannot hold the remaining download plus extraction plus 2 GiB; caps the download at the expected bytes plus 1 MiB; uses the commit-pinned URL only, verifies exact size, `unzip -tq` integrity, the listed uncompressed total and the REQUIRED zip sha256 (`b795b6cd…6712`); extracts into a scratch directory; records the qcow2 size and sha256; deletes the zip; boots the guest with the provider's own container settings to read its OS, kernel, desktop, browser, LibreOffice and package list; removes the container and its volumes. Inbound transfer is not billed by AWS (the owner's price sheet decides); no mirror or other image is used.

## 5. One host, one disk, minimal network, everything tagged and deleted

* Exactly one `m7i.xlarge` (4 vCPU, 16 GiB, $0.2016 per hour Linux us-east-1, as given by the owner) and one gp3 root volume mapped on the AMI's real root device with `DeleteOnTermination=true`. The launch DESCRIBES the AMI and refuses a foreign owner, a Marketplace product code or a non-EBS root. **The official Canonical AMI read live** (`ami-0d6dd9e8265d48906`, `ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-20261023`, owner 099720109477, no product codes, root `/dev/sda1` EBS 8 GiB gp3 with `DeleteOnTermination` true) also carries two instance-store template mappings (`/dev/sdb` ephemeral0, `/dev/sdc` ephemeral1). `m7i.xlarge` is EBS-only, so the launch suppresses every non-EBS mapping with `NoDevice` in its one `--block-device-mappings` argument, and still refuses any extra EBS device (no approval field overrides that). The AMI id is re-resolved at launch time; the one above is what was observed.
* Default-VPC public subnet, the auto-assigned public IPv4 (no Elastic IP), no NAT gateway, no load balancer, no extra network interface, no snapshot or AMI created, one security group with SSH from the owner's single /32 only (inspected, not trusted), IMDSv2 required.
* ONE `--tag-specifications` flag carries both the instance and the volume tags (`drums-run`, `drums-bench=true`, `drums-deadline`).
* Deletion is verified: `awsctl teardown` terminates, deletes unattached owned volumes, releases owned addresses, deletes the deadline schedule if it still exists and reports `clean: true` only when nothing owned remains and the schedule is verified gone; the owner then confirms in the console.
* Nested virtualization: the owner states the live API shows `m7i.xlarge` supports it; if a launch option is required it is copied from the live `describe-instance-types` result into the approval's `cpu_options`. The host `check` refuses without `/dev/kvm` either way. **The Docker guest is level 2 and the Drums sessions level 1 of the same instance**: that asymmetry is part of what this measures and is why the numbers do not transfer to bare metal.

## 6. Timeline inside the 2 h ceiling (planning slices, NOT measurements)

The approval's `ceiling_hours` 1.5 plus `stop_margin_hours` 0.5 equals the 2 h maximum (the launch refuses a longer stage). In-host `shutdown -h +90` and the host's dead-man switch enforce it with no external help.

| From launch | Step | Stop rule |
|---|---|---|
| T+0:00 | launch; the managed schedule is created and read back at once (the interval before that is protected by nothing in the cloud), else the instance is terminated; SSH in | no SSH by T+0:10: tear down |
| T+0:10 to 0:30 | `bootstrap.sh` (apt, docker, pinned image pull, OSWorld venv with librosa, Chromium, kit) | any refusal: tear down |
| T+0:30 to 0:55 | `fetch_docker_image.sh` (download, verify, extract, describe) | any refusal or size mismatch: tear down |
| T+0:55 to 1:10 | `host_run check`, then `gates` | any failure: export logs, tear down (no debugging on the clock) |
| T+1:10 to 1:25 | `run` with `max_runtime_s` 2400 s and 2 blocks | the runner stops at its ceiling after a completed block |
| T+1:25 to 1:35 | owner pulls the export; teardown | |
| T+1:30 | in-host shutdown fires regardless | |

If bootstrap and the image fetch take longer than planned, the attempt is lost, not extended: there is no second approval inside this plan.

## 7. Memory admission and known limits on a 16 GiB host

`memory_admission_mib` is an **owner-set** threshold compared with `MemAvailable` at check time, recorded with `MemTotal`; it is an admission rule, not a derived minimum and not a capacity claim. Arms run strictly one after the other at concurrency 1 (never both guests resident), the guest budget is 4 GiB, and the page cache of the 12 GB+ qcow2 is reclaimable and not counted. Known limits: nested virtualization overhead; a single task; sampled lower-bound CPU and memory for each arm (the Docker arm from its container cgroup, the Drums arm from its session cgroup); host-wide memory and CPU recorded as well; no per-environment parity claim.

## 8. Correct timing boundaries

Inside each arm's run clock: guest open (the first container boot or Drums session), **every reset including the stop-and-recreate cold boot**, steps, evaluation and close (container stop). Reported apart and added to the with-prep figures: the Drums supervisor, template and pool build, between-block cleanup (leftover container removal), and image fetch (not timed). The cross-arm ratio is suppressed because the mode is architecture measurement.

## 9. Gates (both arms, cold-reset set; no snapshot gate exists or is claimed)

State isolation across a reset (a live process with an in-memory token, a file marker and a running Writer must be gone after the reset); the real task evaluator scores 0.0 before any action, 1.0 after the correct action, 0.0 for the wrong action after a fresh reset; KVM actually used by the container; guest-visible CPU and memory read back (4 CPU, within 6% of 4 GiB), and the Drums cgroup limits read back from the kernel. A parity DECISION is recorded per arm from those readbacks, never a claim. The Writer pixel-state gate belongs to the snapshot stage only. The scored task is the public, unmodified `e0df059f`; Impress is not enabled and nothing here claims it works.

## 10. The durable deadline: AWS-managed, not a watcher (`deadline_mode: managed`)

**Why the change:** this execution environment cannot persist a background watcher, the owner's Mac is not selected and has no remote access arranged, the account has only service-linked roles, and EventBridge Scheduler is unused. A watcher receipt for a process nobody can keep alive would be pretend independence. The small stage therefore uses an AWS-managed deadline. The watcher path stays for the bare-metal VirtualBox stage and is untouched there.

**The stops, and when each one actually exists:**
1. **Host timer, only after boot.** User data `shutdown -h +90` (instance-initiated shutdown behaviour TERMINATE) is run by cloud-init **after the instance has booted**; user data can fail, so this timer is **not** a guaranteed stop from the instant `RunInstances` returns. It is the in-host fallback once it has started. (The watcher's dead-man switch is NOT installed in this mode: it would shut the host down for want of a beat that nobody sends.)
2. **One-time EventBridge Scheduler schedule** `drums-term-<run_id>`: expression `at(<deadline UTC>)`, timezone UTC, flexible window OFF, `ActionAfterCompletion=DELETE`, retry 3 times within 1 hour, universal target `arn:aws:scheduler:::aws-sdk:ec2:terminateInstances` with input `{"InstanceIds":["<id>"]}` and the owner-created execution role. Created and **read back immediately after the instance exists**; the readback must show exactly that (enabled, expression, target, role, a single instance id, no flexible window, auto-delete). Once this reads back, an AWS-side stop exists independent of the host and of this process.
3. **Fail closed:** if the schedule cannot be created or does not read back exactly, or the process is interrupted by an exception or Ctrl-C right after `RunInstances`, `awsctl` terminates the instance at once and reports `LAUNCH ABORTED`; if even that terminate is refused, the error says the host timer (if it ever starts) is the only remaining stop.

**The unprotected interval, stated plainly.** TerminateInstances needs instance ids, and Scheduler has no tag selector, so a stop cannot be pre-armed by tag without a Lambda (rejected: more moving parts). `RunInstances` cannot create an instance stopped (stop and start afterwards is not atomic and adds EBS billing and complexity). So **from the moment `RunInstances` returns until the schedule has been created and read back, nothing in the cloud will stop the instance**: the host timer has not necessarily started, and user data may fail. The chosen order shortens that interval to seconds, never leaves a failure unhandled (any error or interrupt terminates immediately), and requires a dry-run verdict of `allowed` first. **Residual risk, explicit:** if the process is killed outright (`kill -9`, power loss, a dropped session that cannot deliver an interrupt) or the termination call is itself refused in that interval, the instance runs until its host timer fires (if it started) or until someone runs `awsctl teardown` or terminates it in the console. `awsctl status` and `teardown` find it by its `drums-run` tag and `drums-deadline` tag. This is a bounded exposure, not a hard cap.

**The role (the owner creates it; `awsctl` never calls IAM, a test asserts that).** `awsctl plan` prints the exact documents. Trust policy (the shape AWS documents for EventBridge Scheduler confused-deputy prevention): principal `scheduler.amazonaws.com`, `sts:AssumeRole`, `StringEquals` on `aws:SourceAccount` = 463186160490 **and** on `aws:SourceArn` = exactly the schedule GROUP ARN `arn:aws:scheduler:us-east-1:463186160490:schedule-group/default`. AWS states the value must be the group ARN and must not be narrowed to a schedule or a name prefix (the earlier schedule-prefix `ArnLike` was wrong and the role could not have been assumed); no dedicated schedule group and no further account change is needed beyond the proposed role and schedule. Permission policy: one action, `ec2:TerminateInstances`, on `arn:aws:ec2:us-east-1:463186160490:instance/*` with condition `StringEquals ec2:ResourceTag/drums-bench = true` (the launch tags the instance with it). No wildcard action, no IAM, no Scheduler action. The LAUNCHING identity would additionally need `scheduler:CreateSchedule/GetSchedule/DeleteSchedule` on the same schedule prefix and `iam:PassRole` for that role only, with `iam:PassedToService = scheduler.amazonaws.com`: documentation only, **no privilege is assumed present**, and its absence simply makes the create fail and the instance terminate. **Owner approval for the role, the schedule and any account change has NOT been given; it is pending with the owner, handled outside this session.**

**Residual risks:** Scheduler delivers at the stated time on a best-effort basis; a role deleted or a permission removed before the deadline makes the schedule's call fail without any alarm (there is no dead-letter target here), leaving the host timer as the stop; a root user on the host can cancel the host timer; the schedule's own invocation is a paid unit (below).

## 11. Prospective bill and stop margin (owner-verified unit rates; not approved; no tax or support)

For ONE attempt: 90 min ceiling plus 30 min stop margin (2.0 billed hours worst case), 120 GiB gp3, 1 GB transfer out, one scheduler invocation. Unit rates read and verified by the owner: gp3 $0.08 per GB-month, public IPv4 $0.005 per hour, EC2 $0.2016 per hour, scheduler $1 per million invocations (after a 14 million free tier whose account-wide use is unknown, so the PAID rate is used with no free-tier deduction).

| Component | Worst case (USD) |
|---|---|
| EC2 `m7i.xlarge`, 2.0 h | 0.403200 |
| EBS gp3, 120 GiB for 2.0 h | 0.026301 |
| Public IPv4, 2.0 h | 0.010000 |
| Data transfer out, 1 GB (conservative) | 0.090000 |
| EventBridge Scheduler, 1 invocation | 0.000001 |
| **Total** | **0.529502** |

The proposed ceiling is **$1.00** for the attempt (`budget_cap_usd`; the launch refuses if the worst case exceeds it). This is **not a hard billing cap and not a zero-cash guarantee**: it holds only if one of the stops works, and credits absorb it only until they run out. `awsctl.bill` reproduces the total and a test asserts 0.529502.

## 12. Remaining actual-host and account checks (all need the host or an owner action)

1. The owner-stated nested-virtualization support and any needed launch option on this instance; `/dev/kvm` presence.
2. Whether the pinned image and the qcow2 boot and answer on the guest server inside the 2 h plan.
3. The extracted qcow2 size and the zip bytes (`imagesize`), the real composition manifest, and the disk number.
4. That the stock provider's cold boot works end to end on nested virtualization, and the cold-reset timings (unmeasured).
5. cgroup v2 paths of `limits.py` and the telemetry on a real v2 host (this sandbox is v1).
6. The `librosa` install and a strict import with no stub; the OSWorld requirements install.
7. The owner-created scheduler role, the launching identity's permissions, the schedule creation and readback, the host timer and the teardown (including schedule deletion) against the real account (none has run; role and schedule approval pending).
8. Whether any software difference can later be given measured equivalence evidence; until then the result stays NOT_A_COMPARISON.
