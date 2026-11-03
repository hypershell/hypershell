---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Concurrent first submits can collide creating the reserved source row

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`Source.reserved` (`src/hypershell/data/model.py:1045-1057`) is a get-or-create with no conflict
handling: it looks up the fixed-id row (`<direct>` or `<stdin>`) and, when it is missing, inserts
it. Two processes that both miss the lookup both insert, and the second fails on the primary key.
So several `hs submit`s started together against a database whose `source` table is still empty
can fail: one exits rc=5 with `IntegrityError: UNIQUE constraint failed: source.id` and writes a
traceback file. Its tasks are not submitted.

Evidence, from 6 concurrent `hs submit`s per run:

- 1 in 20 runs failed on `origin/develop` with a fresh database;
- 2 in 20 runs failed on the `schema-upgrade-step` branch with a fresh database;
- 2 in 10 runs failed on that branch against an upgraded 2.8.3 database.

Every 2.8 database starts with an empty `source` table once the schema upgrade has added it, so
after that cycle lands the race applies to upgraded databases as well as new ones.

## Why it was deferred

Found by the second review of `schema-upgrade-step`, outside its diff: the upgrade step itself
never failed, and the failure comes after it, in the first insert of a reserved row.
**Pre-existing** in `develop`.

## Outcome / vision

Any number of processes can create the reserved source row at once; each ends up using the one
row, and none fails because another got there first.

## Sketch of the acceptance criteria

- **R1** — WHEN several processes call `Source.reserved` for the same id against a database without
  that row, each SHALL return the row and none SHALL fail, on SQLite and PostgreSQL.
- **R2** — WHEN concurrent first `hs submit`s run against an empty `source` table, all SHALL exit 0
  with every task submitted.
- **R3** — A test SHALL reproduce the collision before the fix (e.g. two sessions that both miss the
  lookup before either inserts), so it does not depend on timing luck.

## Notes

- Related: `spec/schema-upgrade-step/REVIEW.md` (review cycle 2, *Out-of-diff context*).
- Found by: `schema-upgrade-step` review cycle 2, 2026-11-02.
