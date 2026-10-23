# Linux-vs-Linux comparison: cost model and shutdown plan

STATUS: DRAFT MODEL, NOTHING APPROVED. The earlier Mac-based plan and its figures
(USD 100, USD 140, a 10-hour cap) are withdrawn and are not carried into this document.
No launch, provisioning or cloud credential use happens, and no approval is assumed.
Prices are the owner's to ground independently; none is a verified figure here. No price below was fetched live; every price is VERIFY LIVE and the dollar values are
illustrations of the formula at a placeholder price, not a request for approval.

## Shape of the first run

One bare-metal x86 host of one instance type, used by every side in turn (design doc
section 2): Drums-Linux, then each runnable incumbent, in alternating order. All sides on
the same host makes hourly cost identical by construction. Drums-Linux needs no KVM and
could be built and tested on a much smaller instance first; only the KVM incumbent
needs metal (or nested virtualization, see below).

Instances: 1 (a second only if the host cannot be reused between a bake-off and the
measured runs, which is not planned).

## Hours (a plan to confirm, not measurements)

| Block | Hours | Notes |
|---|---|---|
| Host setup: filesystem (XFS reflink), kernel limits, image pulls (OSWorld image 23.7 GB), pinned installs | 3 | The image pull and conversion time is the least certain part. |
| Bake-off and tuning, per incumbent (3 incumbents at 1 h each, tuning time capped) | 3 | Cap is part of fairness. |
| Drums-Linux smoke, isolation probes, conformance run | 1 | |
| Measured runs: 4 sides x (concurrency sweep 1, 8, 16, 32 and the largest sustainable level) x 2 orders | 4 | Depends on per-attempt time, which is unknown until W1 runs. |
| Spare, cleanup, copying results off the host | 1 | |
| Total | 12 | |

If the incumbent count shrinks (a candidate cannot run, CUA-Lite stays licence-blocked)
the hours shrink with it.

## Upper bound formula

Upper bound = hours x ceiling price per hour + EBS + transfer + margin.

- Ceiling price per hour: P_ceiling, to be set from the live price of the chosen type, plus
  a stated margin. As an illustration only, at a placeholder P_ceiling of USD 8.00/h:
  12 h x 8.00 = USD 96, plus about USD 2 EBS (500 GB gp3 for one day, remembered rate about
  USD 0.08 per GB-month) and under USD 1 transfer, plus margin. This is an arithmetic
  example, not a quote and not a proposal to approve.
- Not included: inference for Phase B (model, GPU type and hours undecided; it is a
  separate approval), and any GPU for training (Phase C is out of scope).

## Two-stage money path (from the training plan)

Nothing is approved. If and when approval is asked, it is asked in two stages: (a) a bounded
validation job (the infrastructure smoke, then a two-step trainer smoke on the cheapest node that
fits), with a hard wall-clock cap, automatic instance termination and a verified hourly price,
labelled engineering validation with an uncertain outcome; (b) the full matched run, priced from
stage (a)'s observations rather than the planning bounds. See `docs/training-comparison-plan.md`
section 10a. The 12-hour block above is superseded by that staging for any Phase C spend.

## What must happen before any approval request

1. Choose the instance type from the metal families below and read its live on-demand price
   for the chosen region.
2. Read the account's vCPU quota (checklist below) and file an increase if needed.
3. Confirm the type is offered in a zone of the region.
4. Fix the hours after W1 has been dry-run on whichever side can run it locally.
5. Only then state a dollar cap and ask for approval.

## Shutdown plan (applies once something is approved)

1. One tag on every resource (`purpose=drums-linux-comparison`, `expires=<UTC timestamp>`).
2. Instance-initiated shutdown behaviour set to terminate; `DeleteOnTermination` on all volumes.
3. A dead-man timer on the host (`shutdown -h +N`, N from the approved hours), re-armed
   deliberately, never automatically.
4. An AWS Budget alert at half and at the full approved cap, to the owner's email.
5. No Elastic IP, NAT gateway, load balancer, snapshots or AMIs left behind. Results are
   copied off the host (`results/`) before terminating.
6. At the end, list instances, volumes, snapshots and addresses in the region and confirm
   empty; report the result.
7. `run.json` records `provision_s`, `t_start`, `t_end` so billed hours reconcile with the bill.

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
- Consequence for this test: the KVM incumbent should use bare metal for the primary run,
  otherwise nested overhead handicaps it and weakens the baseline. Drums-Linux needs no
  KVM and runs fine on the same host. A nested run on
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

Stop here. Nothing is launched, and no approval is assumed.
