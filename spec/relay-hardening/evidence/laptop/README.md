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
