---
status: adopted:update-group-and-legacy-filters
kind: fix
appetite: small
target: 2.9.0
lane: public
---

# The single-task `hs update ID FIELD VALUE` form ignores filter options given with it

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`TaskUpdateApp.run` (`src/hypershell/task.py:1063-1069`) sends any three positional arguments led
by a task id to `update_legacy` (`:1071-1087`). That method reads only the id, the field and the
value. It never reads the filter options: `-w/--where`, `-t/--with-tag`, `-g/--group`,
`-l/--limit`, `-s/--order-by`, or the aliases `-F`, `-S`, `-C`, `-R`, `-X`, `--retries` and
`--signal`. Given with that form, they are accepted and silently dropped, and the write goes ahead.
The help lists every option for every form and says only that the legacy interface "remains valid"
(`:967-968`; `docs/_include/task_update_desc.rst:30`).

Probed on `develop` `2af6a734` (fresh SQLite site, one task in group 0 that never ran):

- `hs update ID args 'echo changed' -g 99 -w 'exit_status == 7' --limit 1 -F` wrote
  `args = echo changed`, with no warning and exit 0.
- The filtered form with the same filters, `hs update args='echo bulk' -g 99 -w 'exit_status == 7'
  -F -f`, matched no task and wrote nothing.

A user who adds a filter as a guard, such as "only if it failed", gets an unconditional write.

## Why it was deferred

Named as a candidate seed by `update-client-retry`'s shaping (2026-11-22), which takes the same
form's action options (`--cancel`, `--revert`, `--delete`, `--remove-tag`) and leaves its filters
unchanged. That cycle is in flight; this seed was written from `hs update`'s code and the probe
above. **Pre-existing.**

## Outcome / vision

A filter option given with the single-task form is never silently dropped: either it applies, so
the task is written only when it matches, or the command refuses the combination before anything
is written.

## Sketch of the acceptance criteria

- **R1** — WHEN `hs update ID FIELD VALUE` is given any filter option, the command SHALL NOT write
  the task unless the filter is honoured: it SHALL either write only when the task matches, or exit
  2 with a message naming the options before anything is written.
- **R2** — The single-task form without filter options SHALL behave as today.
- **R3** — The help text and `docs/_include/task_update_desc.rst` SHALL state the rule. Docs name
  top-level commands only.

## Open questions for shaping

- **Apply or refuse.** Refusing matches how `update-client-retry` treats the action options with
  this form, but it refuses input accepted today, which the roadmap's Target rules send to a minor
  release (2.10.0) rather than a 2.9.x patch. Applying the filter changes no interface. Shaping
  decides the release with the choice.
- Sequence after `update-client-retry`, which changes the same `run` routing.

## Notes

- Related: [`update-legacy-tag-lost-write.md`](update-legacy-tag-lost-write.md) (the same method's
  tag write), [`spec/update-client-retry/GOAL.md`](../spec/update-client-retry/GOAL.md) (#119: the
  other form's dropped arguments, now refused).
- Found by: `update-client-retry` shaping (2026-11-22), as a candidate seed.
