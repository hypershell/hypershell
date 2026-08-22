---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A run with failed tasks exits 0

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`ClusterApp.run` → `run_local` → `LocalCluster.join()` all return nothing
(`src/hypershell/cluster/__init__.py:272-285`, `src/hypershell/cluster/local.py:45-50`), so cmdkit
exits 0 regardless of task outcomes; the scheduler simply reaches `FINAL` even when a group is
non-viable (`src/hypershell/server.py:225-233`). `hs task wait` only raises under the opt-in
`-r/--return` (`src/hypershell/task.py:332-366`), and `hs task run` waits without `return_status`
(`task.py:422-430`).

Every CI pipeline, Makefile, and Slurm chain that wraps `hsx` therefore sees success while jobs
failed. The information is sitting in the database; the process contract throws it away.

`in_memory` mode makes it worse: all writeback, retry, and revert are skipped by design, so a
`--no-db` run with failures reports nothing *anywhere* — not in a database, not in the exit
status, not in a summary line.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, H5 + L9) — a review
pass. **Pre-existing** in `develop`.

## Outcome / vision

A finished non-`forever` run tells its caller the truth: zero if everything succeeded, non-zero if
tasks failed, in a convention a shell script can act on. `--no-db` runs at least log a final
failed count so the information exists somewhere. Wrapping HyperShell in automation stops
requiring a follow-up `hs list` to find out whether it worked.

## Sketch of the acceptance criteria

- **R1** — WHEN a non-`forever` run completes, the process SHALL exit non-zero if any task in that
  run finished with `exit_status != 0 AND exit_status != CANCEL_STATUS`, and zero otherwise.
- **R2** — The failure count SHALL be reported to the operator (a final log/summary line), and
  SHALL be produced under `in_memory` mode too, where no database record survives the run.
- **R3** — `hs task wait` and `hs task run` SHALL make the failure outcome discoverable by default
  — either failure-aware by default or, at minimum, saying plainly that `-r/--return` exists.
- **R4** — Cancelled tasks SHALL NOT count as failures (the `CANCEL_STATUS` filter that every
  failure query already applies), and the exit-status convention SHALL be documented.

## Notes

- GNU Parallel's convention is "number of failed jobs, capped at 101" (`parallel:159-160`,
  `wait_and_exit(min(...,101))` at `:180-183`). Reasonable prior art, but check it against
  `cmdkit.app.exit_status` — `AGENTS.md` says to reuse those constants rather than invent integer
  literals, and a raw count collides with them. Resolve at shaping time; this is the one real
  design question in the issue.
- Note the existing conflation `.local/IMPROVEMENTS.md` flags: `hs task wait -r` maps the raw `-1`
  status onto process code 255. Per `issues/stance-cancel-status-signal-collision.md` the `-1`
  overlap itself is **accepted behaviour**, but how it surfaces in a *process exit code* is fair
  game here.
- Deliberately excluded: any change to group-gating or the `--forever` scheduler contract.
- Found by: GNU Parallel comparison review, 2026-08 (H5, L9).
