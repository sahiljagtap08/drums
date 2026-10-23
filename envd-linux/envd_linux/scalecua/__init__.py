"""Drums-side environment driver for the AgentRL / ScaleCUA environment contract.

Interface source: the MIT-licensed `agentrl-worker` package (`agentrl/worker/environment`),
which ScaleCUA bundles under `scalecua_rl/worker`. ScaleCUA's own `osworld_env/worker` code has no
licence of its own: it was read for understanding only, and nothing here is copied from it or from
VeriGen. Tested against agentrl-worker 0.4.0 from PyPI (the bundled copy may differ) and against
local stubs of the worker side; the real worker, a real OSWorld image and real infrastructure are
not exercised.

  pool.py          DrumsSessionPool: blocking, thread-safe pool of exclusive, rebuilt sessions
  controller.py    DrumsEnvironmentController / DrumsEnvironmentDelegation (AgentRL interface)
  compat.py        real agentrl-worker base classes when importable, else local equivalents
  patch.py         install() a "drums" driver into agentrl.worker.environment.create_controller
  _quarantined_provider.py  QUARANTINED (licence provenance). Not imported anywhere; see its docstring
  client.py        GuestClient: a minimal client of the OSWorld guest-server contract
  smoke.py         bounded validation job: the first thing to run on real infrastructure
"""
