# GOAL — Scoped, conditional result writeback

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** writeback-scoped-conditional
- **kind:** fix
- **appetite:** big · time box ~1.5 days, targeting a 2.9.0 alpha before 2026-11-13 (see
  Clarifications)

## Problem

When a client returns a finished bundle, the server's `Receiver` writes each task back with
`Task.update_all([task.to_dict() for task in self.tasks])` (`src/hypershell/server.py:459-462`).
`update_all` is a bulk update keyed only on `id` (`src/hypershell/data/model.py:194-199`), and
`to_dict()` carries *every* column. So the client's copy of the row, as it stood when the bundle was
dispatched, overwrites everything the server holds: scheduling stamps (`schedule_time`, `server_id`,
`server_host`), the retry chain (`attempt`, `retried`, `previous_id`, `next_id`), and the submission
and provenance fields. It is last-writer-wins over every column, with no check that the row still
belongs to the result.

Most of the time the two copies agree and nothing goes wrong. They stop agreeing when a client is
evicted but keeps running (partitioned, suspended, or stalled past `evict_after`). The server
reverts its tasks (`Task.revert_orphaned`, `model.py:793-796`) and runs them elsewhere, and nothing
tells the original client to stop. When its result arrives late, it is applied anyway. A row can
then read "completed" while another client is still running the same command, a cancelled row can
lose its `CANCEL_STATUS`, and a row that has already been retried can have `retried` and `next_id`
reset, so the scheduler retries it a second time (`issues/late-result-resets-retried.md`). None of
this produces any signal for the operator. **Pre-existing** on `develop` (`d996579`).

The same whole-row return path is what `relay` (`issues/relay.md`) will use upstream. Relay's R6
requires that relay-local scheduling stamps never leak upstream, and today that would hold only by
convention: the relay would have to restore the upstream stamps before every return.

## Outcome / vision

A returned result can write only what a run produces, and only onto a row it still belongs to. A
stale result from an evicted-but-alive client is detected, logged and dropped instead of applied. The
duplicate run still wastes compute (stopping it is a separate issue), but it can no longer corrupt the
database. Scheduling stamps, submission metadata and the retry chain become columns a returning worker
*structurally cannot* rewrite. That makes relay's "stamps don't leak upstream" a property of the
server rather than a step the relay has to remember, while still accepting relay's legitimate case of
returning a row it holds with attribution to the leaf client that ran it. The homogeneous default path
behaves exactly as today in every case except the stale write.

## Acceptance criteria (the contract)

Terms. A row's **assignment** is a single dispatch of that row: from when it is scheduled to a
client until it is completed, cancelled, or reverted. A returned result **belongs to the current
assignment** when it was produced under the dispatch the row is still in flight under. How the
server recognises that is `hs-plan`'s to design. It SHALL NOT depend on the returned row's
`client_id` (see R4).

- **R1** — WHEN the server applies a returned result, it SHALL write only the execution-outcome
  columns a client produces: executing-client attribution (`client_id`, `client_host`), the expanded
  `command`, `start_time`, `completion_time`, `waited`, `duration`, `exit_status`, `outpath`,
  `errpath`, `csvpath`, `cores_max`, `memory_max`. Every other column (`schedule_time`, `server_id`,
  `server_host`, `attempt`, `retried`, `previous_id`, `next_id`, `group`, `args`, the submit fields,
  declared `cores`/`memory`/`timeout`, `source`, `fingerprint`, `part`, `tag`) SHALL keep the value
  the server holds, whatever the returned copy says. `TECH.md` SHALL list the final result-column
  set, and a test SHALL show that a returned copy differing in every non-result column changes none
  of them.
- **R2** — IF a returned result does not belong to its row's current assignment (the row has since
  been reverted, rescheduled to any client *including the same one*, completed by another
  assignment, or cancelled), THEN the server SHALL leave the row unchanged, SHALL log a warning
  naming the task id and the client the result came from, and SHALL NOT treat it as a completion
  (no completion log line, no `--failures` redirect of its args).
