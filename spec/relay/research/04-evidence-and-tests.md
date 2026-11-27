# 04 — Evidence and tests (R20, R21)

Read against the post-zone tree (develop + Z1 + Z2): `tests/cluster/{drive.sh,smoke.sh,compare.py,build_archive.sh}`,
`spec/zone-dispatch/TECH.md` P6/P7, `spec/zone-queue-primitive/TECH.md` P6/P7 and both `evidence/` trees,
`tests/conftest.py`, and the Z1/Z2 tests named below.

## 1. The zone harness, as it stands

- **Parameterised by environment only** (`HSD_*`), site- and scheduler-neutral. Required: `HSD_ROLE`
  (server|client), `HSD_ARCHIVE` (from `build_archive.sh`: standalone CPython + `--target` site, no console
  scripts), `HSD_RESULTS` (shared root), `HSD_RUN_ID`, `HSD_AUTH`; client needs `HSD_CLIENT_INDEX`. Workload:
  `HSD_TASKS`, `HSD_FAIL_EVERY`, `HSD_GROUPS`, `HSD_RETRIES` (failing task exits 3 on attempt 1 only, via
  `$TASK_ATTEMPT`), `HSD_ZONES` (tasks check `HSD_ZONE_MARK`, exit 7 on mismatch), `HSD_CAP`. Bounds:
  `HSD_TIMEOUT` (TERM, KILL +30 s), `HSD_WAIT`, `HSD_IDLE`.
- **Node layout is not in the repo.** `drive.sh` runs one role per invocation; the foreman's batch script
  (never committed) expands the allocation's host list, starts node 0 as server and nodes 1..3 as clients
  as job steps with a shared `HSD_RUN_ID`, sets `HSD_HOST` to node 0's name and `HSD_LOCAL` to node-local
  scratch. Rendezvous is the shared filesystem: server publishes `server.ready` (port) and `server.crt`
  atomically (`cp` + `mv`); clients poll for it. Archive unpacks once per node behind a `mkdir` lock.
- **Records:** `server.json` (rows, retried, incomplete, exit histogram, per-zone counts,
  zone-mismatched retries, evictions, `group_order`, psutil peak of the server tree) and `client-<i>.json`
  (rc, zone, advertised, received/largest from DEBUG log, exit-7 count). No host, user or path; logs are
  copied (gzipped) only on failure.
- **External-wait convention:** run A (P6) builds the drive, passes `sh tests/cluster/smoke.sh` locally and
  hands back an exact run matrix; run B (P7) is `blocked` ("awaiting multi-node evidence (run B)", not an
  attempt) until `spec/<slug>/evidence/cluster/` exists. The foreman delivers only scrubbed
  `server.json`/`client-*.json` under `<run_id>/` plus a verbatim harness note; P7 re-scrubs, runs the
  judge and writes `SUMMARY.txt`. Both cycles fit 4 whole nodes in a 1 h job with a 54 min guard.
- **Judge:** `compare.py` (stdlib only) — results-identical, throughput, footprint, and `--zoned` per-run
  checks; unit-tested on synthetic records in `tests/test_cluster_compare.py`.
- **Local smoke:** `smoke.sh` builds the archive for HEAD, runs every role on localhost with a separate
  `HSD_LOCAL` per "node", a free port, TLS on and off, then a zoned rehearsal, runs `compare.py
  --results-only`, and greps results for host/user names.

## 2. R21 run script

**Placement: `tests/cluster/relay_drive.sh`**, with `relay_smoke.sh`, `relay_judge.py`, and
`tests/test_relay_judge.py`. Reasons: `tests/cluster/` is where cluster harness code lives and is linted
and unit-tested; `spec/` holds the contract and evidence, not code; `build_archive.sh` is reused as is; and
the deferred relay-topology cycle will drive it again. Do **not** extend `drive.sh`: Z1/Z2 evidence was
judged at a named commit of it, and the relay roles share only its ~40-line prelude (copy it). Evidence
still lands in `spec/relay/evidence/cluster/` as R21 says.

