# 01: One output-comparison rule for judging and AI grounding

**What to build:** Judging a Submission and AI grounding of a Draft decide "do two outputs match" with one shared rule owned by the submissions module. Today the grounding check re-implements the normalisation and only a docstring says it must mirror the judge. After this ticket a Draft that passes grounding also passes the judge on its sample Test Cases, and the mirrored helper is gone.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [x] A test through the generation client's grounding path shows a reference output the judge accepts is accepted by grounding, and one the judge rejects is rejected. It passes on the current code before any change.
- [x] A unit test on the comparison covers trailing spaces on a line, trailing blank lines, internal whitespace preserved, and empty versus empty.
- [x] Grounding calls the submissions module's rule; the mirrored normalisation helper and its "mirrors" docstring are deleted.
- [x] The stored expected output and the compared value come from the same code (the shared module exposes both the comparison and the normalised form).
- [x] No new Protocol is introduced; the rule is a pure function.
- [x] Architecture tests stay green: `core` never imports `ai`, `integrations` or an ORM.
- [x] A row for this ticket exists in the task-scope table before work starts.
