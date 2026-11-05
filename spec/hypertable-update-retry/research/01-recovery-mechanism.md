# 01 — Recovering a contended task-row write: mechanism, bound, exhaustion

Scope: how Confirm, the Receiver and the eviction revert recover from a contended 0-row match
(GOAL R1, R2, R3, R5, R6), read against `develop` `0e8275e`. Codebase-only; the probe facts are the
GOAL's Clarifications.

## What the code does today

- **Confirm** (`server.py` `Confirm.update_info`): `Task.update_all(client_info.transpose())`, i.e.
  `Session.bulk_update_mappings(Task, [{'id', 'client_id', 'client_host'}, …])` then commit. The
  ORM bulk path issues `UPDATE task SET client_id=…, client_host=… WHERE task.id = …` (one
  executemany) and raises `StaleDataError` when fewer rows match than were given. Nothing catches
  it: the Confirm thread dies and the server exits (the CI log names Confirm).
- **Receiver** (`Task.__apply_result`): one prebuilt conditional UPDATE per result (`id`,
  `schedule_time` as dispatched, `completion_time IS NULL`); `rowcount == 1` → APPLIED, else the
  prebuilt `completed` SELECT (same dispatch, `completion_time IS NOT NULL`) → DUPLICATE if found,
  else STALE. A contended 0 on a hypertable falls to STALE although the row still qualifies.
- **Eviction revert** (`HeartMonitor.check_clients` → `Task.revert_orphaned` → `revert_all` →
  `update_all`): same bulk path as Confirm; a contended 0 raises `StaleDataError` in the HeartMonitor
  thread. `revert_orphaned` loops `while select_orphaned(…)`, so a revert that never lands would
  re-select the same row forever.
- The scoped `Session` is per thread; `data/core.py` sets no isolation level, so PostgreSQL runs
  READ COMMITTED: every statement takes a fresh snapshot.

## Why a re-run recovers

The defect is in the re-check a blocked UPDATE does after its holder commits. A second statement
in the same transaction takes a new snapshot that already sees the holder's commit, so it does not
wait on that holder and plain-matches the committed row if the row qualifies. It waits again only if
yet another transaction holds the row. In-server, the writers of one dispatched row are Confirm
(once per dispatch), the Receiver (once per result) and the eviction revert (once per eviction), so
at most two other writers can contend with any one write. A bound of **3 extra attempts** exceeds
that; exhaustion needs a cross-process writer locking the same row repeatedly.

The premise (the re-run matches 1 on 2.31) is not observable locally. It is cheap to pin: a raw
two-session test whose second UPDATE re-runs after the contended one and asserts it matches. It is
true on PostgreSQL and on 2.24 by construction, so it runs green everywhere and fails only if a
future TimescaleDB breaks the re-run itself (see 03).

## Deciding whether to re-run (R3, R6)

A 0-row match is re-run only when a fresh read says the row still qualifies, so waiting can never
turn a refused write into a landed one (R3), and a write that matched first time is neither re-run
nor re-checked (R6).

- **Result write:** change the prebuilt classifying SELECT from "same dispatch and completed" to
  "same dispatch", returning `completion_time`. No row → STALE; `completion_time` set → DUPLICATE;
  `completion_time` NULL → the row still qualifies → re-run the UPDATE. Same statement count as today
  for an uncontended STALE or DUPLICATE, and still one prebuilt SELECT object (the
  `test_writeback_reuses_its_statements` guard holds).
- **Confirm and revert:** the condition is only "the row exists" (both are unconditional by id on
  `develop`, and R3 keeps that). Per row: UPDATE by id; `rowcount == 1` → done; else
  `SELECT id … WHERE id = :id` → missing: the `develop` outcome for a missing row (below); present →
  re-run.

On a non-defective database the "still qualifies" branch is unreachable without a writer committing
between the two statements, which is itself the race the re-run exists for; on SQLite writes are
serialised by the database lock. So SQLite, plain PostgreSQL and TimescaleDB < 2.29 see no change.

## Per-row statements instead of the bulk path

`bulk_update_mappings` gives no per-row count: one executemany, one total, and an exception on a
short total. Recovering per row needs per-row statements. A new private `Task` helper runs one
prebuilt `UPDATE task SET <cols> WHERE id = :r_id` per row (cached per column set, binds typed from
the table, as `__result_statements` does), checks each count, and commits once per call, so a
Confirm still commits once per bundle and a revert once per batch of 100.

- `Entity.update_all` stays as is: `hs task update` catches its `StaleDataError` for a missing id,
  and `Client` and the Scheduler's `retried` mark keep using it (02 audits them).
- Cost: one statement per row instead of one executemany. For Confirm with the cluster runs' bundle
  size of 1 that is unchanged; for a large bundle on PostgreSQL it is a round trip per task, and for
  `revert_interrupted` over a large interrupted set at server start likewise. Not measured; see
  PLAN risks.

## Missing row (uncontended)

`develop` raises `StaleDataError` from Confirm or the revert when a row no longer exists, which no
in-server path causes (rows are only deleted by an external command). R6 says an uncontended write
behaves as on `develop`, so the helper raises `StaleDataError` for a row that is missing after a
0-row match (after rolling back the call's transaction, as the bulk path's failure does). The
alternative, a WARNING and skip, is better behaviour but a change R6 does not ask for; flagged for a
ruling.

## Exhaustion (R5 as signed off)

- **Confirm:** WARNING naming the task and the path, `client_id`/`client_host` left unrecorded on
  that row, the other rows of the bundle stamped, commit, thread keeps running.
- **Eviction revert:** WARNING naming the task and the path; the row stays in flight. To keep the
  loop bounded, `revert_orphaned` (and `revert_interrupted`) stop when a pass lands no revert. The
  row is then reverted by `revert_interrupted` at the next server start, or completed by a result
  from the client if it is still alive. Unreachable in-server (two other writers at most).
- **Result:** classifying it STALE would leave the row in flight under a live client: the
  non-`forever` Scheduler stops only at `count_remaining() == 0`, so the run would hang; reverting it
  would run the task twice. Instead the result is **retained** by the Receiver and presented again
  on its next cycle (the next bundle, or the next empty-queue timeout), each presentation bounded
  the same way. It lands once the contention clears, or becomes STALE/DUPLICATE through the normal
  classification if eviction reverted the row meanwhile. WARNING once when a result is first
  retained; DEBUG on each later presentation. If the Receiver receives its shutdown sentinel with a
  result still retained (only reachable by a signal or forced stop, since a normal stop needs every
  row complete), it logs a WARNING per retained result: the row stays interrupted and is reverted at
  the next start, as for any result lost to a stop.
- `apply_results` reports this as a fourth `ResultOutcome`, `DEFERRED`: the row still belongs to the
  dispatch, nothing was written, the caller may present it again.

## Rejected alternatives

- **Lock first (`SELECT … FOR UPDATE`) on every write:** a second statement on every uncontended
  write (R6), and whether a hypertable's row-lock re-check is sound on 2.29+ is itself unknown.
- **Serialise Confirm and the Receiver through one thread / change isolation level:** a general
  concurrency redesign (non-goal), and cross-process writers would still contend.
- **Change `Entity.update_all` for every caller:** changes `hs task update`'s missing-id handling
  and the `client` writes, which need nothing.
