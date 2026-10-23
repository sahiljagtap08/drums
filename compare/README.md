# compare

Harness, runner and scorer for the Drums vs Linux comparison. Design:
`docs/linux-comparison-design.md`. Everything runs locally; only fake environments exist
in this sandbox.

    pytest compare                       # 38 tests
    # a fake smoke run (fake sides never get a verdict)
    python -m drums_compare.runner --side fake-drums --out /tmp/a --tasks tasks/linux_comparison/tasks.json --slots 4 --attempts 40 --seed 1
    python -m drums_compare.runner --side fake-linux --out /tmp/b --tasks tasks/linux_comparison/tasks.json --slots 4 --attempts 40 --seed 1
    PYTHONPATH=compare python -m drums_compare.scorer /tmp/a /tmp/b

Run with `PYTHONPATH=compare`. Real sides are added by registering a factory in
`runner.SIDES` that returns one `Env` per slot, a `Verifier`, and an agent factory.
Layout: `harness.py` (one attempt and validity), `runner.py` (slots, schedule, audit),
`resources.py` (per-env RSS/CPU), `scorer.py` (metrics and verdict), `envd_http.py`,
`verify_http.py` (real adapters), `fakes.py` (fakes, labelled).

Also: `python -m drums_compare.conformance PORT` checks a guest daemon (envd-linux) against
the envd protocol; it has run only against stubs. Drums-Linux runs set `--drums-level L0|L1`.