**Roles** (`HSD_ROLE`): `upstream` (node 0), `relay` (first node of each site), `leaf` (every site node,
including the relay's). Layout for one 4-node, 1 h allocation:

| node | role | notes |
|---|---|---|
| 0 | upstream | `hs server --forever -r 1`, `HYPERSHELL_SERVER_EVICT=30` (check every 3 s), TLS on |
| 1 | relay A + leaf | site A = {1,2}; relay serves zones `shared` and `east` |
| 2 | leaf | site A; a pure-leaf node, the cleanest place for checks (c) and (e) |
| 3 | relay B + leaf | site B = {3}; serves `shared` only; **the site that is killed** |

Site A survives and holds the second zone, so multi-zone is shown without the killed site stranding a
zone (Z2 R10 holds a group whose zone has no client). Each site runs 2 leaves per node (one per zone at
site A), 8 executors each.

**Mechanics.**
- `leaf` unpacks the archive, writes a node-local `hs` shim (archive Python + `PYTHONPATH`), publishes
  `leaf-<site>-<n>.ready`, then samples every 1 s until the run ends. The relay waits for its site's
  `.ready` files before launching.
- The relay's launcher template is a committed wrapper, `tests/cluster/relay_launch.sh`, that takes the
  next node from `HSD_SITE_NODES` (round-robin under a `mkdir` lock) and execs either a scheduler step
  launch onto that node (overlapping the running step) or `ssh`. **Use step launch for the run:** it
  forwards the caller's environment by default, so (e) tests the relay's own scrub; `ssh` forwards nothing
  and would pass (e) trivially. The wrapper must pass the environment through untouched.
- **Key handling:** the batch script writes the upstream key to a 0600 file in the run dir (transport
  only, deleted at the end) and never exports it. The relay drive exports it only into the `hs relay`
  process itself, in its environment, which is the form most likely to leak. Leaf watchers read the file
  themselves, so the key is never on their argv or in their environment.
- **Failover trigger:** the upstream drive polls its SQLite database every 1 s. When completed rows reach
  `HSD_KILL_AT` (default 0.4·M), it snapshots the rows with `client_id` ∈ relay B's ids and
  `completion_time IS NULL` to a node-local file and publishes `kill-B`. Relay B's drive sees the flag and
  sends SIGKILL to the relay only. Its leaves are left orphaned; they end by `-T` and their rc is recorded
  as non-zero. Variant `HSD_FAILOVER=pause`: SIGSTOP for 45 s, then SIGCONT, so the zombie site returns
  late results that #70 must refuse as `STALE`.
- **Termination:** with `--forever` the server never ends, so the drive does. Done means
  `COUNT(exit_status = 0) == M AND COUNT(completion_time IS NULL) == 0`. Exactly M tasks succeed because
  each failing task fails only on attempt 1, which avoids the race between a failure landing and its
  retry row appearing. The drive then sends TERM to the server and waits a bounded time for the relays
  to halt.
- **Identities:** each relay drive records its upstream identities (one per zone under DQ1(a)) from a
  log line the relay must print, and publishes them as a transport file, never as evidence.

**Checks** (computed on the node, with counts and booleans only in the JSON; `relay_judge.py` asserts):
- **(a)** `rows == M + F`; `incomplete == 0`; `COUNT(exit_status=0) == M`; exactly one `attempt=2` row per
  failing task, each with `previous_id` set and exit 0. That shows no local retry: a relay retry would
  add rows or attempts. `group_order` uses `drive.sh`'s rule, comparing max completion of g with min
  schedule of g+1. Executions: leaves append `$TASK_ID` to a node-local log, which is collected to
  node 0. Require `executions ≥ rows`, and every duplicate id must be in the kill snapshot
  (`dup_outside_snapshot == 0`).
- **(b)** `SELECT COUNT(*) FROM task t LEFT JOIN client c ON c.id = t.client_id WHERE t.completion_time
  IS NOT NULL AND (c.id IS NULL OR c.<site> IS NULL OR t.client_id IN (:relay_ids))` must be 0. Per site,
  the count of upstream `client` rows with that site must equal the leaves its relay launched. Each
  completed row's `client_host` must be in its site's node set; this is compared on the node and only
  a boolean is recorded. `zone_mismatch` (exit 7) must be 0.
- **(c)** Node 0: psutil on the server tree records the peak count of ESTABLISHED connections on the
  port and whether the set of peer addresses ⊆ the relay nodes' addresses (resolved on the node, never
  written). Leaf nodes record the number of connections from any `hs client` process tree to the
  upstream address:port, which must be 0 on every sample. Node 1 hosts both a relay and leaves, so peer
  addresses alone cannot separate them; the per-process ownership check can.
