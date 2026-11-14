# Research digest — relay-hardening

Seven read-only briefs, one per open design question the GOAL left to `hs-plan`. All were probed
against `develop` at `2ffb5cc` (2.9.0a8), with candidates monkeypatched in, never edited into source.
This digest settles each question once and resolves the places where briefs touch each other.

| Brief | Question | Decision |
|---|---|---|
| [01](01-conditional-revert.md) | R1: the eviction revert's SQL shape | Per-row `UPDATE … WHERE id = :r_id AND client_id = :r_held_by AND schedule_time IS NOT NULL AND completion_time IS NULL` (`select_orphaned`'s own predicate), through the shared `__write_rows` loop with a new `held_by` argument; on 0 rows a fresh read with the same binds says gone/settled/still pending; §1 re-run at most `CONTENDED_RERUNS` |
| [02](02-dispatch-stamp.md) | R2: the dispatch stamp off the ORM flush | Per-row Core `UPDATE … SET server_id, server_host, schedule_time WHERE id = :d_id AND schedule_time IS NULL` through the same loop (an "unscheduled" condition and its own bind prefix); return only the rows that landed, in order; no attribute set on the ORM objects (they reload from the database, so the wire `schedule_time` is the stored one, as today) |
| [03](03-reconnect-proxy.md) | R3: stale proxy ids after an upstream restart | Classify a `RemoteError` whose last line is `KeyError: '<hex>'` (the manager's unknown-id lookup) as a transport loss in `reconnect.py`'s `is_transport`, so every role takes the existing `LinkDown` → reconnect path; no proxy rebuild |
| [04](04-tls-mismatch.md) | R4: TLS mismatch in both directions | A relay-side first-connection check handed to `UpstreamLink` as a hook, run only while the link has never connected and inside `dial`'s deadline: a `--no-tls` relay first tries a no-data TLS handshake (only a TLS server completes it); a TLS relay, on an `SSLError` other than a certificate failure, reads for the multiprocessing `#CHALLENGE#` frame (only a plain server sends it). Either proof raises a fatal `TLSError` subclass; exit 5 like a wrong key |
| [05](05-idle-zones.md) | R5: when an idle zone may end | Option (b): once any zone has heard the shutdown, a zone with no site client, nothing held and not finished asks once for itself, for one task; its own reply decides. No wire or server change |
| [06](06-receive-time.md) | R7: eviction by the server's clock | A `received` map of `time.monotonic()` stamps beside `beats`, set at dequeue, popped with `beats`; `check_clients` ages by it. `hb.time`, `connected_at`, the wire and `last_check`'s pacing stay |
| [07](07-tail-investigation.md) | R8: staging the tail investigation | A wrapper over `relay_throughput.py` (unedited) that raises logging, stamps UTC, and records the tail and eight shutdown segments per run; a judge; laptop runs first; a cluster script in the existing environment contract for the foreman |

## Cross-brief resolutions

- **One shared write loop for R1 and R2.** Both generalise `Task.__row_statements` /
  `Task.__write_rows` (`data/model.py:916`, `:937`). R1 adds the holder condition (bind `r_held_by`,
  because the revert's own SET already binds `r_client_id`); R2 adds the unscheduled condition and a
  **bind prefix** (`d_id`), because two existing `test_contended_recovery.py` tests count UPDATEs keyed
  on `params['r_id']` after a dispatch and would count the stamp (R10: those tests stay unchanged).
  The 0-row classification gets a third outcome, *gone or no longer this write's row* → skip at DEBUG,
  never `StaleDataError`. `revert_all` stays unconditional for start-up recovery (`revert_interrupted`).
- **R3 and R4 both edit `core/reconnect.py`.** R3 changes `is_transport` (what a failed call means);
  R4 adds a first-connection hook inside `dial`/`attempt` and a fatal error class. Neither undoes the
  other: a stale-id `RemoteError` cannot arise before the first connect, and R4's check never runs
  after it. R3 lands first.
- **R7 and R1 both touch the eviction path, separately.** R7 changes only how `check_clients` ages a
  client (`server.py`); R1 changes only what `Task.revert_orphaned` writes (`model.py`). The heart
  monitor is not wrapped in a general exception handler (thread death into retry is a non-goal);
  R1 holds because the revert no longer raises on a gone or completed row.
- **R5 and R8 both touch the relay's finish.** R5's extra request happens only after a shutdown
  reply, before the relay's own finish; R8's possible fixes (results sent at once when nothing is
  held unfinished; the shutdown reply not waiting out the heart monitor's 2 s get) shorten the same
  chain. R8 runs last, on the head with R5 in place.

## Facts that differ from the GOAL's wording (for the plan gate and `hs-review`)

1. **R4's premise is half wrong.** `hs client --no-tls` against a TLS server does not fail at once; it
   hangs indefinitely (each side waits for the other to speak first). R4 is unaffected, and the relay
   will do better than a client.
2. **The TimescaleDB 0-row miss does not reproduce on the local TimescaleDB 2.32.** Both R1's and R2's
   re-run arms are shown by SQLite fault injection (`tests/test_contended_recovery.py`'s
   `Contention`) and the lock-wait cases on local PostgreSQL and TimescaleDB; the "fails on
   `2ffb5cc` when merely contended on a hypertable" leg is shown only by CI's TimescaleDB 2.31 jobs.
   The deleted-row and cancelled-row holders fail on `2ffb5cc` on every server database.
3. **R9's "contended case in `tests/test_contended_write.py` on SQLite"**: that file needs a server
   database (SQLite has no row locks). The SQLite leg is `tests/test_contended_recovery.py`, whose
   docstring already pairs the two files.
4. **R10's "existing suites pass unchanged" meets R5 and R7 head-on in seven assertions**, each
   encoding the behaviour those requirements remove: four tests make a client stale by back-dating
   its *sender* time (R7: `test_relay_roster.py:223`, `:500`; `test_topology_server.py:104`, `:435`),
   and three assert that only the busy zone sent a request (R5: `test_relay_connector.py:566` ×2,
   `:607`, the test whose docstring R5 orders rewritten). R10 already exempts R7; R5 is not in its
   exemption list. The plan changes only how those tests set up staleness, and the request list, and
   keeps every other assertion.
5. **R8's 5 s threshold is inside the designed shutdown chain.** A reduced, healthy smoke showed a
   5.15 s relay tail made of the relay's result batching (≤ 5 s `relay.bundlewait`) and two heart
   monitor waits (≤ 2 s each, upstream and relay-local). The chain's worst case is about 9 s, which
   brackets the review's 6–9 s. The plan reads the GOAL's cluster trigger against the runs on the head
   after any such fix (see PLAN §5).
