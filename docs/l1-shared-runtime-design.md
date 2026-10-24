# L1: shared pre-warmed runtime forks (the Drums claim)

**Epistemic status: sections 1 to 7 are DESIGN (L1 is UNBUILT and UNPROVEN). Sections 8 to 23 are PROTOTYPE pre-registrations (each
committed before its measurement) and MEASURED results, with raw data in `docs/data/`.** Nothing here is an L1 proof, and `drums_level`
stays L0. Where this text says "UNBUILT" it refers to the L1 claim, not to the prototypes.

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

## 8. PROTOTYPE pre-registration (written and committed BEFORE any measurement; local sandbox, no spend)

**PROTOTYPE. UNBUILT-to-L1.** This section fixes the thresholds for a minimum feasibility prototype of mechanism F (fork
server) on the real Chromium workload. A positive result is feasibility evidence for the fork mechanism on the processes
that can be forked; it is not an L1 proof, and any scorer run still records `drums_level=L0`.

What is built (only after this section is committed): a zygote process, started once, that pre-imports the guest server's
heavy dependencies and forks one process per session; each forked child becomes the session's PID 1 (new PID, mount, IPC,
UTS and network namespaces), uses the same filesystem and network setup as L0, starts its own Xvfb, openbox, D-Bus, AT-SPI
by `exec`, and runs the REAL OSWorld guest server (the kit, `main.py` unmodified) as a fork of itself. Chromium is
started by `exec` in every arm: a browser with live X, D-Bus, GPU-less compositor threads and a sandbox cannot be forked
from a template (to be shown, not assumed, in the hazard test).

