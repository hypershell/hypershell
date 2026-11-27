Rehearsal evidence - relay-retire P4, run A

Local rehearsal of the multi-node drive (spec/relay-retire/drive/retire_cluster.sh), run by
spec/relay-retire/drive/retire_rehearse.sh with HSD_EVIDENCE pointing here, on 2026-11-13, from the
P4 run A tree on top of b5ca84c. Host: a laptop (macOS, Apple silicon), loopback only, inside the
build sandbox. Host, user, key and paths are replaced or absent; the record names relays, nodes
and tasks by label and index only (its scrub counts are all 0).

Four stand-in nodes (node-0 .. node-3). A stand-in srun, written to the scratch directory and never
committed, runs each job step on this host: it ignores every resource flag, names the step's node
from --nodelist (SLURMD_NODENAME, read by the recorder) and points a site client's -H at loopback.
The knobs are the cluster run's own defaults, nothing scaled down: server --autoscaling=fixed -P 1,
relay heartrate 2 s (R7 bound 6 s, plus the 1 s clock-skew allowance), relay A cpu init = max = 3,
A2 init = max = 2, 12 tasks of 40 s, site-client idle timeout 5 s, eviction 1200 s against a 900 s
run deadline.

Files:
  rehearsal.txt         the drive's output: its judgment of the run (PASS, 267 s)
  judge.txt             retire_judge.py over the record with --require retire: PASS
  judge-broken.txt      the same judge over a copy of the record with A's exit status set to 1 and
                        an eviction of A added: FAIL on retire.exit and retire.no_evict, exit status 1
  rehearsal-retire.json the record the judge read

What the run shows: relay A's 3 clients started; A held a task no client took for 2 s, 43 s after
the submission, and A2 was started on A's node; A2 registered at 48.3 s, the server marked A retired
at 49.1 s, and A read the mark at 49.3 s. A held 5 tasks under its identity then: 4 its clients had
received (each ran there, once) and 1 unstarted, which it handed back; the server requeued it 0.105 s
after the mark and the samples saw it available at +0.290 s (bound 6 s). A launched nothing after the
mark, terminated nothing, sent its final heartbeat and exited 0 by itself at 166.2 s, when its last
client finished; none of A's identities was evicted. A2 stayed connected until drained at 262.0 s and
exited 0. 12 of 12 tasks succeeded, each ran exactly once, and every site client exited by itself
with status 0.

An earlier rehearsal of the same tree, not recorded here, also passed (309 s): A read the mark at
50.3 s, held 2 tasks unstarted and handed them back; the last was available at +0.355 s.
