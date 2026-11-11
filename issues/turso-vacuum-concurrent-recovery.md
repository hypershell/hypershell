---
status: unshaped
kind: fix
appetite: big
lane: public
---

# A killed turso vacuum must not crash the processes that open the database after it

> **Candidate, not a contract.** Evidence captured; appetite, non-goals and R-IDs are not agreed.
> `/hs-feature` promotes it.

## Problem

Under `provider = turso` on a filesystem turso accepts for its multi-process mode, `hs initdb
--vacuum` runs an in-place `VACUUM` on a single-process connection while it holds the database. Other
processes that try to open the database wait. The design took six review cycles on the `turso`
branch (2026-11-08 to 2026-11-10), and each fix exposed a deeper turso limit:

1. **Cycle 4.** A single-process checkpoint left turso's shared WAL index (`-tshm`) stale; after a
   killed vacuum, acknowledged writes were lost. Fixed: the hold zeroes `-tshm` before and after.
2. **Cycle 5.** The shared index holds 262 144 frames. A vacuum killed during its fold with more
   committed frames than that left later processes panicking, hanging, or printing wrong counts with
   exit 0. Fixed: a page limit, now 200 000 pages in use. Turso's `VACUUM` rebuilds indexes 12–14 %
   larger, at most 1.069 × the pages in use over the whole HyperShell schema.
3. **Cycle 6.** Below the limit, a vacuum killed during its fold still leaves processes that open
   the database right after it panicking (`core/storage/wal.rs:1605`, `core/storage/btree.rs:8738`)
   or reading an unreadable row. This was seen from about 100 000 pages in use. No acknowledged
   write is lost.

2.9.0a7 ships `--vacuum` under turso with the risk flagged. The docs carry a warning and a
procedure: pause scheduling, vacuum, run the vacuum again if it was interrupted, then resume.
HyperShell's opt-in test (`HS_TEST_VACUUM_AT_LIMIT=1`, `tests/test_maintenance.py`) records the
fold kill with waiters as an expected failure. A database opened single-process is not exposed.

## Why it was deferred

The failure appears to be in turso's engine. It is not yet known whether HyperShell's own `-tshm`
reset provokes it, or whether `VACUUM` is needed at all. That needs evidence from
[`turso-upstream-repro`](turso-upstream-repro.md) or a turso fix. Another cycle on the same design
without either was judged not worth it.

## Outcome / vision

An administrator can run `hs initdb --vacuum` under multi-process turso with scheduling live. A
vacuum killed at any point leaves every process that opens the database correct, or failing with a
clean error. The warning and the procedure go away.

Possible routes, depending on the evidence:

- a turso release that fixes concurrent recovery;
- HyperShell serialising the first opens after an interrupted vacuum, if the reproduction's variant
  (d) shows that avoids it;
- turso supporting in-place `VACUUM` beside `multiprocess_wal`, so that no single-process connection
  is needed.

## Sketch of the acceptance criteria

- **R1.** WHEN a turso vacuum is killed at any point and several processes then open the database at
  once, each SHALL give a correct result or fail with a clean error, at the page limit, on macOS and
  Linux. The expected-failure mark in the at-limit kill test is removed and the test passes.
- **R2.** The docs warning and procedure SHALL be removed or relaxed to match what R1 shows.
- **R3.** The page limit SHALL be revisited against turso's frame-index capacity at the time.

## Notes

- Related: `spec/turso/` (GOAL rulings after cycles 3, 5 and 6; REVIEW cycles 4 and 5; TECH P3);
  [`turso-upstream-repro`](turso-upstream-repro.md); the upstream issue once filed.
- Found by: `turso` P3, review cycles 4–5 and the cycle 6 build.
