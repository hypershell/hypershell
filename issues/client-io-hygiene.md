---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Client I/O hygiene batch: binary output, disk-full, stdin, crash logs, dead code

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Five small, independent defects clustered in the client/task-output path. Batched because each is a
few lines, none is controversial, and they share reviewers and tests.

- **Binary output crashes retrieval (M8).** `hs task info --stdout` and `hs task run`'s output
  print read capture files in text mode and stream them (`task.py:240-241`, `:422-430`); one
  non-UTF-8 byte raises `UnicodeDecodeError` into the generic traceback handler. The on-disk file
  is byte-faithful — only our reader faults. Fix: open `rb`, copy to `stdout.buffer`.
- **Disk-full during capture is undetected (M10).** Capture files are opened and later only closed
  (`client.py:747-748`, `:776-777`) — no write checking, no flush/fsync, no `ENOSPC` handling.
  Output silently truncates on a full disk while the task records success.
- **Tasks inherit the client's stdin (M16).** The task `Popen` sets no `stdin`
  (`client.py:759-761`), so N concurrent tasks race the shared fd 0 and a stdin-reading command can
  steal the client's input or block. Fix: `stdin=DEVNULL` by default.
- **Crash-log filename collisions (L11).** Uncaught-exception logs are named to 1-second resolution
  and opened `'w'` (`core/exceptions.py:66`, `:74`) — two faulting threads in the same second
  overwrite each other. Add pid/uuid.
- **Dead code (L12).** `core/remote.py`'s `RemoteProcess`/`BufferedTransport` stops reading as soon
  as `exit_status_ready()` (`core/remote.py:190`), dropping buffered tail output, and busy-spins —
  and has no callers. Delete it, or fix it before someone uses it.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`: M8, M10, M16, L11,
L12) — a review pass. All **pre-existing** in `develop`.

## Outcome / vision

Captured output survives the round trip whatever bytes it contains; a full disk is an error, not a
silent truncation; a task cannot steal the client's stdin; concurrent crashes both leave a log; and
the unused transport is gone.

## Sketch of the acceptance criteria

- **R1** — Captured output SHALL be retrieved byte-faithfully regardless of encoding.
- **R2** — IF a capture write fails for lack of space, THEN the condition SHALL surface rather than
  silently truncating a task that reports success.
- **R3** — A task subprocess SHALL NOT inherit the client's stdin by default.
- **R4** — Concurrent uncaught-exception logs SHALL NOT overwrite one another.
- **R5** — The uncalled `core/remote.py` transport SHALL be removed or repaired.

## Notes

- R3 is a behaviour change for anyone whose tasks read stdin — a release-note line, and a plan
  decision on whether an opt-out is warranted.
- M11's host-key half was deliberately routed to the **security lane**, not this batch. The
  shared-filesystem documentation half of M11 is in `issues/docs-and-runtime-surfacing.md`.
- Found by: GNU Parallel comparison review, 2026-08.