- **R3** — WHEN a returned result belongs to its row's current assignment, the server SHALL apply it
  so that every result column ends up with the same value it gets on `develop` today, with the same
  logging and `--failures` behaviour. No legitimate result SHALL be lost or delayed.
- **R4** — WHEN the holder of a row's current assignment returns that row with executing-client
  attribution naming a *different* client (relay's case: rows are confirmed to the relay, and
  returned attributed to the leaf client that ran them), the server SHALL apply the result and record
  the executing client's attribution. The scheduling stamps in the returned copy SHALL NOT overwrite
  the server's (R1). A test SHALL exercise this without relay: return a result for an in-flight row,
  attributed to a client other than the one it was confirmed to, and check that the result and the
  attribution are applied and the server's scheduling stamps are unchanged.
- **R5** — WHEN a result is returned again for a row its current assignment has already completed
  (crash replay, redelivery, or the same task listed twice in one bundle), the row SHALL be unchanged,
  the repeat SHALL NOT count as a second completion, and the log SHALL distinguish it from an R2
  stale refusal.
- **R6** — A test SHALL drive this interleaving: a client is evicted while running, its task is
  reverted, rescheduled, fails, and the retry row is created; then the evicted client's own failed
  result arrives. The test SHALL fail on the pre-fix code. After the fix, the late result SHALL leave
  `retried`, `next_id`, `previous_id` and `attempt` unchanged, and the next `Task.next` SHALL NOT
  create a second retry row for that task. (This discharges `issues/late-result-resets-retried.md`.)
- **R7** — The task-lifecycle predicates in `AGENTS.md` § Task lifecycle contract SHALL be unchanged.
  IF a result arrives for a cancelled row, THEN the row SHALL keep `exit_status == CANCEL_STATUS` and
  its `completion_time`.
- **R8** — WHILE the server runs `in_memory`, behaviour SHALL be unchanged (no database writeback).
  WHILE it runs with `no_confirm`, a legitimate result SHALL still be applied as in R3: the server
  SHALL NOT refuse a result just because it never stamped the client at confirmation. R1 and R7 hold
  in every mode.
- **R9** — The fix SHALL NOT change the bundle wire format. Any client/server version pairing that
  works on `develop` today SHALL keep working. An existing 2.8 or 2.9 database SHALL work without
  manual steps; any schema change the plan needs SHALL go through the in-place upgrade step
  (`spec/schema-upgrade-step/`, #69). Other callers of `Task.update_all` (`hs task update`, cancel,
  revert) SHALL behave as today.
- **R10** — `AGENTS.md` § Task lifecycle contract and `.agents/factory/invariants.md` SHALL be
  updated so the "unguarded doubled retry" lists no longer name a late full-row writeback by an
  evicted client, and SHALL record that a returned result writes only result columns and only for its
  current assignment. The predicates themselves stay as they are (R7).

## Non-goals (no-gos)

- **Stopping the evicted client, or reaching a running task to cancel it.** That needs a
  server-to-client control path, which `zone`'s dispatch RPC will provide
  (`issues/evicted-client-fencing-and-cancel-reach.md`). The duplicate run still wastes compute.
- **Salvaging a late result for a reverted row.** It is refused (R2) and the row runs again.
- **A new metrics, CLI, or persisted surface for refused writes.** Refusals are logged (R2, R5).
- **`restart-pending-retry`.** It is a START-path scheduling decision and never touches writeback.
- **`eager-retry-double-bundle`'s bundle composition.** `Task.next` listing a retry twice stays
  that seed's to fix. This GOAL only guarantees that the second result is harmless (R5).
- **The other unguarded doubled-retry causes:** two servers on one database, a kill between the
  retry insert and the `retried` mark, and a `retried` reset by `hs task update`.
- **`coordinator-resilience-retry-backoff`**, which stays where it is on the roadmap. Its R5
  idempotent redelivery leans on R5 here.
