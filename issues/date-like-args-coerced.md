---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Date-like task arguments are turned into datetimes on the wire

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Task bundles are JSON. On the way in, `Entity.from_json` passes every field through
`from_json_type` (`src/hypershell/core/types.py`), which converts *any* string longer than five
characters that `datetime.fromisoformat` accepts. On Python 3.11 and later that includes basic-format
dates. So a task whose `args` is `20261102` or `2026-11-02` reaches the client as
`datetime(2026, 11, 2, 0, 0)`. Template expansion then fails on the non-string value (a probe of
`Template('echo {}').expand(...)` raises `TypeError`), so the client records the task as a template
error and never runs the line the user submitted.

A list of dates is an ordinary `hsx` input, so a plausible class of workloads fails outright, with
an error that points at the template rather than at the codec.

Evidence: probe during `writeback-scoped-conditional`'s research
(`spec/writeback-scoped-conditional/research/01-assignment-token.md`, side finding), re-checked
with `from_json_type('20261102')` and `from_json_type('2026-11-02')`, both returning datetimes, and
a bundle round-trip of `Task.new(args='2026-11-02')`. Not yet driven end to end through `hsx`.
**Pre-existing** in `develop`. Before `writeback-scoped-conditional`, the whole-row writeback also
wrote the converted value back into `args`; that part is closed, the expansion failure is not.

## Why it was deferred

Out of scope for `writeback-scoped-conditional`, which fenced the result write and left the codec
unchanged.

## Outcome / vision

A string field comes back from the wire as the same string. Only the datetime columns are decoded
as datetimes.

## Sketch of the acceptance criteria

- **R1** — A test SHALL submit tasks whose `args` are date-like (basic and extended ISO forms) and
  show the command a client runs.
- **R2** — WHEN a bundle is decoded, THEN only fields declared as datetimes in the model SHALL be
  converted; every other string SHALL be unchanged.
- **R3** — The wire format SHALL stay readable by peers of the same schema version.

## Notes

- `Task.columns` already declares each field's type, so the decoder can convert by column rather
  than by guessing.
- Related: `issues/submit-comment-truncation.md` (another silent change to the submitted command).
- Found by: `writeback-scoped-conditional` plan research, 2026-11-02.
