# Laptop drive evidence — relay-hardening P6

Seven runs of `spec/relay-hardening/drive/hardening_drive.sh`, one per mode, each in its own throwaway
site (`.agents/factory/bin/temp_site.sh`), with `DRIVE_EVIDENCE` pointing here, on 2026-11-14 from the
P6 build on top of `f961fd8`. Host: a laptop (macOS, Apple silicon), loopback only. Host, user, paths
and the run key are replaced in every file. The earlier build is `2ffb5cc` (`develop`, 2.9.0a8),
exported with `git archive` and put on `PYTHONPATH` for the role named; each file's facts name where
every role imported `hypershell` from.

Each run: one upstream `hs server` and one `hs relay` (its own relay database and site), launching its
site clients through `hardening_shim.sh`, which records each launch and may hold one; every process
logs at DEBUG in the system style, in UTC; each task records its start (`runs.log`). Each file holds
the launch log, the upstream servers' and the relay's relevant lines, the drive's facts and
`hardening_judge.py`'s judgment (which also reads the upstream database).

| Mode | Roles on `2ffb5cc` | Result | What it shows |
|------|--------------------|--------|---------------|
| `restart` | none | PASS | 60 tasks of 1 s, relay with 3 site clients; the upstream server stopped (SIGTERM) and started again on its port at 15 of 60 complete (at once) and at 39 (down 3 s). The relay lost its link twice, reconnected each time and carried on; it never exited 5, logged no traceback and no CRITICAL line, and exited 0 with the third server; every task completed, 15 of them run more than once (in flight at a restart, put back by the restarted server). No first call met a stale proxy id in this run (the window is narrow; R3's restart harness test covers it) |
| `tls-on` | none | PASS | relay with TLS, upstream `--no-tls`: exit 5 about 1 s after start (0.15 s first to last relay line), one CRITICAL line naming `[relay.upstream.tls] enabled` and "must agree on TLS", no traceback, no relay-local server, no launch; the upstream still running |
| `tls-off` | none | PASS | relay `--no-tls`, upstream with TLS: exit 5 about 1 s after start (0.01 s first to last relay line), one CRITICAL line naming `--no-tls` and "must agree on TLS", no traceback, no relay-local server, no launch; the upstream still running |
| `zones` | none | PASS | zones cpu and gpu, upstream with a database; the gpu client ran group 0's 2 gpu tasks and exited idle, and its relaunch was held. Group 1's 3 gpu tasks were staged for the relay's gpu identity (still beating, no site client), and the upstream was drained once every task was staged. The cpu zone heard the shutdown, the gpu zone (no site client, nothing held) asked once and held a staged task, and the held gpu client then came back and ran the other two; 21 of 21 tasks ran once, each in its own zone; relay and upstream exit 0 |
| `zones-nodb` | none | PASS | default zone and gpu against `hs server --no-db` (8 tasks; without a database a task has no zone), every gpu launch held for the whole run: 8 of 8 ran once at the site in the default zone, the gpu zone (nothing staged, no site client) ended with the default zone's shutdown, and the relay and the upstream exited 0 |
| `pair-relay` | relay | PASS | a 2.9.0a8 relay against this server: 40 tasks of 0.2 s, each run once and completed; both exit 0 |
| `pair-server` | server | PASS | this relay against a 2.9.0a8 server: as `pair-relay`; both exit 0 |

`zones` with every role on `2ffb5cc` (`DRIVE_BASE_ROLES='relay server'`, before the gate) fails as
the defect predicts: the gpu zone ended with the cpu zone's shutdown without asking, the relay and the
upstream exited 0, and the 3 tasks staged for gpu never ran (`every task completed upstream` and
`every task ran at the site` FAIL, `the gpu zone ... took a staged task` FAIL).

## Earlier relay drives on the head (R10)

Every mode of the relay-retire and relay-topology laptop drives, and the relay smoke, on the P6 head,
each in its own throwaway site, the same day. Their scrubbed files are not kept here (their own
cycles' evidence directories hold the record of each mode); one line each.

| Drive | Mode | Result | Note |
|-------|------|--------|------|
| `retire_drive.sh` | `retire` | PASS | A held 6 under its identity (3 started, 3 unstarted), handed back 3; requeued +1.085 s; last unstarted task available +1.174 s (bound 3 s); exit 0, no eviction |
| `retire_drive.sh` | `retire-red` | PASS | the stranding reproduced on `fb89670` (the mode's own earlier build) |
| `retire_drive.sh` | `pair-relay` | PASS | an `fb89670` relay against this server strands as then, until stopped and evicted (22.7 s) |
| `retire_drive.sh` | `pair-server` | PASS | this relay against an `fb89670` server, as then; evicted 23.2 s after it was stopped |
| `retire_drive.sh` | `idle` | PASS | both relays stay connected at zero clients until drained, then exit 0 |
| `topology_drive.sh` | `spread` | PASS | |
| `topology_drive.sh` | `fill` | PASS | |
| `topology_drive.sh` | `floor` | PASS | |
| `topology_drive.sh` | `idle` | PASS | |
| `topology_drive.sh` | `kill` | PASS | |
| `topology_drive.sh` | `lower` | FAIL (expected) | the three checks relay-retire recorded as changed by its own fix (`spec/relay-retire/TECH.md` P3, PLAN §5): A, superseded, retires and exits 0, so "every live relay still runs after its clients left", "no relay identity left or was evicted while running" and "every one of A's clients ended by idle exit" fail; every other check passes (60 of 60 tasks, A launched nothing after the warning, A2 launched within 10 s). Not this cycle's change |
| `topology_drive.sh` | `stale` | PASS | |
| `topology_drive.sh` | `bounce` | PASS | |
| `topology_drive.sh` | `stall3` | PASS | |
| `topology_drive.sh` | `stall-min2` | PASS | |
| `relay_smoke.sh` | `kill` | PASS | 300 tasks in 3 groups, relay B killed at 40 %: the judge passes and the records carry no host, user or key |

## The P6 verify gate

The P6 `verify:` command as written, after the evidence run above: the seven modes again (PASS), the
relay smoke, relay-retire `retire` and `idle`, relay-topology `spread` and `kill` (all PASS), the full
suite on SQLite (2087 passed, 101 skipped, 19 min), and `red_check.sh` (25 of 25 red, the 5 `pg:`
lines skipped). Then, with the local PostgreSQL 18 and TimescaleDB 2.32 (sandbox off): `red_check.sh`
with `HS_TEST_POSTGRES` and `HS_TEST_TIMESCALE=community` (30 of 30 red), and P1's PostgreSQL legs
(`test_contended_write.py`, `test_writeback_postgres.py`, `test_zone_postgres.py`): 47 passed with
TimescaleDB, 47 passed on plain PostgreSQL.

## The end-of-run tail (R8) — P7

Ten runs of `spec/relay-hardening/drive/tail_run.py` (through `tail_laptop.sh`), the R8 budget, each
at the R8 size: `spec/relay/drive/relay_throughput.py` unedited, 2000 tasks of 0.2 s, direct `hs
cluster -N 32` then the relay (4 site clients of 8 threads), every process at TRACE in the system
style, UTC. `laptop-NN.json` is each run's record; `laptop-NN-server.txt` and `laptop-NN-relay.txt`
are the upstream server's and the relay's log windows from 10 s before the last task's completion to
the end (per-task lines of other tasks left out; the full logs stay in the scratch sites). Host, user,
home and scratch paths and the run key are replaced (`scrubbed: 0` in every record).

Runs 01–05 are diagnostic, on the P6 head (`475334c`). Runs 06–10 are on the fixed head: `475334c`
with P7's fixes (i) and (ii) in the working tree (`src_changed: true`), committed with them in the P7
commit. The tail runs from the last task's `completion_time` to the upstream server's last log line.
The segments (seconds; `tail_judge.py` holds each design bound):

- s1 the site client returns the result to the relay-local server (its result bundle wait,
  `relay.bundlewait`, 5 s); s2 the relay returns it upstream (`relay.bundlewait`, 5 s); s3 the
  upstream server stores it; s4 the upstream scheduler finishes; s5 the upstream server signals; s6
  the relay hears the shutdown; s7 the relay-local server signals the site; s8 the relay's final
  heartbeat; s9 the upstream server's last line.

| Run | Label | Tail | Direct tail | s1 | s2 | s3 | s4 | s5 | s6 | s7 | s8 | s9 | Relay / direct wall |
|-----|-------|------|-------------|----|----|----|----|----|----|----|----|----|---------------------|
| `laptop-01` | diagnostic | 6.71 | 2.67 | 0.01 | 3.00 | 0.03 | 0.77 | 0.30 | 0.00 | 1.55 | 1.04 | 0.01 | 25.8 / 21.7 |
| `laptop-02` | diagnostic | 13.38 | 2.73 | 5.03 | 3.56 | 0.00 | 1.15 | 1.94 | 0.00 | 1.64 | 0.05 | 0.01 | 35.9 / 21.3 |
| `laptop-03` | diagnostic | 6.64 | 2.73 | 0.00 | 2.91 | 0.03 | 0.92 | 0.12 | 0.00 | 1.64 | 1.01 | 0.00 | 24.8 / 21.2 |
| `laptop-04` | diagnostic | 12.51 | 2.75 | 0.01 | 3.75 | 0.02 | **6.03** | 0.04 | 0.00 | 1.62 | 1.02 | 0.01 | 34.5 / 20.8 |
| `laptop-05` | diagnostic | 6.74 | 2.65 | 0.01 | 3.00 | 0.03 | 0.78 | 0.31 | 0.00 | 1.59 | 1.02 | 0.01 | 24.8 / 21.3 |
| `laptop-06` | post-fix | 1.72 | 0.81 | 0.01 | 0.12 | 0.03 | 0.61 | 0.00 | 0.00 | 0.48 | 0.46 | 0.01 | 20.7 / 19.2 |
| `laptop-07` | post-fix | 1.67 | 1.75 | 0.01 | 0.12 | 0.03 | 0.67 | 0.00 | 0.00 | 0.40 | 0.43 | 0.01 | 19.8 / 20.2 |
| `laptop-08` | post-fix | 7.00 | 1.76 | 5.04 | 0.08 | 0.00 | 0.65 | 0.00 | 0.00 | 0.27 | 0.95 | 0.01 | 29.4 / 20.3 |
| `laptop-09` | post-fix | 6.86 | 1.66 | 5.03 | 0.07 | 0.00 | 0.84 | 0.00 | 0.00 | 0.50 | 0.41 | 0.01 | 29.4 / 20.3 |
| `laptop-10` | post-fix | 6.88 | 1.65 | 5.04 | 0.07 | 0.00 | 0.77 | 0.00 | 0.00 | 0.07 | 0.92 | 0.01 | 29.4 / 20.3 |

What the logs name (TECH.md P7 records the decision):

- **The designed chain** (H0), with one wait research 07 had folded into the relay's: every run's
  tail is the sum of designed waits. Before the fixes, s2 (the relay's result bundle, ≤ 5 s), s5 (the
  upstream heart monitor's 2 s heartbeat read before it signals) and s7 (the relay-local server's
  same read) make the 6.6–6.7 s tails; s1 (the site client's own result bundle, ≤ 5 s:
  `relay.bundlewait` is also the site clients' bundle wait, and their bundle is 8 results, one per
  executor) adds 5 s in `laptop-02` (13.4 s). After fixes (i) and (ii), s2, s5 and s7 fall to
  0.1, 0.0 and ≤ 0.5 s (and the direct runs' tail, which has the same heart-monitor read, from about
  2.7 to 0.8–1.8 s); what is left over 5 s is s1 alone (5.0 s in `laptop-08`–`10`).
- **The upstream scheduler's back-off** (H1): in `laptop-04` the scheduler looked 4 ms after the last
  results landed, saw 8 still incomplete, and backed off 4 s, then 4 s again (a result landing does
  not wake it; only a cut or new demand does), so s4 was 6.0 s, over its 2 s bound. The back-off
  doubles up to `server.poll` (30 s), so with the relay's result waits it can reach far longer tails,
  as the review's one 60 s run may have.
