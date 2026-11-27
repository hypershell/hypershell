---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# A client that times out during a pause in a stream leaves a late line with no one to run it

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Since `task-input-fidelity`, a run fed by a stream keeps scheduling until its input ends. A client
started with `-T`/`--timeout` shorter than a pause in that input sees no work for the length of the
timeout, exits 0 (`ClientScheduler.request_bundle`, `src/hypershell/client.py`), and is gone when
the next line arrives. The server stages the late line for a client that will never come, so the run
waits with work left until it is killed:

```shell
(echo 'echo a'; sleep 8; echo 'echo b') | hsx -N1 -T 3   # prints "a", then waits
hs list --remaining args schedule_time                   # echo b, staged, never run
```

Probed on the `fix/task-input-fidelity` build head (P3, SQLite): `a` printed, the run still going
31 s after start (the input ended at 8 s), `echo b` staged with a `schedule_time` and no result; the
run was killed. The plan's probe saw the same at 53 s. The same holds for any cluster whose clients
idle out:

- `hs cluster` with the local client: `LocalCluster` joins the client, then the server, and nothing
  watches for a server left with no clients.
- Launcher modes (`--ssh`, `--mpi`, `--launcher`): every launched client exited 0, so the
  `cluster-launch-failures` watch keeps waiting, by design (it stops only on non-zero exits).
- Autoscaling relaunches clients, so it is not affected; neither is the default `-T` (never).

On `develop` before `task-input-fidelity`, the same run instead stopped early and dropped the late
line silently, exit 0. The fix turned a silent drop into a visible wait.

## Why it was deferred

Out of scope for `task-input-fidelity` (GOAL non-goal, Clarifications; plan-gate ruling 3: the
foreman schedules this seed after the agreed pre-2.9.0 list). It is a consequence of that cycle's
fix meeting a pre-existing client rule, not a new defect in the fix. The docs now advise no `-T`
(or autoscaling) for a stream with long pauses (`docs/_include/task_groups_alt.rst`).

## Outcome / vision

A stream run whose clients have all idled out does not wait forever on a line no one can run: it
either keeps a client able to take the line, or ends with a clear outcome naming what was left.

## Sketch of the acceptance criteria

- **R1** — WHEN every client of a non-`forever`, non-autoscaling run has exited on its idle timeout
  and a task is then committed from the still-open input, the run SHALL NOT wait indefinitely with
  that task staged.
- **R2** — The chosen outcome (keep a client, relaunch, or stop with a non-zero status naming the
  tasks left) SHALL be documented on the cluster page beside the stream note.

## Notes

- Related: `spec/cluster-launch-failures/` (#107, the non-zero counterpart: a cluster whose
  launched clients have all exited, one non-zero, with work left stops),
  `issues/stream-bundlewait-commit-wait.md`.
- Found by: `task-input-fidelity` (GOAL Clarifications; PLAN § 5 probe), re-probed at build P4.
