# Spec: deepen three shallow modules found in the architecture review

Source: architecture review of `src/questly`, 2026-11-04. Five candidates were
found. Three held up when checked against the code and ADRs and are in this
spec. Two did not; see Out of Scope.

## Problem Statement

Three rules in Questly each live in more than one place, and nothing but
convention keeps the copies equal.

1. **API errors.** Every slice with JSON routes (assignments, submissions, auth,
   generation, admin) writes its own helper that turns a domain error into an
   HTTP error with a `{code, message}` body. The assignments and submissions
   slices each also have their own error-to-status map, and the two maps
   differ. Changing the error shape, or adding a status code, means editing
   five modules and hoping they stay consistent. API clients see inconsistent
   behaviour only when one copy is edited and another is forgotten.
2. **"Do two outputs match?"** Judging a Submission compares a Student's output
   with a Test Case's expected output after dropping trailing spaces on each
   line and trailing blank lines. AI generation re-implements the same
   normalisation when it checks that a reference solution agrees with its
   sample output, and a docstring is the only thing that says it must mirror
   the judging rule. If one changes, a Draft can pass grounding and then fail
   every Student, or the reverse. The Instructor finds out only after
   publishing.
3. **System status.** The Admin status page (A-01) reports whether AI
   generation, the code sandbox and email delivery are available. ADR-0007 says
   each service is "checked through its adapter". Today the composition root
   decides by testing whether each adapter is a stub, and the health checker
   receives three booleans. The adapter that knows whether it is working is
   never asked. Swapping an adapter silently changes what the status page says,
   and the status page cannot show a real failure of an adapter that is
   configured but broken.

## Solution

1. Every JSON route in every slice produces its error responses through one
   shared error-to-response module. Each slice supplies its own table of
   domain error to status and code; the response shape and the mechanism are
   written once. No client-visible status code, error code or message changes.
2. One output-comparison rule, owned by the submissions module, is used by
   both judging and AI grounding. The mirrored copy in AI generation is
   deleted.
3. Each adapter behind a port that the status page reports on answers "are
   you available?" through that port. The composition root passes adapters to
   the health checker and no longer inspects their types.

All three are refactors: an Admin, Instructor, Student or API client sees the
same behaviour. The one exception is item 3, where the status page can now
report an adapter that is configured but failing; the exact failure behaviour
is a decision recorded below.

## User Stories

1. As an API client, I want every JSON error to have the same `code` and
   `message` shape, so that I write one error handler instead of one per slice.
2. As an API client, I want an unknown Assignment, Classroom or Submission to
   return the same status and body on every route, so that I do not special-case
   a slice.
3. As an API client, I want a rule violation to return 422 with a readable
   message on every slice, so that I can show it to the user.
4. As an API client, I want an error response I already depend on to stay
   byte-identical after this change, so that my integration does not break.
5. As a Student, I want a Classroom I am not a Member of to return the same 404
   as a missing one, so that Classrooms stay private (ADR-0007 section 9.6).
6. As an Instructor, I want an Assignment that is not mine to return the same
   response as one that does not exist, so that I cannot probe for other
   Instructors' work.
7. As an Admin, I want admin routes to return errors in the same shape as every
   other slice, so that the admin client reuses the same handling.
8. As a maintainer, I want one place to add an error code, so that I do not
   edit five route modules.
9. As a maintainer, I want one place to change the error response shape, so
   that a change cannot be half applied.
10. As a maintainer adding a new slice, I want to register my error table and
    get the response mapping for free, so that I do not copy a helper from the
    reference slice.
11. As an Instructor, I want a Draft that passes AI grounding to also pass the
    judge on its own sample Test Cases, so that I do not publish an Assignment
    whose reference solution fails.
12. As an Instructor, I want the AI to reject a Draft whose reference output
    does not match its sample output by the same rule the judge uses, so that
    grounding and judging cannot disagree.
13. As a Student, I want trailing spaces and trailing blank lines in my output
    to be ignored the same way everywhere, so that I am not marked wrong for
    whitespace the Instructor's own check ignored.
