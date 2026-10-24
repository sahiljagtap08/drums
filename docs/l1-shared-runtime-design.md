# L1: shared pre-warmed runtime forks (the Drums claim)

**Epistemic status: sections 1 to 7 are DESIGN (L1 is UNBUILT and UNPROVEN). Sections 8 to 13 are PROTOTYPE pre-registrations (each
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

**Overall verdict by the pre-registered rules: no K arm is a product mechanism.** K1 fails M-K1 and M-K2. K2 passes M-K1, M-K3 and M-K4 but fails
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
