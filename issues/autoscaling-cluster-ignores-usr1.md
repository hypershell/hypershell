---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# SIGUSR1 stops an autoscaling cluster's or server's scheduling but not the process

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

SIGUSR1 is HyperShell's cooperative stop: the scheduler stops loading work
(`server.py:250-252`), clients finish what they hold and are told to shut down, and the process
exits. A local `hs cluster` does this in seconds. An autoscaling one does the first two and then
stays up.

Reproduced on `develop` (6a0c28ea, macOS, SQLite):
`hs cluster --autoscaling dynamic -P 2 -X 0 -Y 1 --launcher env -T 300`, two `sleep 1` tasks
submitted and completed, then `kill -USR1`. The log shows `Signal interrupt (SIGUSR1)` and
`Autoscale client disconnected`, and no client process remains. The cluster process was still
running 60 s later and ended only on SIGTERM (exit 143).

The cause, as read: `AutoScalingCluster.run_with_exceptions` blocks on `self.autoscaler.join()`
before it ever reaches `self.server.join()` (`cluster/remote.py:1042-1059`), and the autoscaler's
machine loops through WAIT and CHECK (`cluster/remote.py:559-588`) without looking at the signal
or at the server. It ends only when launches are exhausted, or when `stop()` is called. Whether the
server thread itself had ended was not checked.

A second entry point has the same shape. `hs server --autoscaling=dynamic` with relay sites logged
`Signal interrupt (SIGUSR1)` and was still running about a minute later; SIGTERM stopped it at
once (2.9.0a9, macOS, SQLite, two relays connected; seen while drafting the RCAC tier 4 guide,
2026-11-23). As read: `AutoScalingServer.run_with_exceptions` (`cluster/topology.py`) blocks on
`self.publisher.join()` before `self.server.join()`, and the topology publisher's loop does not
look at the signal.

What it costs a caller: a program that owns an autoscaling cluster for a while and then wants it
gone cannot ask it to drain. `nf-hypershell` (the Nextflow executor) ends its managed cluster with
SIGTERM for that reason. Any client still connected at that moment is killed after its tasks are
done: Slurm records the client's job as `FAILED 0:9` (Gautschi, job 19045051, 2.9.0a8), and the
client's row never gets its `disconnected_at`. The same applies to an operator or a batch script's
`--signal=USR1@…` aimed at `hs cluster --autoscaling`.

**Pre-existing**: first seen on 2.9.0a8 and still on `develop`.

## Why it was deferred

Found from outside the repository, while building `hypershell/hypershell-nextflow`; no cycle was
open on the autoscaler. The plugin works around it with SIGTERM.

## Outcome / vision

SIGUSR1 (and SIGUSR2) mean the same thing to an autoscaling cluster or server as to every other
cluster or server: the scheduler stops, nothing more is launched or published, connected clients
drain and disconnect, and the process exits 0 once they have.

## Sketch of the acceptance criteria

- **R1** — WHEN an autoscaling `hs cluster` or `hs server --autoscaling` receives SIGUSR1 or
  SIGUSR2, it SHALL stop launching clients (cluster) or publishing client targets to relay sites
  (server).
- **R2** — WHEN its server has ended after such a signal, the cluster process SHALL exit, without
  waiting for an idle timeout or a further signal.
- **R3** — Clients connected at the signal SHALL finish the tasks they hold and disconnect, with
  their `disconnected_at` written.
- **R4** — Tests SHALL send SIGUSR1 to an autoscaling cluster with a client connected, and to an
  autoscaling server with a relay connected, and assert that each process exits within a bound.

## Notes

- Check the zone-discovering autoscaler (`launch_for`) as well as the single-zone one: both run
  in the same machine.
- `failed_launches` (`autoscale.failed_launches`) already gives the machine a way to end on its
  own; the signal needs a similar exit, and `run_with_exceptions` must not wait on the autoscaler
  for a server that has finished.
- `AutoScalingServer` (`cluster/topology.py`): the publisher thread needs the same exit as the
  cluster's autoscaler machine; consider one shared rule for "server ended or signalled → stop".
- Docs: the RCAC tier 4 guide sends SIGTERM to the server (pod deletion) and SIGUSR1 only to
  relays, so it doesn't depend on this fix.
- Related: `issues/process-group-signal-teardown.md` (signal handling for SIGTERM and Ctrl-C).
- Found by: `hypershell/hypershell-nextflow` development, 2026-11-21 (its `DESIGN.md`, *What
  HyperShell should grow*, item 6); reproduced on `develop` the same day.
