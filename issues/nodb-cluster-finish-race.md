---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A `--no-db` cluster can report a failed run when a client dies just after its last result

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

**Depends on `cluster-launch-failures` landing.** That cycle (in flight on
`fix/cluster-launch-failures`, 2026-11-14) makes `hs cluster` stop its server and exit non-zero,
with one error, when every client it launched has exited, at least one of them non-zero, and the
server still holds tasks not completed (its R1). Its R4 says a cluster whose clients complete the
work ends as it does today, and that a client exiting non-zero after the work is done is warned
about but does not make the run fail.

With a database, "tasks not completed" is read from the database. Without one (`--no-db`), the
server keeps no task rows to count, so the cycle's predicate (`work_remains` in
`src/hypershell/cluster/watch.py` on that branch) reads the work as remaining when the server is
still running a few seconds after every returned result has been written, and its submitter is
still running or has submitted any task. A server whose clients finished the work ends on its own
within that time, so this is right in every case but one:

A client that returned every result, then exited non-zero in the moment before it sent its
`FINISHED` heartbeat, leaves the server waiting to evict it (`server.evict`, 600 s by default)
before it ends. The predicate cannot tell that server from one whose client failed with work left,
so the cluster reports a failed run although every result is recorded, which R4 forbids. Today
(`develop`, `f6a1d62`) the same run exits 0 once the eviction period has passed.

The window is a client crash within milliseconds of its last result, and only without a database.
The cycle's plan gate accepted it as a known gap (`spec/cluster-launch-failures/PLAN.md` §2.3 and
the gate's ruling 3), recorded it in the predicate's docstring, and left this seed to be filed.

## Direction

Give the predicate a count it can trust without a database: the Receiver in
`src/hypershell/server.py` keeps a count of results written, and the cluster compares it with the
number of tasks the submitter put on the queue. Work remains only while results are fewer than
submitted tasks.

## Related

- `spec/cluster-launch-failures/` (GOAL R1 and R4; PLAN §2.3 and the plan-gate rulings).
- Found by: `cluster-launch-failures` planning, 2026-11-14.
