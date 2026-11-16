---
status: unshaped
kind: fix
appetite: small
lane: public
---

# An autoscaler read that meets a locked SQLite database ends the cluster with a traceback

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`tests/test_autoscale_failed_launches.py::test_cluster_reading_a_stream_stops_after_three_failed_launches[busy-stdin]`
failed once on CI (Python 3.12, 2026-11-15, on the head of #111, whose diff does not touch the
autoscaler or the submitter). The run feeds `hs cluster --autoscaling fixed -P 2s --launcher false`
from a producer that writes a line at a time without stopping, on SQLite. Instead of stopping after
three failed launches with "3 launched clients in a row exited non-zero", the autoscaler thread died
on its registration count:

```
CRITICAL [hypershell.cluster] OperationalError: (sqlite3.OperationalError) database is locked -
  [SQL: SELECT count(*) AS count_1 FROM (SELECT client.id … FROM client WHERE client.server_id = ?) …]
CRITICAL [hypershell.cluster] Exception traceback written to …/exception-….log
```

The read is `client_registrations()` (`src/hypershell/cluster/remote.py`), added by
`cluster-launch-failures` (#107) and called from `AutoScaler.count_failures` on every check while
`autoscale.failed_launches` is above zero (3 by default for `hs cluster`). While the submitter's
committer writes one task per commit, a reader can meet the lock for longer than the busy timeout,
and nothing in the autoscaler treats that as transient: the run ends with a traceback file instead
of either the failed-launch stop or carrying on. The autoscaler's other database reads (sizing a
zone) are likely exposed the same way; check them.

Not yet released: #107 landed after 2.9.0a8.

## Direction

Treat a locked-database error on an autoscaler read as transient: skip that check (log at debug or
warning) and try again on the next one, so a busy writer delays the decision without ending the run.
Confirm whether the busy timeout applies to these reads, and pin the case with a test that holds the
lock across one check.

## Related

- `spec/cluster-launch-failures/` (#107), `issues/database-locked-during-ingest-hangs.md` (the
  submitter's side of a locked database).
- Found by: CI on #111, 2026-11-15; job re-run.
