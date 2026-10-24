# compare

**Status of this page: rewritten to match the code on this branch; the previous text (summarised below as HISTORICAL) said only fake environments and stubs existed. That was true of the early design and is not true now.**

## Current (BUILT; real-session validation lives in `envd-linux/tests`)

- `drums_compare/osworld_matched.py` is the matched-comparison runner: the same task sequence and agent on two adapters (`DesktopEnvAdapter` over the public OSWorld `DesktopEnv`: Drums arm via `osworld_provider`, incumbent arm via a stock provider), valid-rollout accounting, bootstrap ratio, the per-slot interference canary, and snapshot-API reuse (`reuse="post_setup"`) with first-rollout work reported apart. Protocol: `docs/matched-comparison-protocol.md`; bridge and results: `docs/l1-shared-runtime-design.md` sections 20 and 21; preflight for an incumbent arm: `docs/incumbent-preflight-plan.md`.
- `drums_compare/osworld_split.py` is the OSWorld train / held-out split by evaluator signature (our split, not comparable to published ones).
- Tests: `pytest compare` runs 76 tests, all against fake adapters or fake `DesktopEnv` objects (labelled HARNESS VALIDATION). The real-`DesktopEnv`, real-session versions are `envd-linux/tests/test_osworld_matched_dryrun.py` and `envd-linux/tests/test_matched_reuse_real.py` (one public task, one host, no incumbent: **no comparative result exists**).

## Historical (the early fake-sides design; still present and still tested, but not the current comparison path)

`harness.py`, `runner.py`, `scorer.py`, `resources.py`, `fakes.py`, `envd_http.py`, `verify_http.py`, `conformance.py`, `protocol.py`, `types.py` belong to the first design in `docs/linux-comparison-design.md`: slots, a schedule, an audit, and sides registered in `runner.SIDES`. Its smoke run uses fake sides, which never get a verdict:

    PYTHONPATH=compare python -m drums_compare.runner --side fake-drums --out /tmp/a --tasks tasks/linux_comparison/tasks.json --slots 4 --attempts 40 --seed 1
    PYTHONPATH=compare python -m drums_compare.runner --side fake-linux --out /tmp/b --tasks tasks/linux_comparison/tasks.json --slots 4 --attempts 40 --seed 1
    PYTHONPATH=compare python -m drums_compare.scorer /tmp/a /tmp/b

`python -m drums_compare.conformance PORT` checks a guest daemon against the envd protocol; the real-session guest and its conformance live in `envd-linux` (see `envd-linux/tests/test_envd_protocol.py`, `test_envd_real_session.py`). Drums-Linux runs set `--drums-level L0|L1`; `drums_level` stays L0 on this branch.
