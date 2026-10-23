# First run: cost estimate and shutdown plan

STATUS: PROPOSAL ONLY. The USD 100 and USD 140 figures below are NOT approved. Live price
verification and the owner's approval come first. No launch, provisioning or cloud
credential use happens before that.

NOTHING HAS BEEN LAUNCHED. No cloud resource exists, no cloud credential was used, and no
price below was fetched live. Every price is from memory or from published figures
and is marked VERIFY LIVE. Do not approve on these numbers; check them first (AWS Pricing
page or Pricing API, for the exact region, on the day).

## Plan

Linux side only, on AWS. The Drums side runs on a Mac whose availability is undecided
(section "Drums side" below).

| Line | Assumption | Unit price (VERIFY LIVE) | Quantity | Ceiling |
|---|---|---|---|---|
| Bare-metal x86 host, KVM-capable (e.g. a `c5.metal` or `c6i.metal` class, 96-128 vCPU, 192-256 GiB) | 1 instance, on-demand, one region | planning ceiling USD 8.00/h. Remembered list prices are about USD 4-6/h, so the ceiling leaves margin. | 10 h hard wall (setup 3, bake-off 3, measured runs 3, spare 1) | USD 80 |
| EBS gp3 for base image, qcow2 copies, results | 500 GB | about USD 0.08 per GB-month, about USD 0.003 per GB-day, prorated | 1 day | USD 2 |
| Data transfer out / image downloads | image pull ~24 GB in; results out < 1 GB | inbound free; outbound about USD 0.09/GB | < 1 GB | USD 1 |
| Margin for price error and a failed first boot | | | | USD 17 |
| Total proposed hard cap | | | | USD 100 |

Instance choice depends on quota, which I could not check: an on-demand vCPU quota of at
least 96 for the instance family is probably needed and a new account may have less.
Whether newer non-metal instance types now allow nested KVM at acceptable cost is not
verified. If they do, a smaller instance could replace the metal host. Decide at launch
time with live information.

Not included, because it is not AWS and has not been priced: model API spend for
Phase B, and Cua/CUA-Lite cloud services (not used).

## Drums side

Options, none chosen:

1. The owner's own Mac or Mac Studio: no incremental cash, but a declared hourly cost for
   fairness (design doc section 2). Currently unavailable.
2. EC2 Mac dedicated host: remembered on-demand cost is about USD 0.65-1.6/h depending
   on model, with a 24-hour minimum allocation, so roughly USD 16-40 for one host-day
   (VERIFY LIVE). Release is not instant after the minimum.

## Upper bound

Linux side: USD 100 hard cap (above). With option 2 on the Mac side, add about USD 40.
Upper bound for the first run: about USD 140, excluding model spend. The estimate is
dominated by the metal instance hour price and by how long setup takes; neither is
measured.

## Shutdown plan

Before launch, once the owner approves the cost and the exact instance type:

1. One tag on every resource (`purpose=drums-linux-comparison`, `expires=<UTC timestamp>`).
2. Launch with instance-initiated shutdown behaviour set to terminate and
   `DeleteOnTermination` on all volumes.
3. A dead-man timer on the host: `shutdown -h +600` at boot, so it dies at 10 hours
   even if the session is lost; re-armed deliberately, not automatically.
4. An AWS Budget alert at USD 50 and USD 100 to the owner's email.
5. No Elastic IP, NAT gateway, load balancer, snapshots or AMIs left behind. Results are
   copied off the host (`results/`) before terminating.
6. At the end, list instances, volumes, snapshots and addresses in the region and confirm
   empty; report the result.
7. Run time is visible in `run.json` (`provision_s`, `t_start`, `t_end`), so the billed
   hours can be reconciled with the AWS bill.

## Nested KVM on non-metal instances (researched, read-only)

Sources: AWS EC2 User Guide, "Nested virtualization"
(docs.aws.amazon.com/AWSEC2/latest/UserGuide/amazon-ec2-nested-virtualization.html);
AWS What's New 2026-02-16 and 2026-06-18; re:Post article of 2026-02-17.

