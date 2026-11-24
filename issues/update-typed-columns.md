---
status: adopted:update-typed-columns
kind: fix
appetite: small
target: 2.9.0
lane: public
---

# `hs update` stores text in the task's integer columns

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs update` writes the value it is given into a task's integer columns without reading it by the
column's kind. On `develop` (03a02bd8), `hs update exit_status=x`, `attempt=x`, `cores=x`,
`memory=x` and `timeout=x` each exit 0 and store the text (SQLite). `cores`, `memory` and `timeout`
drive dispatch, so a text value there can stall or mis-place work. `memory` and `timeout` also have
their own grammars (sizes and durations) that `hs submit` accepts but `hs update` does not apply.
Found while shaping `update-group-and-legacy-filters` (2026-11-24), which covers `group` only.

## Outcome sought

`hs update` reads each column by its kind, through the same parsers `hs submit` uses:
- `cores`, `attempt` and `exit_status` as integers;
- `memory` as a size;
- `timeout` as a duration.

Ranges come from `bounded-inputs`' table. A value that does not parse, or falls outside its range,
is refused with exit 2 before anything is written, in both the filtered and the single-task forms.

## Notes

- Sequence after `bounded-inputs` (the parsers and ranges) and
  `update-group-and-legacy-filters` (the refusal style for `hs update`).
- Decide at shaping whether `exit_status` and `attempt` should be writable at all, or are
  bookkeeping that `hs update` should refuse to touch (`--revert` and `--cancel` already cover the
  common cases).
- Related: `spec/submit-input-checks/` (#118, the group rule), `spec/update-client-retry/` (#119).
