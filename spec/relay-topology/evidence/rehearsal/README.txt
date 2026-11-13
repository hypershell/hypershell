Rehearsal evidence - relay-topology P5, run A

Local rehearsal of the multi-node drive (spec/relay-topology/drive/topology_cluster.sh), run by
spec/relay-topology/drive/topology_rehearse.sh with HSD_EVIDENCE pointing here. Built on aaaabb7 (P4)
with this phase's drive, which the P5 run A commit carries. Host: a laptop (macOS, Apple silicon),
loopback only, inside the build sandbox. Host, user, key and paths are replaced or absent.

Four stand-in nodes (node-0 .. node-3). A stand-in srun, written to the scratch directory and never
committed, runs each job step on this host: it ignores every resource flag, names the step's node
from --nodelist (SLURMD_NODENAME, read by the recorder) and points a site client's -H at loopback.
Scaled down from the cluster defaults: 1 s server checks and relay heartbeats, 12 s eviction (the
least the server takes), 4 s site-client idle timeout, 1 s tasks; spread-kill 6 highmem + 6 gpu
tasks (group 0) and 160 cpu tasks (group 1), cpu max 5 per site; fill 90 cpu tasks (group 0) and
3 highmem + 3 gpu (group 1), cpu max 3 per site.

Files:
  rehearsal.txt     the drive's output: each run's checks and verdict (both PASS, 106 s in all)
  judge.txt         topology_judge.py over both records with --require spread-kill,fill: PASS
  judge-broken.txt  the same judge with one assertion broken on purpose (a peak equal to its max
                    counted as over): both runs FAIL launch.peak, exit status 1; restored afterwards,
                    the judge passes again (judge.txt)
  records.txt       the two records the judge read

What the runs show:
  spread-kill  cpu launched at both sites (2 each) before relay B was killed at 20.4 s; B's cpu and
               gpu identities were evicted at 32.4 s and their orphaned tasks reverted; site B left
               the topology; A's cpu fleet, held at 2 between the kill and the eviction, grew to 5
               (its max) after it. highmem ran only at A, gpu only at B, all before the kill.
  fill         sites = A,B with B registered first: A launched its max (3) of cpu clients before
               B's first cpu launch, then B filled.
  both         every task completed once, in group order; word = env = --zone on every launch;
               no site-zone over its max; no key in any client; every client exited idle (status
               0) or by the kill; no relay terminated a client; nothing launched after all had
               left; the live relays stayed registered and drained with status 0.
