# 04: Migrate auth, generation and admin routes to the shared error module

**What to build:** The auth, generation and admin JSON routes produce their errors through the shared error-to-response module. Every JSON error in the app, from any slice, now has the same `{code, message}` shape, and an Admin client handles errors the same way as any other client. When this ticket lands, none of the five original copies of the helper remain.

**Blocked by:** 02: Shared error-to-response module, proven on the assignments slice.

**Status:** ready-for-agent

- [ ] Characterisation tests through `build_app` pin today's statuses, codes and messages for each of the three slices and pass on the current code before migration.
- [ ] Each slice registers its own error table; its private `_fail` or `_raise` helper is deleted.
- [ ] The characterisation tests are unchanged and green after migration.
- [ ] A search of the JSON route modules finds no remaining local construction of the `{code, message}` detail outside the shared module.
- [ ] A row for this ticket exists in the task-scope table before work starts.