- Since February 2026 KVM (and Hyper-V) can run as the L1 hypervisor on virtual
  (non-bare-metal) EC2 instances. Launch announcement: C8i, M8i, R8i, all commercial regions.
  June 2026 extension (What's New): also C7i, R7i, M7i, C8id, R8id, M8id, the `-flex`
  variants, I7i and X8i. The user guide lists M7i, M7i-flex, M8i, M8id, M8i-flex, C7i,
  C7i-flex, C8i, C8id, C8i-flex, R7i, R7iz, R8i, R8id, R8i-flex, X8i, I7i, I7ie. Intel only;
  Graviton is not supported.
- It is an option set at launch (CPU option "nested virtualization: enable") or on a
  stopped instance via "Change CPU options". To check a type, the docs give:
  `aws ec2 describe-instance-types --instance-types m8i.2xlarge --query "InstanceTypes[].ProcessorInfo.SupportedFeatures"`
  which must include `nested-virtualization`. This is a read-only call, but it needs
  credentials, so the owner runs it.
- There is no extra charge for the feature itself (per the docs).
- AWS's own guidance: for workloads that need hardware virtualization and are performance
  or latency sensitive, evaluate bare metal. Nested KVM adds a layer (L0 Nitro, L1 your
  instance, L2 the guest).
- Consequence for this test: a tuned baseline should use bare metal for the primary run,
  otherwise nested overhead handicaps the baseline and weakens it. A nested run on
  `m8i`/`c8i`/`r8i` is worth adding as a second data point because it is what many users
  would actually rent; it should be reported as a separate row, never swapped in.
- Not verified: actual nested-KVM performance for desktop guests, and prices of these
  types.

## Bare-metal families that fit

From the AWS Nitro instance list (docs.aws.amazon.com/ec2/latest/instancetypes/ec2-nitro-instances.html),
bare metal exists for: Intel M5, C5, R5, M6i, C6i, R6i, M7i, C7i, R7i, M8i, C8i, R8i, X8i;
AMD M6a, C6a, M7a, C7a, M8a, C8a, R8a. Exact `.metal` sizes and vCPU counts are from
memory and must be confirmed in the console before choosing (I recall c5.metal and
m5.metal at 96 vCPU, c6i.metal and m6i.metal at 128, and c7i/c8i metal in 48xl and 96xl-class
sizes at 192 and 384; VERIFY LIVE). The OSGym paper's own finding is that density is
RAM-bound at high replica counts, so a memory-heavier family (m or r) may beat c for
cost per valid rollout; this is an assumption to test in the bake-off, not a result.

## Quota check (one console visit)

Metal instances count against the On-Demand vCPU quota of their family group, not against
Dedicated Host quotas. The AWS quota page lists the default for "Running On-Demand Standard
(A, C, D, H, I, M, R, T, Z) instances" as 5 vCPUs, adjustable, and for X instances as 0
(source: docs.aws.amazon.com/ec2/latest/instancetypes/ec2-instance-quotas.html). Defaults
rise automatically with usage, so the account's current applied value is what matters and I
cannot see it.

In the AWS console, in the chosen region (Service Quotas, then AWS services, then Amazon
EC2), record, for each region considered:

1. "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances": the Applied quota
   value, in vCPUs. Required: at least the vCPUs of one chosen instance, for example
   96 for a 96-vCPU metal size, 128 for a 128-vCPU size, 192 for a 48xl size. If two hosts
   are run at once (for example a bake-off host plus a measured host), the sum.
2. "Running On-Demand X instances": only if an `x8i` metal type is considered (default 0).
3. Whether any increase request is pending, and the request status.
4. Optional: "Running On-Demand Standard" Spot quota is irrelevant (no Spot for this test).
5. Optionally also in the same visit, EC2 console, Instance types: filter the region for the
   chosen metal type and confirm it is offered in an availability zone (quota does not
   guarantee capacity).

If the applied value is below the need, request an increase to the needed vCPUs in
that console page before approval; increases can take from minutes to days, so do this
first. The quota code for the Standard family is, from memory, L-1216C47A (VERIFY).

## What happens next

Stop here. Launching needs explicit approval of the instance type, count, hours and the
dollar cap, after live price verification.
