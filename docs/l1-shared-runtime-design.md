# L1: shared pre-warmed runtime forks (the Drums claim)

**STATUS: UNBUILT. DESIGN ONLY.** Nothing in this document exists as code. No L1 measurement has been taken,
and nothing here is a result. Where this document names a mechanism, it is a candidate whose feasibility is
unknown. Every number from this repository so far was taken at L0 (below) and is not evidence for L1.

Related: `drums-linux-architecture.md` (the level definitions and the rule that every result states its level),
`linux-comparison-design.md` (the matched comparison and the verdict rule), `envd-linux-spike.md` and
`osworld-contract-spike.md` (the L0 spikes).

## 1. What L1 means, and what it does not

- **L0 (built, a precursor):** one kernel and one set of system services, shared files and page cache,
  private copy-on-write filesystem state, per-session namespaces (PID, mount, IPC, UTS, network), per-session
  display and application processes, an nftables policy between sessions. Each session **launches its
  applications cold**. What is shared is files, not running memory.
- **L1 (UNBUILT, the Drums claim):** L0 plus **shared running runtime**. Attempt processes are created by
  **forking from a pre-warmed template process** (a zygote / fork server that has already loaded the
  application's libraries and finished its initialisation), so the initialisation work and the memory it produced
  are shared copy-on-write across attempts, not just the files on disk. A reset re-forks; it does not
  relaunch.
- **Not L1, whatever it is called:**
  - the page cache and overlay lower layers (already L0, and containers share them too);
  - a pool of pre-created idle sessions with eager rebuild (`scalecua/pool.py` does this; it hides latency
    and is an L0 improvement);
  - fewer processes because something was omitted (no window manager, smaller screen, a stubbed server);
  - snapshotting or restoring a whole VM (that is the incumbent's own fast-reset mechanism, a baseline to
    beat, not a Drums mechanism);
  - memory merging that is only described, not measured (section 3).

The scorer already enforces the label: `drums_level` is recorded in `run.json`, and a Drums result that is not
`L1` is reported `precursor_only`, never `drums_wins`.

## 2. L0 datapoints already taken (kept separate from L1)

All are single samples in one 4-CPU, 16 GiB sandbox, not benchmarks, and none involves forking from a template.

| Datapoint | Source | What it is |
|---|---|---|
| Session ready in about 0.8 s (p50 and p95), mean session PSS about 38 MiB | `osworld-contract-spike.md` smoke run, 4 slots, 8 attempts | EMPTY sessions: Xvfb, the guest stub and init; no browser, no application. A per-session floor, not an application cost. |
| About 363 MiB PSS (814 MiB RSS), 12 processes; about 9 MB private upper state | `envd-linux-spike.md` | One session running Chromium launched cold. This is the number L1 is meant to attack. |
| About 394 MiB PSS (908 MiB RSS), 14 processes; about 35 MiB tmpfs growth after one attempt | `osworld-contract-spike.md` | Same, with the full-root overlay and the guest stub. |
| Reset plus browser settle about 2.5 s | `envd-linux-spike.md` | Cold relaunch time at L0. |

Consequence for the L1 argument: at L0 the floor (38 MiB) is small and the application dominates (about
350 MiB of the 363). If L1 helps it must help the application part. If most of those 350 MiB turn out not to
be shareable (section 4), the honest L1 result is small or negative, and that is a result to publish.

## 3. Candidate mechanisms (all UNBUILT, all with unknown feasibility)

| ID | Mechanism | Shares | Main doubt |
|---|---|---|---|
| F | Fork server per application class: a template process initialised up to a fork-safe point, `fork()` per attempt, then the child opens its own display, network and profile. | Library relocations, initialised heaps, JIT/interpreter state, caches built before the fork point. | Many GUI applications are multi-threaded, hold X or D-Bus connections and sandbox themselves; there may be no fork-safe point late enough to share much (section 4). |
| K | Kernel same-page merging: mark the session processes mergeable (`madvise(MADV_MERGEABLE)`, or `prctl(PR_SET_MEMORY_MERGE)` on kernels that have it) so identical anonymous pages across sessions are merged. | Identical anonymous pages found by scanning, no cooperation from the application. | CPU cost of scanning, merge latency, and cross-session timing side channels. It is page deduplication, not forking; if used it must be labelled K, not F. |
| R | Process checkpoint/restore (CRIU) of a warmed process tree into COW copies. | A whole warmed tree. | Research; restores of GUI and network state are fragile. Out of scope until F and K are understood. |

F is the mechanism the architecture doc means by L1. K is a cheaper fallback worth measuring separately
because it needs no changes to applications. A result must say which mechanism produced it; a K result must
not be called a fork result.

## 4. Hazards that decide feasibility (to be probed, not assumed)

1. **Fork safety.** Threads, file descriptors, X and D-Bus connections, locks, and sandboxes (Chromium's
   zygote and namespace sandbox, LibreOffice's `soffice` process model) do not survive a naive fork. The
   template must stop before any of these exist, which limits how much it can pre-initialise.
2. **Duplicated state.** Forked siblings share random-number state, cached nonces, session ids, cookies,
   timestamps and monotonic-clock anchors. Attempts must not share secrets or identifiers, and task validity
   can depend on time or entropy. Each child needs explicit re-seeding and a hygiene test.
3. **Template staleness.** Anything baked into memory (locale, time zone, window geometry, profile paths,
   environment) is frozen. A task that needs a different value needs a different template.
4. **Sharing measurement is easy to get wrong.** PSS divides shared pages among the sharers, so a lone
   child shows no saving, and the page cache is already shared. Savings must be measured from host memory
   with N siblings against the same N cold launches (section 5), not read off one process.
5. **Interference through shared pages.** Copy-on-write is isolation for correctness, not for timing. A
   shared-page side channel is a security question for multi-tenant use and is not a threat to benchmark
   validity, but it must be written down.
6. **Scope against the real OSWorld image.** The image runs a full GNOME session under systemd
   (`gnome-namespace-investigation.md`, unresolved). A whole desktop session is unlikely to fork cleanly; L1
   probably applies to individual applications (browser, office suite, the guest server), not to the
   desktop. Whether that is enough to matter for OSWorld tasks is unknown.

## 5. Milestones and exit criteria

Every threshold below is a **proposal to be pre-registered before the first measurement**; none has been
chosen. Every milestone has a way to fail, and a failure ends the L1 claim at that milestone, to be published.

The design rule that separates L1 from L0: **one switch.** The runtime has a single flag, `runtime=cold|fork`
(or `cold|ksm`). Everything else (namespaces, overlay, network, firewall, display, applications, tasks, agent)
is byte-identical between arms. L0 is the `cold` arm. An L1 claim is a measured difference between the arms of
the same build, never a comparison between different builds.

| Milestone | Question | Measurement | Pass condition (proposed) | Fails if |
|---|---|---|---|---|
| L1-M0 Feasibility | Can each target application be forked from a template and still work? | For each of: the guest server (Python), Chromium, a LibreOffice document, a text editor: build a template to the latest fork-safe point; fork; run the standard action script; record success and where fork-safety broke. | At least one GUI application class forks and completes its task script correctly in 20 of 20 forks. | No GUI application has a fork-safe point that completes a task. L1-F is not viable for it; fall back to K or stop. |
| L1-M1 Memory sharing | Does fork share real memory, not just files? | Host `MemAvailable` (and cgroup memory) with N = 1, 2, 4, 8, 16 attempts, `cold` vs `fork`, the same application and task script; also per-process `Private_Dirty`, `Shared_Dirty`, `Anonymous` from `smaps`, and for K the kernel's merge counters. | Marginal host memory per additional attempt, `fork` vs `cold`, lower by a pre-registered margin with the 95% interval excluding zero. | The interval includes zero, or the saving is below the margin. Report as "L1 does not reduce memory for this application". |
| L1-M2 Start latency | Is fork-to-usable faster than cold launch? | Time from the reset request to the first successful observation, p50/p95, over at least 50 resets per arm. | `fork` p95 below `cold` p95. | Slower, or only faster at p50. |
| L1-M3 State hygiene | Do siblings share anything they must not? | Tests: distinct pids, RNG output, session ids and cookies per sibling; a write in sibling A absent in B; killing the template or a sibling leaves the others running; no descriptor shared. | All hold in every fork of every run. | Any leak. A leak invalidates every L1 result built on it until fixed. |
| L1-M4 Task validity | Do tasks behave the same at L1? | The same tasks, same agent and model, same observations and actions, both arms; harness failure and interference counts as in the design doc. | Success rates equal within the pre-registered tolerance and no new fault classes; interference count not higher. | A task passes at L0 and fails at L1 for a cause rooted in the fork. |
| L1-M5 Density | Does the saving buy more concurrent attempts at acceptable latency? | Maximum N at which p95 step latency and fault rate stay inside bounds, both arms, on the same host. | Higher N at the same bounds. | No gain, or latency degrades faster at L1. |
| L1-M6 Against incumbents | Does L1 win the matched comparison? | The Linux-vs-Linux harness (valid rollouts per hour, cost per valid rollout, RAM and CPU per environment) with `drums_level=L1`. | The existing verdict rule, with intervals. | A tuned incumbent matches or beats it. |

Only after L1-M0 to L1-M4 pass may a result set `drums_level=L1`. L1-M6 is the first point at which a
`drums_wins` headline is possible, and it can still come out as a loss.

## 6. What would prove L1, and what would not

- **Proves L1 (all of):** M0 and M3 pass (it works and does not leak); M1 shows the saving in **host memory**
  with N siblings against N cold launches, same build; M4 shows tasks behave the same.
- **Does not prove L1:** a lower PSS on one process; a smaller number from a different application set or
  display size; savings that vanish when the page cache is cold; a K result described as a fork; any L0 run
  with a new label.
- **Proves "L1 does not help here" (publish it):** M1 fails for the applications that dominate the L0
  footprint. Then the architectural claim reduces to L0's properties, which a lean container-per-attempt
  baseline may match (the architecture doc already says this is a finding, not a flaw).

## 7. Dependencies and open decisions (not resolved here)

- Kernel features: `PR_SET_MEMORY_MERGE` and KSM need a recent kernel; CRIU needs its own prerequisites; the
  sandbox's kernel is 6.18 but nothing here has been tried. A real host's kernel must be recorded with any run.
- Which application is the first L1-M0 target (browser is the largest measured L0 cost) and which OSWorld
  application classes matter for the claim.
- The pre-registered margins and tolerances above: owner decision, before measuring.
- Whether Drums-on-Linux is allowed to claim anything at all if only K, not F, works.
- How L1 interacts with the OSWorld image's systemd/GNOME stack (section 4, item 6): unknown.

Nothing in this document authorises building or running anything; it is the specification a future build would
be judged against.
