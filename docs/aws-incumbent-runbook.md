# Runbook: first matched Drums vs VirtualBox run on one AWS bare-metal host (action-ready preparation)

**Status: BUILT (code and scripts, unit-tested with fakes) and UNRUN. HYPOTHESIS-UNTESTED for everything that needs the host. No AWS credential was available here, no instance was launched, no quota was changed, nothing was spent, nothing was merged to main. No VirtualBox, no VM image and no real incumbent result exists. The first result this produces will be an engineering validation on ONE task: no throughput, no cost-advantage, no training and no benchmark claim.**

Branch `claude/linux-comparison-prep` only. Owner actions are marked **OWNER**; everything else is code in this repository.

## 1. What was built (and what each piece refuses)

| Piece | Path | Fail-closed behaviour |
|---|---|---|
| Matched runner entrypoint | `compare/drums_compare/host_run.py` (`check`, `gates`, `run`, `export`) and `stages.py` | `check` refuses on: virtualized or non-x86 host, no vmx/svm, OSWorld not at `04718ea` or modified, a module needing a stub (unless named in `allow_stub`, and refused if a scored function lives in a module that imports it), VM not reporting exactly 2 vCPU and 4096 MiB (or with an execution cap), no `init_state` snapshot, Drums preflight not go (runtime probes), kit missing, placeholder prices, no runtime ceiling, no named watcher owner, any task other than the public unmodified `e0df059f` without actions plus a validation record (and a `VARIANT:` label for a custom JSON). `gates` and `run` refuse unless the previous stage passed for the SAME config hash. |
| Live-state gate | `host_run.snapshot_gate`, `score_gate` | on BOTH arms: a live process holding an in-memory token and a file marker survive a snapshot/revert after being killed or deleted post-save; the real evaluator scores correct 1.0, wrong 0.0, correct again 1.0 from the same saved state. **Not covered: an application window with typed text** (reported as such). |
| Per-environment capture | `envd_linux/cgroup_telemetry.py` (Drums cgroups), `compare/drums_compare/vbox.py` (`VBoxProbe`, VM processes) | sampled lower bounds on both arms, empty-sample counts, kernel readbacks; **host-wide figures stay recorded too**. |
| Overhead accounting | `first_check.py` | external prep, adapter build and between-block snapshot deletion are separate fields and are added in the with-prep figures; the VM image preparation is NOT timed unless the owner supplies `vm_prep_s_reported`. Snapshot names are unique per block. |
| Host bootstrap | `deploy/aws_host/bootstrap.sh` | idempotent; refuses root, non-Ubuntu-24.04, virtualized host, no vmx/svm, missing `DRUMS_REF`; no cloud API call. |
| Image fetch | `deploy/aws_host/fetch_image.sh` | refuses without `--accept-licence-risk`; official source only; records the zip sha256 (no expected hash exists) and the REAL image composition; takes `init_state`. |
| AWS lifecycle and control | `compare/drums_compare/awsctl.py` (non-root `aws` CLI profile) | dry-run unless `--execute`; refuses: unapproved or incomplete approval file, root identity, a second host, open SSH, budget below worst case. Examples: `deploy/aws_host/*.example.json`. |

## 2. Accepted deviations and unresolved items (each recorded in the run's `pins.json`)

* **Official image licence:** the dataset card's License field is blank: unresolved uncertainty. **Image composition is UNKNOWN** until `image_manifest.json` exists; "same software" remains a TARGET (openbox vs GNOME, Chromium vs google-chrome, different packages: `docs/incumbent-preflight-plan.md` section 4a). Nothing is claimed as matching.
* **VM networking is a deviation from stock:** the stock manager bridges the VM NIC; an EC2 host does not route a second MAC, so the stock provider would never learn an IP. `bootstrap.sh` creates a host-only network and `fetch_image.sh` attaches it. Reasoned, never run: if it fails, the run stops (no NAT or port-forward alternative is added silently).
* **Observations:** `require_a11y_tree=False` on both arms (Drums has no accessibility tree); screen size 1280x800 passed to both; both differ from published defaults.
* **Stock VirtualBox provider as shipped:** fixed 3 s sleeps in save, revert and start stay (shortening them would be a separately labelled tuning). Its revert is `controlvm savestate` then `snapshot restore`.
* **Drums restore on the host kernel is UNTESTED.** CRIU 4.2 was only exercised on kernel 6.18 here; the host will run Ubuntu's kernel and a CRIU taken from the distribution unless exact `.deb` URLs are supplied. The `gates` stage is the test. **If it fails the run stops;** switching the Drums arm to no reuse is a different, separately labelled experiment, never a silent fallback.
* **cgroup v2:** this sandbox is cgroup v1; Ubuntu 24.04 is v2. The v2 branches of `limits.py` and `cgroup_telemetry.py` have not run on a real v2 host; `check` runs the cgroup-limit probe and the readbacks decide.
* **Task two (Impress `2cd43775`) is not enabled.** It needs the HOME or path decision and a validated GUI action sequence (`docs/task-audit-13.md` section 3). The runner accepts it only with `actions`, an `actions_validated` record and a `VARIANT:` label if the JSON is custom. No direct write to the evaluator's target file is acceptable.
* **Stubs:** `librosa` is installed in the host venv so no stub is needed; if the install fails, `check` refuses rather than stubbing.

