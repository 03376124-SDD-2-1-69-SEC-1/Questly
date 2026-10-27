---
status: accepted
date: 2026-10-27
task: OPS-43
---

# External sign-up and on-screen verification for filtered domains

## Context

ADR-0007 §1.1 limits sign-up to `@kmitl.ac.th` addresses and proves each
address with an emailed link. Two things now break that.

- KMITL's mail system filters the mail Questly sends. Resend reports the
  verification email as Delivered, but it never reaches the inbox, so a
  KMITL Student cannot verify and cannot log in.
- People outside KMITL want to join a Classroom as Students, and the
  `@kmitl.ac.th` rule turns them away.

## Decision

1. **No schema change.** Whether an account is KMITL or external is derived
   from its email domain. Nothing new is stored.
2. **G-03 asks "สมัครในฐานะ".** The form field `affiliation` is `kmitl`
   (the default, "บุคคลใน KMITL") or `external` ("บุคคลภายนอก").
   - `kmitl` keeps today's rule: the email ends with `@kmitl.ac.th`, or the
     page shows `not_kmitl_email`.
   - `external` takes any valid email that does not end with `@kmitl.ac.th`.
     A `@kmitl.ac.th` address gives `use_kmitl_option`. A valid email has
     exactly one `@`, a non-empty local part, a domain with a dot and no
     empty label, no whitespace, and at most 254 characters after it is
     trimmed and lower-cased. Anything else gives `invalid_email`. A
     subdomain such as `a@mail.kmitl.ac.th` does not end with `@kmitl.ac.th`,
     so it counts as external, the same way the `kmitl` rule already rejects
     it.
   - External accounts are Students only. `external` with "I am an
     instructor" ticked gives `external_cannot_request_instructor`. The
     checkbox stays visible, because the page has no JavaScript, and a hint
     under it says so. A logged-in Student whose email does not end with
     `@kmitl.ac.th` gets the same error from
     `POST /api/v1/auth/instructor-requests`, so the rule cannot be dodged
     after sign-up.
   - After an error the form shows the radio the user picked.
3. **The API follows the page.** `POST /api/v1/auth/signup` takes an
   optional `external: bool = false`. `false` behaves exactly as before. The
   new errors are 422 with the same codes.
4. **On-screen verification by domain.** The setting
   `ONSCREEN_VERIFY_DOMAINS` is a comma-separated list of domains,
   case-insensitive and empty by default. An entry that is not a domain stops
   the app at start-up. When an account's email domain is in the list
   (an exact match, so `kmitl.ac.th` does not cover `mail.kmitl.ac.th`):
   - G-04a shows the verification link as a "ยืนยันอีเมล" button with a note
     that the university's mail system filters our mail
     (`data-state="G-04a-onscreen"`). This holds in every `APP_ENV`,
     production included.
   - The email is still sent. When the send fails, the button still shows and
     the OPS-37 delivery-failed warning does not.
   - The `/verify/resend` page shows the fresh link the same way.

   A domain not in the list never gets an on-screen link. The one exception
   is unchanged from OPS-37: with the stub sender outside production, G-04a
   shows the demo link (`data-state="demo-link"`).
5. **One mechanism.** The OPS-37 demo link and the on-screen link share the
   same code. `AuthService` takes the on-screen domains and the stub flag,
   keeps a link in memory when either applies, and the page reads it through
   one method that also says which block to draw. `main.py` builds both
   values from `Settings`; `core/` does not import `integrations/`. Token
   rules do not change: a link lives 24 hours and is used once. The API never
   returns a link.

## Consequences

- A KMITL Student can sign up and verify while KMITL filters our mail, as
  long as `ONSCREEN_VERIFY_DOMAINS=kmitl.ac.th` is set.
- People outside KMITL can join Classrooms as Students. Instructors still
  come only from KMITL addresses through an Admin-approved request.
- The links are kept in process memory. After a restart, or on a second
  worker, G-04a has no link to show until the user presses "ส่งอีเมลอีกครั้ง",
  which issues a fresh one on the worker that answers.
- Landing copy says "สมัครสมาชิก" instead of naming the KMITL domain, and
  the email fields on G-01, G-03 and G-04 say "อีเมล".

## Security

For a listed domain, on-screen verification does not prove that the person
owns the mailbox. Anyone who knows a `@kmitl.ac.th` address can create a
Student account with it and verify it on the spot. The resend page also
shows a link for any unverified account in a listed domain, so it tells a
visitor whether such an account exists and lets them verify it; they still
cannot log in without its password.

Two further effects follow. An attacker can sign up as `victim@kmitl.ac.th`
with their own password, and the real owner then meets `already_registered`;
there is no password reset to take the address back. An Instructor request
from a listed domain no longer shows that the requester owns the KMITL address,
so an Admin approving one must not treat the email as proof of identity.

The damage is bounded. The account is a Student, and a Student sees only the
Classrooms it joins with a join code. Instructor rights still need an Admin
to approve the request (ADR-0007 §1.2), and an Admin can deactivate an
account.

Remove the domain from `ONSCREEN_VERIFY_DOMAINS` once KMITL delivers our
mail. Accounts already verified stay verified.
