---
status: unshaped
kind: fix
appetite: big
lane: public
---

# No process-group ownership, and SIGTERM/Ctrl-C tear down without cleanup

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Two defects that must be fixed together, because fixing either alone makes things worse.

**Kills reach only the `sh` wrapper.** Tasks spawn as `Popen(self.task.command, shell=True, ...)`
with no `start_new_session`/`process_group`/`preexec_fn` (`src/hypershell/client.py:759-761`;
grepping `setsid|start_new_session|killpg|process_group` across `src/` returns nothing). The
walltime and stop escalation ladder is real and well built — INT, wait, TERM, wait, KILL
(`client.py:855`, `:862`, `:869`) — but every signal targets `self.process`, i.e. the shell. Any
task that is a pipeline, a wrapper script, or backgrounds children leaves grandchildren running
after "kill", so **walltime does not actually bound node usage** (`client.py:823-850`) and the
final branch merely logs `Process ignored SIGKILL`. The resource monitor already enumerates the
descendant tree (`src/hypershell/core/resource.py:75`, `p.children(recursive=True)`) — we observe
the tree and never signal it.

**The default kill signal produces the worst available outcome.** `register_handlers()` installs
USR1/USR2/HUP only (`src/hypershell/core/signal.py:111-115`) — deliberate, and the cooperative
drain built on it is the right design — but it means a plain `kill`, which is what Slurm/PBS
epilogues and most supervisors send, hits Python's default disposition. The client dies instantly,
subprocesses reparent and keep running, rows stay "interrupted", and the next server start re-runs
them (`server.py:197`, `Task.revert_interrupted()`). Ctrl-C has the same hole one layer up: the
cleanup idiom is `try: thread.join() / except Exception: thread.stop(); raise`
(`client.py:1412-1416`, `src/hypershell/cluster/local.py:45-50`) and `KeyboardInterrupt` is a
`BaseException` — so `stop()` never runs. Cluster teardown only terminates *local* handles: the
launcher `Popen` (`src/hypershell/cluster/remote.py:323`) or the local `ssh` processes
(`src/hypershell/cluster/ssh.py:320`; clients are spawned without a PTY at `ssh.py:311`, so
dropping ssh does not reliably kill the remote `hs client`), and the `AutoScaler` has no terminate
path at all (`remote.py:531-554`).

Together: orphaned compute on every node, *plus* guaranteed duplicate re-execution after restart.

**Why they are one issue.** Today an interactive Ctrl-C incidentally kills tasks *because* they
share the client's foreground process group. Adding `setsid`/`process_group` without fixing signal
handling removes that accidental mitigation and makes the common case worse.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, H6 + H7) — a
review pass. Both **pre-existing** in `develop`.

## Outcome / vision

A task is a process *tree* HyperShell owns: walltime and stop bound the whole tree, not just the
shell. A plain `kill`/`SIGTERM` drives the same cooperative drain USR1 already does — kill task
process groups, flush client state, exit — instead of abandoning the node. Ctrl-C does the same
thing deliberately rather than by accident. Remote and autoscaled fleets get a real stop path
rather than a dropped ssh connection and hope.

## Sketch of the acceptance criteria

- **R1** — WHEN a task subprocess is spawned, it SHALL be placed in its own process group, and the
  existing INT→TERM→KILL escalation ladder SHALL be delivered to that group.
- **R2** — WHEN a task exceeds its walltime, the whole descendant tree SHALL be terminated —
  verified by a test that backgrounds a grandchild.
- **R3** — WHEN a client or cluster process receives SIGTERM, it SHALL run the existing cooperative
  drain (terminate task groups, flush state, exit) rather than dying on the default disposition.
- **R4** — WHEN a user interrupts an interactive run (Ctrl-C), cleanup SHALL run — the
  `KeyboardInterrupt`/`BaseException` gap in the join-then-stop idiom SHALL be closed at every site
  that uses it.
- **R5** — Remote, SSH, and autoscaled fleets SHALL have a teardown path that actually stops
  remote clients; a stopped cluster SHALL NOT leave `hs client` processes running on remote hosts.
- **R6** — `SIGINT`/`SIGTERM` handling SHALL NOT disturb the documented USR1/USR2/HUP cooperative
  semantics or the sticky `signal.RECEIVED` contract in `AGENTS.md`.

## Notes

- `process_group=0` on `Popen` is available on Python ≥3.11 and we require ≥3.11, so no
  `preexec_fn` is needed. Send the existing ladder with `os.killpg`.
- R5 may want a control message on the queue rather than a remote kill; note the overlap with
  `issues/evicted-client-fencing-and-cancel-reach.md`, which wants a control path for a different
  reason. `/hs-plan` should decide whether they share one mechanism — and if so, whether this
  issue should wait for `zone`.
- Touches `client.py` (no dedicated test file) and `core/signal.py` (load-bearing, untested) —
  both on the `AGENTS.md` high-risk list.
- Found by: GNU Parallel comparison review, 2026-08 (H6 + H7).
