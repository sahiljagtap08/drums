# Small stage plan: stock OSWorld Docker/KVM cold-reset baseline versus Drums, on one m7i.xlarge (max 2 h provision-to-terminate)

**Status: BUILT (code, scripts and this plan) and UNRUN. No instance was launched, no AWS credential or API call was used, nothing was spent, no image or package was downloaded or installed here, nothing was merged to main. The full cost approval is handled outside this session. The first result this produces is an ARCHITECTURE MEASUREMENT of cold-reset task execution on one task: not a comparison (software composition is not shown equivalent), not a snapshot or live-state result, no throughput, cost-advantage, training or benchmark claim. It is separate from, and does not replace, the original VirtualBox live-snapshot goal (`docs/aws-incumbent-runbook.md`), which stays unrun and needs the 96 vCPU quota.**

## 1. What the stock Docker provider does (read from OSWorld `04718ea`, `desktop_env/providers/docker/provider.py`)

* It creates a container from `happysixd/osworld-docker` (the floating tag `latest`) with environment `DISK_SIZE=32G RAM_SIZE=4G CPU_CORES=4`, the qcow2 bind-mounted read-only at `/System.qcow2`, port forwards for 5000/9222/8006/8080, and `/dev/kvm` **only if it exists**; otherwise it sets `KVM=N` and silently runs without hardware acceleration. The runner therefore refuses unless `/dev/kvm` exists and the container actually has it.
* `save_state` raises `NotImplementedError`; `revert_to_snapshot` is `stop_emulator` (stop and remove the container), after which `DesktopEnv.reset` starts a new container: **every reset after the first is a full cold boot of a new VM**. No VM snapshot exists in this provider and none is claimed. Both arms run with reuse OFF (the Drums arm destroys and recreates its session per rollout).
* Guest budget is hard-coded in the provider at 4 CPU and 4 GiB; `host_run check` reads that from the provider source and refuses unless the config's budget equals it. The Drums arm gets cgroup limits at the same 4 CPU and 4 GiB.

## 2. Measured facts about the artifacts (metadata reads only, nothing pulled)

| Item | Value | Basis |
|---|---|---|
| Docker image `happysixd/osworld-docker` | digest `sha256:0e6497a9295647cf05bf2b2af522fdd79bdeba2737595259cab310a3bcf6baa9`, linux/amd64, created 2024-10-17, 6 layers, 84,258,814 compressed bytes (32,359,025 + 49,993,725 + 16,045 + 3,392 + 758 + 1,885,869) | Docker Hub registry manifest and config read with an anonymous pull token; no layer was downloaded |
| Its own defaults | `CPU_CORES=1 RAM_SIZE=1G DISK_SIZE=16G`, entrypoint tini, ports 22, 5900, 8006; the provider overrides the three sizes | image config |
| The tag | floating: `latest` can move. The run pins by digest; `bootstrap.sh` pulls that digest and retags it `latest`; `host_run check` requires the local image to carry the digest | registry |
| qcow2 archive | `Ubuntu.qcow2.zip`, shown as 12.3 GB (rounded) on the dataset page; Hugging Face is unreachable from this sandbox (proxy 403), so the exact bytes, the extracted size and any checksum header are **UNMEASURED here** | dataset file listing read through a text fetch |
| Extracted qcow2 size | **UNKNOWN until `python -m drums_compare.imagesize <url>` is run** (a range read of the zip directory, a few KB to MB) from any machine that can reach Hugging Face | tool built and tested on local zips including zip64 |
| Licence | the dataset card's License field is BLANK: unresolved uncertainty, neither a ban nor permission | card |
| Guest composition | **UNKNOWN until `fetch_docker_image.sh` boots the guest and writes the manifest**; nothing is assumed to match the Drums arm (openbox vs GNOME, Chromium vs Chrome, packages) | not yet read |

## 3. Disk: gp3 GB from measured bytes plus named allowances, never a guess

`imagesize.disk_budget` computes it: **measured** zip bytes plus extracted bytes (both resident while extracting, and the zip is deleted after verification), plus **allowances** (named ceilings, not measurements): OS plus apt packages 20 GB, extracted Docker image 2 GB, the container's writable ceiling 32 GB (the provider's `DISK_SIZE`), Drums template and session state 5 GB, then a 15% margin, rounded up to 10 GB. `ebs_gb` in the approval must be that number, a whole GiB within 8 to 400 for this stage (the launch refuses otherwise, and refuses a size below the AMI's own root).
Illustration only, **assuming** an extracted size of 24.0 GB (not measured): (12.3 + 24.0 + 20 + 2 + 32 + 5) x 1.15 = 109.6, so 110 GB. At the owner-supplied (unverified) $0.08 per GB-month that is about $0.024 of storage for the 2 h window. The real number replaces this once `imagesize` has run.

## 4. Bytes, limits and refusals in the image scripts

`fetch_docker_image.sh` refuses to download without `--zip-bytes` and `--uncompressed-bytes` from `imagesize`; refuses if free space cannot hold the remaining download plus extraction plus 2 GiB; caps the download at the expected bytes plus 1 MiB; verifies exact size, `unzip -tq` integrity, the listed uncompressed total and (if the owner provides one) the zip sha256; extracts into a scratch directory; records the qcow2 size and sha256; deletes the zip; boots the guest with the provider's own container settings to read its OS, kernel, desktop, browser, LibreOffice and package list; removes the container and its volumes. Inbound transfer is not billed by AWS (the owner's price sheet decides); no mirror or other image is used.

