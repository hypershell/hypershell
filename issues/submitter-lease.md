---
status: unshaped
kind: feature
appetite: big
target: 2.10.0
lane: public
---

# Submitter leases: a shared site cannot tell a live submitter's tasks from a dead one's

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim. Not for 2.9.0: a maybe on
> the roadmap.

## Problem

More and more of what reaches a HyperShell database is submitted by a program that stays around to
act on the results: the `nf-hypershell` Nextflow executor (`hypershell/hypershell-nextflow`),
hypershell-mcp, agents, workflow scripts. Each one *owns* its tasks. It polls them, cancels them,
and will resubmit them its own way if it comes back after a crash. When it dies without cleaning up
(SIGKILL, out of memory, a lost login session, a node failure), its unfinished tasks run on. Their
results go to directories nobody will read, they hold allocations other work needs, and if the owner
comes back it runs the same work again beside them.

Nothing in HyperShell can tell those orphans apart from live work:

- **A tag names an owner, not whether it is alive.** `nf-hypershell` tags every task `nf:<session>`
  (and `nf_run:<execution>`), so a person can cancel one owner's leftovers by hand
  (`hs update --cancel -t nf:<key> --remaining -f`). No rule can do it unattended, because nothing
  says the owner is gone. The plugin can only sweep its *own* session when it is resumed. An owner
  that never returns leaves its tasks to run, and on a database shared by many users and many
  submitters nobody is placed to clean up after it.
- **The only lifecycle HyperShell has is the server's.** A server that starts on a database reverts
  every in-flight row and runs it again (`Task.revert_interrupted`, `src/hypershell/server.py:234`).
  That is right for `hs cluster`'s own tasks and wrong for an external submitter's. It is why
  `nf-hypershell` gives each managed run a database of its own and refuses to start a server on one
  that already holds rows. A shared, long-lived site, which is what attached mode and multi-site
  relays are for, has no such escape.
- **`source` records where tasks came from, not who is waiting for them.** `Source`
  (`src/hypershell/data/model.py`, the provenance row for re-submission gating) is written once at
  ingest and never again. Every external submitter uses the reserved `<direct>` source.

Tags alone also cannot carry liveness cheaply. A heartbeat rewritten on every task row is one write
per task per interval, on a table with no tag index. A sentinel row in `task` would be scheduled.
Both abuse the one table every user of a shared site depends on.

## Why it was deferred

Not for 2.9.0. It is new schema and a new concept, raised while building the Nextflow executor
(2026-11-21) for attached mode: Nextflow runs submitting into a large, shared, long-lived HyperShell
deployment that also holds tasks from outside Nextflow, possibly spanning sites through relays. This
is a gap, not a defect; nothing on `develop` is broken. The plugin works around it today with
owner tags, a sweep of its own session at start-up, and a documented manual cancel.

## Outcome / vision

A submitter that owns tasks says so, and keeps saying so. It claims a *lease* (a row of its own, not
a tag) with a kind (`nextflow`), a key it chooses (the Nextflow session id) and a time to live, and
renews it while it runs. Its tasks reference the lease. While the lease is fresh, nothing but the
owner touches its tasks. When the lease lapses, the site sweeps it: the owner's unfinished tasks are
cancelled (or kept, if the owner asked for that at claim), and the sweep is recorded on the lease.
An owner that exits in order releases its lease, with or without a cancel. An owner that comes back
(a resumed Nextflow session) re-claims its key, and fencing makes sure two live processes never
both believe they own it.

Tasks with no lease behave exactly as they do today, so `hs submit`, `hs cluster FILE` and every
existing database are unaffected. The task history stays HyperShell's: swept tasks are cancelled,
not deleted, and the lease row says who owned them and what happened.

What this could look like (for `/hs-plan` to decide):

