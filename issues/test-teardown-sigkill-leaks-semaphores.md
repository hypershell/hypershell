---
status: unshaped
kind: test
appetite: small
lane: public
---

# Test teardown by SIGKILL leaks a 2.8 server's named semaphores on macOS

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Several tests and the queue bench start `hs` in a session of its own and end it with
`os.killpg(..., SIGKILL)`. SIGKILL gives no process in the session a chance to clean up, and that
includes Python's multiprocessing resource tracker, which is what unlinks a process's POSIX named
semaphores when it exits. The tracker ignores SIGINT and SIGTERM and unlinks whatever is still
registered once the process it serves is gone (CPython's `multiprocessing/resource_tracker.py`), so
only SIGKILL defeats it. On macOS a named semaphore that is never unlinked lives until reboot, and
the system-wide pool is small. Once it runs out, every `multiprocessing.Lock()` on the machine fails
with `OSError: [Errno 28] No space left on device`, and no 2.8 `hs server` can start.

That is how a development Mac stood on 2026-11-06, after repeated runs on the 2.8 line: a bare
`multiprocessing.Lock()` failed with ENOSPC.

**Who holds named semaphores.** A 2.8 server makes four `multiprocessing.JoinableQueue`s, each built
from several semaphores, before it starts its manager process (`src/hypershell/core/queue.py:459-467`
on `origin/master`, 2.8.5). Since `zone-queue-primitive` (#76) a 2.9 server serves its queues from
threads of its own process with `queue.Queue` (`src/hypershell/core/queue.py:32`, `:850-880` on
`origin/develop`, `2d0d74e`), and nothing under `src/` on `develop` makes a multiprocessing
semaphore. So killing a 2.9 `hs` leaks nothing today.

**Where the SIGKILL teardown is, on `origin/develop`:**

- `tests/test_cluster_launch.py:324-329` — `kill_group` SIGKILLs the whole session. `run_in_session`
  calls it in `finally` after every run (`:320`), including a run that `until()` ended with the
  process still alive (`:312`). `start_server` and the missing-key tests end a live `hs server` the
  same way (`:1298`, `:1323`). The same helper is on `origin/master` (`:327-333`), where the server it
  kills is a 2.8 server.
- `tests/bench/bench_queue.py:353-359` — the bench's `kill_group` SIGKILLs a group still running.
  The `skew` subcommand starts the *old* build's `hs server` and kills it in `finally`
  (`new_client_old_server`, `:421-433`). With an old ref on the 2.8 line, every `skew` run leaks
  that server's semaphores. This is the live exposure on `develop`.
- Timeout-only uses, which leak only when a test has already failed:
  `tests/test_dispatch_staged.py:52`, `tests/test_dispatch_version.py:186`,
  `tests/test_dispatch_workload.py:62`, `tests/test_queue_address_family.py:347`,
  `tests/test_queue_gc.py:132`, `:137`, `:379`, and `tests/test_zone_model.py:137`.

## Why it was deferred

Found out of cycle (2026-11-06), while the 2.8 line's tests were run repeatedly on macOS. Out of
scope for the change that found it, which touched docs and the roadmap only. **Pre-existing:** the
`kill_group` helper is on `origin/master` (2.8.5) and on `develop`; the leak needs a 2.8 server,
so on `develop` it is latent in the suite and live only in the bench's `skew` runs.

## Outcome / vision

A test or bench run ends what it started by asking first: SIGTERM to the session, a short wait,
then SIGKILL only for what is left. The server goes, its resource tracker outlives it long enough to
unlink its semaphores, and repeated local runs never exhaust the machine's pool. A run that had to
fall back to SIGKILL says so.

## Sketch of the acceptance criteria

- **R1** — WHEN a test or the queue bench ends a session it started, it SHALL send SIGTERM to the
  session first and SHALL send SIGKILL only to a session still alive after a bounded wait.
- **R2** — WHEN the bench's `skew` subcommand ends an old build's server, that server SHALL have
  had the chance to exit on SIGTERM.
- **R3** — WHEN a teardown falls back to SIGKILL, the test or bench SHALL report it in its output.
- **R4** — Timeout paths that SIGKILL only after a test has already failed MAY stay as they are.

## Open questions for shaping

- **One shared helper or one per file?** The tests carry the same few lines in seven files; a
  shared helper in `tests/` would keep them alike.
- **Backport to the 2.8 line?** The leak bites hardest there, since every `hs server` its suite
  kills is a 2.8 server. A hotfix branch can carry the same helper.
- **The wait.** Long enough for a 2.8 server to stop its manager and for the tracker to finish
  unlinking (a SIGKILL fallback that lands on the tracker leaks as before), short enough not to slow
  the suite.

## Notes

- Related: [`process-group-signal-teardown.md`](process-group-signal-teardown.md) — HyperShell's
  own SIGTERM handling. A SIGTERM-first teardown does not wait on it: the tracker cleans up however
  the server exits, short of SIGKILL.
  [`integration-test-hang-timeout.md`](integration-test-hang-timeout.md) — the per-test timeout
  that will add more kill paths.
- Found by: out-of-cycle review, 2026-11-06.
