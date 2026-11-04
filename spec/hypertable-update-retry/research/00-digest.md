# 00 — Digest: the decisions the briefs settle

Three briefs, one per open question. The fan-out ran because the change touches `data/model.py` and
`server.py` (high blast radius); 01 and 03 were written by the planner, 02 by a read-only subagent.

1. **Mechanism** ([01](01-recovery-mechanism.md)). A contended 0-row match is re-run only when a
   fresh read says the row still qualifies; READ COMMITTED gives the re-run a snapshot that already
   holds the holder's commit, so it plain-matches. Bound: **3 extra attempts** per write
   (`Task`-module constant), each logged at DEBUG naming the task and the path.
   - Result write: the prebuilt classifying SELECT returns `completion_time` for the same dispatch:
     none → STALE, set → DUPLICATE, NULL → re-run. Same statement count when uncontended.
   - Confirm and revert: a new private `Task` helper writes one prebuilt `UPDATE … WHERE id` per
     row, checks each count, re-runs while the row exists, commits once per call.
     `Entity.update_all` is unchanged (02 confirms `hs task update` and the revert loops depend on
     its raise).
2. **Exhaustion** (01, as signed off in R5). Confirm: WARNING, client columns left unrecorded on
   that row, thread keeps running. Result: a new `ResultOutcome.DEFERRED`; the Receiver retains the
   result and presents it again on its next cycle until it lands or classifies STALE/DUPLICATE;
   WARNING once, at shutdown a WARNING per result still retained. Revert: WARNING, the row stays in
   flight; the revert loops stop when a pass lands nothing, so they stay bounded (02, finding 2).
3. **Missing row** (01, 02 finding 5). Uncontended, so R6 keeps `develop`'s outcome: the helper
   raises `StaleDataError` after rolling back. Flagged for a ruling.
4. **Audit** ([02](02-write-path-audit.md)). In scope: Confirm, the Receiver, `revert_orphaned`.
   Fixed through the same lines: `revert_interrupted`, `Task.revert` (via `revert_all`). Seeded:
   the Scheduler's writes (`Task.next` stamp, the `retried` mark), `cancel_all`, and every
   `hs task update` path. Their exhaustion needs its own design (an unmarked `retried` row doubles a
   retry), and `hs task update` inlines its own statements. Unaffected: `client` writes,
   `rotatedb`, vacuum, upgrade, TimescaleDB DDL, `submit`. 02 also found an in-server contender for
   the Scheduler (a late Confirm) that the GOAL's framing omits; it is a risk for the foreman.
5. **R7** ([03](03-contention-test.md)). A new module `tests/test_contended_write.py` reusing
   `tests/test_timescale.py`'s fixtures; a holder connection takes the row lock; HyperShell's write
   runs in a thread; the test waits until `pg_stat_activity` shows that backend blocked by the
   holder (`pg_blocking_pids`), then commits. Five tests; three red on `0e8275e` at 2.31.0. The CI
   `postgres` job gains the module path. The foreman proves red/green with
   `spec/hypertable-update-retry/contention_run.py`; CI on the PR is the second proof.