- **Relay itself**, and amending `issues/relay.md`. When relay is promoted, its return-path note
  ("keep the upstream Receiver unchanged by restoring stamps") should be re-read against R1/R4.

## Clarifications

- **Q:** Sequencing against relay and resilience? — **A:** Pull this fix ahead of relay as its own
  fix cycle; `coordinator-resilience-retry-backoff` stays where it is (maintainer, alignment memo
  Q6, 2026-11-01).
- **Q:** Must relay's case be in the contract, given relay is not built yet? — **A:** Yes. Relays
  own rows that they return with leaf attribution, and the fence must accept that; R6's "stamps
  don't leak upstream" becomes structural (maintainer, alignment memo, 2026-11-01). Consequence for
  the seed's draft R1, which listed `client_id` as unwritable: executing-client attribution is a
  *result* column (R1), and ownership is the assignment, not the returned `client_id` (R4).
- **Q:** Appetite? — **A:** The seed says `big`; the time box is about 1.5 days, aiming at a 2.9.0
  alpha before 2026-11-13, ahead of relay (maintainer, alignment memo, 2026-11-01). Kept `big` because
  `data/model.py` is the repo's highest-blast-radius file and warrants the research fan-out.
- **Q:** Compatibility limits? — **A:** No 2.8 wire or database break unless it goes through the
  #69 in-place upgrade step. The homogeneous default path behaves the same except for the stale-write
  fix. Any change to `AGENTS.md` invariants gets flagged at sign-off (maintainer, alignment memo,
  2026-11-01). R10 is such a change: it edits the lifecycle-contract prose, not the predicates.
- **Q:** Does this subsume `late-result-resets-retried`? — **A:** Yes. R6 carries its test and its
  two outcomes, and the seed is marked `adopted:writeback-scoped-conditional`. The other seeds
  deferred by `group-advance-retry-gate` (#68) stay out; see Non-goals (shaping, 2026-11-02).
- [NEEDS CLARIFICATION: how should refused writes be surfaced? (a) a per-task warning log only, as
  R2 states now; (b) (a) plus a count in the server's shutdown log; (c) a persisted or queryable
  counter. Recommendation: (a). The seed's "counted" has no existing surface to land on, and adding
  one grows the appetite.]
- [NEEDS CLARIFICATION: when a reverted row has not yet been rescheduled, should its late
  result be refused, as R2 and Non-goals state now, or applied to save a re-run? Recommendation:
  refuse. Applying it races the scheduler, and keeping it is the waste-recovery half that belongs
  with the fencing issue.]

## Related materials

- Seed: [`issues/writeback-scoped-conditional.md`](../../issues/writeback-scoped-conditional.md)
- Subsumed seed: [`issues/late-result-resets-retried.md`](../../issues/late-result-resets-retried.md)
- Downstream: [`issues/relay.md`](../../issues/relay.md) (R3, R5, R6, and the return-path note);
  [`issues/coordinator-resilience-retry-backoff.md`](../../issues/coordinator-resilience-retry-backoff.md) (R5)
- Other half: [`issues/evicted-client-fencing-and-cancel-reach.md`](../../issues/evicted-client-fencing-and-cancel-reach.md)
- Out of scope, related: [`issues/restart-pending-retry.md`](../../issues/restart-pending-retry.md),
  [`issues/eager-retry-double-bundle.md`](../../issues/eager-retry-double-bundle.md),
  `spec/group-advance-retry-gate/` (#68)
- Upgrade machinery: `spec/schema-upgrade-step/` (#69)
- Source: `src/hypershell/server.py` (`Receiver.update_tasks`, `HeartMonitor` eviction),
  `src/hypershell/data/model.py` (`Entity.update_all`, `Task.revert_orphaned`, `Task.cancel_all`,
  `Task.next`), `src/hypershell/client.py` (the columns a client stamps on a result)
- Origin: GNU Parallel comparison review, 2026-08 (H2, with H1)