## 5. One host, one disk, minimal network, everything tagged and deleted

* Exactly one `m7i.xlarge` (4 vCPU, 16 GiB, $0.2016 per hour Linux us-east-1, as given by the owner), one gp3 root volume mapped on the AMI's real root device with `DeleteOnTermination=true`, **no other volume, no instance store mapping, no Marketplace AMI** (the launch describes the AMI and refuses a foreign owner, a product code, extra mapped devices or a non-EBS root).
* Default-VPC public subnet, the auto-assigned public IPv4 (no Elastic IP), no NAT gateway, no load balancer, no extra network interface, no snapshot or AMI created, one security group with SSH from the owner's single /32 only (inspected, not trusted), IMDSv2 required.
* One `--tag-specifications` flag carries both the instance and the volume tags (`drums-run`, `drums-bench=true`, `drums-deadline`; the watcher adds `drums-watch-beat`).
* Deletion is verified: `awsctl teardown` terminates, deletes unattached owned volumes, releases owned addresses and reports `clean: true` only when nothing owned remains; the owner then confirms in the console.
* Nested virtualization: the owner states the live API shows `m7i.xlarge` supports it; if a launch option is required it is copied from the live `describe-instance-types` result into the approval's `cpu_options`. The host `check` refuses without `/dev/kvm` either way. **The Docker guest is level 2 and the Drums sessions level 1 of the same instance**: that asymmetry is part of what this measures and is why the numbers do not transfer to bare metal.

## 6. Timeline inside the 2 h ceiling (planning slices, NOT measurements)

The approval's `ceiling_hours` 1.5 plus `stop_margin_hours` 0.5 equals the 2 h maximum (the launch refuses a longer stage). In-host `shutdown -h +90` and the host's dead-man switch enforce it with no external help.

| From launch | Step | Stop rule |
|---|---|---|
| T+0:00 | the watcher is already armed; launch; SSH in | no SSH by T+0:10: tear down |
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

## 10. The external watcher and the host fallback

* **Requirement:** one watcher on a machine that stays powered, awake and online for the whole window (the owner's always-on machine, not a laptop that sleeps; the Mac the owner has is known, and no access or port work is done here), started with `--execute --receipt` BEFORE launch. `launch --execute` refuses without a fresh receipt from a watcher that can terminate, for this run and account, inside its arm window.
* While nothing is owned the watcher waits armed and heartbeats; it expires if no launch occurs within `watch_arm_timeout_s`; after launch it keeps watching and tags the instance `drums-watch-beat` every beat.
* **Host fallback, independent of the watcher:** the host reads that tag from its own metadata service every minute and shuts itself down (terminate) if the beat is more than 600 s old, and the ceiling timer shuts it down at 90 min. A dead watcher therefore costs at most about 11 minutes of extra billing; it does not extend the run.
* Failure modes: a root user on the host can cancel both timers; a tag API throttle or metadata fault shuts the host down early (run lost, bill stops); credentials expiring mid-run stop the watcher's teardown, leaving the two in-host stops; if everything fails, the manual console steps in the runbook apply.

## 11. Prospective bill and stop margin (owner inputs; not approved)

With the illustrative 110 GB disk, 1 GB of transfer out, a 1.5 h ceiling and a 0.5 h stop margin (2.0 billed hours worst case), computed by `awsctl.bill`: EC2 instance hours $0.4032, EBS $0.0241, public IPv4 $0.0100, data transfer out $0.0900, **worst case about $0.53**. Only the instance rate is from the owner; the EBS, IPv4 and transfer prices are placeholders to replace from the current price list. Not included: taxes, support charges, untagged resources, price changes. A timer is not a hard dollar cap: the figure holds only if the in-host stops or the watcher work, and credits absorb it only until they run out. The full cost approval is the owner's and happens elsewhere.

## 12. Remaining actual-host checks (all need the host)

1. The owner-stated nested-virtualization support and any needed launch option on this instance; `/dev/kvm` presence.
2. Whether the pinned image and the qcow2 boot and answer on the guest server inside the 2 h plan.
3. The extracted qcow2 size and the zip bytes (`imagesize`), the real composition manifest, and the disk number.
4. That the stock provider's cold boot works end to end on nested virtualization, and the cold-reset timings (unmeasured).
5. cgroup v2 paths of `limits.py` and the telemetry on a real v2 host (this sandbox is v1).
6. The `librosa` install and a strict import with no stub; the OSWorld requirements install.
7. The watcher, receipt, dead-man switch and teardown against a real account (none has run).
8. Whether any software difference can later be given measured equivalence evidence; until then the result stays NOT_A_COMPARISON.
