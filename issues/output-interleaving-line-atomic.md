---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Merged task output is interleave-corruptible, with no safe shared-stream mode

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Every executor defaults to the client's own streams (`client.py:664-665`) and hands them straight to
`Popen` (`client.py:759`), so N concurrent subprocesses write to one shared file description with no
locking and no line discipline — long lines interleave mid-line. `-o FILE` shares a single file
object across executors the same way. There is no grouped mode, no line-buffered mode, no
keep-order, and no per-line task tagging (grepping `keeporder`/`tagstring`/`linebuffer` returns
nothing). The only safe alternative today is per-task capture files.

For contrast, GNU Parallel makes grouping the *default* (`parallel:2950-2951`): each job buffers to
its own temp file and is emitted atomically.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, H9) — a review pass.
**Pre-existing** in `develop`.

Deferred with a deliberate ranking: it is the **lowest-priority of the high-severity findings** for
HyperShell specifically. Unlike Parallel, merged stdout is not this tool's product — the database
and the per-task capture files are, and both are byte-faithful and collision-free today. The finding
is real; the urgency is inherited from a tool with a different centre of gravity. Document the
current interleaving contract in the near term (see `issues/docs-and-runtime-surfacing.md`) and let
the emitter be a shaped decision later.

## Outcome / vision

A user who wants merged output on one stream can get it line-atomically, optionally tagged with the
task id, without falling back to N scattered files.

## Sketch of the acceptance criteria

- **R1** — A mode SHALL exist in which concurrent task output written to a shared stream is
  line-atomic.
- **R2** — Optional per-line task attribution SHALL be available.
- **R3** — The current default behaviour and its interleaving contract SHALL be documented, whatever
  the default becomes.

## Notes

- Minimum viable shape from the review: client-side reader threads per task with a per-line lock.
  Note the cost — that is a thread per running task on top of the executor model.
- Whether keep-order (Parallel's `-k`) belongs here at all is a scope question: it implies buffering
  a task's whole output, which at HyperShell's scale is a different feature.
- Found by: GNU Parallel comparison review, 2026-08 (H9).
