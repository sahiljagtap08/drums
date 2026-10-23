# First run: cost estimate and shutdown plan

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

## What happens next

Stop here. Launching needs explicit approval of the instance type, count, hours and the
dollar cap, after live price verification.
