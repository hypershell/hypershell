# Cluster evidence — relay-topology P5

Records written by `spec/relay-topology/drive/topology_cluster.sh`, each run inside one held
scheduler allocation and copied here unchanged from the drive's results directory. Each record
scans itself for node names, host, user, paths and key (`record.scrubbed`, all 0).

## Current: `b98e994`, the final reconciler

These two records are the cluster evidence for the branch as it ships: `b98e994` is the last
commit to change `src/` (RV4, the reconciler as one rule), after review cycles 1 and 2. The drive
and the judge are the ones that wrote and judged the `793d386` records below, unchanged.

| Record | Scenario | Wall time | Judge |
|--------|----------|-----------|-------|
| `topology-b98e994-45755911-spread-kill.json` | `spread-kill` | 362 s | PASS |
| `topology-b98e994-45755911-fill.json` | `fill` | 264 s | PASS |

**Topology.** As before: 4 nodes; node 0 ran `hs server --autoscaling=dynamic` with no launcher;
relay A (`cpu`, `highmem`) on node 1 and relay B (`cpu`, `gpu`) on node 2, each launching its site
clients through the scheduler's own launcher, one job step per client. Site clients landed on
nodes 1–3 (A on 1 and 3, B on 2 and 3).

**Judge.** `uv run python spec/relay-topology/drive/topology_judge.py
spec/relay-topology/evidence/cluster --require spread-kill,fill` prints
`topology_judge: PASS (4 runs, 0 failed)`, and the two `b98e994` records alone pass the same way
(2 runs). In `spread-kill`, A and B each ran 2 `cpu` clients before relay B was killed at 196 s;
B was evicted with its orphans reverted at 218 s, its site left the topology at 222 s, and A's
`cpu` fleet grew from 2 to its max of 6 after the eviction. 632 of 632 tasks succeeded. In `fill`,
A launched its max of 4 `cpu` clients before B's first, though B registered first; 316 of 316
tasks succeeded. No client was terminated in either run.

## Superseded: `793d386`, kept as the record of P5

| Record | Scenario | Wall time | Judge |
|--------|----------|-----------|-------|
| `topology-793d386-45729057-spread-kill.json` | `spread-kill` | 322 s | PASS |
| `topology-793d386-45729057-fill.json` | `fill` | 157 s | PASS |

These are the records P5 was judged on (run B). They stay because P5 and review cycle 1 cite them,
and because they show the same scenarios passing before the review remediations (RV1–RV4) changed
how a relay reconciles to its entry. They do not show the shipped reconciler; the `b98e994`
records do.

In `spread-kill`, relay B was killed at 149 s, evicted with its orphans reverted at 169 s, and A's
`cpu` fleet grew from 2 to its max of 6 after the eviction. In `fill`, A launched its max of 4
`cpu` clients before B's first, though B registered first.

**An earlier attempt failed.** The first run on `793d386` failed one check, `relay.idle_seen`, in
both scenarios. On one node the relay's nested client steps were refused with "Unable to satisfy
cpu bind request" (status 192), because they inherited the outer step's CPU binding. The rerun added
`--cpu-bind=none` to `HSD_SRUN_ARGS`, a site argument, with no change to the drive. The judge was
right to fail it: those were failed launches, not idle exits. Every later run kept that argument.