- **(d)** After the kill, relay B's row in `client` has `evicted = 1`. Server log counts are recorded
  (`Evicting client` == number of relay B's identities; `Reverting orphaned tasks` the same). Every id in
  the kill snapshot completes with `client_id` among site A's leaves (`snapshot_completed_at_A ==
  snapshot_size > 0`), and (a) holds, so no task was lost. Under the pause variant, `stale > 0` and none
  of the snapshot rows is overwritten.
- **(e)** On every leaf node, every sample walks each `hs client` process and its descendants (tasks
  included) and checks psutil `cmdline()` and `environ()` for the key as a substring:
  `upstream_key_hits == 0`, with `procs_scanned > 0` (otherwise the check is vacuous). Also record
  that the site key differs from the upstream key.
- **(f, R10)** Each relay process gets a sentinel `HYPERSHELL_DATABASE_FILE`. The check is
  `primary_db_created == false`, and the relay-local DB must be under the relay's site directory.
  R8: the relay drive samples its local DB, and `peak_local_backlog ≤ bound(fleet)` is recorded.

**Evidence** (`spec/relay/evidence/cluster/<run_id>/`): `upstream.json`, `relay-A.json`, `relay-B.json`,
`leaf-A-1.json`, `leaf-A-2.json`, `leaf-B-3.json`, plus `SUMMARY.txt` from the judging phase. The repeat
matrix: r1 kill, r2 pause, r3 no-failover control. Never commit logs, `server.crt`, key or id files, or
snapshots.

**Local smoke (`relay_smoke.sh`):** one host, one upstream and two relays, each with its own `HSD_LOCAL`
and its own free relay port. The launcher is the wrapper in a local mode that runs directly, with no
step launch and no ssh, and still does not scrub the environment. Settings: M=300, 3 groups,
`HSD_RETRIES=1`, `HYPERSHELL_SERVER_EVICT=10` (the minimum), a short relay heartrate, the kill variant,
then `relay_judge.py`. Skip the address half of (c); the ownership half and (e) still work, since psutil
`environ()` works for the user's own processes on Linux and macOS. Reuse `smoke.sh`'s host/user grep.

## 3. R20 test inventory

Unit tests drive one state-machine action at a time against fakes, in the style of
`test_client_request.FakeDispatcher` and `test_server_fsm`'s `Queue` stand-ins plus `task_db`.
Integration tests run real processes on loopback, in the style of `test_dispatch_workload.Group`
(`start_new_session`, killpg on teardown, `wait_listening` without probe connections, and `until()`
polling a DB or log condition with a timeout instead of sleeping to sync).

| R20 clause | file :: test | kind | deterministic / red-first |
|---|---|---|---|
| durable insert before confirm | `test_relay_connector.py::test_confirm_follows_durable_insert`, `::test_failed_insert_is_not_confirmed` | unit | Events recorded on insert-commit and `confirmed.put`; insert monkeypatched to raise. Red: no `hypershell.relay` |
| no local retry; failure retried upstream | `test_relay_local.py::test_local_server_max_retries_zero` (even with `HYPERSHELL_SERVER_MAXRETRIES` set) + workload (a) | unit+int | Config read in a fresh subprocess |
| leaf attribution, upstream `schedule_time` kept | `test_relay_return.py::test_return_restores_upstream_stamp` → upstream `apply_results` classifies the copy APPLIED, `client_id` = leaf | unit | Local scheduler re-stamps first, so a missing restore turns STALE |
| duplicate and stale write nothing | `test_relay_return.py::test_replayed_return_is_duplicate`, `::test_return_after_revert_is_stale`, `::test_deferred_return_not_pruned` | unit | Reuse `test_contended_recovery.Contention` for DEFERRED |
| R8 bound | `test_relay_connector.py::test_no_request_while_backlog_at_bound` | unit | Fake fleet count and local count: at the bound zero requests, below it one |
| roster forward and removal | `test_relay_roster.py::test_leaf_registered_with_site`, `::test_leaf_disconnect_forwarded`, `::test_relay_eviction_disconnects_roster`, `::test_count_connected_with_roster` | unit | `HeartMonitor` on `Queue`s plus `task_db` |
| multi-zone | `test_relay_connector.py::test_each_zone_requests_independently`; `test_relay_workload.py::test_multizone_relay_takes_only_its_zones` | unit+int | A zone `c` with no client stays unscheduled while `a` and `b` complete |
| upstream outage | `test_relay_outage.py::test_partition_then_results_land` | int | In-test TCP forwarder between relay and upstream, cut and restored once rows are completed locally but not returned (poll the relay DB). Evict 600 s |
| restart durability | `test_relay_outage.py::test_restart_keeps_unreturned_results` | int | Cut the forwarder, SIGKILL the relay, restart it on the same site, restore: each id RUNNING once, rows landed |
| credential fence (R17) | `test_relay_launch.py::test_client_argv_env_exclude_upstream_key` (unit: built argv/env); `test_relay_workload.py` scans psutil `cmdline`/`environ` of the site clients and their tasks | unit+int | Key placed in the relay's env deliberately; launcher `env` (no scrub) |
| local DB never the primary (R10) | `test_relay_db.py::test_binds_sqlite_under_postgres_provider`, `::test_primary_untouched` | unit (subprocess) | Import-time engine, so a fresh interpreter with provider=postgres and a bogus host |
| late-confirm fence (R19) | `test_writeback.py::test_late_confirm_keeps_leaf_attribution`; `test_contended_recovery.py::test_late_confirm_on_complete_row_is_not_contention` | unit | **The genuine red-first**: on today's tree `confirm_all` overwrites, so the assertion fails |
| re-enter on timeout, halt on shutdown | `test_relay_fsm.py` per relay machine: `test_reenters_on_timeout`, `test_halts_on_sentinel`, `test_halts_on_signal`, `test_final_on_upstream_shutdown_after_drain` | unit | `DEFAULT_REMOTE_TIMEOUT` patched to 0.05 |
| loopback, 2 relays | `test_relay_workload.py::test_two_relays_grouped_workload_with_retry` | int | Below |

**Loopback build.** `temp_site` gives the upstream DB and `HYPERSHELL_SERVER_PORT`. Draw two more
`free_port()`s for the relays' local servers. Each relay starts with
`env={'HYPERSHELL_SITE': site/'relay-a', 'HYPERSHELL_DATABASE_FILE': site/'relay-a'/'primary-sentinel.db',
'SITE_MARK': 'a', <relay port var>: pa}`. `Group.start` merges into `os.environ`, which `clean_env`
re-isolates each test, and `isolate_environment` blanks `HYPERSHELL_CONFIG_FILE`, so nothing leaks from
the developer's config. Upstream: `hs server -r 1 -b 4 --restart`, not `--forever`. It ends when the
work is done and sends shutdown, which exercises "halt on shutdown". Workload: 3 groups × 6 tasks
`echo "$SITE_MARK $TASK_ID"`, plus one `test "$TASK_ATTEMPT" -gt 1 || exit 3` in group 0, all in zone
`shared`. Each relay launches 2 site clients with launcher `env`. Assertions:
- rows == 19 + 1;
- all complete, with exactly one `attempt=2` row;
- every completed `client_id` is a site client of a relay, never a relay id;
- both sites ran work;
- the RUNNING log count per id is 1;
- group order holds;
- the key scan is clean;
- both sentinels are absent;
- every process exits 0 within `DEADLINE = 120`.

Add an optional parametrised `failover` case: `HYPERSHELL_SERVER_EVICT=10`, a short relay heartrate,
and SIGKILL of relay B once upstream rows confirmed to it exist.

## 4. Timing budget

- **R21:** unpack and byte-compile about 1 min. Per run with M = 6,000 × `sleep 0.5`: 48 executors at
  about 96 tasks/s before the kill and 32 at about 64/s after it. Group stalls while the kill waits on
  eviction add about 35 s, for about 2.5–4 min per run with `HSD_TIMEOUT=900` as the guard. Three
  repeats plus spacing come to about 15–20 min, well inside 54 min. Upstream throughput in the Z2 drive
  was 335–490 tasks/s, so it is not the bottleneck.
- **Suite:** about 25 new unit tests in under 5 s. 5–6 relay integration tests at about 15–30 s each:
  each layer adds `hs` start-up, and each group barrier crosses two bundlewaits. Failover and outage
  cases add about 12 s because of the 10 s minimum evict. That is about 2–3 min more; mark them
  `integration`.

## Recommendations

- Put the drive in `tests/cluster/relay_drive.sh` (with `relay_launch.sh`, `relay_smoke.sh`, `relay_judge.py`), leaving `drive.sh` untouched; evidence still goes under `spec/relay/evidence/cluster/`.
- Follow Z1/Z2's two-phase external wait: run A = build drive + pass `relay_smoke.sh` + hand back; run B = blocked until evidence, then judge + `SUMMARY.txt`.
- Layout: node 0 upstream (evict 30 s), site A = {1,2} multi-zone survivor, site B = {3} shared-zone, killed at 40 %.
- Use a step-launch launcher (it forwards environment) so check (e) actually bites; keep the upstream key in a 0600 file, never in the batch environment.
- Detect completion by `exit_status=0` count == M, not by "no incomplete rows", to avoid the failure-to-retry race.
- Add a pause/resume repeat to show #70's `STALE` refusal of a zombie site, and a no-failover control.
- Make the plan give each relay a logged upstream identity per zone; checks (b)–(d) need it.
- Use an in-test TCP forwarder for outage and restart tests; never restart the upstream to simulate an outage.
- Land R19's test first: it is the only red-first test against existing behaviour.

**Surprises for the plan.**
- (1) An upstream *restart* runs `revert_interrupted`, so every buffered relay result becomes `STALE`. R9's "results delivered on reconnection" holds only for partitions, which is a DQ2 input.
- (2) Today the relay has no per-result acknowledgement, so "a `DEFERRED` return SHALL NOT be pruned" (R5) needs an ack signal the plan has to define before its test can be written.
- (3) Conditioning `confirm_all` on `completion_time IS NULL` (R19) makes a zero-row match legitimate, and `CONTENDED_RERUNS` must not count it as contention.
- (4) The task environment is the client's `os.environ` plus `TASK_*`, and `hs cluster` launchers inherit the environment, so the relay must scrub explicitly.