- A `lease` table: `id`, `kind`, `key` (unique among unreleased leases of a kind), `host`,
  `claimed_at`, `renewed_at`, `ttl`, `generation` (fencing), `on_expiry` (`cancel` | `keep`),
  `released_at`, `swept_at`. A nullable `lease` column on `task`, indexed, following the `source` and
  `zone` pattern. Upgrades stay additive (`data/upgrade.py`).
- Library calls for submitters: `Lease.claim(kind, key, ttl, on_expiry, force=False)`,
  `lease.renew()`, `lease.release(cancel=True)`, and a `lease=` argument to `Task.new`.
- Command line: `hs lease list|show|release|sweep`, `hs list --lease KEY`,
  `hs update --cancel --lease KEY`.
- Who sweeps: the server, on a period (`server.lease_sweep`, off by default), plus `hs lease sweep`
  for sites that run no long-lived server. One sweeper at a time is enough. A sweep is idempotent.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN a submitter claims a lease with a kind, a key and a TTL, the database SHALL record it
  and return its id and generation.
- **R2** — WHEN tasks are submitted with a lease, each task SHALL reference it, and `hs list --lease`
  SHALL select exactly those tasks.
- **R3** — WHILE a lease is renewed within its TTL, no sweep SHALL change any of its tasks.
- **R4** — WHEN a lease's TTL has passed without renewal and its `on_expiry` is `cancel`, the next
  sweep SHALL cancel its unfinished tasks, SHALL leave every completed task as it is, and SHALL
  record the sweep on the lease.
- **R5** — WHEN a lease is released with a cancel, its unfinished tasks SHALL be cancelled at once.
  WHEN released without one, they SHALL stay queued and unowned.
- **R6** — IF a claim names the key of a live lease, the claim SHALL be refused unless forced. A
  forced claim SHALL raise the generation, and a renewal or release that carries an older generation
  SHALL fail.
- **R7** — Tasks with no lease SHALL be scheduled, reverted, retried and listed exactly as before.
- **R8** — WHEN a swept task is held by a relay or already running on a client, the cancel SHALL
  reach it (depends on `evicted-client-fencing-and-cancel-reach`).

## Notes

- **Depends on a conditional cancel.** `Task.cancel_all` writes every row "whatever state it is in"
  (`src/hypershell/data/model.py:1436`), so a result that lands between a sweep's read and its write
  would be overwritten with `-1`. R4 needs `completion_time IS NULL` on the write itself.
  `nf-hypershell`'s bridge already does this outside the model.
- **Depends on cancel reach for running work.** Until
  [`evicted-client-fencing-and-cancel-reach`](evicted-client-fencing-and-cancel-reach.md) lands, a
  sweep stops what is queued and staged, not what a client is already running.
- **Clocks.** Expiry should be judged on the database's clock (`now()` on PostgreSQL), not the
  sweeper's. SQLite stores naive local times, so a SQLite sweep must run on the database's host or
  carry an explicit offset. An open problem for `/hs-plan`.
- **Relays.** Leases live upstream, with the canonical task rows. A relay needs no lease of its own,
  but R8 needs the relay's withdraw path (`Dispatcher.withdraw`, used by `hs relay`).
- **Why not `Source`.** `Source` is per-ingest provenance, consulted by re-submission gating and
  written once. A lease is per-owner liveness, written every interval. Folding one into the other
  would make gating depend on a heartbeat. They may still be linked (a lease's tasks share one
  source).
- **Who would use it first:** `nf-hypershell` (renew from the executor; claim key = Nextflow session
  id, so `-resume` re-claims with `force`), and hypershell-mcp (its `submitter` and `batch` tags are
  the same idea without liveness).
- Related: [`process-group-signal-teardown`](process-group-signal-teardown.md) (what a cancel can
  stop on the client), [`progress-eta-surface`](progress-eta-surface.md),
  [`queue-submit-database-server`](queue-submit-database-server.md) (a submitter without database
  credentials would need leases through the queue too).
- Found by: `hypershell-nextflow` spike, attached-mode design (2026-11-21), on the maintainer's
  request.
