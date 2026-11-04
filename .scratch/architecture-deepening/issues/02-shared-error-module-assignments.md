# 02: Shared error-to-response module, proven on the assignments slice

**What to build:** One shared module under `core/` owns turning a domain error into a JSON error response with a `{code, message}` body. Each slice supplies its own table of domain error to status and code; the module imports no slice. The assignments slice's JSON routes are the first to use it, so an API client sees byte-identical errors from assignments routes and the pattern is proven before the other four slices follow.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [x] Characterisation tests through `build_app` pin the current statuses, codes and messages of the assignments JSON routes (unknown Assignment, not-visible, rule violation as 422) and pass on the current code before migration.
- [x] The shared module is the only place that builds the `{code, message}` detail and exposes: register a slice's error table, and run a use case under that mapping.
- [x] The assignments routes use it and their two private helpers are deleted.
- [x] The characterisation tests are unchanged and green after migration. A needed test change means a behaviour change and is out of scope.
- [x] Page handlers (HTML) are untouched.
- [x] A row for this ticket exists in the task-scope table before work starts.
