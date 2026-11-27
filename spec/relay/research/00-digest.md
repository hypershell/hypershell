# 00 — Research digest (relay)

Four briefs, read against the post-zone tree (develop `bd2faf9` + Z1 `9f36de4` + Z2 `b6fcb55`,
previewed as a merge). Each line is one decision; where briefs disagree, the decision is the
single recommendation and the reason is given.

| Brief | Topic |
|---|---|
| [01](01-upstream-surfaces.md) | Confirm, result fence, heartbeat, counting; DQ1, DQ3, R19 |
| [02](02-upstream-connector.md) | Client machinery, outage behaviour, DQ2, return and prune |
| [03](03-local-server-db-fleet.md) | Relay-local database binding, stamps, embedded server, fleet, CLI |
| [04](04-evidence-and-tests.md) | R20 test inventory, R21 run script, timing |

## Decisions

1. **DQ1 — one upstream identity per zone (option (a)), no upstream change.** Nothing upstream
   binds an id to a connection; requests, confirms, heartbeats and results all name their id
   (01 §5). Eviction is per id: `revert_orphaned(id)` plus `dispatcher.forget(id)`, so a dead
   relay's K identities are each evicted and every zone it held is reverted and unstaged. Option
   (b) re-keys `demand`, which `tests/test_zone_dispatch.py:197` assigns directly, so it would edit
   a Z2 test. Identities are `uuid5(relay instance, zone name)` and are logged at start.
2. **One puller thread cycles the served zones** with a short request `wait`; upstream descriptors
   scale with relay threads (puller, returner, heartbeat), not with identities (02 §1).
3. **The relay builds its own request text** (six JSON fields, `PROTOCOL` imported from
   `core/dispatch.py`) instead of refactoring `ClientScheduler.request_text`, which is Z2 code.
   An error reply raises the client's `QueueVersionError`.
4. **DQ3 — roster piggyback on the relay's heartbeat.** Optional `site` and `roster` fields, read
   with `.get` (the decoder raises on a missing key, ignores an extra one). Leaves never enter
   `HeartMonitor.beats`, so the monitor cannot evict one on its own; a relay eviction or FINISHED
   disconnects its roster in one UPDATE. New nullable `client.site` and `client.relay` columns
   through #69. Relay identity rows self-reference (`relay = id`), which is what
   `count_connected` excludes. Per-leaf heartbeats (01 §3 option B) rejected: more WAN traffic,
   independent leaf eviction, no cascade.
5. **R19 — `completion_time IS NULL` on the confirm UPDATE**, and a third zero-row outcome in the
   contended-write helper: *complete* is settled (no re-run, no WARNING). Revert keeps its
   statement. Red-first in `tests/test_writeback.py` (01 §1, 04 §3).
6. **Relay-local database: wholesale `config['database']` swap in `core/config.py`** before
   `data.core` is imported, triggered by `hs relay` on argv or a private marker, with a re-exec
   when a relay is started after import (03 §1). Env-var steering fails over a PostgreSQL config;
   rebinding after import leaves frozen copies on the primary. One relay per process.
7. **Upstream stamps in a relay-only side table** on its own `MetaData` (never `Entity`, or
   `upgrade_schema` would create it in users' primary databases), with `schedule_time` stored
   **verbatim as text**: SQLite drops tzinfo, and a round-tripped stamp would be STALE against a
   `timestamptz` upstream (03 §2).
8. **Completed rows leave the local `task` table at once, into a relay outbox.** With local
   `max_retries = 0`, one failed local row would hold the local group forever in forever mode;
   the outbox is also R9's durable buffer (03 §2, 02 §4).
9. **Insert upserts by id and refreshes the stored stamps**: an evicted or restart-reverted id can
   come back to the same relay with a newer stamp (02 §3, 03 §2).
10. **R5's DEFERRED clause: the relay records *handed over*, never *landed*.** There is no
    per-result acknowledgement: the upstream Receiver marks the queue item done before applying,
    and holds a DEFERRED result in its own custody (01 §2, 02 §4). The rule: an outbox row is
    marked `returned` only after `completed.put` returns; never on `QueueFull`, timeout or a link
    error; `returned` rows are pruned after a short retention. Briefs 01 and 04 suggested amending
    R5's wording; the plan instead records this reading for the foreman to rule on (PLAN §5).
11. **DQ2 — `UpstreamLink` in `core/reconnect.py`**: transport-vs-fatal error classification,
    full-jitter exponential backoff (1 s to 60 s), halt-responsive waits, an `epoch`, injected
    clock and rng. While down the site keeps running and the puller stops; on reconnect:
    heartbeat, replay pending confirms only for an outage shorter than the eviction hint, drain
    the outbox, resume pulling.
12. **Bounded upstream I/O without editing `core/queue.py`.** Brief 02 proposed opt-in socket
    timeouts in `core/queue.py` (a Z1 file and the security cycle's declared no-touch file).
    Instead every upstream call runs on a link worker thread with a deadline; on overrun or a
    transport error the link goes down and the worker is retired (abandoned if stuck). A fresh
    worker thread has no cached proxy connection, which also sidesteps the per-thread
    `BaseProxy` connection cache that breaks in-process reconnection (02 §2).
13. **An upstream restart is proven by a new `server_id`** on a received record. Unstarted local
    rows carrying the old one are dropped; started ones run and return STALE. Otherwise the relay
    never drops local tasks on an inferred eviction (02 §3).
14. **Fleet: compose `AutoScalerThread` (fixed policy, `init = min = max = N`) beside the relay's
    own `ServerThread`**, and edit nothing in `cluster/*.py`, so the relay inherits whatever key
    delivery the security cycle lands (03 §4). Scrub upstream credentials from `os.environ` by
    name and by value before the fleet starts; launchers copy `os.environ` at `Popen` time.
15. **No in-process site clients.** Outbound TLS uses the process-wide context, which is the
    upstream trust (03 §3). Tests run the upstream server and each relay as separate processes.
16. **Local scheduler latency**: the relay passes a small `poll` to its `ServerThread` instead of
    adding `Dispatcher.nudge()` to a Z2 file.
17. **R21 run script under `spec/relay/drive/`** (the foreman's brief), reusing
    `tests/cluster/build_archive.sh` and leaving `drive.sh` untouched. Brief 04 preferred
    `tests/cluster/`; the foreman's placement wins and the judge's unit test lives in `tests/`.
    Layout: node 0 upstream (evict 30 s); site A = nodes 1–2, survives; site B = node 3, killed
    at 40 % completion; step-launch launcher so the env-scrub check (e) bites.
18. **Done means `COUNT(exit_status = 0) == M`** in the drive and the loopback test, never "no
    incomplete rows" (failure-to-retry race, 04 §2).

## Flags carried to PLAN §5 (risks)

- An upstream *restart* makes every buffered pre-restart result STALE: R9's hand-over yields
  landed results only when the upstream process survived the outage (02 §3, 04 surprise 1).
- Eviction age uses the sender's clock (`hb.time`); cross-site skew beyond `evict_after` evicts
  falsely (02 surprises).
- The #29 re-registration branch keeps a stale `server_id`, so after an upstream restart a
  surviving relay and its roster drop out of `count_connected` (02 surprises). The roster phase
  refreshes it.
- A PostgreSQL primary with no driver installed exits `hs` at import, before relay code runs
  (03 surprise 1).
