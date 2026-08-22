---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Ingest robustness: all-or-nothing submission, and no ARG_MAX awareness

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

**Submission is all-or-nothing brittle (M9).** Task files are opened in strict text mode
(`submit.py:155`, `:1073`, `:1403`, `:1416`) — one undecodable byte aborts the whole batch — and one
malformed `# HYPERSHELL:` tag raises out of the loader (`data/model.py:436-437`, re-raised at
`submit.py:996-998`) *after* part of the file may already be submitted. A million-line task file
with one bad line leaves the user with a partial, unlabelled submission and no per-line diagnosis.

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
- Sequence after `issues/submit-comment-truncation.md` — same ingest path, and that fix changes what
  "malformed" means.
- Found by: GNU Parallel comparison review, 2026-08 (M9, L6).
