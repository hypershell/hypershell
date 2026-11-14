---
status: unshaped
kind: fix
appetite: small
lane: public
---

# `hs update` misreports or skips rows: contended on a TimescaleDB hypertable, and when it combines changes

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Verified against `develop` at `6da633d` (2.9.0a8); line numbers are that commit's
(`src/hypershell/task.py`).

**Every `hs update` write treats a 0-row match as proof the row is gone.** Under the `timescale`
provider, `task` is a hypertable, and on TimescaleDB 2.29 and later an UPDATE that waited on another
writer's row lock matches 0 rows once that writer commits, although the row exists and still
qualifies (AGENTS.md § Data layer specifics; invariants §1). The server's writes re-run in that case
(`hypertable-update-retry`, `relay-hardening` #103, and `contended-writes` for the `retried` mark and
`Task.cancel_all`). `hs update`'s do not:

- **The legacy single-row form** (`update_legacy`, `:1070-1086`, through `Task.update` →
  `Entity.update_all`, a `bulk_update_mappings`) turns a contended row's `StaleDataError` into
  `Task.NotFound`: a **false "not found"**, exit 5.
- **`--limit`** (`:1152-1177`) selects the rows, then writes them by id in batches of 100, each
  committed by its own `update_all`; a contended row raises after earlier batches committed — a
  **partial update and a traceback** (exit 5, through the shared `Exception` handler). The by-id
  write never re-applies the user's filter, so a row that stopped matching between the select and
  the write is overwritten anyway (e.g. `hs update --limit 10 -R --cancel` can cancel a task that
  completed meanwhile, discarding its result).
- **Without `--limit`** (`:1179-1216`), set, `--cancel`, `--revert`, tag and `--remove-tag` edits are
  filtered bulk UPDATEs (`Query.update`) whose matched counts are never read: contended rows are
  **silently skipped**, and "Updated N tasks" reports the count taken before the write (`:1101`).
  `--revert` inlines its own column list (the same columns as `Task.revert_columns`).
- **`--delete`** (`query.delete()`, `:1118`; `Task.delete_all` per batch with `--limit`, `:1160`) is
  not probed: upstream TimescaleDB #9315 moved hypertable DELETE onto the same expansion as UPDATE,
  so it may miss rows the same way.

**Two defects need no contention** (reproduced on SQLite by driving the CLI with
`.agents/factory/bin/temp_site.sh` on four submitted tasks, 2026-11-14):

- With `--limit`, every kind of change shares one iterator over the selected rows (`:1157`), so only
  the first kind is applied: `hs update --limit 2 -f group=7 color:red` sets `group` on two rows,
  never the tag, and logs "Updated 2 tasks".
- Without `--limit`, a field change and a tag change are separate UPDATEs that each re-apply the
  filter, so a field change that takes rows out of the filter makes the tag change miss them:
  `hs update -f group=7 color:red -w group==0` sets `group` on all four rows, tags none, and logs
  "Updated 4 tasks".

**Pre-existing**: the hypertable miss since the `timescale` provider landed (#71); the two
non-contention defects in the `hs update` code as it stands, not introduced by a recent commit.

## Why it was deferred

Shaped as R4–R7 of the first `contended-writes` GOAL draft; the foreman split it out at that GOAL
gate (2026-11-14) to keep `contended-writes` to the server half (the `retried` mark, `Task.cancel_all`)
and the reserved source row, and scheduled it right after that cycle, within the pre-2.9.0 list.

## Outcome / vision

`hs update` applies every requested change to exactly the rows its filter selects at the time of the
write, on every supported database including a contended TimescaleDB hypertable, reports the count it
wrote, and says plainly when it could not write.

## Proposed direction (from the `contended-writes` draft; for sign-off at this seed's GOAL gate)

- **All-or-nothing at the re-run limit.** Either "Updated N" and exit 0, or an error naming the
  contended task(s), nothing written, and exit 5, so rerunning the same command is safe. The
  filtered path is already one transaction today; `--limit`'s per-batch commits are one of the
  defects. (Alternative: commit what landed, report it, exit 5.)
- **`--limit` re-checks the user's filter** at write time, on the first attempt as on a re-run; a
  row that no longer matches is left as it is and not counted. A user-visible change.
- **Take the two non-contention defects** in the same cycle: "count the rows actually written"
  cannot hold while a requested change is silently dropped.
- **Include `--delete`.** If a real-race test on CI's TimescaleDB 2.31 shows a contended DELETE does
  not miss, the test stays as the guard and the accurate "Deleted N" count is the only change.

## Sketch of the acceptance criteria

- **R1** — WHEN the legacy `hs update ID FIELD VALUE` form contends with another writer, it SHALL
  write the row if it still exists and report "not found" only for a row that is missing; IF the row
  still exists but is not written after the re-runs, THEN it SHALL write nothing, say the update was
  contended, and exit with the runtime-error status (5).
- **R2** — WHEN `hs update` (with or without `--limit`; set, `--cancel`, `--revert`, tag,
  `--remove-tag` or `--delete`) contends with another writer, every row its filter still selects at
  the time of the write SHALL be written (or deleted), a row that no longer matches SHALL be left as
  it is and not counted, and "Updated N" / "Deleted N" SHALL count the rows actually written or
  deleted, saying how many no longer matched when that is fewer than the count confirmed at the
  prompt (exit 0).
- **R3** — IF a row the filter still selects is not written after the re-runs, THEN `hs update` SHALL
  write nothing, log an error naming the task(s), and exit 5, never with a traceback or a partial
  update on that account.
- **R4** — WHEN `hs update` combines a field change (including `--cancel` or `--revert`) with tag
  changes or `--remove-tag`, every row it reports as updated SHALL carry all of them, with or without
  `--limit`, including when the field change takes the row out of the filter.
- **R5** — Tests SHALL force each race deterministically and fail before the fix: fault injection on
  SQLite (as `tests/test_contended_recovery.py`) for R1–R3, the real race on a server database (as
  `tests/test_contended_write.py`) for each form including `--delete`, red on CI's TimescaleDB 2.31
  jobs at a tests-first commit, and R4 by the CLI.
- **R6** — The user-visible changes SHALL be documented on `docs/cli/task_update.rst` (and its
  `docs/_include/task_update_*.rst` snippets where they change) with `.. versionchanged:: 2.9.0`, and
  AGENTS.md / invariants §1 SHALL drop `hs update` from the exceptions to the 0-row-match rule.

## Notes

- The local TimescaleDB 2.32 does not reproduce the 0-row miss; CI's 2.31.0 jobs do. Evidence needs
  fault injection plus a tests-first commit pushed to a draft PR for CI's red.
- Not in scope as drafted: the legacy tag edit's read-modify-write (`hs update ID tag VALUE` reads the
  tag, then writes the merge; a concurrent tag write in between is lost, with or without
  TimescaleDB), and a reset `retried` flag (AGENTS.md § Task lifecycle, an unguarded doubled retry).
- Pattern and harness: [`spec/hypertable-update-retry/`](../spec/hypertable-update-retry/PLAN.md),
  [`spec/relay-hardening/`](../spec/relay-hardening/GOAL.md) (`__write_rows`, `__row_statements`),
  [`spec/contended-writes/`](../spec/contended-writes/GOAL.md) (the server half, landing first).
- Found by: `hypertable-contended-scheduler-and-cli-writes` (the `hypertable-update-retry` audit,
  2026-11-04) and the `contended-writes` shaping (the two non-contention defects, 2026-11-14).