14. As a Student, I want a Run and a Submission to compare output by the same
    rule, so that a Run that passes the samples does not fail on Submit for a
    comparison reason.
15. As a maintainer, I want one definition of "outputs match", so that changing
    it (for example to ignore case) is one edit with one test.
16. As a maintainer, I want the rule to be a pure function with no dependency on
    the runner or the repository, so that I test it with strings only.
17. As an Admin, I want the system status page to reflect what each adapter
    reports, so that I can trust a green or red status.
18. As an Admin, I want the code execution sandbox shown as unavailable when the
    production runner is a stub, so that I know Run and Submit execute nothing.
19. As an Admin, I want email delivery shown as unavailable when the sender is
    a stub, so that I know sign-up links will not arrive.
20. As an Admin, I want AI generation shown as available only when the
    generation client can actually be used, so that the status matches what an
    Instructor sees when they click Generate.
21. As an Admin, I want the status page to keep refreshing every 60 seconds and
    keep its six rows, so that A-01 is unchanged to look at.
22. As a Student, I want the banner on every Student page while a core service
    is down to behave as before, so that the status change does not alter
    pages I use.
23. As a maintainer, I want to swap Judge0 for another code runner by changing
    only the composition root, so that the status page follows without another
    edit.
24. As a maintainer, I want the demo mode on the in-memory fakes to report
    status through the same mechanism, so that demo and production do not
    diverge.
25. As a maintainer, I want the integration tests that build the whole app to
    share one recipe, so that adding a slice does not mean editing five
    near-identical test setups.
26. As an agent working a ticket, I want the three rules to each have a single
    owner module named in this spec, so that I do not re-introduce a copy.

## Implementation Decisions

### 1. Shared HTTP error mapping

- A new shared module under `core/` owns turning a domain error into an HTTP
  error response. It is the only place that builds the `{code, message}`
  detail.
- Its interface is a registration of "this slice's errors map to these
  statuses and codes" plus a way for a route to run a use case under that
  mapping. The existing per-slice error classes stay in their slices; the
  shared module does not import any slice.
- Each of the five JSON route modules is migrated to the shared module in one
  change; there is no period where both exist (clean cutover, no shim).
- Behaviour is preserved exactly. Before migrating a slice, capture its current
  statuses, codes and messages in tests (see Testing Decisions); the migration
  must leave those tests unchanged and green.
- The submissions slice's mapping of the "not visible" errors to 404 is kept.
  The assignments slice's mapping of its own errors to 404 and of rule
  violations to 422 is kept. Where the two maps overlap, the shared module must
  not change either slice's result.
- Page handlers (HTML) are not part of this change; they redirect or render and
  do not use the JSON error shape.

### 2. One output-comparison rule

- The rule "equal after dropping trailing spaces on each line and trailing
  blank lines" has exactly one definition, owned by the submissions module.
- Judging uses it directly. AI generation's grounding check calls it instead of
  re-implementing the normalisation. This is an allowed import direction: `ai`
  may import `core` domain code (AGENTS.md, Layering), and the AI module already
  imports the submissions port and model types.
- The mirrored normalisation helper and the docstring that asks it to mirror the
  judge are deleted.
- Because grounding stores the normalised form as the sample's expected output,
  the shared module exposes both the comparison and the normalised form so that
  the stored value and the compared value come from the same code.
- No new Protocol is introduced. The rule is a pure function, and a Protocol
  with one implementation is a hypothetical seam.

### 3. Adapter-reported health

- Each port that the status page reports on (generation client, code runner,
  email sender) gains a way to answer whether that adapter is available. The
  stub adapters answer "not available" and the real adapters answer from their
  own configuration. Ports are defined where they are used, as today.
- The composition root hands the adapters to the health checker and does not
  inspect adapter types.
- The generation row keeps its current meaning: available when an AI provider
  key is configured or when a non-stub generation client was injected. Moving
  that condition behind the adapter is the point of the change; the observable
  status for each existing configuration must stay the same.
