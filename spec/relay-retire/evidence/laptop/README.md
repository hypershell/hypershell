# Laptop drive evidence — relay-retire P3

Five runs of `spec/relay-retire/drive/retire_drive.sh`, one per mode, each in its own throwaway site
(`.agents/factory/bin/temp_site.sh`), with `DRIVE_EVIDENCE` pointing here: the runs of the P3 verify
gate itself, on 2026-11-13, from the P3 build on top of `736848c`. Host: a laptop (macOS, Apple
silicon), loopback only, inside the build sandbox. Host, user, paths and the run key are replaced in
every file. The earlier build is `fb89670` (`develop`), exported with `git archive` and put on
`PYTHONPATH` for the roles named; each file's facts name where every role imported `hypershell` from.

Each run: one `hs server --autoscaling=fixed -P 1` with no launcher and relays as separate processes,
heartrate 1 s (so R7's bound is 3 s), site-client idle timeout 3 s, launching through
`retire_shim.sh`; every process logs in the system style, in UTC. The scenario (all modes but `idle`)
is RV5 of the relay-topology review: relay A (`cpu`, `init = max = 3`) runs 12 tasks of 10 s; its
clients pass their idle timeout while their tasks run and A pulls for them; once A has held a task no
site client took for 2 s, A2 starts at site A (`init = max = 2`). Each file holds the launch log, the
site clients running at A and A2 each second, the server's registration, duplicate, retirement,
requeue, departure and eviction lines, each relay's identity, topology, retirement, hand-back, staged
revert, final-heartbeat and drain lines, the drive's facts and `retire_judge.py`'s judgment (which
also reads a 0.25 s sample of the upstream task table and a run log of `$TASK_ID` per task start).

| Mode | Roles on `fb89670` | Result | What it shows |
|------|--------------------|--------|---------------|
| `retire` | none | PASS | A read the mark 0.3 s after A2 registered (44 ms after the server's duplicate warning), held 6 tasks under its identity (3 run by its clients, 3 unstarted), handed the 3 back; the server requeued them +0.979 s after A's "Superseded" line and the sampler saw the last one unassigned at +1.229 s (R7 bound 3 s). A launched nothing after, its clients finished, its final heartbeat went out and it exited 0 by itself; no eviction; A2 stayed connected; 12 of 12 tasks ran exactly once |
| `retire-red` | server, A, A2 | PASS (red reproduced) | the same scenario on the earlier build: a task stayed on A's identity for more than 20 s with A at zero site clients (the stranding), and A never retired |
| `pair-relay` | A | PASS | this server marked A retired; A (earlier build) read it with no topology error, did not retire and stranded a task as today, until it was stopped (SIGTERM) and evicted 24.4 s later; then every task completed, each once |
| `pair-server` | server | PASS | the earlier server sent no mark; A (this build) did not retire, made no hand-back, stranded a task as today until stopped and evicted 22.2 s later; every task completed, each once |
| `idle` | none | PASS | one relay per site (A, B): 6 tasks, every client exited by itself, both relays stayed connected at zero clients (none retired, none left or was evicted) until drained, then exited 0 |

Calibration (PLAN §6, research 03 §4): with `--autoscaling=dynamic` and A2 started once A ran 3
clients, A read the mark before it held anything its clients had not taken, so `retire` was
INCONCLUSIVE; the fixed policy (which launches no replacement while a client runs) and the
"held a task no client took for 2 s" trigger fix that. `retire-red` with these knobs reproduced the
stranding 3 of 3 before the gate run (each run under 60 s), and again in the gate run.
