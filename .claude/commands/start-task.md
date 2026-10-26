---
description: Pick one of my assigned tasks, branch for it, then interview me before any code is written.
argument-hint: [owner name, e.g. พาย] [--maintainer]
---

Owner for this session: $1

Do not write or edit application code during this command. Stop after the plan.

## Step 1 — Preconditions

Run `git status --porcelain` and `git branch --show-current`.

- If the working tree is dirty: stop. Show what is uncommitted and let them
  deal with it. Do not stash, commit, or discard anything.
- If `$1` is empty: ask whose tasks to list. Do not guess from git config.

## Step 2 — Offer only their tasks

Run `uv run python -m scripts.task_scope $1`. Add `--maintainer` when พาย asks
for it. The script reads the Status column of `docs/task-scope.md`; that column
is the only thing that decides what is offered. Do not look at branches to
decide a task's state.

It prints, in order:

- `open` rows owned by `$1`, and `open` rows owned by `TBD` (tagged
  "ยังไม่มีเจ้าของ รับได้");
- `in-progress` rows owned by `$1` only. Rows owned by someone else are not
  shown;
- `done` rows and the `OPS-*` / `BUG-*` pattern rows are never shown;
- a row whose "May touch" path is missing from the repo, or whose text uses
  wording from the retired two-service design, is hidden. Only พาย (or
  `--maintainer`) sees it, under "งานที่ต้องอัปเดต". A path the task must
  create is written `creates:` in the row and does not count as missing.

If the script exits with an error (an unknown Status, a row with no Status, a
malformed table), stop and show the message. Do not guess a Status.

Show the printed list as it is. If it is empty, say so and stop.

Ask which one. Wait. Accept only a number or a TASK-ID from that list — if they
name a task that is not in it, say it is not offered to them and stop. If it
appears under "งานที่ต้องอัปเดต", tell them it needs พาย to update the row first.

When they pick an `in-progress` task, look for a branch named
`<type>/<TASK-ID>-<slug>` with `git branch -a`. If one exists, switch to it (make
a local tracking branch if it is only on the remote), skip Step 3 and continue
at Step 4. If none exists, continue at Step 3.

## Step 3 — Branch

Skip this step when Step 2 already checked out the task's branch.

Confirm the type prefix with them (`feat`, `fix`, `chore`, `docs`, `refactor`),
propose a slug from the task title, and show the full branch name for approval.

Once approved:

```
git switch dev
git pull
git switch -c <type>/<TASK-ID>-<slug>
```

If `git pull` fails or `dev` is behind, stop and report — do not branch from a
stale `dev`.

Stacked task: when the row depends on a branch that is not merged yet, do not
run the commands above. Run `git fetch origin`, then
`git switch -c <type>/<TASK-ID>-<slug> origin/<dependency-branch>`. Later,
update with `git fetch origin && git rebase origin/<dependency-branch>`; never
`git pull origin dev` on a stacked branch (AGENTS.md "Stacked branches").

## Step 4 — Interview

Now follow `.claude/commands/grill-me.md` from its Step 2 onward, using the task
selected above. Do not repeat its Step 1; the task is already known.

Its push-back rules apply in full. Being the tech lead is not an exemption —
if this task needs a schema change, an alembic run, or a contract edit, say so
out loud and make it an explicit decision rather than a quiet one.

## Step 5 — Stop

Emit the plan block from `grill-me.md` Step 4, then wait. Do not start
implementing until told to.