- Decision for the owner to confirm at implementation time: whether "available"
  means "configured" (today's meaning, no network call) or "reachable" (a
  probe). Default is **configured**, so the 60-second page refresh makes no
  outbound calls. A reachability probe is a separate change.
- **Ownership.** The system health checker lives in `database/`, which is
  owned by พาย (AGENTS.md, "Who may change database and locked files"). The
  health-checker part of this change is an OPS task for พาย; the port changes
  and composition-root change are outside `database/` but must land with it.
- The six-row layout of A-01, the 60-second refresh and the Student banner
  (ADR-0007 section 8) are unchanged.

### 4. Shared recipe for tests that build the whole app

- The five database test modules that each assemble the app by hand reuse one
  shared builder, with per-test overrides for the adapters under test.
  This is test-only and ships no production change.

## Testing Decisions

A good test here exercises external behaviour through a seam: a request in, a
response out; two strings in, a verdict out. It does not assert on helper
names, call counts or which module did the mapping.

Two seams, both already used by the codebase:

- **HTTP through `build_app`** (the in-memory fakes app builder): covers items
  1 and 3 and the Admin status page. Tests send requests and assert status,
  `code` and `message`; for health, they inject a stub or a real-shaped fake
  adapter and assert what A-01 renders.
- **The shared output-comparison function plus the `GenerationClient`
  grounding path**: covers item 2. One unit test on the function (trailing
  spaces, trailing blank lines, internal whitespace preserved, empty versus
  empty). One test on grounding showing that a reference output that the judge
  would accept is accepted by grounding, and one the judge would reject is
  rejected. These two tests together are what stops the rules drifting.

Steps:

1. **Characterise first.** For each of the five JSON slices, write tests that
   pin today's error statuses, codes and messages through `build_app`. Run them
   green on the current code, then migrate. A migration that needs a test
   changed is a behaviour change and is out of scope.
2. **Item 2 is red-green:** add the grounding agreement test, watch it pass on
   the current mirrored code, then delete the mirror and keep it green. Add the
   unit test on the comparison function.
3. **Item 3:** tests for each existing configuration (stub runner, real
   runner, stub sender, real sender, provider key present or absent, injected
   non-stub generation client) assert the same status rows as today.

Prior art: the status page has unit coverage and a database-backed test already
(system health unit test and the Postgres system health test); route tests use
`build_app` throughout; adapter contract tests live in the contracts folder;
the import-direction guards live under the architecture tests and must stay
green (core never imports `ai`, `integrations`, or an ORM).

## Out of Scope

- **T-05 edit rules into the edit use case.** Checked and not a defect. The
  JSON route's request schema already rejects an empty test-case input or
  expected output, and its extension argument is an explicit posting-to-date
  map, so the form's "drop a blank new row" and "every ticked line needs a
  date" rules are form parsing that has no JSON equivalent. Leave them in the
  page module.
- **Cross-slice reads from page handlers.** Checked and not a defect. Pages call
  other slices' services through the application state, authorisation stays in
  the service (ADR-0007 sections 9.4 and 9.5), and AGENTS.md documents pulling
  services off the state. Not changed.
- The keyword-only `None` defaults of the app factory (ADR-0007 decided them).
- The Submission lease and recovery split between service and repository
  (ADR-0011).
- The production code runner being a stub (ADR-0007 records it as open).
- A reachability probe for adapters; item 3 keeps "configured" semantics.
- Any HTML page, template or CSS change.
- Any schema or migration change.

## Further Notes

- Evidence for each item was read in the code on 2026-11-04: the five error
  helpers in the JSON route modules; `outputs_match` in the submissions service
  and its mirror in the AI client; the `isinstance` checks that feed the health
  checker in the composition root.
- Recommended order: item 2 (smallest, no ADR in the way), then item 1, then
  item 3 once พาย has an OPS task for the health checker.
- Item 4 is test-only and can ride along with item 3 since it touches the same
  tests.
- `docs/task-scope.md` needs a row per item before work starts, with the
  database health-checker row owned by พาย. This spec does not add those rows.
