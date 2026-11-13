# Cluster evidence — relay-topology P5, run B

Two records written by `spec/relay-topology/drive/topology_cluster.sh` at `793d386` (P5 run A),
run inside one held scheduler allocation, copied here unchanged from the drive's results directory.
Each record scans itself for node names, host, user, paths and key (`record.scrubbed`, all 0).

| Record | Scenario | Wall time | Judge |
|--------|----------|-----------|-------|
| `topology-793d386-45729057-spread-kill.json` | `spread-kill` | 322 s | PASS |
| `topology-793d386-45729057-fill.json` | `fill` | 157 s | PASS |

The drive took 479 s in total.

**Topology.** 4 nodes, 1 task of 8 CPUs per node. Node 0 ran `hs server --autoscaling=dynamic`
with no launcher. Node 1 ran relay A (`cpu`, `highmem`) and node 2 relay B (`cpu`, `gpu`), each
launching its site clients through the scheduler's own `srun` as its per-zone launcher, one
overlapping job step per client. Site clients landed on nodes 1–3 (A on 1 and 3, B on 2 and 3).

**Judge.** `uv run python spec/relay-topology/drive/topology_judge.py
spec/relay-topology/evidence/cluster --require spread-kill,fill` prints
`topology_judge: PASS (2 runs, 0 failed)`. In `spread-kill`, relay B was killed at 149 s, evicted
with its orphans reverted at 169 s, and A's `cpu` fleet grew from 2 to its max of 6 after the
eviction. In `fill`, A launched its max of 4 `cpu` clients before B's first, though B registered
first.

**An earlier attempt failed.** The first run on the same commit failed one check, `relay.idle_seen`,
in both scenarios. On one node the relay's nested client steps were refused with "Unable to satisfy
cpu bind request" (status 192), because they inherited the outer step's CPU binding. The rerun added
`--cpu-bind=none` to `HSD_SRUN_ARGS`, a site argument, with no change to the drive. The judge was
right to fail it: those were failed launches, not idle exits.
