---
status: adopted:cluster-launch-failures
kind: fix
appetite: small
lane: public
---

# `hs cluster` waits forever when every launched client fails

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs cluster` starts a server, launches clients, and waits. When every launched client exits early,
it keeps waiting with work left and nobody to run it. Nothing names the failure and the command
never exits. Typical causes are an authkey or TLS mismatch, a client that cannot reach the
advertised address (see [`advertised-host-ignores-bind.md`](advertised-host-ignores-bind.md)), or a
bad `--remote-exe`. The user sees the server's "listening" line and then silence.

Where it waits, on `origin/develop` (`02b292a`, 2.9.0a4):

- **Launcher and `--mpi` modes.** `RemoteCluster.run_with_exceptions` waits on the one launcher
  process, ignores its exit status, then joins the server (`src/hypershell/cluster/remote.py:325-326`).
- **`--ssh` mode.** `SSHCluster.run_with_exceptions` waits on each `ssh` process, ignores every exit
  status, then joins the server (`src/hypershell/cluster/ssh.py:321-323`).
- **The server does not stop for want of clients.** With demand-driven dispatch the scheduler
  loads work only when a client asks for it; with no clients it waits on that need and re-enters
  LOAD (`src/hypershell/server.py:247-250`). It stops only when nothing is left to schedule
  (`:259-265`). The heartbeat monitor finishes only once the scheduler is done (`:781`), and a
  client that never connected sends no heartbeat, so eviction never fires either.
- **Autoscaling** behaves differently but also never ends. `AutoScaler.clean` logs a warning for
  each client that exits non-zero (`src/hypershell/cluster/remote.py:551-563`), and the next check
  launches another, because the fleet is empty and work remains (`:492-499` fixed, `:515`/`:533`
  dynamic). The server runs in forever mode (`:817`), so the result is a relaunch loop.

Reproduced on `origin/develop`: `hs cluster TASKFILE --no-db --no-tls --launcher false` runs
`false hs client …`, which exits 1 at once. The cluster logs that the server is listening, prints
nothing about the client, and was still running when killed after 25 seconds.

## Why it was deferred

Found out of cycle (2026-11-05), not by a cycle's review. **Pre-existing:** the launcher wait and
server join are the same in 2.8.0.

## Outcome / vision

When every client a cluster launched has exited and work remains, `hs cluster` exits non-zero with
one clear error: how many clients it launched, that all have exited, and their exit statuses. A
client that exits non-zero is logged when it exits, in every mode, so a partial failure is visible
too. Autoscaling stops relaunching into a failure it cannot fix.

## Sketch of the acceptance criteria

- **R1** — WHEN every client launched by `hs cluster` (launcher, `--mpi` or `--ssh` mode) has exited
  and tasks remain, the cluster SHALL stop the server and exit non-zero, with an error naming the
  number of clients launched and their exit statuses.
- **R2** — WHEN a launched client exits with a non-zero status, the cluster SHALL log it at warning
  level, in every mode.
- **R3** — WHILE autoscaling, IF launched clients keep exiting non-zero without any having
  connected, THEN the autoscaler SHALL stop relaunching after a bound and the cluster SHALL exit
  non-zero with the same kind of error.
- **R4** — A cluster whose clients finish the work and exit 0 SHALL exit as it does today.

## Open questions for shaping

- **What counts as "every client" under a launcher?** One launcher process (`mpirun`, `srun`) stands
  for many clients. Its exit is the only signal the cluster sees, and a launcher that exits 0 after
  its clients failed looks like success. Shaping decides whether launcher exit alone is enough, or
  whether the cluster also checks how many clients ever registered.
- **The autoscaling bound.** Consecutive failed launches, a failure rate over a window, or the
  failure-budget policy in [`failure-budget-halt-policy.md`](failure-budget-halt-policy.md)?
  Autoscaling is a long-running service by design, so it should not stop on one bad launch.
- **Clients that connected and then died** with tasks assigned already wait for eviction
  (`server.evict`, 600 s by default). Is that in scope, or left to
  [`client-partial-connect-hang.md`](client-partial-connect-hang.md)?

## Notes

- Related: [`advertised-host-ignores-bind.md`](advertised-host-ignores-bind.md) (one way every client
  fails to connect), [`client-partial-connect-hang.md`](client-partial-connect-hang.md) (a client that
  half-connects), [`run-exit-status-failure-aware.md`](run-exit-status-failure-aware.md) (the cluster's
  exit status when tasks fail).
- Found by: out-of-cycle review, 2026-11-05; reproduced on 2.9.0a4.
