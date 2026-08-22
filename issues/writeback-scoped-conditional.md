---
status: unshaped
kind: fix
appetite: big
lane: public
---

# Result writeback is last-writer-wins over every column

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The `Receiver` commits completed bundles with
`Task.update_all([task.to_dict() for task in self.tasks])` (`src/hypershell/server.py:455-458`).
`update_all` is a bulk mapping keyed only on `id` (`src/hypershell/data/model.py:194-199` — which
still carries the maintainer's `# NOTE: why is this necessary?`), and `to_dict()` serialises
*every* column. So a returning result blindly rewrites `schedule_time`, `client_id`, `attempt`,
`retried`, `previous_id`/`next_id`, and the provenance fields — from the client's wire copy of the
row, which may be arbitrarily stale.

That is a correctness hole on its own, and it is the mechanism that converts a *waste* problem
into a *corruption* problem. When a client is evicted (`server.py:621-635` → `Task.revert_orphaned`,
`model.py:737-740`) nothing is ever sent *to* that client — the queue exposes only
`scheduled`/`completed`/`heartbeat`/`confirmed` (`src/hypershell/core/queue.py:377-380`), so there
is no disconnect, no stop sentinel, no fence. A client that was merely partitioned, SIGSTOP'd, or
NFS-stalled past `evict_after` keeps running its bundle. Its results land *after* the task was
reverted and rescheduled, and stamp the row "done" — with `retried=False`, `next_id=None` — while
another client is still executing the very same command. The UNIQUE `previous_id`/`next_id` retry
chain can desync in the process.

The result: rows that read "completed" while the work is still running, broken retry linkage, and
falsified provenance — with no operator signal at all.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, H2/H1) — a review
pass. **Pre-existing** in `develop`.

Deliberately scoped *narrower* than the finding that produced it. H1 (fencing evicted-but-alive
clients) needs a server→client control path that does not exist today, and `zone`'s
request-return dispatch RPC is exactly that seam — building a control channel here and again in
`zone` would be building it twice. This issue therefore takes the half that needs no new
transport: make the write itself refuse to apply a stale result. See
`issues/evicted-client-fencing-and-cancel-reach.md` for the other half.

## Outcome / vision

A result can only ever write result columns, and only onto the row it actually belongs to. A stale
bundle from an evicted-but-alive client is detected, logged, and dropped rather than applied — so
the duplicate execution is still wasteful, but it can no longer corrupt the database. Submission
metadata and the retry chain become things a worker structurally cannot rewrite.

## Sketch of the acceptance criteria

- **R1** — WHEN a completed bundle is written back, only result columns SHALL be updated
  (`exit_status`, `completion_time`, `start_time`, `duration`, `outpath`/`errpath`, and the
  resource/monitor fields); submission and lifecycle columns (`schedule_time`, `client_id`,
  `attempt`, `retried`, `previous_id`/`next_id`, `group`, `source`, provenance) SHALL NOT be
  writable from a returned result.
- **R2** — IF a returned result does not match the row's current owner and in-flight state, THEN
  the write SHALL be refused, logged with enough detail to identify the stale client and task, and
  counted — never silently applied.
- **R3** — The refusal SHALL NOT lose a legitimate result: a result that arrives for a row still
  legitimately in flight to that client SHALL be applied exactly as today.
- **R4** — The task-lifecycle predicates in `AGENTS.md` SHALL be preserved exactly; cancelled rows
  (terminal by `completion_time` + `CANCEL_STATUS`) SHALL NOT be resurrected by a late result.

## Notes

- `.local/IMPROVEMENTS.md` sketches the guard as
  `UPDATE ... WHERE id = :id AND client_id = :client AND completion_time IS NULL`. The exact
  predicate is `/hs-plan`'s to root-cause and confirm — in particular whether `client_id` alone is
  a sufficient fence or whether an attempt/epoch token is needed, and how `in_memory` mode (server
  skips all DB writeback) and `no_confirm` (client id never stamped, so the fence has nothing to
  compare) behave under it.
- **`relay` inherits this.** `relay` R5/R6 specify an idempotent whole-row update by task id and
  require relay-local scheduling stamps not to leak upstream; a result-columns-only writeback makes
  that guarantee structural instead of conventional. Sequencing this before `relay` is worth a
  line in the shaped GOAL.
- Highest-blast-radius file in the repo (`data/model.py`), plus `server.py`. Expect a research
  fan-out regardless of appetite, per the harness rule.
- Related: `issues/evicted-client-fencing-and-cancel-reach.md` (the control-channel half),
  `issues/coordinator-resilience-retry-backoff.md` (result durability on the same path).
- Found by: GNU Parallel comparison review, 2026-08 (H2, with H1).
