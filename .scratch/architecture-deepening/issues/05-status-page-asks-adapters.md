# 05: Status page asks adapters for availability

**What to build:** The Admin status page (A-01) reports AI generation, the code execution sandbox and email delivery from what each adapter says about itself, instead of from the composition root testing whether each adapter is a stub. An Admin sees the same six rows, the same 60-second refresh and the same Student banner as today, and swapping an adapter changes the status without another edit.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

**Owner note:** the system health checker lives in the database module, which belongs to พาย (AGENTS.md, "Who may change database and locked files"). This is an OPS task for พาย, or needs their explicit approval, even though the port changes sit outside that module.

- [ ] The generation client, code runner and email sender ports each gain a way to answer whether that adapter is available. Stub adapters answer "not available"; real adapters answer from their own configuration.
- [ ] "Available" keeps today's meaning, configured, with no network call, so the 60-second refresh makes no outbound requests. A reachability probe is a separate change.
- [ ] The health checker receives the adapters and the composition root no longer inspects adapter types.
- [ ] Through `build_app` and the existing health tests, each configuration renders the same A-01 rows as today: stub or real runner, stub or real sender, provider key present or absent, and an injected non-stub generation client.
- [ ] The six-row layout, the 60-second refresh and the Student banner are unchanged; demo mode on the in-memory fakes reports status through the same mechanism.
- [ ] Architecture tests stay green: `core` never imports `integrations`, `ai` or an ORM.
- [ ] A row for this ticket, owned by พาย, exists in the task-scope table before work starts.
