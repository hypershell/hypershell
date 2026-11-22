---
status: unshaped
kind: feature
appetite: small
target: 2.10.0
lane: public
---

# A submitter that owns its retries cannot opt its tasks out of the server's

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Retries are a server-wide setting: `--max-retries` (`server.attempts`) reaches `Task.next` as
`attempts`, and `query_retryable` selects every failed task under it (`data/model.py:802-817`).
Nothing on a task says "never retry this one".

Some submitters own their retries. Nextflow retries a failed task itself, in a new work directory,
with new resources if the pipeline says so (`errorStrategy 'retry'`). If HyperShell also retries,
the same task runs again in its old work directory while Nextflow's retry runs in the new one, and
the second result Nextflow reads may be HyperShell's. Agents that submit through hypershell-mcp are
in the same position.

So `nf-hypershell` can attach only to a site whose operator runs it with `--max-retries 0`, which
takes retries away from everyone else's tasks on that site. Its managed clusters set
`--max-retries 0` themselves. That costs nothing there, because every task on them is Nextflow's.

**Pre-existing**: retries have always been per server.

## Why it was deferred

Found from outside the repository, while building `hypershell/hypershell-nextflow`, which
documents the `--max-retries 0` requirement instead. It is an interface addition, so it belongs in
a minor release.

## Outcome / vision

A submitter can mark its tasks as not retryable. The server never retries those, whatever its
`--max-retries`, and treats every other task as it does today.

## Sketch of the acceptance criteria

- **R1** — WHEN a task is submitted marked as not retryable, the server SHALL never schedule a
  retry of it, whatever its `attempts`.
- **R2** — A failed not-retryable task SHALL count toward its group exactly as a failed task with
  its retries spent does today, so group gating is unchanged.
- **R3** — Tasks without the mark SHALL be retried exactly as today.
- **R4** — The mark SHALL be settable from `hs submit` and from the Python API, and visible in
  `hs list`.

## Notes

- Two shapes to weigh at promotion. A column, such as a per-task `attempts` ceiling, is the most
  general but needs a schema migration. A reserved tag that `query_retryable` honours needs none,
  but makes a tag part of the scheduler's contract, and the tag filter costs more on a large table.
  A per-task ceiling also covers "retry this one three times on a server that retries once".
- `count_retryable` must agree with `Task.next`. That is the invariant its docstring states, and
  it is what keeps a group from waiting on a retry that will never come.
- Related: `issues/resource-error-terminal-and-retry-defaults.md` (sentinels that should be terminal
  for every task), `issues/failure-budget-halt-policy.md`,
  `issues/cluster-max-retries-defaults.md`.
- Found by: `hypershell/hypershell-nextflow` development, 2026-11-21 (its `DESIGN.md`, *What
  HyperShell should grow*, item 9).
