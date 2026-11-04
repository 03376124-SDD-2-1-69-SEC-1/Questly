# 07: Migrate classrooms, documents, notifications and topics routes to the shared error module

**What to build:** The last four JSON route modules produce their errors through the shared error-to-response module, so every JSON error in the app, from any slice, has one builder. This closes a gap found while merging 04: the spec says "every JSON route in every slice", and the first survey listed only five slices. An API client sees the same status, code and message as before on every classrooms, documents, notifications and topics route.

**Blocked by:** 02: Shared error-to-response module, proven on the assignments slice.

**Status:** ready-for-agent

- [x] Characterisation tests through `build_app` pin today's statuses, codes and messages for each error path of the four slices and pass on the current code before migration.
- [x] Each slice declares its own error table or uses `fail`; its private helpers (for example the six `_raise_*` helpers in documents) are deleted.
- [x] The characterisation tests are unchanged and green after migration.
- [x] A search of every JSON route module finds no construction of the `{code, message}` detail outside the shared module.
- [x] A row for this ticket exists in the task-scope table before work starts.
