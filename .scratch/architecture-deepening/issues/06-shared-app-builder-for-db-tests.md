# 06: Shared app builder for the db tests that rebuild create_app

**What to build:** The five database-backed test modules that each assemble the whole app by hand use one shared builder, with per-test overrides for the adapters under test. Adding a slice no longer means editing five near-identical setups. This is test-only and changes no production behaviour.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] The shared builder exists and the five modules (production wiring, admin pages, classroom exports, instructor pages, submit recovery on Postgres) use it.
- [ ] Each module keeps its current assertions; none is weakened or deleted to make the refactor fit.
- [ ] The full suite passes, including the architecture tests.
- [ ] No production module changes.
- [ ] A row for this ticket exists in the task-scope table before work starts.