## 3. Shortest credible gate sequence (stop at the first failure; export and tear down, do not debug on the clock)

0. **OWNER:** quota request 5 to 96 is APPROVED and in effect; a non-root IAM profile exists; the owner's SSH CIDR is known. (Both candidate instances are 96 vCPU, so 96 is exactly enough with nothing else running. Metal capacity in the chosen AZ is not known until a launch is attempted.)
1. **OWNER:** copy `approval.example.json`, fill every field with current prices, set the budget at or above the `awsctl plan` worst case, set `approved: true`. `python -m drums_compare.awsctl plan --approval approval.json` must print the bill.
2. **OWNER:** start the external watcher on a machine other than the host (`awsctl watch --approval approval.json --execute`), then `awsctl launch --approval approval.json` (dry run) and, if it answers as expected, `--execute`.
3. On the host: `DRUMS_REF=<commit> bash deploy/aws_host/bootstrap.sh`.
4. On the host: `bash deploy/aws_host/fetch_image.sh --accept-licence-risk`.
5. `python -m drums_compare.host_run check --config host_run.json --out RUN` (all PASS, or stop).
6. `... gates ...` (both arms pass, or stop).
7. `... run ...` with `blocks: 2` first (a smoke), then the intended blocks only if the smoke is clean and time and budget remain.
8. **OWNER:** pull `run_export.tar.gz` and `SHA256SUMS` (the run writes block files as it goes; pull them periodically, because the watcher terminates at the deadline regardless).
9. `awsctl teardown --approval approval.json --execute`; confirm `clean: true`, then confirm in the console that no instance, volume or address tagged `drums-run=<run_id>` remains and no billing line is accruing.

## 4. Cost and control design (no launch; prices are the owner's inputs)

**Candidates (not approved; selection and budget are the owner's):** `m7i.metal-24xl` (96 vCPU, 384 GiB, $4.8384/h) or `m5.metal` ($4.608/h), Linux, us-east-1, as given by the owner.

**Prospective bill components** (`awsctl.bill`, itemized, worst case): EC2 instance-hours for ceiling plus stop margin; EBS root volume (size times a per-GB-month price, prorated by the same hours); the auto-assigned public IPv4 address (hourly); data transfer out. Not included: taxes, support plan charges, untagged resources, price changes. **Illustrative arithmetic only**, with a 4 h ceiling, 0.5 h stop margin, 200 GB gp3 at $0.08 per GB-month, public IPv4 at $0.005 per hour, 20 GB out at $0.09 per GB (the last three are placeholders to be replaced with current values): `m7i.metal-24xl` about $23.69 (instance $21.77), `m5.metal` about $22.66 (instance $20.74); with a 2 h ceiling and 0.5 h margin on `m7i.metal-24xl` about $13.96. The VM runs 2 vCPU on a 96-vCPU machine: the cost is the machine, not the test.

**Not a hard dollar cap.** A timer bounds hours, not dollars. Credits absorb charges only until they are exhausted, so this design does not promise zero cash: it promises two independent stops plus a priced margin.

| Control | Where | What it stops | How it fails |
|---|---|---|---|
| One host maximum | `awsctl launch` and `watch` | a second instance | a launch made outside `awsctl` (console, other tool) is not counted unless it carries the `drums-run` tag; the watcher only sees tagged resources |
| In-host shutdown | user data `shutdown -h +N`, instance-initiated shutdown behaviour TERMINATE | the host at the ceiling | the timer can be cancelled on the host by a command; `host_run` and the scripts never do so |
| External watcher | `awsctl watch` on the owner's machine | tears down at the deadline or on a second host | the machine sleeps or loses network; credentials expire (then the in-host shutdown is the remaining stop) |
| Deletion of owned storage | `DeleteOnTermination=true`, `awsctl teardown` deletes unattached owned volumes and reports any still attached | leftover EBS charges | an attached volume that outlives its instance is reported, never called clean |
| Public address | no Elastic IP is allocated; the auto-assigned address is released with the instance | address charges | a manually allocated address is released only if tagged |
| Export | `host_run export`, owner pulls | losing logs at teardown | the deadline does not wait for the pull |
| Manual fallback | console: terminate every instance tagged `drums-run=<run_id>`, delete volumes, release addresses; set an AWS Budgets alert as a notification only | everything above failed | an alert is not a stop |

## 5. Material blockers, in order

1. The quota request is PENDING (5 to 96): nothing can launch until it is approved, and bare-metal capacity in the chosen zone is unknown.
2. No AWS credential or approval file exists here: every host action is an owner action.
3. Untested on a real host: VirtualBox on Ubuntu 24.04 metal (DKMS, Secure Boot), the host-only network, Guest Additions in the official image, Drums CRIU restore on the host kernel, the cgroup v2 paths.
4. The official image's licence field is blank and its composition unknown until fetched.
5. The second task needs a HOME decision and a validated GUI action sequence.
6. Prices other than the two instance rates are unverified placeholders until the owner fills the approval file.
