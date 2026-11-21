---
status: unshaped
kind: fix
appetite: small
target: 2.9.0
lane: public
---

# `hs update --delete` silently ignores field assignments and tag changes

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs update` accepts field assignments and tag changes together with `--delete`, and then deletes
the selected tasks without applying or rejecting the rest: `hs update --delete --limit 1 group=9`
deletes one task and says nothing about `group=9` (observed 2026-11-15, recorded in
`spec/hs-update-contended-writes/GOAL.md` § Non-goals). `--cancel`, `--revert` and `--delete` are
already mutually exclusive in the parser (`src/hypershell/task.py`, the action group), but
assignments are not excluded from `--delete`. A user who meant an update and typed `--delete` by
mistake gets no warning before rows disappear (the confirmation prompt counts the rows, not the
mistake).

Pre-existing; out of scope for `hs-update-contended-writes` (#111), which kept the behaviour.

## Direction

Refuse `--delete` combined with any field assignment, tag change or `--remove-tag`, as a usage error
(exit 2) naming the conflicting arguments, before the count or the prompt. Update the `hs update`
docs.

## Related

- `spec/hs-update-contended-writes/GOAL.md` (Non-goals).
