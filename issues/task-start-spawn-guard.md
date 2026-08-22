---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Unguarded task start kills the executor, leaks resources, and poisons the task

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`start_task` acquires resources (`src/hypershell/client.py:730`), stamps `start_time`, and then
performs unguarded I/O: the capture-file opens (`client.py:747-748`), the monitor CSV
(`client.py:751`), and the `Popen` itself (`client.py:759-761`). Any `OSError` from that region —
an unwritable capture directory (site-dir creation swallows `PermissionError`,
`src/hypershell/core/platform.py:126`), `ENOSPC`, `E2BIG` from an oversized line, a `cwd` that does
not exist on that particular client, fork `ENOMEM` — propagates through the FSM (`core/fsm.py`)
into `Thread.run`, which stores the exception and lets the thread exit
(`src/hypershell/core/thread.py:37`).

Three consequences compound:

1. **The executor dies.** Each occurrence permanently removes one executor from that client. A
   client degrades executor-by-executor, silently, until it is doing nothing.
2. **Declared resources are never released.** `acquire` ran; there is no `finally`, so the
   process-global counters under `executor_lock` (`src/hypershell/core/resource.py`) leak.
3. **The task is a poison pill.** It stays in-flight, a later revert re-runs it, and it crashes
   the next executor the same way.

The contrast inside the same file makes the gap obvious: template and resource errors *are*
handled gracefully, with dedicated sentinels and no executor loss (`client.py:712-724`,
`TASK_TEMPLATE_ERROR`/`TASK_RESOURCE_ERROR` at `client.py:612-613`). Spawn failure simply was
never given the same treatment.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, H8) — a review
pass, not a fix pass. **Pre-existing** in `develop`.

## Outcome / vision

A task that cannot be started fails as a *task*, exactly like a template or resource error: it is
recorded with a distinguishable status, its declared resources are returned, and the executor
picks up the next task. One bad directory or one oversized command line costs a task, not a
client.

## Sketch of the acceptance criteria

- **R1** — IF an `OSError` occurs while opening capture files or spawning the task subprocess,
  THEN the task SHALL be recorded as failed with a dedicated never-ran sentinel and the executor
  SHALL remain alive and continue with the next task.
- **R2** — Declared resources acquired for a task SHALL be released on every exit path from the
  start region, including the failure paths (no leak of the `executor_lock` counters).
- **R3** — The new sentinel SHALL be below `-1000`, per the reserved-range rule in `AGENTS.md`
  (`-1..-64` remain reserved for signal deaths), and SHALL be documented in the same table.
- **R4** — The failure SHALL be visible to the operator: logged with the underlying `OSError`, and
  distinguishable in `hs list`/`hs info` from a real non-zero process exit.

## Notes

- `.local/IMPROVEMENTS.md` suggests `TASK_SPAWN_ERROR = -1003`; the value is the plan's to confirm
  against the current sentinel table.
- Interaction to check at plan time: whether a spawn-error sentinel should be *terminal* or
  retryable. `issues/resource-error-terminal-and-retry-defaults.md` asks the same question for
  `-1001`/`-1002`, and the two answers should be consistent — a deterministic failure retried to
  exhaustion pins the group.
- `client.py` has **no dedicated test file** (`AGENTS.md` high-risk list); its coverage is
  indirect. Plan for real tests here rather than relying on integration coverage.
- Found by: GNU Parallel comparison review, 2026-08 (H8).
