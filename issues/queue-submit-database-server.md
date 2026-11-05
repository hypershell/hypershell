---
status: unshaped
kind: feature
appetite: small
lane: public
---

# `hs submit -q` cannot feed a server with a database

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs submit -q` puts task bundles on a running server's live queue. Only a server without a
database (`--no-db`) serves that queue: a server with a database schedules from its database, and
nothing drains the live queue.

- **Released (2.8.x, 2.9.0a2):** a server with a database handed the submitted bundles to its
  clients, but their rows were not in its database. The tasks ran, and the server's Confirm and
  Receiver threads each died on an uncaught `StaleDataError`, after which the server recorded no
  results for the rest of the run.
- **Unreleased `ec75169` (zone-dispatch's base):** only the Confirm thread died. The Receiver
  refused the submitted tasks' results as stale, so those tasks ran and nothing was recorded for
  them.
- **zone-dispatch (Z2), before its review-1 remediation:** nothing drained the queue, so the
  submission hung and no task ran.
- **Since that remediation:** `hs submit -q` asks the server first and, against a server with a
  database, exits at once with an error that names the reason.

So a host that can reach the server but not its database cannot add work to a database-backed run.

## Intention

Decide whether a server with a database should accept live submissions. If it should, the server
writes each submitted bundle to its database (as `hs submit` would), so the rows are scheduled,
confirmed and written back like any other, and zones, groups and source gating keep their meaning.
If not, the refusal stands and the docs say so (they already do).

## Refs

- `spec/zone-dispatch/REVIEW.md` (cycle 1, F4, and the triage that ruled the refusal; cycle 2, F6,
  for what the released servers did).
- `src/hypershell/core/dispatch.py` (`Dispatcher.takes_submissions`), `src/hypershell/submit.py`
  (`LiveSubmitThread.check_server`).
