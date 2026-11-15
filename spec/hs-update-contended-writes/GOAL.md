# GOAL — `hs update` writes every requested change to exactly the rows it counts, contended or not, and says plainly when it cannot

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** hs-update-contended-writes
- **kind:** fix
- **appetite:** big  ·  *Every `hs update` write path changes (the legacy form, `--limit`'s by-id
  batches, the filtered bulk UPDATEs, `--delete` in both forms), and so do its printed counts and
  exit status. Three questions need research before a design: how a filtered, set-based UPDATE
  tells a contended miss from a row that no longer matches; whether a contended DELETE misses on
  TimescaleDB 2.31 at all; and how the user's arbitrary filter becomes a per-row write condition
  where `__write_rows` knows only fixed ones. The seed's `small` does not cover that (see
  Clarifications).*

## Problem

Verified against `develop` at `fbc656e` (2.9.0a8 plus #109 `contended-writes`); line numbers are
that commit's (`src/hypershell/task.py`, `TaskUpdateApp`). They match the seed's, taken at
`6da633d`: `task.py` has not changed between the two.

**Every `hs update` write treats a 0-row match as proof the row is gone.** Under the `timescale`
provider, `task` is a hypertable, and on TimescaleDB 2.29 and later an UPDATE that waited on another
writer's row lock can match 0 rows once that writer commits, although the row exists and still
qualifies (AGENTS.md § Data layer specifics; invariants §1). CI's TimescaleDB 2.31.0 jobs reproduce
it; the local TimescaleDB 2.32 does not. Since #109 every server write re-runs in that case, and
invariants §1 and AGENTS.md now name `hs update` as the one remaining exception. Its writes do not:

- **The legacy single-row form** (`hs update ID FIELD VALUE`, `update_legacy`, `:1070-1086`) writes
  through `Task.update` → `Entity.update_all` (`data/model.py:246-256`), a `bulk_update_mappings`,
  and turns the `StaleDataError` of a 0-row match into `Task.NotFound`: a contended row that exists
  is reported **not found**, exit 5.
- **`--limit`** (`:1152-1177`) selects the rows, then writes them by id in batches of 100
  (`UPDATE_BATCH_SIZE`), each batch committed by its own `update_all`. A contended row raises
  `StaleDataError` after earlier batches committed; `TaskUpdateApp.exceptions` maps only
  `Task.NotFound`, so the shared `Exception` handler writes a traceback file and exits 5: a
  **partial update and a traceback**. The by-id write never re-applies the user's filter, so a row
  that stopped matching between the select and the write is overwritten anyway (e.g.
  `hs update --limit 10 -R --cancel` can cancel a task that completed meanwhile, discarding its
  result).
- **Without `--limit`** (`:1179-1216`), field, `--cancel`, `--revert`, tag and `--remove-tag` changes
  are filtered bulk UPDATEs (`Query.update`) whose matched counts are never read, all in one
  transaction: contended rows are **silently skipped**, and "Updated N tasks" (`:1216`) reports the
  count taken before the write (`:1101`). `--revert` inlines its own column list (`:1137-1150`, the
  same thirteen columns as `Task.revert_columns`).
- **`--delete`** (`query.delete()`, `:1118`; `Task.delete_all` per batch with `--limit`, `:1160`,
  each batch its own commit) is not probed: upstream TimescaleDB #9315 moved hypertable DELETE onto
  the same expansion as UPDATE, so it may miss rows the same way. "Deleted N tasks" is the count
  taken before the write.

**Two defects need no contention.** Reproduced on SQLite at `fbc656e` by driving the CLI with
`.agents/factory/bin/temp_site.sh` on four submitted tasks (2026-11-15):

- With `--limit`, every kind of change shares one iterator over the selected rows (`tasks_it`,
  `:1157`), so only the first kind is applied: `hs update --limit 2 -f group=7 color:red` set
  `group` on two rows, never the tag, and logged "Updated 2 tasks".
- Without `--limit`, a field change and a tag change are separate UPDATEs that each re-apply the
  filter, so a field change that takes rows out of the filter makes the tag change miss them:
  `hs update -f group=7 color:blue -w group==0` set `group` on all four rows, tagged none, and logged
  "Updated 4 tasks"; `hs update -f -R --cancel color:green` cancelled all four, tagged none, and
  logged "Updated 4 tasks".
- The same mechanism, by reading the code (not driven): on PostgreSQL each tag key and each
  `--remove-tag` name is its own filtered UPDATE (`:1195-1198`, `:1212-1213`; SQLite merges them
  into one), so `hs update -t color:red color:blue size:big` should set `color` and then miss `size`
  on every row, since the first UPDATE took the rows out of `-t color:red`.

**Pre-existing**: the hypertable miss since the `timescale` provider landed (#71); the
non-contention defects in the `hs update` code as it stands, not introduced by a recent commit.

## Outcome / vision

`hs update` applies every requested change to exactly the rows its filter selects at the time of the
write, on every supported database including a contended TimescaleDB hypertable; reports the number
of rows it actually wrote or deleted; and either finishes the whole update or writes nothing and
says the update was contended, so rerunning the same command is always safe. After this cycle
AGENTS.md and invariants §1 list no exception to the 0-row-match rule.

### User-visible changes (the release note)

Top-level `hs update` only; the confirmation prompt ("Update affects M tasks, continue?"), the
"Update affects 0 tasks - stopping" exit, and `--cancel`'s "already scheduled" warning are unchanged.

1. **Combined changes all land.** `hs update --limit N` with more than one kind of change (a field
   assignment, `--cancel`, `--revert`, tag `KEY:VALUE`, `--remove-tag`) applies all of them; before,
   only the first kind was applied. Without `--limit`, tag changes and `--remove-tag` now reach rows
   that a field change, `--cancel` or `--revert` moved out of the filter; before, those rows got the
   field change only.
2. **`--limit` re-checks the filter at the time of the write.** A selected task that another process
   changed so that it no longer matches is left as it is and not counted; before, it was
   overwritten (a `--cancel` could discard a result that had landed meanwhile).
3. **Counts are of rows written.** "Updated N tasks" and "Deleted N tasks" count the tasks actually
   written or deleted, not the count taken at the prompt. When N is lower than that count, a
   warning line says how many tasks no longer matched and were left as they are; the exit status
   is 0.
4. **A contended update writes nothing and says so.** If a task the filter still selects cannot be
   written after the re-runs (another writer holding it throughout), `hs update` writes nothing at
   all — with or without `--limit`, across every batch — logs an error naming the task and saying
   the update was contended and nothing was written, and exits 5, with no traceback file. Before:
   with `--limit`, a partial update, a traceback file and exit 5; without `--limit`, contended rows
   silently skipped, a wrong count and exit 0.
5. **Contended rows are written.** On a TimescaleDB hypertable, a task held by another writer that
   still matches once the writer commits is now written and counted; before, it was skipped (filtered
   form), raised mid-update (`--limit`), or reported not found (legacy form).
6. **The legacy form reports "not found" only for a missing task.** `hs update ID FIELD VALUE` on a
   task another writer holds now writes it and exits 0 as an uncontended update does; "not found"
   (exit 5) is reserved for an `ID` with no row, and the contended case of item 4 exits 5 with the
   contended error instead.

## Acceptance criteria (the contract)

Throughout, "contended" means the write waited on another writer's row lock; "written" includes
deleted for `--delete`; "the count" is the number `hs update` counted before its prompt (shown as
"Update affects M tasks", also taken under `-f`/`--no-confirm`).

- **R1** — WHEN the legacy `hs update ID FIELD VALUE` form (any field, including `tag`) is contended
  and the task still exists once the other writer commits, `hs update` SHALL write it and exit 0 as
  for an uncontended update; it SHALL report the task not found, exit 5, only WHEN no row has that
  `ID`; IF the task still exists but is not written after the re-runs, THEN it SHALL write nothing,
  log the contended-update error of R4, and exit 5.
- **R2** — WHEN `hs update` writes by filter — with or without `--limit`; a field assignment,
  `--cancel`, `--revert`, tag changes, `--remove-tag`, or `--delete` — it SHALL write every task
  that matches its filter at the time of the write (with `--limit`, every selected task that still
  matches), including a task that was contended and still matches once the other writer commits,
  and SHALL leave as it is, unwritten, a task counted or selected that no longer matches at that
  time.
- **R3** — WHEN `hs update` finishes, "Updated N tasks" / "Deleted N tasks" SHALL give N as the
  number of tasks actually written or deleted, and WHEN N is lower than the count, a warning SHALL
  say how many tasks no longer matched and were left as they are; the exit status SHALL be 0 in
  both cases.
- **R4** — IF a task its filter still selects is not written after the re-runs, THEN `hs update`
  SHALL write nothing (no task changed or deleted, with or without `--limit`, whatever the number
  of rows), log an error that names the task and says the update was contended and nothing was
  written, and exit with the runtime-error status (5), never with a traceback file and never after a
  partial update.
- **R5** — WHEN `hs update` combines changes — a field assignment, `--cancel` or `--revert` with tag
  changes or `--remove-tag`, or several tag keys or `--remove-tag` names — every task it counts as
  updated SHALL carry all of them, with or without `--limit`, on SQLite and PostgreSQL, including
  WHEN one change takes the task out of the filter (`hs update --limit 2 -f group=7 color:red`;
  `hs update -f group=7 color:blue -w group==0`; `hs update -f -R --cancel color:green`).
- **R6** — Tests SHALL force each race deterministically, not by timing luck, and fail before the
  fix: fault injection on SQLite (as `DriverContention` in `tests/test_contended_recovery.py`) for
  R1–R4, including the exhausted re-run and a row changed out of the filter between the select and
  the write; the real race on a server database (as `contend` in `tests/test_contended_write.py`)
  for the legacy form, the filtered form, `--limit` and `--delete`, red on CI's TimescaleDB 2.31
  jobs at a tests-first commit (or, for `--delete`, kept as the guard if it does not miss there);
  and R5 by driving the CLI on SQLite and PostgreSQL.
- **R7** — The user-visible changes SHALL be documented on `docs/cli/task_update.rst` (and its
  `docs/_include/task_update_*.rst` snippets where they change) with `.. versionchanged:: 2.9.0`,
  and AGENTS.md (§ Data layer specifics) and `.agents/factory/invariants.md` §1 SHALL, in the same
  change, drop `hs update` from the exceptions to the 0-row-match rule, adding it to the pattern
  list.

## Non-goals (no-gos)

- **No change to what a change overwrites** beyond R2's write-time filter: `--cancel` still cancels
  any matching task whatever its state, `--revert` still clears the same columns, and the
  "already scheduled" warning is still counted before the write.
- **No new options or argument rules.** Not the confirmation prompt, not the 0-count stop, not
  `--order-by`/`--limit` semantics at selection. Field assignments given with `--delete` are
  silently ignored today in both forms (observed 2026-11-15, `--delete --limit 1 group=9`); they
  stay so (a candidate seed, not this cycle).
- **Not the legacy tag edit's read-modify-write** (`hs update ID tag KEY:VALUE` reads the tag, then
  writes the merge; a concurrent tag write in between is lost, with or without TimescaleDB).
- **Not a reset `retried` flag** from `hs update` (AGENTS.md § Task lifecycle, the unguarded doubled
  retry stays as listed).
- **No server write path change**: everything #109 and its predecessors put on the pattern stays as
  it is; `Task.cancel_all`'s API is unchanged. No change to `rotatedb`, `hs list`/search, or the
  relay store.
- **No CI image change.** The TimescaleDB 2.31.0 pins in `.github/workflows/tests.yml` stay, since
  they carry R6's red; whether 2.32 fixed the miss upstream is not investigated, and nothing is
  reported upstream from this cycle.
- **Not two servers on one database** (unsupported), and **no 2.8-line backport** (the `timescale`
  provider is 2.9 only).

## Clarifications

- **Q:** Starting directions? — **A:** The seed's four proposed directions are accepted as this
  cycle's starting point: (a) all-or-nothing at the re-run limit (nothing written, a
  contended-update error, exit 5, no traceback); (b) `--limit`'s by-id write re-checks the user's
  filter at write time; (c) take the two non-contention defects; (d) include `--delete`. Also: "not
  found" only for a truly missing row; "Updated N"/"Deleted N" count rows actually written, with a
  "no longer matched" line when lower, exit 0 (foreman's brief, 2026-11-15).
- **Q:** Appetite? — **A:** `big`, rounded up from the seed's `small` (shaping, 2026-11-15; see the
  header). The `contended-writes` GOAL gate already judged this half `big` when it sized the
  undivided draft ("sized for `hs update`'s five write paths and output changes"); its three open
  design questions are research, which `small` caps.
