---
status: unshaped
kind: fix
appetite: small
lane: public
---

# `rotatedb` has an acknowledged lost-update window

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The clone-then-delete rotation concedes the race in its own comment — "more tasks may be updated in
the time between" (`src/hypershell/data/__init__.py:134`). A crash between the `VACUUM INTO` and the
delete leaves rows in both files or neither. Nothing in the CLI or the docs tells an operator that
`hs initdb --rotate` against a live campaign can lose updates.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, M15) — a review
pass. **Pre-existing** in `develop`.

## Outcome / vision

Rotation is either safe against concurrent updates, or honestly documented as an offline
operation — with the CLI saying so rather than the source comment.

## Sketch of the acceptance criteria

- **R1** — The rotation window SHALL be closed (quiesce or transaction-fence), or `rotatedb` SHALL
  be documented and surfaced as offline-only, with a runtime warning when a live server is detected.
- **R2** — A crash mid-rotation SHALL leave a recoverable state, and the recovery SHALL be
  documented.

## Notes

- **Rides the `turso` work if it is not fixed sooner.** `turso` R8 already puts `rotatedb`/
  `--vacuum` under test on the new provider, and R9–R11 build partition-attached listing on top of
  the rotation family — the mechanism gets reworked there anyway. Documenting the limitation now is
  cheap and can land in any docs pass; the mechanism fix is best folded into that phase rather than
  done twice.
- Related: `issues/turso.md` (R8–R11).
- Found by: GNU Parallel comparison review, 2026-08 (M15).