Arms, identical in everything else (kit, template, Xvfb size, Chromium flags and profile, host, run order interleaved):
- **C0** cold as shipped: `python main.py` (OSWorld's debug reloader, so two interpreter processes);
- **C1** cold module entry: a fresh interpreter that imports `main` and calls `main.app.run(debug=False)`, the same
  entry the fork arm uses, so C1 isolates fork from entry-point differences;
- **F** fork: the guest server is a fork of the zygote, `main` imported after the fork (pyautogui connects to X at import,
  so the fork-safe point is before that import).

Measurements, N >= 20 per arm:

| ID | Measurement | Design | PASS threshold (fixed now) |
|---|---|---|---|
| a | Marginal host memory per additional session: slope of (MemTotal - MemAvailable) against concurrent session count at 1, 4, 8, 12, 16, 20 sessions, 3 trials per arm, sessions destroyed between trials | Same as L1-M1, host-level, not per-process PSS | F slope <= 0.85 x C1 slope (at least 15% lower) AND the 95% bootstrap interval of (C1 slope - F slope) over trials excludes 0 |
| b | Server-ready latency: session create request to guest `/platform` answering, inside the session | 20 sequential sessions per arm, interleaved | F p95 < C1 p95 AND F median <= 0.7 x C1 median |
| c | App-ready latency: session create request to the Chromium page title visible over CDP | 20 sequential sessions per arm | F p95 < C1 p95 |
| d | State hygiene of siblings: distinct PID namespaces and pids, each with its own Xvfb, no shared listening socket, numpy global RNG state differs between siblings | direct checks in two simultaneous forked sessions | all hold |
| e | Hazard test, fork of a RUNNING server: fork the live guest server (X connection, D-Bus and listening socket already open) and report exactly what is shared or broken | direct experiment | no threshold; every observed breakage is reported |
| f | Share bound: Chromium processes' share of per-session private memory, and the resulting upper bound on what forking only the non-Chromium processes can save | smaps_rollup of one session of each arm | no threshold; reported |

L0 reference datapoints (single samples, not comparable by construction, same sandbox): 0.8 s empty session ready, about
2.5 s reset plus settle with Chromium, about 350 MiB application memory per session.

Expected outcome, stated in advance so it can lose: (a) likely FAILS, because Chromium dominates per-session memory and is
not forked; (b) likely passes; (c) likely shows no gain; (d) and (e) are where fork risk shows. If (a) fails the honest
conclusion is that mechanism F alone cannot deliver the L1 memory claim on this workload and mechanism K (page merging) or a
different sharing mechanism would be needed; K is measured separately only if the host allows it and is labelled K.

## 9. PROTOTYPE results (measured; thresholds are the ones committed in 84a9241)

**PROTOTYPE. Not an L1 proof.** Everything below is mechanism F applied to the guest-server process only; Chromium is started by
exec in every arm. Any scorer run built on it records `drums_level=L0`. Raw data: `docs/data/l1-prototype/` (`bench.json`,
`hazard.jsonl`, `hygiene.json`). Code: `envd_linux/l1proto/` (zygote, bench, hygiene, hazard test, report) and the
`server_mode`/`workload` options of the Supervisor and `session_init`. Single sandbox (4 vCPU, 16 GiB, kernel 6.18), the real
OSWorld guest server from the kit, Chromium 1194 on a blank data page with a template profile, N = 20 sessions per arm for
latency (after 2 discarded warm-ups, arm order rotated) and 4 trials per arm of 20 concurrent sessions for memory.

### Verdict against the pre-registered thresholds

| ID | Threshold (fixed in 84a9241) | Measured | Verdict |
|---|---|---|---|
| a memory | F slope <= 0.85 x C1 slope AND 95% bootstrap interval of (C1 - F) excludes 0 | C0 281.0, C1 231.1, F 216.8 MiB per additional session (per-trial slopes C1 231.0 / 232.6 / 230.2 / 230.8, F 218.1 / 216.9 / 215.7 / 216.6). F/C1 = 0.938; C1 - F = 14.3 MiB, interval [13.1, 15.5] | **FAIL**: the saving is real in direction (the interval excludes 0) but 6.2%, not the required 15% |
| b server ready | F p95 < C1 p95 AND F p50 <= 0.7 x C1 p50 | p50 0.86 / 1.08 / 1.53 s and p95 0.93 / 1.17 / 1.62 s for F / C1 / C0. F p95 < C1 p95 holds; F p50 is 0.80 x C1 p50 (needs <= 0.70) | **FAIL** on the median condition (p95 condition passes) |
| c Chromium ready | F p95 < C1 p95 | 1.33 s vs 1.53 s (C0 2.01 s). The Chromium-specific part is the same 0.35 s in every arm | **PASS**, and the whole gain is the server's, not Chromium's |
| d hygiene | all hold | distinct PID, mount and network namespaces; pid sets disjoint; one Xvfb per session; each session sees only its own X socket (X90, X91); listening ports 5000 and 9222 exist in each separate network namespace; no zygote file descriptor in either session; siblings' `random`, `os.urandom`, `uuid4` and numpy global draws all differ | **PASS** (the numpy-global duplicate-state hazard did NOT reproduce with numpy 2.5.4: distinct draws; a library with its own RNG state is still a hazard class, this one does not exhibit it) |
| e hazard, fork of a RUNNING server | no threshold | see below | reported |
| f share bound | no threshold | see below | reported |

Overall: mechanism F, applied where it can be, does not meet the memory threshold (a) or the latency threshold (b). It meets
(c) and (d). This is the outcome the pre-registration said was likely.

### Full latency table (seconds, p50 / p95, n = 20 per arm)

| | C0 as shipped | C1 module entry | F fork |
|---|---|---|---|
| session init to server ready | 1.49 / 1.57 | 1.03 / 1.13 | 0.85 / 0.91 |
| request to server ready (b) | 1.53 / 1.62 | 1.08 / 1.17 | 0.86 / 0.93 |
| request to Chromium ready (c) | 1.89 / 2.01 | 1.43 / 1.53 | 1.21 / 1.33 |
| `create()` returns (includes network setup) | 2.44 / 2.62 | 2.00 / 2.09 | 1.74 / 1.88 |

The zygote is a fixed cost: preload 0.22 s, RSS 55 MiB (PSS 51 MiB), paid once. With a saving of 14.3 MiB per session it breaks
even at about 4 sessions, and at 20 sessions the net host saving is about 235 MiB of roughly 4.6 GiB (about 5%).

Reading C0 honestly: C0 (OSWorld's `python main.py`, whose debug reloader runs two interpreters) is 281 MiB per session and F is
23% below it, but C1 (the same fresh-interpreter entry as F, without the reloader) already accounts for 18 points of that. The
fair comparison for the fork mechanism is F against C1: 6.2%.

### (f) Share bound: what fork can reach at all

Private memory of one session at N = 20, trial 0 (from `smaps_rollup`, MiB private; Xvfb, D-Bus, AT-SPI and openbox are exec'd and
are not shared by fork):

| process group | C0 | C1 | F |
|---|---|---|---|
| Chromium (10 processes) | 89 | 90 | 90 |
| guest server (Python) | 91 | 41 | in the next row |
| session init (+ forked server in F) | 10 | 10 | 37 |
| Xvfb | 8 | 8 | 8 |
| D-Bus, AT-SPI, openbox | 6 | 6 | 6 |
| total private | 207 | 157 | 142 |

The forkable part is the Python server plus session init: 51 MiB in C1, 37 MiB in F, a difference of 14 MiB, which is exactly the
measured slope difference (14.3). Even if ALL of that Python memory were shared across sessions the saving could not exceed about
51 MiB of the 231 MiB per-session host cost (22%). The rest of the per-session host cost is Chromium (about 90 MiB private here),
the other exec'd processes, tmpfs for the session uppers (31.5 MiB per session in every arm) and kernel structures. Chromium is the
largest forkable-in-principle component and it was not forked: it is exec'd in all arms.

These per-session numbers are not comparable with the L0 datapoints of section 2 (363 MiB PSS for a Chromium session that
loads a form page, 0.8 s empty session, 2.5 s reset plus settle): the workload (blank page, real guest server, no form), the
accounting (host delta versus per-process PSS) and the readiness definition all differ.

### (e) Hazard test: forking a RUNNING server (what breaks)

`hazard_running_fork.py` starts a process that holds what the live guest server holds: an X connection with a window, a D-Bus
connection, a listening socket and a thread holding a lock, forks it, and uses both copies. Findings (raw: `hazard.jsonl`):

1. **No isolation from X.** The forked child saw the parent's window (`PARENT-SESSION-WINDOW`): both copies are clients of the same
   X server, so a "second session" made this way shares one display. Isolation requires a separate Xvfb per session, which
   means connecting AFTER the fork, as the working F arm does (pyautogui imports and connects after the fork).
2. **A shared X connection deadlocks both copies.** Parent and child issued requests on the inherited socket concurrently: the
   parent hung (no result after 12 s) and the child hung (killed at 15 s, no output): replies are consumed by the wrong process.
3. **A shared listening socket is shared.** Of 100 connections, the child accepted all 100 and the parent 0 (either process can
   accept; there is no routing to "its" session).
4. **D-Bus identity is duplicated and the child's connection is dead.** Parent and child report the same unique name (`:1.0`); the
   parent's call returned in 8 ms, the child's call failed after 3.0 s with a timeout, because the connection's worker thread
   does not exist in the child.
5. **A lock held by another thread at fork time stays held.** The child could not acquire it in 2 s. (A first run of this check was
   invalid: the holder's 30 s sleep had ended before the check ran, so it reported "acquired"; it was re-run with a 600 s hold.)

Chromium itself was not forked or probed: a multi-process, multi-threaded, sandboxed browser with live X and D-Bus state would
hit every one of the findings above, and it is exec'd in every arm. That is an inference from the findings, not a Chromium test.

### What this does and does not support

- Supports: fork-from-a-pre-warmed-template WORKS for a process that has not yet opened its X, D-Bus or socket state (hygiene
  passes, the server answers the real guest contract, and the session machinery is the same as L0), and it gives a small, real
  saving (14 MiB per session, 0.2 s per start) on the part it can reach.
- Does not support: any claim that mechanism F delivers the L1 memory saving on this workload. It fails the pre-registered
  thresholds, and the share bound shows the reachable ceiling is about 22% even if the Python part were wholly shared.
- Open, not run: whether another mechanism (page merging, checkpoint/restore, a different process model for the browser) reaches the
  Chromium memory; none was attempted. The design doc's section 3 entries K and R remain candidates with unknown feasibility.

## 10. PROTOTYPE B pre-registration: shared browser branching (written and committed BEFORE any measurement)

**PROTOTYPE of mechanism B. Not an L1 proof; `drums_level` stays L0.** Section 9 showed that mechanism F cannot reach the browser, where
most per-session memory is. Mechanism B changes the unit of sharing: ONE warmed Chromium browser process tree per host, each session
attached as its own PROFILE inside it, instead of one exec'd browser per session.

### The design under test, and its honest constraints

- One Chromium started once on the host, with remote debugging, one `--user-data-dir`. A session is attached by launching
  `chrome --user-data-dir=<shared> --profile-directory=<SlotN> <url>`, which hands off to the running browser and opens a window
  of that profile there. Each profile directory is a copy of the template profile (the copy cost is part of the attach latency).
- **The isolation unit becomes a Chromium profile. That is WEAKER than process isolation**: the browser process, the network service,
  the GPU process, the DNS and socket machinery and the debugging endpoint are shared by every session.
- **A shared browser cannot draw into per-session X servers.** It connects to one X display, so every profile's windows live in
  one host-level Xvfb, not in the sessions' own Xvfb (which the sessions still have, for the guest server and the desktop stack).
  Consequence to be shown, not assumed: a session's own screenshot does not contain its browser window, so an OSWorld-style
  observation (an X screenshot of the session) would be blank for the browser, and browser files (downloads, profile data) live
  outside the session's filesystem. Making the observation work would need per-target capture over CDP, which is a different
  observation path. This prototype measures memory, latency and isolation only; it does not claim OSWorld equivalence.
- The shared debugging endpoint is reachable from each session's network namespace through its gateway address (the firewall
  allows a session to reach its own gateway on any port). It is a candidate cross-profile channel and is tested as one.

### Arms (identical in everything else: same sessions, real OSWorld guest server in `module` entry, Xvfb, openbox, D-Bus, AT-SPI, overlay, network namespaces, template profile)

- **A0 (cold, the existing arm)**: each session execs its own Chromium (as C1 in section 9), headful on the session's Xvfb.
- **B (shared browser)**: the sessions run no browser; one shared headful Chromium on a host Xvfb; session k attaches profile `Slot<k>`.

### Measurements, N >= 20 per arm, arms interleaved

| ID | Measurement | Design | PASS threshold (fixed now) |
|---|---|---|---|
| M1 memory | Marginal host memory per additional session: slope of (MemTotal - MemAvailable) against attached sessions at 1, 4, 8, 12, 16, 20; 4 trials per arm; the shared browser's fixed cost (browser started, zero extra profiles) is measured separately and reported, and excluded from the slope but included in the total at N = 20 | same method as section 9(a) | B slope <= 0.80 x A0 slope AND the 95% bootstrap interval of (A0 - B) over trials excludes 0 |
| M2 latency | Browser-ready latency, same interval in both arms: A0 = Chromium spawn to its page title visible; B = attach command (including the profile copy) to the profile's page title visible over CDP; 20 per arm, 2 discarded warm-ups, interleaved | in-session marks for A0, host-side polling for B | B p95 < A0 p95 AND B p50 <= 0.7 x A0 p50 |
| M3 storage isolation | 20 profiles attached at once; profile k writes a unique tag to cookies, localStorage, IndexedDB and a cached HTTP resource of the SAME origin; every profile then reads back | 20 x 4 stores; the server also logs the Cookie header and cache hits | 0 foreign tags observed anywhere and 0 cross-profile cache hits |
| M4 targets and routing | The 20 page targets: distinct target ids, distinct browser-context ids, each opened in the intended profile (verified through `chrome://version` Profile Path); 20 concurrent attaches, 3 repetitions | CDP | 60 of 60 correct |
| M5 shared-channel exposure | From INSIDE a session's network namespace: can it reach the shared debugging endpoint; can it list sibling targets (titles, URLs); can it execute script in a sibling target; what does `SystemInfo.getProcessInfo` reveal about other profiles' processes | direct tests; also enumerate by process tree what is shared by design (network service, GPU, utility processes) and test cross-profile socket reuse (client source ports seen by the server) | **Any cross-profile observation or influence path counts as a leak = the isolation check FAILS** (reported, not patched over) |
| M6 display and observation | A session's own screenshot before and after its profile is attached: is the browser window in it | hash comparison | reported (expected: no) |
| M7 blast radius | Kill one profile's renderer; kill the GPU process; kill the browser main process: which other attached profiles survive | direct tests with 4 profiles | reported |

### Expected outcome, stated in advance so it can lose

- M1: a real saving is expected (the browser, GPU, network and zygote processes are not repeated per session), but modest, because every
  profile window still gets its own renderer process. My estimate of the slope ratio is 0.65 to 0.85, so the 0.80 threshold is close to a
  coin flip; the fixed cost of the shared browser will eat part of it at small N.
- M2: probably FAIL. Handing off to a running browser still starts a transient browser process and copies a profile, which I expect to cost
  about as much as the cold launch (0.35 s) it replaces.
- M3 and M4: expected PASS (Chromium keeps cookie, storage and cache state per profile; target mapping should hold unless concurrent
  handoffs race on the singleton lock).
- M5: expected to FAIL the isolation check: one debugging endpoint serves every target, so a session that can reach it can enumerate and
  drive its siblings. A per-session filtering proxy would be needed and is NOT part of this milestone.
- If the browser process model blocks safe branching (crashes, profile cross-talk, target confusion), the failure is documented in
  hazard-test style as a result.

## 11. PROTOTYPE B results (measured; thresholds are the ones committed in d116f92)

**PROTOTYPE of mechanism B. Not an L1 proof.** Raw data: `docs/data/l1-branch/` (`bench.json`, `isolation.json`). Code:
`envd_linux/l1proto/branch*.py`. One sandbox (4 vCPU, 16 GiB, kernel 6.18), real OSWorld guest server in every session. The readable
version with diagrams is `mechanism-b-shared-browser.md`.

| ID | Threshold (fixed in d116f92) | Measured | Verdict |
|---|---|---|---|
| M1 memory | B slope <= 0.80 x A0 slope AND interval of (A0 - B) excludes 0 | A0 229.8, B 104.6 MiB per additional session (4 trials each; per-trial B 105.3 / 106.5 / 104.3 / 102.3). Ratio 0.455; A0 - B = 125.1 MiB, interval [122.9, 127.3]. Fixed cost of the shared browser 175 MiB; total at 20 sessions 4596 vs 2281 MiB | **PASS** (far better than the 0.65 to 0.85 I expected) |
| M2 latency | B p95 < A0 p95 AND B p50 <= 0.7 x A0 p50 | A0 p50 0.320 / p95 0.380 s; B p50 0.205 / p95 0.233 s (profile copy 0.014 s of it, handoff 0.093 s); B p50 is 0.64 x A0 | **PASS** (I expected a FAIL: the handoff is cheaper than a cold launch) |
| M3 storage isolation | 0 foreign observations, 0 cross-profile cache hits, 20 profiles | cookies, localStorage, IndexedDB: 0 foreign observations in 60 store checks; server saw 20 cache requests for 20 profiles (0 cross-profile hits); 0 requests with a foreign cookie header; each profile's second fetch hit its own cache | **PASS** |
| M4 targets and routing | 20 distinct targets and contexts, 60 of 60 concurrent attaches correct | 20 distinct target ids and 20 distinct browser-context ids; every profile's `chrome://version` path matched; **concurrent attaches: 59 of 60 correct** (one, `C2x19`, failed its verification; cause not diagnosed: a handoff race or a verification artifact, not separated) | **FAIL** (by one) |
| M5 shared-channel exposure | any cross-profile observation or influence path = leak = isolation FAIL | see below | **FAIL** |
| M6 display and observation | reported | a session's own screenshot is byte-identical before and after its profile is attached; 24 windows sit in the one shared host display | reported: **the observation is not in the session** |
| M7 blast radius | reported | see below | reported |

### M5 in detail

- **As built, a session cannot reach the endpoint**: Chromium bound the debugging port to 127.0.0.1 only (the requested
  `--remote-debugging-address=0.0.0.0` was ignored in headful mode), so from inside a session the connection was refused. That is an
  accident of the configuration, not a design property: any session that is to drive ITS OWN profile needs a path to the endpoint.
- **With the simplest such path** (a plain TCP forwarder on the session's gateway address) a session could: list all 21 page targets
  with their titles; read a value held only in a sibling's page memory (`SECRET-IN-P2`); execute script in the sibling (it changed the
  sibling's title, which the host then saw); and read process information for the whole browser through the browser-level endpoint.
  Reading the sibling's cookies through `Storage.getCookies` returned an empty list (not conclusive; the other channels already
  suffice). Per-session filtering (a proxy exposing only that session's targets) is NOT built.
- No connection or socket was shared between profiles (the server saw different client ports for different profiles).
- Shared by design (process inventory, 20 profiles): 1 browser process, 2 zygotes, 1 GPU process, 1 network service, 1 storage service,
  and 22 renderers (one per window plus spares).

### M7 in detail (4 to 20 profiles attached)

- Killing one renderer lost exactly ONE profile in each of 8 kills.
- Killing the GPU process lost none of the remaining profiles (it is restarted).
- Killing the browser main process took the debugging endpoint down: **every attached profile is lost**. The crash domain of a
  shared browser is the whole host's sessions.

### What this does and does not support

- Supports: sharing one browser gives a real, large memory saving (54% less per additional session, 50% less in total at 20 sessions)
  and a faster browser-ready time, with storage kept separate per profile.
- Does not support B as a path: the isolation check fails (a sibling session can read and drive another session's pages through the
  shared debugging endpoint), and the browser is not in the session's own display, so an OSWorld-style screenshot of the session
  would be blank for the browser (measured, M6). A CDP screenshot substitute is not equivalent to the session's real desktop.
- Useful as a bound: about 125 MiB per session is browser-side state that is duplicated when each session has its own browser. That is
  the target for a mechanism that keeps per-session processes (next section).


## 12. PROTOTYPE K pre-registration: page merging across sessions, with and without address-space randomisation (written and committed BEFORE any measurement)

**PROTOTYPE of mechanism K. Not an L1 proof; `drums_level` stays L0.** Written after sections 9, 10 and 11, so the choice of mechanism
below is argued from what those measured.

### Why the evidence points here

1. Section 9 (share bound): of about 231 MiB per session, the Python part that fork can reach is 51 MiB; Chromium is about 90 MiB
   private; the rest is the other exec'd processes, 31.5 MiB of RAM-backed writable layer and kernel structures. So the cost is
   mostly browser and desktop-stack processes that are started fresh in every session.
2. Sections 10 and 11 (mechanism B, measured): putting every session's browser in ONE process cut the per-session memory from 229.8 to
   104.6 MiB, so about 125 MiB per session is duplicated browser-side state. But B failed its isolation check
   (a session that can reach the shared debugging endpoint can read and run script in its neighbours' pages) and it changes what the
   agent observes (the browser window is not in the session's own display). A win over a tuned incumbent needs the same desktop semantics
   and per-session isolation, so B is out as a path.
3. Therefore the candidate must keep one browser process PER session, untouched, and remove the duplication from underneath: let the
   kernel merge pages whose content is identical across those processes (kernel same-page merging, KSM). The sessions' processes,
   namespaces, displays and tasks do not change, so observation semantics and process-level isolation are preserved by construction.
4. The obstacle is address-space randomisation: identical browsers hold pointers at different random addresses, so pages that "should" be
   identical differ. The design variant K2 turns randomisation off inside sessions so that identical startup work yields identical pages.
   That is a security trade inside a benchmark sandbox, stated plainly, not hidden.

Other candidates considered and why they rank lower on this evidence: CRIU restore reads saved pages into private memory, so it shares
nothing by itself; userfaultfd lazy loading needs a shared base image of memory that live processes do not have; a custom browser build
with partitioned renderers is a large build whose benefit is bounded by the same duplicated-state estimate and would change the browser
under test. Any of them can still be tried later.

**Prior art, and what counts.** Page merging is standard in virtual-machine hosts, and VM-based incumbents can use it too; per the
project's framing a mechanism label is not the contribution. This milestone measures the INCREMENT of K over Drums' own existing arm
(OFF), plus its CPU cost and its side channel. It does not measure an incumbent (no KVM here), so no claim against an incumbent follows
from it; that needs the matched run (`matched-comparison-protocol.md`) with the incumbent's reuse and its own merging switched on.

### Arms (identical sessions: real OSWorld guest server in module entry, per-session Chromium on its own Xvfb, overlay, namespaces)

- **OFF**: no merge flag; randomisation as normal (the existing A0 arm).
- **K1**: every session process is marked mergeable (`prctl(PR_SET_MEMORY_MERGE)` in the session's PID 1, inherited by its children);
  randomisation as normal.
- **K2**: K1 plus randomisation OFF for every process exec'd in the session (`personality(ADDR_NO_RANDOMIZE)`).

Host settings, the same in all arms and recorded in the ledger before they are written and restored on close: KSM run = 1,
`pages_to_scan` = 20000, `sleep_millisecs` = 2, `use_zero_pages` = 1 (aggressive on purpose, so convergence waits are short; its CPU
cost is measured and reported, not hidden). Only processes that opt in are scanned, so the OFF arm is not merged.

### Measurements

| ID | Measurement | Design | PASS threshold (fixed now) |
|---|---|---|---|
| M-K1 memory | Marginal host memory per additional session: slope of (MemTotal - MemAvailable) against sessions at 1, 4, 8, 12, 16, 20, 3 trials per arm interleaved, after the merge counters settle (less than 1% change over 5 s, at most 90 s) | same method as sections 9 and 10 | **K2 slope <= 0.80 x OFF slope AND the 95% bootstrap interval of (OFF - K2) excludes 0.** K1 is reported against the same rule |
| M-K2 CPU | CPU time of the merging thread divided by the trial's wall time | `/proc` accounting | <= 0.25 vCPU on average (a saving bought with more CPU than that is not free) |
| M-K3 semantics | In K2: the session's guest contract checks and the real-server checks pass (accessibility tree, real pyautogui); one public OSWorld task still scores 0.0 then 1.0; sibling hygiene (separate PID, mount and network namespaces, own Xvfb) holds; randomisation is really off (mapping base addresses identical across two sessions) | direct checks | all hold |
| M-K4 latency | Session create latency | 60 creations per arm from the memory trials | create p95 in K2 <= 1.10 x OFF |
| M-K5 side channel | Can a process in one session detect, by write latency, that its page content also exists in another session | two probes in two sessions, shared-content versus unique-content pages | reported, no threshold; a clear gap means the known deduplication timing channel exists here |

### Expected outcome, stated in advance so it can lose

- K1: expected to FAIL M-K1 (a saving under about 10%), because pages that hold pointers differ under randomisation.
- K2: genuinely uncertain. My estimate of the K2 to OFF slope ratio is 0.70 to 0.95, so the 0.80 threshold is not safe; I would put the chance of
  passing near 40%. Browser startup is not fully deterministic (threads, timing), which limits identical pages even with randomisation off.
- M-K2: the aggressive scan settings may cost more than 0.25 vCPU while sessions are being added; if so that fails M-K2 even if M-K1 passes.
- M-K5: expected to show a clear gap (merged pages take a copy-on-write fault on write).
- If K2 passes M-K1 only narrowly, or only at a CPU cost that fails M-K2, the report says exactly that.


## 13. Mechanism K results (MEASURED; raw data in `docs/data/l1-ksm/`)

**Status: MEASURED on one 4-vCPU host, 3 trials per arm (2 for the re-run under limits). PROTOTYPE. Scored against the thresholds committed in `8062ff0`.**
Headline in plain terms: page merging saves a lot of memory only when address randomisation is switched OFF for every process in
the session, which is a security regression and not a shippable default. That arm also fails its CPU threshold. With randomisation ON the
saving is under 8%. Merging also introduces a strong, measured cross-session timing channel.

| Measurement | Arm | Result | Threshold | Verdict |
|---|---|---|---|---|
| M-K1 memory per added session | OFF | 229.7 MiB/session | | |
| | K1 (merging, randomisation ON) | 212.7 MiB (0.926 x OFF); OFF-K1 95% interval [14.2, 19.8] MiB | K1 reported against the K2 rule (<= 0.80 x OFF) | **K1 FAIL** (a real but small saving, 7%) |
| | K2 (merging, randomisation OFF) | 135.4 MiB (0.589 x OFF); OFF-K2 95% interval [92.2, 96.9] MiB | <= 0.80 x OFF and interval excludes 0 | **K2 PASS** |
| M-K2 merging-thread CPU | K1 / K2 | 0.895 / 0.706 vCPU on average over the trial wall time | <= 0.25 vCPU | **FAIL for both** (the pre-registered aggressive scan settings cost more than a quarter of a core; I did not tune them down and re-measure, so this does not say what a slower scan would cost in saving) |
| M-K3 semantics (K2 only) | K2 | guest-contract and real-server checks pass, real accessibility tree, public OSWorld task scored 0.0 then 1.0, namespaces distinct, one Xvfb per session, personality 0x40000 set in both sessions, mapping bases identical in the two sessions | all hold | **PASS** (`ksm_semantics.json`) |
| M-K4 create latency | OFF / K1 / K2 | p95 3.20 / 2.68 / 2.28 s (60 creations each) | K2 p95 <= 1.10 x OFF | **PASS**, but K2 being FASTER than OFF is not a benefit I claim: the arms were interleaved yet creation latency here also depends on host state, and I did not isolate why |
| M-K5 side channel | probes in two sessions | see below | reported | **A strong channel EXISTS** |

**Overall verdict by the pre-registered rules: K1 fails CPU, K2 fails CPU and is a security regression; no K arm is shippable.** K1 fails M-K1 and M-K2 (and carries the timing channel). K2 passes M-K1, M-K3 and M-K4 but fails
M-K2, and in addition is not shippable on security grounds (next subsection). The measured saving at K2 is real; the cost to obtain it is not
acceptable.

### K2 is benchmark-sandbox only: security regression, not shippable

K2 turns address-space layout randomisation (ASLR) off for every process started in the session. ASLR is what makes the memory addresses
of a program's code and data unpredictable to an attacker; with it off, a memory-corruption bug in ANY program in the session (the
browser, the office suite, the agent's own tool calls, the guest server) becomes far easier to exploit because every address is the same on every
run. The K2 saving comes from exactly this: pages merge only because two sessions' processes are laid out identically. There is no
configuration of K2 that keeps the saving and the randomisation. If K2 were ever used, it would be in a throwaway benchmark sandbox
that runs only trusted tasks, and its numbers would have to be labelled that way. It must not be a default, a recommendation or a headline.

### Page merging does NOT preserve isolation by construction

Process isolation (separate PID, mount and network namespaces, private root, own display) is unchanged and was audited. What K introduces is a
new CROSS-SESSION channel that the other arms do not have: when two sessions hold the same page content the kernel merges them, and a write
to a merged page is measurably slower (copy-on-write fault). A process in one session can therefore learn whether a page with a given
content exists in another session.

**What was inferred (M-K5, `ksm_sidechannel.json`).** Two cooperating probe processes, one in each of two real sessions (default session
arguments, host merging knobs at the K settings). Each maps two private anonymous regions of 8192 pages. In `shared` every page has a
deterministic content that BOTH probes know and both hold; in `unique` every page is random. After a 40 s merge wait each probe times the first write to
every page. The classifier decides, for one page, **"is this page's content also held by a process in another session?"** (page CONTENT
presence, one yes/no per page; it does not measure when the other session did anything). The threshold is fitted on the even-indexed pages
and scored on the odd-indexed pages it never saw.

| | probe 1 | probe 2 |
|---|---|---|
| pages merged before timing (`ksm_merging_pages`) | 8192 | 8192 |
| median first-write latency, merged vs unmerged | 3854 ns vs 345 ns (11.2x) | 2973 ns vs 342 ns (8.7x) |
| held-out true-positive / true-negative rate | 1.000 / 0.999 | 0.999 / 0.999 |
| held-out balanced accuracy | 0.9995 | 0.9989 |
| binary-channel capacity per page | 0.994 bit | 0.988 bit |
| rate: 8192 candidate pages per 40.0 s round | about 203 bits/s | about 202 bits/s |

Reading it: a sibling that can GUESS a page's exact content (4 KiB of known bytes: a known file's page, a known library page, a known
secret with a guessable surrounding structure) can confirm whether another session holds it, at about 200 yes/no answers per second in this
setup, with about 99.9% accuracy. That is an upper bound for this probe design: the two probes cooperate and the content is known to both, so it is a
covert-channel and content-existence bound, not an attack on real task data. What it does NOT show: that a sibling can READ another
session's memory, or find content it cannot guess. Whether the channel is usable against real task secrets was not tested. It is a cost of
the mechanism and counts against it, independent of the memory saving. The known mitigation (disable merging for sensitive regions, or only merge
within one tenant) removes exactly the sharing K sells.

**Correction recorded.** My first M-K5 run reported a 1.1x latency gap and 56% accuracy. It was INVALID: Python's `mmap(-1, ...)` default is a shared
anonymous mapping, which KSM does not merge (`ksm_merging_pages_before` was 0, the reading I had not checked). The probe now uses private
mappings and the record above is the re-run. The invalid run is not in the data directory.

### Scope of the isolation audit (declared boundary)

The M-K3 hygiene audit ran against **K2 only** (K1 was not audited; its namespace set is the same code path but that is an inference, not a
check). It covers process, mount, network and display isolation. **The timing channel is OUTSIDE the scope of that audit.** It was
measured separately by M-K5 and is reported here as a cost; no verdict rule treats "isolation audit passed" as meaning "no
side channel".

### Under the incumbent resource shape

Re-run with every session in a cgroup limited to 2 vCPU and 4 GiB (the incumbent's shipped shape, `docs/data/l1-ksm/ksm_bench_limits.json`, 2 trials,
`--limits`): OFF 229.7, K1 214.7, K2 136.6 MiB per session; OFF-K2 interval [91.3, 94.9]; ksmd fraction 0.90 / 0.71. The limits changed nothing that
matters for the memory result (a session uses about 230 MiB, far below 4 GiB, and the merging thread is not in the session cgroup). This
is a READINESS check, not a separate result.

### Caveats

- One host, one kernel (6.18), 4 vCPU, 3 trials; the intervals are over trials, not over hosts.
- Incumbents' VMs can use KSM too (each QEMU guest is a mergeable process), so nothing here is a claim against an incumbent.
- The 0.25 vCPU threshold was fixed before measuring and is failed by a wide margin; scan settings were not re-tuned.
- Merging pays only while sessions are being added and settled; the saving is a steady-state number after counters settle.


## 14. PROTOTYPE pre-registration: mechanism C (CRIU checkpoint and restore of a warm session)

**Status: PRE-REGISTERED, thresholds fixed before any measurement. PROTOTYPE. Nothing here is built yet. `drums_level` stays L0.**
Committed before the probe is run; results will be section 15.

### Why this route, and what it is not

Memory sharing failed three ways: F saved 6.2% (too small), B was disqualified on isolation, observation semantics and crash domain, K saved 7%
(fails CPU, carries a timing channel) or 41% only with address randomisation off (fails CPU, security regression). On a fixed-cost host the other half of
results per dollar is rollout throughput, which is time-to-ready per rollout. The idea: checkpoint a WARM session once (browser launched, guest server up,
a known task page loaded) and restore copies of it, instead of starting every session cold.

**Design intent, not a claim.** A restored session keeps its own processes, so the intent is that the isolation boundary and the screenshot semantics are the
same as a cold session. That is only true if a measured restore-correctness audit shows no shared fds, sockets, X state or file locks between restored siblings. Until
that audit passes it is an intent. A fast restore is also prior-art territory (VM snapshot resume); the only claim that could ever count is a
measured increment over the tuned incumbent's snapshot-revert latency in the matched runner, which this probe cannot measure (no KVM here).

### Considered and not chosen now

- **userfaultfd lazy restore** (page in the checkpoint image on demand): an optimisation on top of CRIU that only matters if the base restore works. Revisit only if section 15 shows
  restore time dominated by reading memory.
- **A custom shared-browser build**: weeks of work and it reintroduces shared-state risk (the lesson of B). Deprioritised.

### Arms

| Arm | What starts | Time-to-agent-ready ends when |
|---|---|---|
| COLD | `Supervisor.create` (namespaces, overlay root, Xvfb, real guest server), then launch Chromium and load the known task page | the guest server returns a screenshot that shows the browser with the page loaded |
| RESTORE (full) | `criu restore` of a checkpoint taken from a COLD session in that same ready state | the same condition |
| RESTORE-SERVER-ONLY (fallback) | checkpoint WITHOUT the browser (desktop and guest server warm), browser cold-started after restore | the same condition |

**RESTORE-SERVER-ONLY is PARTIAL by definition**: if Chromium will not checkpoint and only this arm works, it is reported as a partial result with the limited
scope F's lesson taught (a fork or restore of the server alone leaves the dominant cost, the browser, cold). It is measured separately and is never described as the mechanism.

### Probe ladder (each rung is recorded as pass or fail with the CRIU error; a failed rung is a result)

1. CRIU runs on this kernel (`criu check`), binary and libraries held privately (not installed system-wide).
2. Dump and restore a trivial process tree in its own PID namespace.
3. Dump and restore a session with only the guest server (the real OSWorld server) and Xvfb.
4. Add Chromium. The known hard risks, probed rather than assumed: Chromium's sandbox and seccomp filters under CRIU, the X connection state of restored X clients, the guest server's
   TCP connections (listening and established), shared-memory and GPU state (`/dev/shm`, no GPU here).
5. Restore into a fresh network namespace (same session address) and mount namespace (same overlay root), since a session is more than its processes.

### Thresholds (fixed now), in order

**CORRECTNESS (all must hold, or the arm fails regardless of speed):**
- C1: the same public OSWorld task (`e0df059f`, the one used in `test_osworld_real_server`) scores 0.0 then 1.0 through the real evaluator from a restored session.
- C2: the restored session's screenshot shows the browser: a Chromium window is present in the X window tree, its title is the loaded page's, and the screenshot differs from a blank-desktop screenshot in more than 10% of pixels.
- C3 (explicit audit, two sessions restored from ONE checkpoint, running at the same time): no shared host PIDs; no shared open pipe, socket or eventfd inodes; no shared open
  regular files other than read-only files in the shared template lower layer (listed, each judged); no shared entries in `/proc/locks`; distinct Xvfb displays and X socket
  inodes; distinct network namespaces and addresses; the guest-server TCP state of one is not visible to the other. Any shared writable object fails C3.

**LATENCY:** 20 or more starts per arm, interleaved. Time-to-agent-ready of RESTORE (full) p50 <= 0.50 x COLD p50 (target at least 2x), and p95 not worse than COLD p95.

**MEMORY:** host memory per additional session, same slope method as sections 9 to 13, restored <= 1.05 x cold (with the browser in both).

**Host-level effects** (anything CRIU writes outside the session: sysctls such as `kernel.ns_last_pid` in the host namespace, mounts, image directories) are recorded in
the sweep ledger before they are made and restored on close, like the KSM knobs. The CRIU binary and its libraries stay in a private directory.

### Expected outcome, stated in advance so it can lose

- Chromium checkpoints and restores cleanly in this sandbox: about 30%. The likely failures are its seccomp/sandbox setup, `--no-sandbox` still leaves many helper processes with
  unix-socket pairs and shared memory that CRIU handles unevenly, and X client connections.
- If it does restore: P(C1, C2 and C3 all pass) about 50% (the audit is the likely casualty). P(latency >= 2x | correct) about 25%: a cold session start here is about 2.4 s plus
  browser start, and restoring a multi-process browser's memory image is not free.
- RESTORE-SERVER-ONLY: expected latency ratio about 1.0 (a cold guest server is already about 2 s, and restore overhead is comparable), so it is expected to LOSE on latency even when correct.
- Most likely overall result: the probe stops at a failed rung in the ladder, which is documented as the result.


## 15. Mechanism C results (MEASURED; raw data in `docs/data/l1-criu/`)

**Status: MEASURED on one 4-vCPU host (kernel 6.18), scored against the thresholds committed in `8f5ed06`. PROTOTYPE. `drums_level` stays L0.**
In plain terms: a full running desktop session, browser included, CAN be checkpointed with CRIU and restored as isolated siblings, which I did not
expect; it starts about 2.1 times faster than a cold L0 session and costs no more memory. The margin on the speed target is thin and it disappears
if the page cache is cold. The absolute saving is about one second per start, so its effect on valid rollouts per hour depends entirely on rollout length. No
increment over any incumbent was measured or can be measured here (no KVM).

### Probe ladder (the registered rungs; each is a pass or a recorded failure)

| Rung | Result |
|---|---|
| 1. `criu check` on this kernel | PASS with CRIU 4.2. **CRIU 4.1.1 FAILED** on this kernel: it cannot read the socket options of a TCP socket (`getsockopt` of `SO_PASSCRED` and `SO_PASSSEC` returns EOPNOTSUPP on kernel 6.18), so any session with a listening TCP socket would not dump. 4.2 handles it. Both were run from private extracts of Ubuntu archive packages, not installed system-wide |
| 2. trivial tree in its own PID namespace | NOT run as a separate rung: the first attempt failed on host mounts this sandbox has (`/mnt/skills/*`), which showed the general constraint below, and I moved to the real session |
| 3. session with only the real guest server and Xvfb (+ AT-SPI, openbox, D-Bus) | **PASS**: dump 0.21 s, image 74.8 MiB, restore in 0.66 s, the real OSWorld server answers at the new address |
| 4. add Chromium | **PASS**: dump 0.81 s, image 253 MiB, restore in 0.76 s, the browser's page is alive with its title. Chromium was started with `--no-sandbox` as in the K bench; a sandboxed Chromium was NOT tested |
| 5. restore into a fresh network and mount namespace | **PASS** after four fixes described below |

What had to change to make a session dumpable (each was a failure first, so each is a finding):
1. **Every fd and mapped file must belong to a mount that exists in the dumped mount namespace.** The session init exec'd its interpreter before the pivot into the overlay root, so its mappings and stdio pointed into the detached old root. The init now re-execs itself after the pivot, moves stdio into the new root, closes inherited fds and becomes its own session leader (`criu_friendly`).
2. **`unshare --kill-child` arms SIGKILL-on-parent-exit**, which fires at once when the restoring CRIU exits. Friendly mode clears it. Cost: if the Supervisor dies, friendly sessions are no longer killed by the kernel; the ownership sweep is the only cleanup.
3. **The overlay's upper layer must be where the host can keep it.** It now sits on a host-visible tmpfs; the checkpoint keeps that tmpfs as a read-only layer and each sibling mounts `[checkpoint layer, template, host root]` under its own empty tmpfs upper. Overlay refuses layers that overlap, so the upper cannot sit on the host root's own filesystem.
4. **CRIU needs help with:** inotify handles on overlay files (`--force-irmap` and scan paths), deleted shared-memory files Chromium maps (`--ghost-limit`), file locks Chromium holds (`--file-locks`), the veth (`--external veth[...]`, then re-addressed per sibling), the X11 socket directory and the six `/dev` bind mounts (external mounts). CRIU also needed `ip`, from a private copy of iproute2.

### Thresholds (fixed in section 14), in order

**CORRECTNESS**

| Check | Result | Verdict |
|---|---|---|
| C1 public task `e0df059f` through the real evaluator from a restored session | score 0.0 before, 1.0 after the hand-written step; the sessions were restored (checked) | **PASS** (one task, hand-written step) |
| C2 screenshot shows the browser | 99.7% of pixels differ from a blank desktop (threshold 10%); the page title is the loaded page's | **PASS** |
| C3 audit, two siblings restored from ONE checkpoint, running together | 19 processes each; distinct PID, mount, network, IPC and UTS namespaces; no shared pipe, socket or eventfd inode; no shared lock entry; no shared writable regular file; distinct Xvfb socket inodes and addresses; a file written in one sibling is invisible to the other. **Two shared character devices: `/dev/null` and `/dev/urandom`** (the host's device nodes, bind-mounted into every session) | **FAIL by the literal wording** ("any shared writable object fails"), **PASS against a control**: the same audit on two COLD sessions finds the same two devices, so restore introduced no sharing. I did not relax the registered rule; the wording and the control are both reported |

So correctness is NOT unconditionally passed: whether host device nodes shared by every L0 session are acceptable is a design decision about L0, not something restore changed.

**LATENCY** (time to agent-ready: a screenshot that differs from blank by more than 10% pixels AND the browser page title is the loaded page's; 20 starts per arm, interleaved, page cache warm)

| Arm | p50 | p95 | vs COLD (p50) |
|---|---|---|---|
| COLD (create, launch browser, load page) | 2.05 s | 2.19 s | |
| RESTORE (full) | 0.98 s | 1.22 s | **2.09x** (bootstrap 95% interval 1.99 to 2.15) |
| RESTORE-SERVER-ONLY (**PARTIAL**: restore, then cold-start the browser) | 1.22 s | 1.28 s | 1.68x |

- RESTORE (full): p50 <= 0.50 x COLD and p95 not worse: **PASS by the registered point rule**, with a margin inside the noise (the interval's lower bound is 1.99).
- RESTORE-SERVER-ONLY: below the 2x target, **FAIL**, as predicted. It is a partial arm (the browser stays cold), not the mechanism, and it works only because the full arm also works.
- **NOT pre-registered robustness check, labelled as such:** dropping the page cache before every start (10 starts per arm): COLD 2.59 s, RESTORE 1.38 s, ratio **1.87x** (interval 1.81 to 1.96), server-only 1.55x. With the 253 MiB images read from disk the 2x target is **not met**. This sandbox's disk is a virtual device and a real host's would differ; I did not measure one. The drop-cache write is a host-level action that has no state to restore.
- The restore time is not all CRIU: of the 0.98 s p50, the CRIU restore itself is about 0.32 s and the restore call as a whole (overlay mount, tmpfs, re-addressing the veth, waiting for the server) about 0.76 s; the rest is the readiness check (screenshot, browser title). The one-time checkpoint costs 0.8 to 1.9 s and 253 MiB per checkpoint.

**MEMORY** (host memory per additional session, 3 trials, 6 points each, with the browser in both): COLD 229.4 MiB, RESTORE 221.9 MiB, ratio **0.967** (cold minus restore 6.1 to 8.9 MiB, 95% interval over trials) against the threshold <= 1.05: **PASS**. A 3% saving, real but small.

**Host-level effects.** A before and after snapshot of 793 non-volatile `/proc/sys` entries showed no difference. What CRIU needed on the host: the private binary and libraries, a private `ip`, the host-side veth (created by CRIU, ledgered by name before the restore), the overlay root mounts and tmpfs mounts (ledgered), and the X11 link (ledgered). I also ran `apt-get update` (the package index changed) and `apt-get download` into a scratch directory to obtain the packages; nothing was installed system-wide.

### What the registered expectations got wrong, stated plainly

I put the chance that Chromium checkpoints cleanly at about 30% and said the most likely result was a probe that stops at a failed rung. It did not stop: every rung passed after the four fixes above. I put the chance of a 2x speedup given correctness at about 25%; it came out at 2.09x, narrowly. The server-only arm lost on latency as predicted.

### What is NOT established

- **No increment over an incumbent.** The comparison is restore against OUR cold L0 start (2 s, already fast). A tuned incumbent's snapshot-revert latency was not measured (no KVM). VM snapshot resume is prior art.
- **Throughput effect is small unless rollouts are short.** Saving about 1.07 s per start changes valid rollouts per hour by about (1.07 s / rollout length): about 1.8% for a 60 s rollout and 0.2% for a 600 s one. This is arithmetic from the measured saving, not a measured throughput result; the matched runner is where it would be measured.
- **Restored siblings are clones.** They start with identical in-process state: random-number generator state, in-memory tokens, browser profile contents and the same hostname. Whether that contaminates rollouts was not tested. Clock behaviour of restored processes was not tested.
- **Portability.** CRIU images depend on the kernel and CPU; restore on another host was not tested. A checkpoint also records the template's mount path and the host's tag baked into the session command line, so a checkpoint cannot yet be reused across supervisor runs.
- **Tested scope:** one host, one kernel, no GPU, `--no-sandbox` Chromium, one task, one checkpoint image per arm.


## 16. Mechanism C follow-up: registered revision 2 (amended C3 rule), cleanup test and clone hygiene

**Status: PRE-REGISTERED before any of these checks is run. PROTOTYPE. Nothing here is a result yet; results will be section 17.**
This follows reviewer directives on `ca07330`. Section 15's record stands unchanged: **C3 under the original wording is a recorded FAIL** (two host
device nodes shared). Nothing below rewrites it.

### 16.1 C3, revision 2 (a NEW rule, registered now, applied only to retests from now on)

The original rule said any shared writable object fails. Revision 2 changes exactly one thing: a shared writable object is tolerated IF all three hold:
(a) it is one of the six host character-device nodes that every L0 session bind-mounts from the host (`/dev/null`, `/dev/zero`, `/dev/full`, `/dev/random`, `/dev/urandom`, `/dev/tty`);
(b) a control audit of two COLD sessions run in the same pass shows the same object shared (so L0 already shares it and restore added nothing);
(c) nothing else is shared: no pipe, socket or eventfd inode, no regular file, no lock entry, no write in one sibling visible in the other.
Justification, stated before the retest: these are stateless kernel devices (`/dev/null` discards, `/dev/urandom` is the kernel's generator with no per-reader state that
another reader can observe), the sharing is a property of the L0 design and not of restore, and the control is what proves restore added none. The amended rule is a judgement about
L0 that the reviewer may reject; if rejected, C3 stays FAIL. Retest: one pass of the audit on two restored siblings and two cold sessions; outcome recorded under both rules.

### 16.2 Orphan cleanup after a supervisor crash (the regression friendly mode introduced)

Friendly sessions cleared `unshare --kill-child`, so the kernel no longer kills them when the Supervisor dies. Test: a Supervisor runs in its own process, creates a cold friendly
session, checkpoints it, restores two siblings; it is killed with SIGKILL; the sessions must still be alive (this confirms the regression); the ownership sweep is run; then:
- PASS needs ALL of: no session process of that run remains; every mount recorded for the run (overlay roots, tmpfs stages, checkpoint layer, template tmpfs) is gone; the `dvh*` links, the run's nftables table and the X11 links are gone; the ledger entry is removed;
- and a decoy that is NOT ours (a process whose command line carries a different run tag, and a tmpfs mount at an unrecorded path) is untouched.
Expected outcome, stated in advance so it can lose: about 75% that it passes first time; the likely failure is a CRIU-created object the ledger does not know (the restored veth if its alias was not yet set when the supervisor died) or a restored process the tag match misses.
No cleanup-readiness claim is made until this is demonstrated.

### 16.3 Clone hygiene: per-sibling reseed that keeps task semantics

Restored siblings are clones: identical in-process random-number state, in-memory tokens, browser state and hostname. The reseed is applied automatically to every restored sibling and consists of:
- hostname set to a per-sibling value;
- the long-lived Python guest-server process reseeds `random` (and `numpy.random` if loaded) from fresh `os.urandom`, through a signal handler installed before the checkpoint;
- the browser: cookies and storage of the default context cleared, and a NEW browser context (a new renderer process, so a new V8 isolate and fresh randomness) opened on the same page, with the old page closed.
It does NOT touch the wall clock, `/dev/urandom` semantics or the task's files, so time- and entropy-dependent tasks see real time and real entropy (fresher, not frozen).
Known not fixable without restarting the process: Python's string-hash seed and the address-space layout are identical across clones; recorded as a limit, not fixed.

**Hygiene test (F-style, direct observation, thresholds fixed now):**
1. Without reseed, the clone problem must be SHOWN: two siblings from one checkpoint report identical hostname, identical next `random.random()`/numpy draw from the server process, identical `Math.random()` sequence in the pre-checkpoint page, and the same pre-checkpoint cookie. (If they do not, the problem was not real and that is reported.)
2. With reseed: hostnames differ; the server process's next draws differ in Python (and numpy if loaded); the new-context page's `Math.random()` and `crypto.randomUUID()` differ; the pre-checkpoint cookie is absent in both; the browser still shows the page (screenshot changed fraction > 10% vs blank and page title present).
3. SEMANTICS: public task `e0df059f` scores 0.0 then 1.0 through the real evaluator from a reseeded restored session, and the wall clock inside the session equals the host's to within 2 s.
4. LATENCY with reseed ON, 20 starts per arm interleaved (the section 15 figure of 2.09x was WITHOUT any reseed and is not the number for a usable restore): target restore p50 <= 0.50 x cold p50.
Expected outcome in advance: 1 holds (about 90%); 2 holds (about 70%, the browser step is the risk); 3 holds (about 80%). **4 is expected to FAIL**: the reseed adds a browser-context round trip of an estimated 0.2 to 0.4 s to a 0.98 s restore, so I expect a ratio near 1.5 to 1.7x. If it fails, that is the result.


### 16.4 Corrections to the section 16 registration (made after `080a893`, before any retest or hygiene run)

The text above is left as committed; these corrections take precedence over it. **Nothing was run under the broader rule:** the C3 audit code in `ca07330` implements only the
literal wording and the cold control, the amended-rule code does not exist yet, and no C3 retest or hygiene run had been executed when this correction was made (the orphan-cleanup test, which does not
involve the C3 rule, was being built).

**Revision 2a of C3 (supersedes the device list in 16.1).** The exception is narrowed to the two objects actually observed in the section 15 audit and in its cold-session control: **`/dev/null` and `/dev/urandom`
and nothing else**. Condition (a) of 16.1 now reads "it is `/dev/null` or `/dev/urandom`"; conditions (b) and (c) are unchanged. Not claimed: that the six bind-mounted nodes are harmless. `/dev/tty` in particular is not a stateless discard or generator device and can be a
terminal-interference route; `/dev/zero`, `/dev/full` and `/dev/random` were not observed shared and are not covered. If any other device is observed shared in a later audit it is justified and registered separately before that retest. The original C3 FAIL in section 15 stands.

**Browser hygiene scope (supersedes the browser bullet of 16.3).** Clearing cookies and storage and opening a new browser context is a property of a DECLARED CLEAN CHECKPOINT only: one taken from a session whose browser holds no task setup and no login (the probe's data-URL page). A checkpoint is
declared clean when it is created (`clean=True`); the reseed applies the browser step only to a clean checkpoint. For a later real task checkpoint (declared not clean) the browser is left untouched: its cookies, storage, logged-in sessions and task setup are PRESERVED, because erasing them would change the task. The cost of that choice is stated, not hidden: siblings of a non-clean checkpoint share identical logged-in state by design and the page renderer's random-number state (V8 `Math.random`) is a clone; that clone state is a known residual for such checkpoints.
What is reset for every restored sibling regardless: the hostname, and the guest-server process's Python and numpy random-number state (neither is task state). What is reset only for clean checkpoints: default-context cookies and storage, and the renderer. The single public task `e0df059f` validates only that task from a clean checkpoint; it does not validate general benchmark semantics, logged-in tasks, or non-clean checkpoints.


## 17. Mechanism C follow-up results (MEASURED; raw data in `docs/data/l1-criu/`)

**Status: MEASURED on one host, against the thresholds registered in section 16 and its corrections in 16.4 (`080a893`, `fc1d88d`). PROTOTYPE. `drums_level` stays L0.**
In plain terms: the supervisor-crash cleanup works, restored siblings can be made to diverge without breaking the public task, and the amended audit rule passes; but with the hygiene step in place the restore is
1.76 times faster than a cold start, not 2.09 (that number was for a restore with no hygiene, which is not a usable restore), so the latency target is missed, and a restored session now costs 4% MORE memory than a cold one.

### 17.1 C3 under the amended rule (the original FAIL stays in the record)

| Rule | Result |
|---|---|
| Original wording (section 14), `ca07330` run and this run | **FAIL**: two shared writable objects, `/dev/null` and `/dev/urandom` |
| Revision 2a (section 16.4: exactly those two, and the cold control shares them too, and nothing else) | **PASS**: the shared objects are exactly those two; the cold-pair control shares the same two; restore introduced no shared device, writable file or lock; no pipe or socket inode shared; a write in one sibling is invisible to the other |
Whether sharing those two host device nodes is acceptable is still a judgement about L0 that the reviewer may reject, in which case C3 stays FAIL. Nothing was run under the withdrawn six-device wording.

### 17.2 Supervisor crash: orphan cleanup (the regression friendly mode introduced)

`tests/test_criu_orphan_cleanup.py`, data `orphan_cleanup.json`. A Supervisor with one cold friendly session and two restored siblings (and a checkpoint layer) was killed with SIGKILL.
- **The regression is real:** after the kill, all of the run's session processes were still alive (4 processes carrying the run's tag), 2 veth links and 7 mounts under the state directory remained.
- **The ownership sweep reaped it all:** it killed the 4 tagged processes, unmounted all 7 recorded mounts (overlay roots, tmpfs stages, checkpoint layer, template tmpfs), deleted the run's nftables table and the X11 links; afterwards 0 tagged processes, 0 mounts, 0 `dvh*` links, no table, no ledger file.
- **It spared what is not ours:** a decoy process with another run's tag and a tmpfs at an unrecorded path were untouched.
- Passed on the first complete run (my expectation was about 75%). A second, unplanned demonstration: when I killed a probe run with SIGKILL mid-measurement, its leftover session was also reaped by the sweep.
- Two things the test taught me: a killed-but-unreaped supervisor still looks alive to the sweep (the test waits for it), and a restored session carries the checkpointing run's tag in its command line, so cleanup works only for the run that made the checkpoint; reuse of a checkpoint across runs would need the tag to be rewritten or recorded, which is not built.
- A leak found while measuring, now fixed and covered by a test: a restore that failed after the session was registered (the reseed step) left its processes and veth behind, because `destroy()` needed a `session.json` that is written last.
**Cleanup is demonstrated for these cases (supervisor SIGKILL with restored and cold friendly sessions); it is not demonstrated for a crash during the restore call itself, or for sessions of a different run than the checkpoint's.**

**Restore-failure leak, preserved as a result (not folded into a green report).** What happened: a probe run's server-only arm raised in the reseed step AFTER the restored session was registered; `destroy()` read `session.json`, which is written last, found nothing, and skipped the kill; the processes and `dvh*` links outlived the supervisor and then made the NEXT run's restores fail with "Can't restore link: -17 (File exists)" (that second run's data was overwritten by its rerun and is not kept; the first failing run is kept as `probe_reseeded_run1_serveronly_leak.json`, whose server-only arm shows the reseed error). I removed the two leaked inits by pid. Fixes: `destroy()` kills the registered pid when `session.json` is absent, and everything in `_restore` after registration is under a teardown. Regression tests (`tests/test_criu_restore.py`): an induced reseed failure after registration, and an induced failure at the very last step (writing `session.json`); both assert no session process, no `dvh*` link and no mount of the failed slot remain. Both passed on 3 repeats.
**A real failure versus a harness issue:** the leak was a real defect. Separately, three of these tests failed transiently for a harness reason, not a product one: they sampled `pgrep -f envd_linux.session_init`, which also matched the shell running them whenever that shell's own command line mentioned the module; they now match on argv structure and poll for the namespace to be reaped.

### 17.3 Clone hygiene (`tests/test_criu_clone_hygiene.py`; data `clone_hygiene.json`, `clone_task_semantics.json`)

**1. The clone problem, shown (reseed off, two siblings from one checkpoint):** same hostname (`drums-s90`); the guest-server process's next `random.random()` is identical (0.646335 in both); the page's next three `Math.random()` values are identical (0.49652, 0.499815, 0.918771); the pre-checkpoint cookie is present in both; Python's string-hash seed is identical. **Not a clone, contrary to what I registered:** the numpy global generator differed between the siblings (it is seeded lazily on first use), and `crypto.getRandomValues` differed (kernel-backed). So the cloned parts are Python's `random`, V8's `Math.random`, the cookie jar and the hostname.

**2. With the reseed (clean checkpoint):** hostnames differ (`drums-s1-dfd639`, `drums-s2-f39478`); the server's Python draws differ (re-run three times, all different); numpy still differs; the new-context page's `Math.random()` differs between siblings; the pre-checkpoint cookie is gone in both; the page is still there (one page target, title present, screenshot 99.7% different from blank).

**3. Scope for a checkpoint NOT declared clean (a stand-in for a real task checkpoint):** the cookie `task-login-token` is PRESERVED in both siblings and the page is untouched; hostname and the server's Python state are still reseeded; the page's `Math.random()` sequence is identical across those siblings, the stated residual. What is preserved and what is reset, and why, is in 16.4: the browser is reset only for a declared clean checkpoint because erasing cookies or storage from a real task checkpoint would change the task.

**4. Semantics:** the public task `e0df059f` scores **0.0 then 1.0** through the real evaluator from a reseeded restored session (sessions confirmed restored and reseeded), and the session's wall clock differs from the host's by 0.19 s. This validates that task from a clean checkpoint with a hand-written step. It does not validate logged-in tasks, tasks that depend on browser state, non-clean checkpoints, or general benchmark semantics.

Known residual, not fixable without restarting processes: Python's string-hash seed and the whole address-space layout are identical across clones (a security-relevant property of restored siblings, as with any fork-style cloning).

### 17.4 Latency and memory WITH the reseed (20 starts per arm interleaved, 3 memory trials; the registered pass criteria of section 14 and 16.3)

| Arm | p50 | p95 | vs COLD (p50) |
|---|---|---|---|
| COLD | 2.10 s | 2.24 s | |
| RESTORE (full, reseeded) | 1.19 s | 1.29 s | **1.76x** (bootstrap 95% interval 1.73 to 1.83) |
| RESTORE-SERVER-ONLY (PARTIAL; hostname and server reseed only, no browser to reset) | 1.25 s | 1.37 s | 1.68x |

- **Latency target (>= 2x): FAIL**, as I predicted in advance (I expected 1.5 to 1.7x; it is slightly better, still below). The reseed costs about 0.2 s per start (the new browser context round trip dominates). The 2.09x of section 15 was a restore with no hygiene.
- **Memory:** COLD 229.6 MiB per added session, RESTORE 238.3 MiB (ratio **1.038**; restored sessions cost 7.4 to 9.9 MiB MORE, the extra renderer of the new browser context). Against the registered numeric threshold (<= 1.05x) this **passes**; against the plain statement "no more than cold" it does not. I report both.
- The earlier no-hygiene numbers (section 15) are unchanged and remain the record for a restore that skips the reseed; they are not the figures for a usable restore.
- A host-level check: 793 `/proc/sys` entries before and after were identical.

### What is still NOT established

No increment over an incumbent (no KVM here). Cross-run checkpoint reuse and crash-during-restore cleanup are untested. Task semantics are validated for one public task from a clean checkpoint only. One host, one kernel, no GPU, `--no-sandbox` Chromium.


## 18. Assessment and registration: branching an expensive real task setup state (not an empty start)

**Status: ASSESSMENT (static survey, MEASURED from the public files, raw data `docs/data/l1-criu/osworld_setup_survey.json`) plus a PRE-REGISTRATION of one test. Nothing in the test has been run. No benchmark claim, no isolation claim and no incumbent claim is made here.**

### 18.1 Why branch a setup state instead of an empty start

Section 17 showed that shaving about a second off an empty start does not move valid rollouts per hour (and with hygiene it misses its target). The recurring cost in a training run is per rollout of the same task: the guest is reset and the task's `config` is replayed
(`desktop_env.py` `reset`: revert the snapshot if the environment was used, then `setup_controller.setup(config)`). If a group of G rollouts shares one task, the setup could be done once, the post-setup state checkpointed, and G siblings restored from it.

### 18.2 What the public task files say (static survey of the 369 public tasks; counts, not durations)

- 358 tasks have a setup `config`; **260 download files, all 458 files from `huggingface.co`** (the OSWorld file cache); 175 launch an application, 152 open a file; 54 involve browser tabs, logins or Google Drive.
- The **explicit sleeps are negligible** (mean 0.08 s, maximum 5 s, none of 10 s or more): the cost of setup is not written into the configs as waits. It is downloads (cached per cache directory after the first rollout), application launches and file opens, whose durations the files do not state.
- Launched applications: Chrome (79, each with a `socat` helper), VS Code (28), Thunderbird (25), GIMP (23), VLC (20), Nautilus (11), LibreOffice (10).
- Only **73 tasks need nothing from the network in setup**; 46 of those launch or open something. `huggingface.co` is not reachable from this sandbox, and the licence of the file-cache dataset is unresolved here, so the 260 download tasks cannot be run and their files are not used.

### 18.3 What this does and does not say about economics

- **The mechanism is not unique to Drums.** The incumbent's VM stack can also snapshot AFTER setup (`save_state` with a custom snapshot name) and revert to that for each rollout of the task. A checkpoint-after-setup is prior-art territory; the only claim that could count is a measured increment over a tuned incumbent using post-setup snapshots, which needs the matched run and a real host.
- **Possible value, by arithmetic only (not a measurement):** per rollout the saving is (setup time) minus (restore time); it applies only to rollouts 2 to G of a group, so the share is (G-1)/G, and its effect on valid rollouts per hour is that saving over the rollout length. Which tasks have a setup long enough to matter is unmeasured: the survey gives counts of steps, not seconds.
- **Risk to real state:** the checkpoint must preserve what the task set up (open applications, files, windows). It is therefore declared NOT clean (16.4): no browser reset. Whether heavy desktop applications survive a CRIU checkpoint is unknown; Chromium did, LibreOffice has not been tried.

### 18.4 The registered test (one real public task setup, run through the real setup code)

**Task state under test:** the public task `e1fc0df3-c8b9-4ee7-864c-d0b590d3aa56`'s setup, which is exactly `launch libreoffice --writer` (LibreOffice 24.2.7.2 is installed in this sandbox; the task's own evaluation needs the internet to install an extension and is NOT run). The setup is executed by OSWorld's real `SetupController` against the Drums guest server. This validates branching of ONE application-launch setup state; it does not validate that task, any evaluator, or general benchmark semantics.

**Arms.** SETUP: a cold friendly session plus the real setup, timed from request to agent-ready. BRANCH: a restore of a checkpoint taken after that setup, declared not clean, with the standard reseed (hostname, server RNG; the browser untouched). 10 starts per arm, interleaved. **Agent-ready** = the real accessibility tree from the guest server contains the LibreOffice Writer window AND the screenshot differs from a blank desktop in more than 10% of pixels.

**Thresholds, in order:**
1. FEASIBILITY: the checkpoint of the running LibreOffice dumps and restores without error (a failed rung is recorded with the CRIU error; no workaround is allowed that would drop LibreOffice from the checkpoint, because that would not preserve the task state).
2. STATE PRESERVED: in each restored sibling the Writer window is present in the real accessibility tree with the same title as at checkpoint time (a document is open).
3. IN-APP STATE WORKS AND IS PRIVATE: a unique token typed into sibling A through the real pyautogui path appears in A's accessibility tree and does NOT appear in sibling B's; the reverse too.
4. SHARING AUDIT: the C3 audit under revision 2a (section 16.4) on two siblings, with a cold-pair control, passes. Anything beyond `/dev/null` and `/dev/urandom` shared fails it.
5. LATENCY: BRANCH p50 <= 0.50 x SETUP p50 and p95 not worse (target at least 2x).
6. MEMORY: restored sibling memory per added session <= 1.05 x the cold-with-setup session (same slope method, 3 trials).

**Expected outcome, stated in advance so it can lose:** LibreOffice checkpoints and restores: about 40% (it forks helpers, uses many pipes and unix sockets and shared memory). If it does: threshold 2 about 85%, 3 about 70%, 4 about 60%; latency 5 about 75% (a LibreOffice cold launch is several seconds against a restore of about a second); memory 6 about 60%. Most likely failing point if it fails: the dump of the LibreOffice process tree. The economics reading, if everything passes: a measured saving of seconds per rollout for this one setup, worth (G-1)/G of it; nothing about any incumbent.


### 18.5 Amendment to the 18.4 registration (made after the first attempt produced no measurement, before any measured run)

The first attempt of the 18.4 probe stopped at its first stage: **the readiness criterion "the real accessibility tree contains the LibreOffice Writer window" cannot be evaluated in this sandbox.** LibreOffice 24.2.7 here has only the `gen` VCL plugin (no GTK plugin), so it does not expose itself to AT-SPI: the screenshot shows the window (99.6% of pixels differ from a blank desktop) but the accessibility tree stays at 541 bytes. This is an environment limit, not a CRIU result and not a product defect; the real OSWorld image has a GTK-enabled LibreOffice. No timing or restore result was produced under the original criterion.
Amended criteria, replacing only the a11y parts (everything else in 18.4 stands, including the thresholds and the expected outcomes):
- **Agent-ready** = `wmctrl -l` on the session's X display lists a window whose title contains "LibreOffice Writer" AND the screenshot differs from a blank desktop in more than 10% of pixels.
- **Threshold 2 (state preserved)** = in each restored sibling that window exists with the same title as at checkpoint time.
- **Threshold 3 (in-app state works and is private)** = a unique token is typed into sibling A through the real pyautogui path, then select-all and copy; the clipboard read from A's X display (`xclip`) contains A's token and not B's; the same for B. This reads actual document text, which is stronger than a pixel check.
These checks are weaker than the a11y tree in one respect: the window title shows that a document window exists, not what the document contains beyond the typed token. The claim stays what 18.4 says: branching of ONE application-launch state; no evaluator and no benchmark semantics.


## 19. Results: branching the post-setup state of one real public task setup (MEASURED; `docs/data/l1-criu/task_branch_probe.json`)

**Status: MEASURED on one host against the 18.4 registration as amended in 18.5. PROTOTYPE. This is one application-launch setup (`launch libreoffice --writer`, run by the real `SetupController`), not the task `e1fc0df3` (its evaluation needs the internet and was not run), not a benchmark result and not an incumbent comparison.**

| Threshold | Result | Verdict |
|---|---|---|
| 1 feasibility: LibreOffice dumps and restores | dump 0.35 s, image 236 MiB; two siblings restored (0.73 to 0.80 s each); no workaround, LibreOffice stayed in the checkpoint | **PASS** (I put it at about 40%) |
| 2 state preserved | both siblings show the Writer window `Untitled 1 - LibreOffice Writer`, the same title as at checkpoint time | **PASS** |
| 3 in-app state works and is private | a token typed into A is in A's clipboard and not B's, and the reverse | **PASS** (needed a harness correction, below) |
| 4 sharing audit, revision 2a, with a cold-pair control | shared objects: `/dev/null` only, shared by the cold pair too; no pipe or socket inode, lock, or writable file shared; a write in one sibling is invisible to the other | **PASS** |
| 5 latency (target >= 2x) | SETUP (cold session plus the real setup) p50 3.12 s, p95 3.24 s; BRANCH p50 0.87 s, p95 0.92 s; ratio **3.6x** (bootstrap 95% interval 3.4 to 3.8), 10 starts per arm interleaved, no faults | **PASS** |
| 6 memory (<= 1.05x) | per added session COLD (with setup) 238.5 MiB, RESTORE 238.5 MiB; ratio 1.00; difference interval -10.4 to +6.7 MiB | **PASS** (no measurable difference); measured to 12 sessions per trial, not 20, to stay within host memory |

**Two harness findings, both recorded, neither a CRIU result.** (1) The first attempt could not use the registered accessibility-tree readiness: this LibreOffice build has only the `gen` VCL plugin and exposes nothing to AT-SPI (18.5, registered before the measured run). (2) The first measured run (kept as `task_branch_probe_run1_T3_dialog_harness_fail.json`) failed threshold 3 for BOTH siblings: LibreOffice's first-run "Tip of the Day" dialog, which is part of the preserved state, swallowed the keystrokes; a COLD session did the same in a debug run (screenshot inspected), so it was the harness, not restore. The second run dismisses the dialog with OK before typing. Thresholds 1, 2, 4, 5 and 6 were also passed in run 1 (latency ratio 3.6x, interval 3.3 to 3.8).

**What this is worth, by arithmetic from the measured numbers (not a throughput measurement):** the saving is about 2.25 s per rollout after the first of a group, so with G rollouts per task the average saving is 2.25 s x (G-1)/G (1.97 s for G = 8): about 3% of a 60 s rollout and 0.7% of a 300 s rollout. The setup here is cheap (a LibreOffice launch is about 1.6 s on top of the session start); the survey in 18.2 says that most real setups are downloads (cached after the first rollout) and application launches of unknown but similar order. **On this evidence, branching the setup state of launch-only tasks is a small saving.**

**Where a larger saving would have to come from (HYPOTHESIS, UNTESTED):** not the setup but the agent's own trajectory prefix. Branching a session after N agent steps lets G continuations share N steps of environment time AND of model inference; that is where a rollout's real cost sits. Whether a mid-task state (a browser with live page state, an application mid-edit) survives restore with its semantics intact is not known: Chromium and LibreOffice survived at their launch state only. It is also not unique to Drums: a VM snapshot taken mid-task is the incumbent's equivalent, and tree-style rollouts are known in the agent-training literature. Any claim needs a registered test of mid-task branching against the incumbent's snapshot on a real host.

### What is NOT established

The effect on valid rollouts per hour (not measured), anything about the task's evaluator, tasks with downloads (the file host is unreachable here and its licence is unresolved), tasks with logged-in or networked state, restore on another host, the incumbent's post-setup snapshot latency, and isolation beyond the audit above on two siblings.


## 20. Bridge from setup cloning into the matched runner, and a mid-task branch test (REGISTERED before implementation or any run)

**Status: ASSESSMENT + PRE-REGISTRATION. Nothing here has been run. No incumbent result exists or is claimed; there is no KVM host in this sandbox.** Final suite on the code at `eb571a9` (the final teardown, `session.json` and argv-test edits included): 184 passed in 431.8 s (recorded separately from the targeted repeat runs of the restore tests).

### 20.1 The smallest honest bridge

Both arms must get the SAME reuse through the SAME code path: OSWorld's public snapshot API on `DesktopEnv` (`_save_state(name)` after the setup, `snapshot_name` plus `reset` to revert before later rollouts of the same task). The incumbent arm gets this from its own provider; the Drums arm gets it from a Drums provider that implements `save_state` as "CRIU-checkpoint this session and restore it in place" and `revert_to_snapshot(name)` as "destroy this session and restore it from that checkpoint". `DesktopEnvAdapter` gains `reuse="post_setup"` and nothing else differs between arms.
What the incumbent's equivalent really is, from the public provider sources (checked, `desktop_env/providers/*/provider.py`): `vmware` and `virtualbox` implement `save_state`/`revert_to_snapshot` with `vmrun snapshot` / `VBoxManage snapshot take`, which snapshot a running VM; `aws` implements `save_state` as `create_image` (a disk image: an application launched during setup is NOT part of it, and reverting launches a new instance); `docker` raises `NotImplementedError("Snapshots not available")`; `modal` raises; `pyromind` makes `save_state` a no-op. So an incumbent has equivalent LIVE-state reuse only on the local-hypervisor providers; on AWS its post-setup reuse is disk-level only. A matched run must say which provider the incumbent used and what its reuse preserves; this sandbox can check none of it.

**Accounting, nothing hidden.** Every rollout is labelled with its reuse phase: `setup+save` (the first rollout of a task on an environment: the full setup AND the save, both inside its measured reset time and so inside the arm's wall time and cost) or `revert` (later rollouts). The summary reports the first-rollout cost separately from the reuse cost, and the headline valid-per-hour and cost-per-valid still include all of it.
**State kind, not blended.** `setup_only` = a state that does not depend on any agent action (equivalent to a clean reset of that task; valid for ordinary rollouts). `agent_prefix` = a state reached by one agent's actions: valid only as the shared prefix of branch-style training, where the trainer must credit the prefix to the branching sample; it is never counted as if each continuation were an independent fresh rollout. The runner records the kind per rollout and reports valid counts per kind; they are not summed into one headline.

### 20.2 Real-host checks still missing (concrete)

1. On a hypervisor host (VMware or VirtualBox provider): `save_state` after a task's setup keeps the launched application alive after `revert_to_snapshot`, and the reverted VM answers the guest server without a re-setup; measure the revert latency per rollout.
2. On AWS (the public provider): confirm that the post-setup AMI loses the launched application and record what the incumbent must redo; this decides whether the incumbent's post-setup reuse is disk-level only.
3. The same task sequence, the same agent and the same reuse flag on both arms, with the incumbent's provider settings and instance price in the run specification (`docs/matched-comparison-protocol.md`).
4. Whether the incumbent's snapshot revert restores the guest server's random state, clock and network identity correctly, which the Drums arm handles with the reseed.
5. First-rollout cost with real downloads (the file host is unreachable here): cache behaviour across environments.
6. Concurrency: `W` workers each pay the first-rollout cost once per task; measure the effect at the planned concurrency.

### 20.3 Registered tests (one task, real local evaluator, real `DesktopEnv`, real Drums sessions)

Task: public `e0df059f` ("rename `todo_list_Jan_1` to `todo_list_Jan_2`"), whose evaluator is a local `exact_match` on a shell command; its setup creates the directory. Validates this one task only.

**V1, setup-only reuse.** 6 rollouts of the task at concurrency 1 on the Drums arm with `reuse="none"` and with `reuse="post_setup"`, a scripted correct action, the interference canary on. PASS needs: every rollout scores 0.0 before and 1.0 after the action in both arms; the reuse arm's first rollout is labelled `setup+save` and the other five `revert`; the canary finds no interference (a marker written in rollout k is absent in k+1: the snapshot predates the marker, so this tests that the revert really undoes a rollout); the setup directory is present after each revert. Latency is reported, not thresholded.
**V2, mid-task branch with divergent continuations.** After the setup the agent prefix creates the file `~/Desktop/branch_prefix.txt` holding a unique token and starts a long-lived Python process that seeds `random` and prints its next draw on a signal. The state is saved there (`agent_prefix`). Three siblings are restored from it: A renames to `todo_list_Jan_2` (expected score 1.0), B renames to `todo_list_Jan_3` (expected 0.0), C repeats A (expected 1.0, which shows the prefix state is unharmed by A and B). PASS needs ALL of: (i) task state preserved: every sibling has the file with the same token and the same Python process alive, and its next random draw is IDENTICAL across siblings (an agent's own process state is task state and must not be reseeded); (ii) infrastructure state reseeded: hostnames differ and the guest server's next Python draw differs across siblings; (iii) divergent outcomes under the real evaluator, 1.0 / 0.0 / 1.0; (iv) isolation: before B acts, B still sees `todo_list_Jan_1` and not `todo_list_Jan_2`, whatever A did; (v) the C3 audit under revision 2a on two concurrent siblings with a cold control passes.
**Expected outcome in advance (so it can lose):** V1 passes about 85% (the risk is a quirk of `DesktopEnv`'s snapshot handling with a non-VM provider); V2 (i) about 80%, (iii) about 90%, (iv) about 90%, (v) about 80%, all of V2 about 55%.
**What passing would and would not mean:** one task, one local evaluator, one host. It would show that the snapshot-API bridge preserves and branches task state with correct random-state handling and that the runner can account for it; it would NOT show any increment over an incumbent, any benchmark result, or semantics of tasks with downloads, logins or networked state.


## 21. Results: the snapshot-API bridge and the mid-task branch on real sessions (MEASURED; raw data `docs/data/l1-criu/matched_reuse/`, test `tests/test_matched_reuse_real.py`)

**Status: MEASURED on one host against the section 20 registration. PROTOTYPE. One public task (`e0df059f`), its real local evaluator, the real public `desktop_env`, real Drums sessions. Harness validation of the reuse path and a state-preservation test: no incumbent exists in this sandbox, so NO comparative or economic claim follows.** The three runs are kept separately because the first two failed for real reasons.

### 21.1 Final run (`run3_final.json`) against the registered gates

| Gate | Result | Verdict |
|---|---|---|
| V1 setup-only reuse, 6 rollouts, no reuse vs `post_setup` | both arms: 6/6 valid, scores 0.0 before and 1.0 after on every rollout, canary 6 clean / 0 interference; the reuse arm's rollouts are labelled `setup+save`, then 5 x `revert`; all counted as `setup_only` | **PASS** |
| V2 (i) task state preserved | all siblings: same prefix file token, the agent's own Python process alive, and its next random draw IDENTICAL (0.862...) | **PASS** |
| V2 (ii) infrastructure reseeded | hostnames differ (`drums-s0-8722e7`, `drums-s1-56b78f`) and the guest server's next draw differs | **PASS** |
| V2 (iii) divergent outcomes under the real evaluator | A correct 1.0, B wrong 0.0, C (B reverted to the saved state, then correct) 1.0 | **PASS** |
| V2 (iv) isolation | before acting B saw `todo_list_Jan_1` and no `todo_list_Jan_2`; after A renamed, B's directory held only its own `todo_list_Jan_3`; after reverting, B again saw `todo_list_Jan_1` | **PASS** |
| V2 (v) sharing audit, revision 2a, cold control | shared: `/dev/null` only, shared by the cold pair too; no lock, pipe or socket shared; no write visible across | **PASS** |

Timings on this host (one task, not a comparison): per-rollout reset, no reuse 2.4 s (first rollout 0.8 s); with reuse, first rollout (setup AND save) 1.9 s, then 0.8 s per revert; 6 rollouts took 28.1 s without reuse and 20.9 s with. The mid-task save took 1.2 s and a restore 1.0 s. So the saving here is about 1.6 s per rollout on a task whose setup is one `mkdir`; the first-rollout work is inside the 20.9 s and inside the cost.

### 21.2 What the real evaluator and real task actions found (each is a recorded defect, kept in the raw data)

1. **Restored state broke renaming a directory (`run1`, `run2` before the fix).** After a checkpoint, the task's setup directory lives in a read-only overlay lower layer, and `os.rename` of a lower-layer directory fails with EXDEV ("Invalid cross-device link") unless the overlay is mounted with `redirect_dir=on`. The task scored 0.0 after the correct action on every restored rollout. This is exactly the kind of failure a fake or a no-action test cannot see; it was found only because a real evaluator scored a real action on restored state. Fixed for friendly and restored sessions (`redirect_dir=on`, verified with a standalone overlay test). **Likely also true, UNTESTED, for ordinary L0 sessions:** a directory that comes from the template or host layer cannot be renamed with `os.rename` there either.
2. **A race on restore (`run1`).** A restore into a slot whose previous session had just died failed with "Can't restore link: -17" because the old veth is removed asynchronously; restore now waits for the link to disappear.
3. **A 24-second stall in every `sudo` call (`run1`, `run2`).** The session's hostname did not resolve (no DNS in the session), so `sudo` waited 24 s for a lookup. The task's setup uses `sudo`, so the setup cost 24 s and the measured reuse benefit (about 26 s down to 0.8 s) was almost entirely this defect. It is an L0 defect affecting any task that uses `sudo`; sessions now get an `/etc/hosts` entry for their hostname, and the reseed updates it. **The 26 s figures in `run1` and `run2` must not be quoted;** they are kept because they show how an environment defect can fake a large reuse benefit.
4. **The canary caught a hole in `DesktopEnv`'s own bookkeeping (fake test).** `DesktopEnv` skips the snapshot revert when it believes the environment is clean, and it only learns it is dirty through `step`; a guest mutation made any other way (the canary's marker) survived into the next rollout. The adapter now marks the environment used after the canary writes. This is a property of the public API an incumbent also has.

### 21.3 What is NOT established

Any comparison with an incumbent, any throughput or cost effect (the saving above is about 1.6 s on a trivial setup), semantics of any other task, tasks with downloads (host unreachable, licence unresolved), logged-in or networked state, restore on another host, and whether a heavy mid-task state (a live browser page, an application mid-edit) survives branching with its semantics intact (only a file, a background process and a directory were tested). The real-host checks still missing are listed in 20.2.


### 21.4 Accounting and scope statements for section 21 (explicit)

- **The two product findings are product defects, not harness glitches, and have before/after evidence** (`matched_reuse/findings_before_after.json`, produced by `envd_linux/l1proto/findings_evidence.py`, same code): (a) overlayfs rename of a directory that lives in a lower layer: default mount `rename failed: [Errno 18] Invalid cross-device link`; with `redirect_dir=on` `rename OK`. (b) `sudo` in a session: hostname absent from `/etc/hosts` 24.04 s (`sudo: unable to resolve host drums-s90: Temporary failure in name resolution`); present 0.01 s. Fixes are in `session_init.py` (`redirect_dir=on` for friendly sessions, `/etc/hosts` entry for full-root sessions), `session.py` (`redirect_dir=on` on every restore) and `clone.py` (the reseeded hostname is written to `/etc/hosts`). Regression test: `tests/test_criu_restore.py::test_product_findings_a_lower_layer_directory_renames_and_sudo_does_not_stall` (a directory made before the checkpoint renames with `os.rename` after restore; `sudo` completes in under 5 s in the cold and the restored, reseeded session). The real-evaluator run (`test_matched_reuse_real.py`) is the end-to-end regression for (a). Whether ordinary non-friendly L0 sessions have the rename limitation was not tested.
- **Canary and the forced revert.** The interference canary writes its marker through the guest controller, which `DesktopEnv` does not count as use, so it would skip the next revert; the adapter therefore marks the environment used after each canary write. Consequence for the numbers: with the canary on, every rollout after the first starts with `DesktopEnv`'s revert-to-initial-state (visible in the no-reuse arm: first rollout 0.8 s on a fresh environment, later ones about 2.4 s). A real agent's `step` marks the environment used anyway, so this matches normal behaviour for any rollout that takes a step; it is not a speed-up or a penalty for either arm, and both arms get it from the same adapter code.
- **The fresh, unseeded baseline.** The first rollout of the no-reuse arm runs on a fresh environment with no revert (0.8 s); the first rollout of the reuse arm does the full setup AND the save (1.9 s). Both appear in the per-rollout rows and inside the 28.1 s and 20.9 s wall times; neither is dropped or amortised away. With 6 rollouts the first-rollout work is a large share of the reuse arm; the per-rollout figures are not steady-state.
- **V2 continuations are not independent training samples.** A, B and C share one prefix state by construction. They validate that the saved state is preserved, that continuations diverge under the evaluator and that siblings are isolated; they must not be counted as three independent rollouts or as `setup_only` rollouts. They are labelled `agent_prefix`.
- **Scope of the evaluator claim:** one local evaluator (the `exact_match` of task `e0df059f`), one task. Nothing here supports a benchmark, throughput or incumbent claim.
- **Test suite on the bridge code:** 190 passed (`b8d1aad`, 364 s). The regression test above and the two findings' evidence were added after that run; a final full-suite run on the final tree is recorded in the commit that adds them.

## 22. The next concrete blocker for a real incumbent arm (checked against the public sources; no authentication, spend, quota or merge action taken)

The matched run needs an incumbent arm with the same reuse as the Drums arm. Concretely, from what was checked here:
1. **No hardware virtualization in this sandbox:** no `/dev/kvm`, no `vmx` CPU flag. Any hypervisor-backed incumbent (the `docker` provider's QEMU, VMware, VirtualBox) cannot run here, so the incumbent arm needs a different host: bare metal or a nested-virtualization-capable instance. Choosing and paying for it is a user decision (host, quota, cost, bounded shutdown); nothing was provisioned.
2. **The OSWorld VM images are on `huggingface.co`** (`xlangai/ubuntu_osworld`: `Ubuntu.qcow2.zip` for the docker provider, `Ubuntu-x86.zip` for VMware), which is unreachable from this sandbox, and **the licence of the image has not been checked**. The no-unresolved-licence rule applies: it must be resolved before the image is used.
3. **The provider decides what "equivalent reuse" means.** With the `docker` provider the incumbent has NO snapshot (`save_state` raises), so its reuse would be container reuse only, not a post-setup snapshot. A live-state post-setup snapshot exists only on `vmware` (proprietary VMware Workstation or Fusion: its licence and availability on the chosen host must be checked) and `virtualbox`. On `aws` the snapshot is a disk image (a launched application is not preserved). The run specification must therefore state which provider is the "tuned incumbent" and what its reuse preserves; the comparison is meaningful only against a provider that offers live-state snapshots, or it must be labelled disk-level.
4. **The first concrete step that needs no spend:** verify, from the public provider code and documentation, whether the `virtualbox` provider can be run on a nested-virtualization instance type and what its revert latency would be to measure; this is a desk check followed by a user approval package, not a run.


### 21.5 Scope statements corrected after review (how V2 was actually run)

The 20.3 registration speaks of "three siblings restored from it". As run: **A and B are two CONCURRENT siblings** (A is the original environment carried on in place after the save, B a second environment restored from the same saved state), and they are what the sharing audit and the divergence checks compare. **C is NOT a third concurrent sibling:** it is B reverted to the same saved state and run again, SEQUENTIALLY, after A and B had finished; it shows that the saved state is unharmed by the earlier continuations, not that three siblings coexist. Anything in this document or the README that reads as three concurrent siblings means this. The V2 continuations share a prefix and are `agent_prefix` state, never independent training samples.

### 21.6 A further product finding: checkpointing a RESTORED session destroyed the kept layer (chained checkpoints), fixed

Found by the chained-checkpoint test added after the review (raw: `docs/data/l1-criu/matched_reuse/chained_checkpoint_whiteout_finding.txt`). Restoring from a checkpoint taken of a session that was itself restored failed in CRIU's mount restore. Cause: the kept layer was filled with whiteouts, because the slot directory was removed with `rmtree` while the restored session's overlay root was still mounted inside it, so the walk deleted the tree through the overlay. Every earlier test had only checkpointed cold sessions. Fixed (the restored root is unmounted first); regression test `tests/test_criu_restore.py::test_chained_checkpoints_keep_each_layers_state_and_repeated_reverts_leak_nothing` (a second-level checkpoint sees both changes, the first checkpoint is unharmed, 25 restore-and-destroy cycles leave no extra mounts, links or processes; the test waits for the asynchronous removal of the old veth before comparing). Mid-task branching of a state that was itself reached by a restore is therefore supported only since this fix.

## 23. Incumbent image, licence and provider semantics: desk research from primary sources (no authentication, spend, quota change or merge; read-only web)

**Status: DESK RESEARCH, fetched on the date of this commit; nothing was downloaded, provisioned or authenticated. The sandbox cannot reach `huggingface.co`; one unreachable source is not proof that no route exists, so each route below is classified by what a primary source says.**

### 23.1 The OSWorld VM image: sources and licence

- **Official image, Hugging Face:** `https://huggingface.co/datasets/xlangai/ubuntu_osworld` (card: "virtual machine snapshots ... loaded by VMware/VirtualBox", total size 84.2 GB; `Ubuntu.qcow2.zip` is what the `docker` provider downloads, `Ubuntu-x86.zip` what `vmware` downloads: `desktop_env/providers/docker/manager.py`, `.../vmware/manager.py`). **The dataset card's License field is empty, so the image licence is UNRESOLVED;** the OSWorld code is Apache-2.0 (`https://github.com/xlang-ai/OSWorld/blob/main/LICENSE`), which does not by itself license the image. The manager code also supports `hf-mirror.com` as a substitute host for the same files; that is a mirror of the same unlicensed artefact, not a licence.
- **Official AWS image route (owner-supplied image, no Hugging Face download, no nested virtualization):** the AWS guideline states `IMAGE_ID_MAP` "already set ... the official OSWorld image of Ubuntu supported by us", `"us-east-1": "ami-0d23263edb96951d8"` (`https://github.com/xlang-ai/OSWorld/blob/main/desktop_env/providers/aws/AWS_GUIDELINE.md`). Each OSWorld environment is then an EC2 instance launched from that AMI. Whether the AMI is public to an arbitrary account, and under what terms, is NOT verifiable without an AWS API call, which this task excludes; it is a remaining check. This route's snapshot semantics are disk-level (23.3).
- **Official build-from-source route (licence-clean in principle):** `https://github.com/xlang-ai/OSWorld/blob/main/desktop_env/server/README.md` ("Server setup ... useful if you want to set up your own machine for the environment"; "not yet finished"): install an Ubuntu ISO, create the `user`/`password` account, auto-login, VNC, dummy display, install the Apache-2.0 guest server and the listed software. This yields an image whose own content is Ubuntu plus Apache-2.0 code plus whatever applications are installed, each under its own licence, which is a licence position this project can document. **Equivalence caveat:** the self-built image is not the official image, the README is incomplete, and published benchmark numbers (and the baseline run specification) are not transferable to it; a matched run would compare both arms on the SAME self-built or owner-supplied image and say so.
- **Other providers' images:** `daytona` runs the sandbox AS the desktop on a Debian 13 XFCE snapshot that the provider builds ("not Ubuntu with GNOME like the upstream OSWorld image", `desktop_env/providers/daytona/DAYTONA_GUIDELINE.md`), needs a Daytona account and API key, and `modal` needs a Modal account; both are services with authentication and spend, outside this task, and their guests are not equivalent to the Ubuntu image.

### 23.2 Hypervisor software and licences

- **VMware Workstation Pro:** free for personal, educational and commercial use since the versions 17.5.2 (Workstation) and 13.5.2 (Fusion), no licence key (`https://www.vmware.com/docs/desktop-hypervisor-faqs`, `https://knowledge.broadcom.com/external/article/368667`, `https://techdocs.broadcom.com/us/en/vmware-cis/desktop-hypervisors/workstation-pro/17-0/release-notes/vmware-workstation-1763-pro-release-notes.html`). The download requires a Broadcom account with a trade-compliance profile (the KB article says so): creating it is an account action for the owner, not done here. Features include snapshots.
- **VirtualBox:** its own manual describes nested VT-x/AMD-V as a setting for guests that run hypervisors (`https://www.virtualbox.org/manual/ch03.html`; an older manual notes it is available on AMD hosts and greyed out for Intel, `https://docs.oracle.com/en/virtualization/virtualbox/6.0/user/settings-system.html`); the current manual warns not to run other hypervisors together with VirtualBox (`https://www.virtualbox.org/manual/UserManual.html`). Licence terms (GPL for the base package, a separate licence for the Extension Pack) were not re-checked in this pass and remain a remaining check.

### 23.3 What each provider's snapshot preserves (from the public provider sources in the checkout `04718ea` and the OSWorld docs)

| Provider | `save_state` / revert | Preserves live application state? |
|---|---|---|
| `vmware` | `vmrun snapshot` / `revertToSnapshot` of a running VM | yes (running-VM snapshot); the exact memory-state behaviour is VMware's and was not verified on a host |
| `virtualbox` | `VBoxManage snapshot take` / restore | likewise, not verified on a host |
| `docker` (QEMU in a container, needs KVM) | `save_state` raises "Snapshots not available for Docker provider" | no snapshot at all |
| `aws` | `create_image` (an AMI); revert launches a new instance from it | disk image only: a launched application is NOT in it |
| `daytona` | snapshot of a sandbox; each launch restores a built snapshot | snapshot semantics (live or filesystem) not established from the docs read; account and spend required |
| `modal` | `save_state` raises (TODO); revert tears down and starts fresh | no |
So a "tuned incumbent with a post-setup snapshot of live state" exists off the shelf only on `vmware` and `virtualbox`. QEMU itself can snapshot a running guest, but using it from `docker` would be a custom provider written for this comparison, i.e. extra engineering that would have to be disclosed as a tuning of the incumbent.

### 23.4 Hosts: nested-virtualization support is one question, a given hypervisor working on it is another

- **AWS, virtual instances:** nested virtualization is documented as supported on specific families only (C7i/M7i/R7i, their `-flex` variants, C8i/M8i/R8i and `id` variants, I7i, I7ie, X8i, queried per region with `describe-instance-types`), **with KVM and Hyper-V as the supported L1 hypervisors**, "no additional cost", with a performance caveat and a recommendation to evaluate bare metal for performance-sensitive work (`https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/amazon-ec2-nested-virtualization.html`; announcements `https://aws.amazon.com/about-aws/whats-new/2026/02/amazon-ec2-nested-virtualization-on-virtual/` and `https://aws.amazon.com/about-aws/whats-new/2026/06/nested-virtualization-intel-us-gov-cloud/`). VMware Workstation and VirtualBox are NOT listed as supported L1 hypervisors there.
- **Google Compute Engine:** nested virtualization supported with "the only hypervisor supported in an L1 VM is Linux KVM"; E2, memory-optimized and Arm VMs excluded; expect 10% or more performance loss (`https://cloud.google.com/compute/docs/instances/nested-virtualization/overview`).
- **OSWorld's own guidance:** VMware/VirtualBox on a non-virtualized machine (desktop, laptop, bare metal); on a virtualized platform use the Docker provider, which needs KVM (`https://github.com/xlang-ai/OSWorld/blob/main/README.md`).
- **Conclusion from the sources, no guessing:** the documented-supported way to run VMware or VirtualBox is a non-virtualized host (bare metal, which AWS offers as `.metal` instance types and which the owner may already own); nested virtualization on the clouds above is documented for KVM and, on AWS, Hyper-V. Whether VirtualBox or VMware works inside a nested EC2 or GCE instance is therefore **undocumented and must be tested before any price or plan is built on it**; no price is stated here because none was verified.

### 23.5 Remaining checks (each needs the owner or a host; none was attempted)

1. Licence of the official image (the Hugging Face card is blank): ask the maintainers (contact in the OSWorld README) or adopt the self-built route and document its licence composition.
2. Whether `ami-0d23263edb96951d8` is public/shareable to another account and under what terms (needs an AWS API call: excluded here).
3. Which host the owner can supply (an existing machine with hardware virtualization) or approves renting (bare metal, or a nested-capable instance with KVM), including whether VMware or VirtualBox is allowed and works on it.
4. On that host: a live-state snapshot test of the chosen incumbent provider (does a launched application survive revert, and how long does revert take), the same test that was run on the Drums arm.
5. The licence terms of VirtualBox's Extension Pack if VirtualBox is chosen, and the Broadcom account step if VMware is.
6. The matched run specification: the same image on both arms, the same task sequence and agent, prices from the owner's own price sheet.