- **Q:** How is the red shown when the local TimescaleDB 2.32 does not reproduce the miss? —
  **A:** As for `contended-writes`: fault injection on SQLite, deterministic everywhere, plus the
  real-race tests on CI's TimescaleDB 2.31 jobs, red at a tests-first commit the foreman pushes to a
  draft PR (Evidence plan); locally the real-race tests pass before and after the fix on PostgreSQL
  18 and TimescaleDB 2.32 (foreman's brief, 2026-11-15).

**Proposed by the shaper, awaiting the foreman's sign-off at the GOAL gate** (2026-11-15):

- **Q:** Without `--limit`, what of a task that comes to match the filter after the count (a new
  submission matching `-R`, say)? — **A:** The filter at the time of the write decides,
  as today, so it is written and counted. R3's warning then reports the net shortfall (count − N)
  only when N is lower; with `--limit` the written set is always within the selected ids, so the
  figure is exact. The alternative — bound the write to the tasks counted — means reading every id
  before writing, which a set-based update avoids.
- **Q:** Level and wording of R3's "no longer matched" line? — **A:** A WARNING (visible
  at the default level, as `--cancel`'s "already scheduled" warning is), along the lines of "K of
  the M tasks counted no longer matched and were left as they are"; exact wording is the plan's.
- **Q:** Is the PostgreSQL multi-key variant (several tag keys or `--remove-tag` names, one moving
  the task out of a tag filter) in scope? — **A:** Yes, in R5. It is the same mechanism
  as the seed's second defect (each change re-applies a filter an earlier change altered), found by
  reading `:1195-1198` and `:1212-1213`; `hs-plan` confirms it on the local PostgreSQL, and if it
  does not reproduce R5's clause for it is met by a passing test.
- **Q:** Cost of an uncontended update? — **A:** Without `--limit`, an uncontended
  `hs update` stays set-based: its statement count does not grow with the number of tasks it writes
  (a test pins it, as the `contended-writes` tests pin one UPDATE per row for the server paths).
  With `--limit`, per-row writes are acceptable (the batches already read every selected row).
- **Q:** R4 with `--limit` makes the whole update one transaction where today each batch of 100
  commits; is the longer lock acceptable? — **A:** Yes. It follows from direction (a),
  and the filtered form already holds every matched row's lock until its one commit; a running
  server's writes to those tasks wait for it as they do today. `hs-plan` notes the cost for a large
  `--limit`.

**Ruled at the GOAL gate** (2026-11-15): approved by the foreman, gate judge under the maintainer's
mandate. Appetite big, and all five proposed answers confirmed:
- tasks that begin matching after the count are written and counted as today;
- the "no longer matched" line is a WARNING;
- the PostgreSQL multi-key variant is in R5's scope, which `hs-plan` confirms by driving it;
- an uncontended update without `--limit` stays set-based, with a test pinning that, and per-row
  writes are accepted with `--limit`;
- `--limit` becomes one transaction, with the lock cost noted in the plan.

The contract is locked; `hs-plan` designs against it.

## Evidence plan

- **Tests-first commit, CI red**: `hs-build` lands R6's tests before the fix in a commit of their
  own; the foreman pushes the branch at that commit to a draft PR, where the real-race additions
  fail on the `timescale` and `timescale-oss` jobs (TimescaleDB 2.31.0) and the fault-injection and
  CLI tests fail on every job. A real-race `--delete` test green there is recorded as the finding
  that DELETE does not miss, and stays as the guard. An external wait the foreman schedules;
  `hs-plan` makes it its own phase.
- **CI green**: the same jobs green on the fix; `timescale-floor` (2.24) and the plain PostgreSQL
  jobs green throughout for the real-race tests.
- **Unit and integration, laptop**: the full suite on SQLite; `tests/test_contended_write.py`,
  `tests/test_contended_recovery.py` and the `hs update` tests on the local PostgreSQL 18 and
  TimescaleDB 2.32 (`HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres`,
  `HS_TEST_TIMESCALE=community` or empty, sandbox off), where the real-race tests pass before and
  after the fix; each red line shown red on the base `fbc656e` for the defect
  (`spec/relay-hardening/drive/red_check.sh`).
- **Laptop drives** (`.agents/factory/bin/temp_site.sh`, SQLite; and the local PostgreSQL): the
  three reproductions above before and after (every row tagged; "Updated N" equal to the rows
  written); a `--limit` update with a task completed between the prompt and the write (left as it
  is, the warning shown, exit 0).
- **Cluster**: none needed.

## Related materials

- Seed: [`issues/hs-update-contended-writes.md`](../../issues/hs-update-contended-writes.md)
  (`status: adopted:hs-update-contended-writes`), split from
  [`spec/contended-writes/`](../contended-writes/GOAL.md) (#109) at its GOAL gate, 2026-11-14.
- Pattern: [`spec/contended-writes/`](../contended-writes/PLAN.md) (`__write_rows`'s `each`,
  `commit=False`, conditions and bind prefixes; `DriverContention`, the driver-level injector whose
  lost kinds are today only the retry mark and the cancel; the `contend` real-race harness; `red.txt`
  / `ci-red.txt` with `red_check.sh` and `drive/ci_check.py`);
  [`spec/hypertable-update-retry/`](../hypertable-update-retry/GOAL.md) (`CONTENDED_RERUNS`, the
  write-path audit); [`spec/relay-hardening/`](../relay-hardening/GOAL.md) (`__row_statements`);
  invariants §1.
- Source (likely touchpoints, to be confirmed by `hs-plan`): `src/hypershell/task.py`
  (`TaskUpdateApp.update_legacy`, `update_tasks`, `exceptions`); `src/hypershell/data/model.py`
  (`Entity.update_all`/`update`/`delete_all`, `Task.__write_rows`, `Task.__row_statements`,
  `Task.revert_columns`); `tests/test_update.py`, `tests/test_contended_write.py`,
  `tests/test_contended_recovery.py`; `docs/cli/task_update.rst`,
  `docs/_include/task_update_desc.rst`; `.github/workflows/tests.yml` (the TimescaleDB jobs).
- Upstream: TimescaleDB #9315 (hypertable DELETE on the UPDATE expansion).
