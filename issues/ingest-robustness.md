---
status: unshaped
kind: fix
appetite: small
target: 2.10.0
lane: public
---

# Ingest robustness: all-or-nothing submission, and no ARG_MAX awareness

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

**Submission is all-or-nothing brittle (M9).** Task files are opened in strict text mode
(`open(…, mode='r')` throughout `submit.py`) — one undecodable byte aborts the whole batch — and one
malformed `# HYPERSHELL:` tag raises out of the loader (`Task.split_argline`,
`data/model.py:683-684`) *after* part of the input may already be submitted. A million-line task
file with one bad line leaves the user with a partial, unlabelled submission and no per-line
diagnosis.

**Since `submit-input-checks` (#118; checked on `develop` `2af6a734`, 2026-11-22).** Input read in
full is now checked before anything is inserted: a named file given to `hs submit`, `hs cluster` or
`hs server` is scanned first (`scan_task_lines`, `submit.py:224`), and `hs submit` with a database
reads standard input in full too. In a probe, `hs submit` given a file or piped standard input with
a malformed tag on line 3, or a file with an undecodable byte on line 3, inserted nothing. The
partial submission remains where input is streamed, as into `hs cluster` or `hs server`: there the
loader refuses a line with a non-integer group alone, and any other error still ends the input
(`Loader`, `submit.py:535-537`). The per-line policy, diagnosis and exit status asked for below are
still open. The file-line citations above were refreshed at the same check.

**No ARG_MAX awareness (L6).** One line becomes one `sh -c` string; beyond Linux's
`MAX_ARG_STRLEN` (128 KiB) it fails at spawn — and today that failure lands as
`issues/task-start-spawn-guard.md` describes, killing an executor. A submit-time length warning
would catch it where the user can act.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, M9 + L6) — a review
pass. Both **pre-existing** in `develop`.

## Outcome / vision

A large task file with a few bad lines submits the good ones, reports the bad ones with line
numbers, and exits with a status that reflects what happened — the user chooses the policy. A line
too long to ever spawn is caught at submit time, not on some node an hour later.

## Sketch of the acceptance criteria

- **R1** — An undecodable byte in a task file SHALL NOT abort the whole submission; the encoding
  behaviour SHALL be explicit and configurable.
- **R2** — A malformed line SHALL be handled by a stated per-line policy (skip / log / count /
  non-zero exit) rather than raising mid-file after partial submission.
- **R3** — Submission SHALL be diagnosable: bad lines reported with line numbers and a count.
- **R4** — A command line too long to spawn SHALL be flagged at submit time.

## Notes

- Validate-then-load versus stream-with-policy is the plan's call; streaming matters at HyperShell's
  scale, and a two-pass read of a huge task file may be unacceptable.
- Sequenced after the `#`-truncation fix, which has landed (`submit-input-checks`, #118): a `#` is
  now part of the command unless it starts the line or a `# HYPERSHELL:` marker, which changes what
  "malformed" means here.
- Overlap: `submit-input-checks` (#118) added a validate-then-insert pass on this same ingest path:
  `hs submit` with a database refuses, all or nothing, a task below the reached group or with a
  non-integer group, buffering piped input in full, and a streamed line with a non-integer group is
  refused alone (`spec/submit-input-checks/GOAL.md`, R4–R7). That cycle's GOAL says whichever lands
  second keeps both contracts, so this seed's per-line policy must keep them.
- Found by: GNU Parallel comparison review, 2026-08 (M9, L6).
