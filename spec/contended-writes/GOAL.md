# GOAL — Contended writes: the `retried` mark and `Task.cancel_all` land when contended on a hypertable; concurrent first submits share the reserved source row

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** contended-writes
- **kind:** fix
- **appetite:** small  ·  *After the GOAL-gate split (see Clarifications): two writers moved onto
  the re-run pattern `__write_rows` already provides, one get-or-create made conflict-safe, both test
  harnesses already in the tree. The retry path is in `data/model.py` (high-blast-radius) and changes
  for every server, which the invariant gate covers; it does not need a research fan-out.*

## Problem

Verified against `develop` at `6da633d` (2.9.0a8); line numbers below are that commit's.

**A task-row UPDATE that waited on another writer's row lock can match 0 rows on a TimescaleDB
hypertable.** Under the `timescale` provider, `task` is a hypertable, and on TimescaleDB 2.29 and
later such an UPDATE matches 0 rows once the other transaction commits, although the row exists and
still qualifies; PostgreSQL, and TimescaleDB before 2.29, re-check the row and match 1 (AGENTS.md
§ Data layer specifics; invariants §1). `hypertable-update-retry` moved the server's Confirm, revert
and result writes onto the re-run pattern, and `relay-hardening` (#103, its R2) did the same for the
dispatch stamp in `Task.next` (`src/hypershell/data/model.py:793-822`, now a per-row conditional
write through `__write_rows`, `:970`). Two writers in `data/model.py` still treat a 0-row match as
proof the row is gone:

- **The Scheduler's `retried` mark.** `__schedule_next_failed_tasks` (`model.py:847-867`) commits
  the retry rows (`add_all`, `:864`), then marks the failed rows `retried`/`next_id` with
  `update_all` (`:865`), a `bulk_update_mappings` that raises `StaleDataError` on a 0-row match.
  The Scheduler thread does not catch it, so the server exits mid-run; the retry rows are already
  committed while the failed row stays unmarked, so it is retried a second time on the next
  selection or restart. The same two commits leave the doubled retry open when a process stops
  between them (the "kill between the retry insert and the `retried` mark" case AGENTS.md
  § Task lifecycle lists as unguarded). The seed names a late confirmation and an eviction revert
  as in-server contenders; since #82 (R19) and #103 (R1) both write only a row that is still
  incomplete, which a failed row is not, whether an in-server writer can still contend the mark is
  unverified here. Another process (`hs update`) can.
- **`Task.cancel_all` / `Task.cancel`** (`model.py:1165-1185`): a `bulk_update_mappings` by id that
  raises `StaleDataError` to the caller on a contended row that exists. No in-tree caller (Python
  API only).

Every `hs update` write has the same defect, and two further `hs update` defects need no contention;
all of that moved to [`issues/hs-update-contended-writes.md`](../../issues/hs-update-contended-writes.md)
at the GOAL gate (Clarifications).

**Concurrent first submits can collide on the reserved source row** (second seed). `Source.reserved`
(`model.py:1594-1606`) is a get-or-create with no conflict handling: two processes that both miss the
fixed-id `<direct>`/`<stdin>` row both insert it, and the second fails on the primary key
(`IntegrityError`, a traceback file, exit 5, its tasks not submitted). Its callers are `hs submit`
(`src/hypershell/submit.py:1480`, `:1487`, `:1511`) and `hs cluster`
(`src/hypershell/cluster/__init__.py:511`, `:538`). The seed measured 1 failure in 20 runs of six
concurrent `hs submit`s on a fresh database and 2 in 10 against a 2.8.3 database upgraded by
`schema-upgrade-step`, which starts with an empty `source` table (the table is new in 2.9's schema);
those rates were not re-measured here.

All of it is **pre-existing**: the hypertable miss since the `timescale` provider landed (#71), the
source race since the `source` table did; none was introduced by a HyperShell commit since.

## Outcome / vision

On every supported database, including a TimescaleDB hypertable of any supported version, the
Scheduler never exits because a `retried` mark was contended, a failed row is never retried twice on
that account or because the process stopped between the retry insert and the mark, and
`Task.cancel_all` raises only for a row that is gone. Any number of processes can create a reserved
source row at once. After this cycle AGENTS.md lists only `hs update` as an exception to invariant §1,
and no longer lists the kill between the retry insert and the mark as an unguarded doubled retry.

## Acceptance criteria (the contract)

- **R1** — WHEN the Scheduler's `retried` mark of a failed row waits on another writer's row lock
  and the row still awaits retry once that writer commits, the mark SHALL land, the server SHALL
  keep running, and the failed row SHALL end with exactly one retry row.
- **R2** — A failed row's retry row and its `retried` mark SHALL be committed together or not at
  all. IF the mark cannot be written — still contended after the re-runs, or the process stops
  between the two writes — THEN no retry row of that failed row SHALL remain, the row SHALL stay
  retryable for a later round, and (when contended) the server SHALL log a WARNING naming the task
  and keep running. A failed row that no longer awaits retry when its mark is written (deleted,
  marked retried, or cancelled meanwhile) SHALL get no retry row.
- **R3** — WHEN `Task.cancel_all` or `Task.cancel` waits on another writer's row lock and the row
  still exists, the row SHALL be cancelled; the call SHALL raise only for a row that is gone, as
  today, and IF a row still exists but is not written after the re-runs, THEN the caller SHALL be
  able to tell which rows were not cancelled.
- **R4** — WHEN several processes create the same reserved source row (`<direct>` or `<stdin>`) on a
  database that lacks it, each SHALL use the one row and none SHALL fail, on SQLite and PostgreSQL
  (both the `postgres` and `timescale` providers); concurrent first `hs submit`s and `hs cluster`
  runs against an empty `source` table, fresh or upgraded from 2.8.3, SHALL all exit 0 with every
  task submitted.
- **R5** — Tests SHALL force each race deterministically, not by timing luck, and fail before the
  fix: fault injection on SQLite (as `tests/test_contended_recovery.py`) for every path of R1–R3,
  including the exhausted re-run and the stop between the retry insert and the mark; the real race
  on a server database (as `tests/test_contended_write.py`) for the mark and `Task.cancel_all`, red
  on CI's TimescaleDB 2.31 jobs before the fix and green everywhere after; and R4 by two sessions
  that both miss the lookup before either inserts, on SQLite and PostgreSQL. An uncontended mark or
  cancel SHALL cost what an uncontended dispatch stamp does (one UPDATE per row), pinned as the
  existing tests pin it.
- **R6** — AGENTS.md (§ Data layer specifics, § Task lifecycle) and `.agents/factory/invariants.md`
  (§1, §3) SHALL, in the same change, drop the `retried` mark and `Task.cancel_all` from the
  exceptions to the 0-row-match rule (leaving `hs update`, pointing at its seed) and drop the kill
  between the retry insert and the mark from the unguarded doubled-retry cases.

## Non-goals (no-gos)

- **No `hs update` change.** Its contended writes (legacy form, `--limit`, the filtered bulk paths,
  `--delete`), its counts and exit status, and the two non-contention defects (change kinds dropped
  under `--limit`; tag changes missing rows a field change moved out of the filter) are
  [`issues/hs-update-contended-writes.md`](../../issues/hs-update-contended-writes.md), scheduled
  right after this cycle. No `docs/cli/task_update.rst` change here; this cycle has no user-visible
  CLI change.
- **No change to what a cancel overwrites.** `Task.cancel_all` still cancels a row whatever its
  state, as today; only the contended case changes (R3).
- **No other write of `task` rows**: the server paths already on the pattern stay as they are;
  `rotatedb` (SQLite only) and the relay store's deletes are not touched. No change to `client`,
  `zone` or named-file `source` rows: two concurrent submits of the same named file both passing
  source gating is a different race, not this one.
- **Not two servers on one database** (unsupported), and not a reset `retried` flag from `hs update`
  (that unguarded doubled retry stays as AGENTS.md lists it).
- **No CI image change.** The TimescaleDB 2.31.0 pins in `.github/workflows/tests.yml` stay, since
  they carry R5's red; whether 2.32 fixed the miss upstream is not investigated, and nothing is
  reported upstream from this cycle.
- **Not the neighbouring seeds**: the Confirm/Receiver deadlock and thread death turned into retry
  ([`issues/coordinator-resilience-retry-backoff.md`](../../issues/coordinator-resilience-retry-backoff.md)),
  [`issues/stale-confirm-hides-orphan.md`](../../issues/stale-confirm-hides-orphan.md).
- **No group-gating change**: a failed row left retryable by R2 holds its group exactly as an
  unretried failure does today.
- **No 2.8-line backport**: the `timescale` provider and the `source` table are 2.9 only.

## Clarifications

- **Q:** One GOAL for both seeds? — **A:** Yes, `hypertable-contended-scheduler-and-cli-writes`
  primary (its server half), with `source-reserved-race`, both marked `adopted:contended-writes`
  (foreman's brief, 2026-11-14). Both are concurrent writers that end in a false failure, and both
  are tested by forcing the interleaving.
- **Q:** Size circuit-breaker: the first draft had 10 R-IDs and four deliverables. Split? —
  **A:** Split. This cycle keeps the `retried` mark with its retry rows, `Task.cancel_all`/`cancel`,
  the source row, their tests, and the AGENTS.md/invariants edits for what it closes. Every
  `hs update` change (its contended writes, counts and exit status, the two non-contention defects,
  and the `task_update.rst` docs) moved to the new unshaped seed
  [`issues/hs-update-contended-writes.md`](../../issues/hs-update-contended-writes.md), which carries
  the draft's proposed answers (all-or-nothing at the re-run limit; `--limit` re-checks the filter;
  take the two non-contention defects; include `--delete`) and the reproductions; the foreman
  schedules it right after this cycle, within the pre-2.9.0 list. The primary seed stays
  `adopted:contended-writes` for its server half and says which half moved (ruled at the GOAL gate,
  2026-11-14).
- **Q:** Appetite? — **A:** `small` after the split (see the header); the draft's `big` was sized
  for `hs update`'s five write paths and output changes, which moved (ruled at the GOAL gate,
  2026-11-14: "big if honest, else small").
- **Q:** How is R1–R3's red shown when the local TimescaleDB 2.32 does not reproduce the miss? —
  **A:** By fault injection on SQLite, which is deterministic everywhere, plus the real-race tests on
  CI's TimescaleDB 2.31 jobs, shown red at a tests-first commit the foreman pushes to a draft PR
  (Evidence plan); locally the real-race tests must pass before and after the fix on PostgreSQL 18
  and TimescaleDB 2.32 (ruled at the GOAL gate, 2026-11-14).
- **Q:** What the mark's re-check asks, and how R2's "together" is made atomic? — **A:** Left to
  `hs-plan` (e.g. the mark conditional on `retried IS FALSE` in the statement, in the retry rows'
  transaction); R1 and R2 bind whichever is chosen (shaping, 2026-11-14).

**Ruled at the GOAL gate** (2026-11-14): the foreman, gate judge under the maintainer's mandate,
split the draft. This cycle keeps R1–R6 as written above; every `hs update` change went to
`issues/hs-update-contended-writes.md`, scheduled right after this cycle. One GOAL for both seeds is
approved as re-sized; the appetite is `small`. The evidence plan must include a tests-first commit
the foreman pushes to a draft PR so CI's TimescaleDB 2.31 jobs show red before the fix. The contract
is locked; `hs-plan` designs against it.

## Evidence plan

- **Tests-first commit, CI red**: `hs-build` lands R5's tests before the fix in a commit of their
  own; the foreman pushes the branch at that commit to a draft PR, where the real-race additions
  fail on the `timescale` and `timescale-oss` jobs (TimescaleDB 2.31.0) and the fault-injection and
  two-session tests fail on every job. An external wait the foreman schedules; `hs-plan` makes it
  its own phase.
- **CI green**: the same jobs green on the fix; `timescale-floor` (2.24) and the plain PostgreSQL
  jobs green throughout.
- **Unit and integration, laptop**: the full suite on SQLite, and `tests/test_contended_write.py` /
  `tests/test_contended_recovery.py` with their additions on the local PostgreSQL 18 and TimescaleDB
  2.32 (`HS_TEST_POSTGRES`, `HS_TEST_TIMESCALE=community`), where the real-race tests must pass both
  before and after the fix.
- **Laptop drives** (`.agents/factory/bin/temp_site.sh`): an `hsx` run with retries (`-r 1`,
  failing tasks) on SQLite and the local TimescaleDB, every failed row ending with one retry
  row; for R4, repeated runs of six concurrent first `hs submit`s from stdin against a fresh SQLite
  database, a fresh PostgreSQL database and an upgraded 2.8.3 SQLite database, all exiting 0 with
  every task present (the seed's rates as the before).
- **Cluster**: none needed.

## Related materials

- Seeds: [`issues/hypertable-contended-scheduler-and-cli-writes.md`](../../issues/hypertable-contended-scheduler-and-cli-writes.md)
  (primary, server half; its R1 met by #103), [`issues/source-reserved-race.md`](../../issues/source-reserved-race.md),
  both `status: adopted:contended-writes`. Split off at the GOAL gate:
  [`issues/hs-update-contended-writes.md`](../../issues/hs-update-contended-writes.md) (`unshaped`).
- Pattern: [`spec/hypertable-update-retry/`](../hypertable-update-retry/GOAL.md) (`CONTENDED_RERUNS`,
  the write-path audit in `research/02-write-path-audit.md`, both test harnesses);
  [`spec/relay-hardening/`](../relay-hardening/GOAL.md) (#103: `__write_rows`, `__row_statements`,
  their prefixes and conditions; the dispatch stamp); invariants §1, §3, §12.
- Source row: [`spec/schema-upgrade-step/`](../schema-upgrade-step/GOAL.md) (the `source` table;
  REVIEW cycle 2, *Out-of-diff context*).
- Source (likely touchpoints, to be confirmed by `hs-plan`): `src/hypershell/data/model.py`
  (`__schedule_next_failed_tasks`, `Entity.update_all`, `Task.cancel_all`/`cancel`, `__write_rows`,
  `__row_statements`, `Source.reserved`); `tests/test_contended_write.py`,
  `tests/test_contended_recovery.py`; `.github/workflows/tests.yml` (the TimescaleDB jobs).
