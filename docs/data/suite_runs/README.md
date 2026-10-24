# Raw full-suite logs (pytest -q on the whole repository, with DRUMS_CRIU_HOME, DRUMS_AGW_PYTHON, DRUMS_OSW_PYTHON, DRUMS_OSW_SRC set)

Every full-suite run made on this branch since the K measurement, kept as run, in order. A later run does not replace an earlier one; a later run that differs is a different tree, not a correction of the earlier number.

| Log | Last line | Tree it ran on |
|---|---|---|
| `full_suite.log` | 175 passed | after the K results (`1218b2c`), before the CRIU work |
| `full_suite2.log` | 179 passed | after the first CRIU results (`ca07330`) |
| `full_suite3.log` | 183 passed in 423.85 s | after the clone-hygiene and orphan-cleanup tests; BEFORE the later teardown / `session.json` / argv-matching edits |
| `full_suite4.log` | 184 passed in 431.83 s | at `eb571a9` (those edits included) |
| `full_suite5.log` | 190 passed in 363.89 s | at `b8d1aad` (snapshot-API bridge, provider and runner changes) |
| `full_suite6.log` | **191 passed** in 376.41 s | at `6366826` (product-findings regression test added). This is the "191" result |
| `full_suite7.log` | **1 failed, 191 passed** in 415.06 s; failed: `test_session_network_policy.py::test_control_without_the_firewall_sessions_do_reach_each_other` | at `f37be36` (chained-checkpoint test added): the probe read the HTTP headers without the body, a test-harness flake, repeated 3 times alone afterwards after the probe fix |
| `full_suite8.log` | 192 passed in 388.93 s | at `65da9c4` (probe fix, `close()` fix) |

`full_suite6.log` (191 passed) and `full_suite7.log` (1 failed, 191 passed) are both kept: they are different trees (the second has one more test and the flaky probe that failed once).
| `full_suite9.log` | 214 passed in 574.03 s | at `cc3a560` (adds the 22 fault-window tests); BEFORE the path-component sweep correction and the preflight / composition / first-check additions: an intermediate result |
| `full_suite10.log` | **221 passed in 554.88 s** | at `8b3bfd2` (path-component stray ownership, preflight, composition, first-check driver and their tests). Exact last lines: `221 passed in 554.88s (0:09:14)`; no leftover session, `criu` process, `dvh` link or test mount afterwards |
| `full_suite11.log` | **1 failed, 224 passed in 572.88 s** | started at `3c87319`, but the tree CHANGED DURING the run: `first_check.py` gained per-environment capture and the file `test_osworld_matched.py` was edited (committed later in `4bc9afc`), plus the new `task_coverage` and `cgroup_telemetry` modules. The one failure, `test_alternating_blocks_keep_first_rollout_work_in_every_block_and_make_no_claim`, is the old assertion (`per_environment_resource_capture.startswith("MISSING")`) meeting the new dict-valued field (`AttributeError`): a test-versus-code edit race I caused, not a product fault. NOT a clean final-tree result; it is kept as run, and a clean suite on the committed tree follows as the next log. |
