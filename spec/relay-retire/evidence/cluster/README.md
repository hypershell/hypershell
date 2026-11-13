# Cluster evidence — relay-retire P4

Records written by `spec/relay-retire/drive/retire_cluster.sh`, each run inside one held
scheduler allocation and copied here unchanged from the drive's results directory. Each record
scans itself for node names, host, user, paths and key (`record.scrubbed`, all 0).

| Record | Scenario | Commit | Wall time | Judge |
|--------|----------|--------|-----------|-------|
| `retire-824e19f-45774110-retire.json` | `retire` | `824e19f` | 329 s | PASS |
| `retire-29c9bc7-45764803-retire.json` | `retire` | `29c9bc7` | 335 s | PASS |

**Current: `824e19f`**, the review-cycle-1 remediation (RV1, RV2) and the last commit to change
`src/`, reviewed and approved at review cycle 2. The drive and judge are unchanged from the
`29c9bc7` run. A2 registered at 62.0 s; the server marked A retired at 62.0 s; A logged
"Superseded" once at 63.0 s and launched nothing after it. A held 5 tasks at the mark (4 started
by its site clients, 1 unstarted), handed back the 1 unstarted, and the server requeued it at
+0.975 s; it was seen available upstream at +1.130 s of the mark (R7, bound 6 s + 1 s). A exited
by itself with status 0 at 179.6 s, with no eviction and no orphan revert; 12 of 12 tasks
succeeded, each run once; no site client was terminated.

The `29c9bc7` record below is the one P4 was first judged on (run B), kept as that phase's record.

**Topology.** 4 nodes; node 0 ran `hs server --autoscaling=fixed -P 1` with no launcher and a
read-only sampler of its task table; relay A (site A, `cpu`, init and max 3) on node 1, then relay
A2 (site A, init and max 2) on the same node once A held a task no site client had taken for 2 s.
Each relay launched its site clients through the scheduler's own launcher, one job step per
client, on nodes 1–3. Heartbeat 2 s, so R7's bound is 6 s (plus a 1 s clock-skew allowance between
nodes); eviction 1200 s against a 900 s run deadline.

**Result.** A2 registered at 62.9 s; the server marked A retired at 63.8 s; A logged "Superseded"
once at 65.4 s and launched nothing after it. A held 4 tasks at the mark (3 started by its site
clients, 1 unstarted), handed back the 1 unstarted, and the server requeued it at +0.994 s; it was
seen available upstream at +1.193 s of the mark (R7). A's clients finished their 3 tasks, A sent
its final heartbeat, the server saw its identity leave, and A exited by itself with status 0 at
179.8 s, with no eviction and no orphan revert. A2 launched 2 site clients at 65.9 s and 1 more at 274.0 s, and stayed connected until its drain. 12 of 12 tasks
succeeded, each run once; no site client was terminated.

**Judge.** `uv run python spec/relay-retire/drive/retire_judge.py spec/relay-retire/evidence/cluster
--require retire` prints `retire_judge: PASS (1 runs, 0 failed, 0 inconclusive)`.
