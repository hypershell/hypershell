---
status: shaped
kind: research
appetite: small
lane: public
shaped-as: turso-upstream-repro
---

# Reproduce turso's crash after a killed vacuum without HyperShell

> **Shaped candidate, not a contract.** The maintainer set its scope in session on 2026-11-10 (the
> clarifications below). Acceptance into a cycle is still `/hs-feature` creating the branch and
> `spec/{slug}/GOAL.md`. Until then nothing here is graded.

## Problem

The `turso` cycle's sixth vacuum pass (2026-11-10, pyturso 0.8.3, macOS APFS) found that in turso's
multi-process mode (`multiprocess_wal`), a `VACUUM` killed while its final `TRUNCATE` checkpoint
folds the compacted image into the database can leave the processes that open the database right
after it panicking or reading an unreadable row:

- at about 200 000 pages in use (210 282–210 302 committed WAL frames, about 52 000 below the
  262 144-frame shared index), killed at the fold with SIGKILL or SIGTERM, two runs: waiters
  panicked (`core/storage/wal.rs:1605`, "shared WAL frame index length changed while publishing an
  entry"; `core/storage/btree.rs:8738`, "corrupted database, stack is bigger than expected") or
  raised `TypeError: fromisoformat` on an unreadable row;
- at about 100 000 pages in use (104 859 frames): a waiter panicked at `wal.rs:1605`;
- no acknowledged write was lost.

That evidence comes from HyperShell's own kill tests (`tests/test_maintenance.py`, opt-in with
`HS_TEST_VACUUM_AT_LIMIT=1`). There the vacuum runs on a single-process connection inside a lock that
HyperShell holds, and HyperShell zeroes turso's `-tshm` before and after the vacuum. So the claim to
put to the Turso team is not yet settled. It might be any of these:

- turso's multi-process recovery races when several processes open a large committed WAL;
- HyperShell's `-tshm` reset provokes it;
- `VACUUM` has nothing to do with it, and any large transaction killed during its checkpoint does
  the same.

## Why it was deferred

The `turso` cycle shipped `--vacuum` with the risk flagged (maintainer ruling during cycle 6) rather
than hold 2.9.0a7 for the research. The research needs no HyperShell code, so it is its own cycle.

## Outcome / vision

A script that needs only `pyturso`, run on two platforms, that shows exactly which conditions make
the failure happen. An independent check either confirms or refutes it. The upstream issue leads
with it.

## Clarifications (2026-11-10, Geoffrey in session)

- **Q:** What goes to the Turso team? — **A:** One issue, headlined by this reproduction, the
  strongest item. The cycle's other turso findings follow in a table as additional findings, with
  an offer to file each as a separate issue if they want that. The maintainer files it; agents only
  draft it.
- **Q:** When? — **A:** Now, as its own cycle beside the 2.9.0a7 release.

## Sketch of the acceptance criteria

- **R1.** The reproduction SHALL use only `pyturso` (pinned, 0.8.3 first) and the standard library, and
  run from one command. No HyperShell import.
- **R2.** It SHALL run these variants, each for enough trials to give a failure rate. Each SHALL
  record the frames committed when stopped, every opener's outcome, and the exact panic or error
  text:
  - (a) a large `VACUUM` on a single-process connection, killed during its final checkpoint,
    then several multi-process openers at once, with `-tshm` left as turso left it;
  - (b) as (a), with `-tshm` zeroed before the openers, as HyperShell does;
  - (c) as (a), with a large `INSERT` transaction in place of `VACUUM`;
  - (d) as (a), with the openers serialised behind a lock so that only one recovers at a time.
- **R3.** Every variant SHALL run on macOS (APFS) and on Linux (a local filesystem turso accepts for
  its multi-process mode). The results SHALL be recorded per platform.
- **R4.** An agent that has not seen this cycle SHALL be given only the script, the claim and the
  pinned version, and SHALL try to reproduce or refute the claim. Its result SHALL be recorded as it
  stands.
- **R5.** The upstream draft (`spec/turso/upstream/00-issue.md` once `turso` lands) SHALL be
  rewritten:
  - the headline is the reproduction, its claim worded only as strongly as R2–R4 support;
  - a table lists the other findings (the 262 144-frame shared index and the lone-writer Busy
    past it, the stale `-tshm` after a single-process checkpoint, in-place `VACUUM` refused beside
    `multiprocess_wal`, and the rest of the draft), each with its evidence;
  - an offer to file each finding separately.
- **R6.** The cycle SHALL make no HyperShell code change. Its result feeds
  [`turso-vacuum-concurrent-recovery`](turso-vacuum-concurrent-recovery.md): if (d) avoids the
  failure, that seed gains a HyperShell-side design to try.

## Notes

- Related: `spec/turso/` (GOAL rulings after cycles 5 and 6; TECH P3 cycle 6 and 6b; REVIEW cycle 5),
  [`turso-vacuum-concurrent-recovery`](turso-vacuum-concurrent-recovery.md).
- Found by: `turso` P3, cycle 6 build.
