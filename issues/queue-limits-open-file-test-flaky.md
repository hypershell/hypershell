---
status: unshaped
kind: test
appetite: small
target: 2.9.x
lane: public
---

# The open-file-limit test can time out waiting for two distinct clients

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`tests/test_queue_limits.py::test_server_keeps_its_files_at_the_open_file_limit` has failed twice
on CI and passed on re-run both times, with no change to the file in between: once on Python 3.12
(2026-11-14, #104) and once on Python 3.13 (2026-11-15, #110). Both times the failing assertion is
the wait for every admitted client to have completed a task:

```
assert wait_for(lambda: count(db_path, 'SELECT COUNT(DISTINCT client_id) FROM task '
                                       'WHERE completion_time IS NOT NULL') == 2)
```

The test starts two clients against a server whose open-file limit is lowered, with 20 quick tasks
and 4 gated ones. If one client takes the quick tasks faster than the other connects and gets a
bundle, every quick task can complete under one client id; the other client then gets only gated
tasks, which do not complete until the test opens the gate after this wait. The wait can never be
met, and it times out.

## Direction

Confirm the cause from the CI logs (which client ids completed tasks). If confirmed, make the
precondition not depend on scheduling order: wait until both clients are connected (for example,
both hold a task, completed or not), or give each client its own quick tasks. The test's purpose,
refusing clients at the limit while keeping the database and log, must stay as it is.

## Related

- Found by: CI on #104 (3.12) and #110 (3.13); both re-runs green.
