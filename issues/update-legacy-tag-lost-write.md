---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# The legacy `hs update ID tag KEY:VALUE` can lose a concurrent tag change

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The legacy single-task form edits a tag by reading the task's whole tag set and writing back the
merge (`src/hypershell/task.py`, `TaskUpdateApp.update_legacy`:
`Task.update(uuid, tag={**Task.from_id(uuid).tag, **Tag.parse_cmdline_list([value])})`). A tag
change another writer commits between the read and the write (another `hs update`, or a server
writing tags) is overwritten and lost, on every database, with or without TimescaleDB. The filtered
form writes tag changes in one statement and does not have this window.

Pre-existing; out of scope for `hs-update-contended-writes` (#111), which named it in its Non-goals.

## Direction

Write the legacy tag change the way the filtered form does (one statement that merges the key into
the stored tags), or route the legacy form through the filtered path with an id filter, and pin it
with a test that commits a tag change between the read and the write.

## Related

- `spec/hs-update-contended-writes/GOAL.md` (Non-goals); the filtered form's tag write in
  `src/hypershell/data/model.py`.
