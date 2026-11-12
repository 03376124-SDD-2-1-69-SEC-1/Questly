# 03: Migrate submissions routes to the shared error module

**What to build:** The submissions slice's JSON routes produce their errors through the shared error-to-response module. A Student or API client sees the same status, code and message as before for every submissions route, including a Classroom they are not a Member of returning the same 404 as a missing one (ADR-0007 section 9.6).

**Blocked by:** 02: Shared error-to-response module, proven on the assignments slice.

**Status:** ready-for-agent

- [x] Characterisation tests through `build_app` pin today's submissions error statuses, codes and messages, including the not-visible-to-404 mapping, and pass on the current code before migration.
- [x] The submissions routes register their error table with the shared module; their private helpers are deleted.
- [x] The characterisation tests are unchanged and green after migration.
- [x] Where the submissions and assignments maps overlap, neither slice's result changes.
- [x] A row for this ticket exists in the task-scope table before work starts.
