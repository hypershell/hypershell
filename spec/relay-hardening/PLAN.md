# PLAN — Relay hardening for 2.9.0

> **Status:** Draft for review · **Last updated:** 2026-11-13
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md) (locked at
> the GOAL gate, 2026-11-13); the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail
> is in [`research/`](research/) (digest: [`research/00-digest.md`](research/00-digest.md)). Every
> design element traces to a GOAL R-ID. Line numbers are `develop` at `2ffb5cc`.

## 1. Summary

Six independent fixes, each small and each proved by a probe on `2ffb5cc` with the candidate
monkeypatched in, one bounded investigation, and one docs line.

- **Model.** The eviction revert and the dispatch stamp become per-row conditional writes through
  the one §1 write loop `Task` already has (`__row_statements` / `__write_rows`). A row that is gone,
  complete, or no longer the write's row is skipped, never raised on; a row that waited on a lock and
  still qualifies is re-run as §1 prescribes. One change serves every server (R1, R2).
- **Link.** A stale proxy id from a restarted upstream counts as a lost link, so every role
  reconnects and carries on (R3). At first connection a relay proves a TLS mismatch in milliseconds,
  in each direction, and exits (R4).
- **Puller.** After a shutdown reply, an idle zone asks once for itself instead of being ended
  blind (R5); the docs say what a relay with no client anywhere does (R6).
- **Heart monitor.** Eviction ages a client by the server's monotonic receive time (R7).
- **Tail.** A measured, segmented investigation of the end-of-run tail on the head, within the
  GOAL's run budget, with a cluster recipe the foreman submits (R8).

No wire change, no schema change, no new configuration key. Eight phases, at the size breaker's
soft cap (the GOAL gate noted the size and declined a split): the shared model fixes first (core,
novel, the most coupled), then the heart monitor, the link (R3, then R4), the puller, the end-to-end
evidence, the tail on the laptop, and the conditional cluster job with the recorded outcome.

## 2. Design

### R1 — the conditional eviction revert (`data/model.py`, every server)

`HeartMonitor.check_clients` (`server.py:913-929`) keeps its call to `Task.revert_orphaned(uuid)`
(guarded by `not in_memory and not no_confirm`, §4). `revert_orphaned` (`model.py:1224`) keeps its
read loop (`select_orphaned`, 100 at a time) but writes through `__write_rows(ids, revert values,
'revert', 'task left in flight', held_by=client_id)` instead of `revert_all`:

```sql
UPDATE task SET <revert_columns> = NULL
 WHERE id = :r_id AND client_id = :r_held_by
   AND schedule_time IS NOT NULL AND completion_time IS NULL
```

That is `select_orphaned`'s own predicate, applied per row in the statement that writes it. It is
not a dispatch token (Non-goals): no `schedule_time = :read`, no `server_id`. On a 0-row match the
loop re-reads `completion_time` with the same binds (`id`, `client_id`, `schedule_time IS NOT NULL`):

- no row → gone, already reverted, or held by another client: skipped at DEBUG;
- `completion_time` set → a result landed: settled at DEBUG (`task already complete`), as the
  confirm path does;
- `completion_time` NULL → still this client's in-flight row: re-run, at most `CONTENDED_RERUNS`,
  DEBUG per extra attempt, then the existing WARNING (`… revert … task left in flight`).

The bind is `r_held_by` (the SET clause already binds `r_client_id`), and the re-read receives it
too. `revert_all` stays unconditional for its other callers (`revert_interrupted` at start-up,
`Task.revert`), so start-up recovery and `hs update --revert` are unchanged (R10). The heart monitor
keeps running because the revert no longer raises on these rows; it is **not** wrapped in a general
exception handler (thread death into retry is a non-goal). A late result for a reverted row is still
STALE in `apply_results`, as today. Interleavings and probe tables: research 01.

### R2 — the dispatch stamp off the ORM flush (`data/model.py`, every server)

`Task.next` (`model.py:793-810`) keeps its selection (`__next_eager` / `__next_not_eager` and the
retry branch, untouched: Non-goals). It takes the selected ids without loading
(`sqlalchemy.inspect(task).identity[0]`, since the retry branch's commits expire the objects and a
deleted row would raise `ObjectDeletedError` on `task.id`), then stamps them through the same loop:

```sql
UPDATE task SET server_id = :d_server_id, server_host = :d_server_host, schedule_time = :d_schedule_time
 WHERE id = :d_id AND schedule_time IS NULL
```

with an *unscheduled* condition (matching `select_new`'s definition exactly, §1; not
`completion_time IS NULL`, which would let a hand-edited row spin) and a bind prefix `d_`. On a 0-row
match the re-read selects `schedule_time` by id: gone or already scheduled (a cancel stamps
`schedule_time`) → skipped at DEBUG ("already scheduled"); still NULL → re-run up to
`CONTENDED_RERUNS`, then WARNING naming the task and "dispatch", and the row is left unscheduled for
the next round (not stranded). `Task.next` commits and returns `[task for task in selected if its id
landed]`, order kept. It sets **no attribute** on the ORM objects: after the commit they reload from
the database when `pack_bundle` (`server.py:354`) calls `to_json()`, exactly as today, so the wire
`schedule_time` is the stored value that `apply_results` and `revert_handed_back` match later. One
`now` is written to every row of a chunk (today one per row; nothing needs distinct stamps). The sole
caller, `Scheduler.next_tasks` (`server.py:292-307`), already handles a short or empty chunk.

Shared-loop changes (`__row_statements` `:916`, `__write_rows` `:937`): a condition argument
(`pending` today; `held_by`; `unscheduled`), a bind prefix (default `r_`, so every existing statement
and the `Contention` harness keys stay as they are), and a non-strict 0-row outcome that skips instead
of raising `StaleDataError`. The prefix exists because two existing `test_contended_recovery.py`
tests count UPDATEs keyed on `params['r_id']` after a dispatch (research 02). Docstrings of
`revert_orphaned`, `select_orphaned`, `next`, `__write_rows` are updated; `AGENTS.md` and
`invariants.md` §1 name the eviction revert and the dispatch stamp in the "Pattern" line, in lockstep.

Side effect within R2's "no longer unscheduled": a row cancelled between the select and the stamp is
no longer re-stamped and dispatched (it is on `2ffb5cc`, on every database). `cancel_all` is
untouched.

### R3 — a stale proxy id counts as a lost link (`core/reconnect.py`)

A role's first call on a new worker thread dials afresh and sends the old server's proxy id; the new
server's `serve_client` fails the id lookup and returns `RemoteError(<traceback ending "KeyError:
'<hex>'">)`. Add `is_stale_proxy(error)`: a `multiprocessing.managers.RemoteError` whose text's last
non-blank line matches `KeyError: '[0-9a-f]+'`. `is_transport` (`reconnect.py:101`) ORs it in, so
`UpstreamLink.call` takes the existing `mark_down` → `LinkDown` path and `attempt` stays consistent.
The logged reason is short ("upstream server restarted (unknown proxy id …)"), not the remote
traceback. Every role already handles `LinkDown` as after an outage: the puller and heartbeat wait
for the link and send again, the returner resends its bundle, the hand-back stays due and is retried
(harmless on the restarted server: `Task.revert_handed_back` is conditional, so rows already requeued
by `revert_interrupted` are skipped). A HyperShell method's own `KeyError` arrives as a plain
`KeyError` (the `#ERROR` path), never as this `RemoteError`, so no genuine error is swallowed; any
other `RemoteError` still refuses a hand-back, as today. The stdlib path is identical in 3.11–3.15.
`reconnect.py` is imported only by the relay, so plain clients and servers are unchanged (R10).
No proxy rebuild: the link cannot know the server changed until a call fails (research 03).

### R4 — TLS mismatch fails fast, both directions (`relay/upstream.py`, `core/reconnect.py`, `relay/__init__.py`)

`UpstreamLink` gains an optional first-connection hook, run inside `dial`'s connect thread only while
`not self.connected` (`reconnect.py:243`, set at `:439`), so the whole attempt, check included, stays
within one `relay.upstream.deadline`. The relay supplies it from `relay/upstream.py` beside
`upstream_config` (which knows host, port and TLS):

- **`--no-tls` relay:** before the plain dial, `speaks_tls(address, remaining)` attempts a TLS
  handshake with a throwaway `CERT_NONE` context, sends no data and no key, and closes. Only a TLS
  server completes it (3–16 ms on the laptop). True → fatal mismatch; otherwise the plain dial
  proceeds as today.
- **TLS relay:** on an `ssl.SSLError` from the dial other than `SSLCertVerificationError` (already
  fatal), `speaks_plain(address, remaining)` reads a 4-byte length and `#CHALLENGE#`. Only a plain
  multiprocessing server sends it first (1 ms). True → fatal mismatch raised from the original;
  otherwise the original error is re-raised as transport. The detection never depends on OpenSSL's
  message text.

Both dial IPv4 as the queue clients do (`queue.py:311`). The mismatch error subclasses `TLSError`, so
the existing `FATAL_ERRORS` makes it fatal with no edit there; `RelayApp.exceptions`
(`relay/__init__.py:1094-1100`) maps it to `handle_exception` with `exit_status.runtime_error` (5, as
a wrong key), printing one CRITICAL line that names the relay's own setting (`--no-tls` /
`[relay.upstream.tls] enabled`) and says the relay and its upstream server must agree on TLS. The
relay launches nothing before the first connection succeeds (`relay/__init__.py:583-611`). Refused
or unroutable upstreams raise `ConnectionRefusedError`/`OSError` before any proof and are retried as
today; after a first success the hook never runs, so a later `SSLError` or timeout retries as today.
`core/queue.py` and `core/tls.py` are untouched; plain clients and servers keep their TLS behaviour.
The relay docs gain one sentence on the mismatch exit (research 04).

### R5 — an idle zone asks once for itself (`relay/upstream.py`)

`UpstreamPuller.gate` (`upstream.py:365-398`) today skips a zone whose bound is 0 (no site client)
and, once any zone has heard the shutdown, `finish_idle` (`:400-411`) ends every zone with no client
and nothing held. Replace that with: once `self.finished` is non-empty, a zone with bound 0, nothing
held and not finished is gated in with a request of size 1. Its own reply decides:

- `shutdown` → the zone ends with the others (nothing was staged for it);
- a task → held (backlog > 0, so it is not asked again) until a site client in the zone takes it, and
  the zone then asks as usual (the dispatcher serves a zone's staged rows before it tells that zone
  to shut down, `dispatch.py:313-360`);
- empty (not at the head of the line) → asked again on the next pass, each wait bounded by the
  request's own wait.

This uses the existing protocol against the dispatcher every server runs, so it works against a
2.9.0a8 server too, and a 2.9.0a8 relay against this server is unaffected (no server change). A relay
with no site client in any zone never hears a shutdown, so it never asks (Non-goal unchanged). The
comment at `upstream.py:247-249` and the docstring at `tests/test_relay_connector.py:557-561` are
restated to the exact guarantee: the upstream server finishes for every zone at once but serves each
zone what is still staged for it before it answers that zone with a shutdown. `finish_idle` goes; an
INFO line says when an idle zone takes tasks at a finish and waits for a client (research 05).

### R6 — docs

`docs/_include/relay_desc.rst:56-59` (shared with the man page source) states the R5 rule and that a
relay with no site client in any zone never asks for work, so it never hears an upstream shutdown,
and a non-forever upstream server waits for it too, until it is drained or stopped. Draft wording:
research 05 §R6. No CLI change, so no `share/` completion change; the man page picks the text up at
the next `hs-release` rebuild.

### R7 — eviction by the server's receive time (`server.py`, every server)

`HeartMonitor` keeps a `received: Dict[str, float]` beside `beats`, stamped with `time.monotonic()`
in `get_next` right after the queue `get` (`server.py:770`), stored with the beat (`:801`), popped
wherever `beats` pops (`:810`, `:921`). `check_clients` takes one monotonic `now` per check and ages
each client as `now - received[uuid]`, keeping the strict `> evict_after`; a missing stamp never
evicts. `hb.time`, `connected_at`, `disconnected_at`, `last_check` (still wall clock, still pacing
checks) and the wire are unchanged. A beat dequeued late only ages less, which spares a live client.
The relay-local server runs the same class, so site clients are covered too. Docs: remove the
clock-skew bullet at `docs/cli/relay.rst:116-118`; restate `server.evict` in
`docs/_include/config_param_ref.rst:269-274` ("…by its own clock, from when it received the last
heartbeat") with `.. versionchanged:: 2.9.0` (this reaches plain clients, which 2.8 aged by the
sender's clock). Research 06.

### R8 — the tail investigation (`spec/relay-hardening/drive/`)

Tooling, not product code: `tail_run.py` loads `spec/relay/drive/relay_throughput.py` by path (the
relay cycle's record stays unedited), wraps its `prepare` to set `HYPERSHELL_LOGGING_LEVEL=TRACE`,
`HYPERSHELL_LOGGING_STYLE=system` and `TZ=UTC`, and its `watch` to record the exit time, and writes
one JSON record per run: commit, sizes, both walls, the tail (`max(completion_time)` in the upstream
database → the upstream server's last log line), the eight segment stamps of research 07, the
WARNING/ERROR counts, and the kept site paths. `tail_laptop.sh N` runs N ≤ 10 (refuses more);
`tail_cluster.sh` follows `retire_cluster.sh`'s environment contract (`HS` required; `HSD_WORK`,
`HSD_RESULTS`, `HSD_NODE_TMP`, `HSD_LABEL`, `HSD_RUN_ID`, `HSD_RUNS` ≤ 10; no `#SBATCH`, no site
names, one node with ≥ 36 cores, scratch copied back after each run, PID cleanup); `tail_judge.py`
prints per run `tail · direct tail · s1…s8 · flags` (`TAIL>5`, `OVER` per segment past its design
bound) and exits 1 under `--require-none-over 5` if any run is over. Evidence goes to
`spec/relay-hardening/evidence/{laptop,cluster}/` (records, README table, host-scrubbed log windows
from `T_last − 10 s` to exit). Staging and the decision rule: §5 and TECH P7.

If the logs name the designed chain (H0) as the cause, two order-preserving fixes fit the appetite
and give outcome (a): **(i)** the relay's returner sends at once when its store holds no unfinished
task (`relay/upstream.py`, relay only); **(ii)** the heart monitor's shutdown signal does not wait out
its 2 s heartbeat `get` (`server.py` `switch_mode`/`get_next`, every server: a latency change, §6
order kept). (ii) changes every server's shutdown timing, which R10 does not exempt; the build takes
it only with the foreman's go at the plan gate (§5). Otherwise outcome (b) with a narrowed seed.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `Task.revert_orphaned` → `__write_rows(…, held_by=)`, the conditional per-row revert with §1 re-run; `revert_all` unchanged for start-up |
| R2   | `Task.next` → per-row `schedule_time IS NULL` stamp through `__write_rows` (prefix `d_`), returns only landed rows; no ORM flush |
| R3   | `reconnect.is_stale_proxy` in `is_transport` → `LinkDown` → existing reconnect for all four roles |
| R4   | `UpstreamLink` first-connection hook; `speaks_tls` / `speaks_plain` in `relay/upstream.py`; `TLSError` subclass mapped to exit 5 in `RelayApp.exceptions` |
| R5   | `UpstreamPuller.gate` asks once (size 1) for an idle zone after a shutdown; `finish_idle` removed; comment and docstring restated |
| R6   | `docs/_include/relay_desc.rst` wording |
| R7   | `HeartMonitor.received` monotonic stamps; `check_clients` ages by them; `relay.rst` bullet removed; `server.evict` restated with `versionchanged` |
| R8   | `drive/tail_run.py`, `tail_laptop.sh`, `tail_cluster.sh`, `tail_judge.py`; runs and outcome recorded in `TECH.md` and `evidence/` |
| R9   | One regression test per defect, each listed in `drive/red.txt` and proved failing on `2ffb5cc` by `drive/red_check.sh` (§6) |
| R10  | No wire/schema/config change; plain clients untouched by R3/R4; existing suites pass, with the seven assertion set-ups of §3's deviation table; laptop drives (`relay`, `relay-topology`, `relay-retire`) re-run on the head; pair modes against `2ffb5cc` |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research (§1, §3, §4, §5, §6, §9, §11, §12 named as touched; the GOAL sane against each)
and again against this design.

- **§1 task lifecycle.** Both new writes reproduce the state predicates exactly (`select_orphaned`'s
  and `select_new`'s); both are per-row Core UPDATEs with a rowcount check and the §1 re-run on a
  0-row match only while a fresh read says the row still qualifies, `CONTENDED_RERUNS` at most, DEBUG
  per extra attempt, WARNING at the bound, safe outcomes (an unrevertable row stays in flight as today
  on exhaustion; an unstampable row stays unscheduled for the next round). The dispatch stamp leaves
  the ORM flush, as §1 requires. Logic stays in `Task` classmethods. A confirmation still writes only
  onto `completion_time IS NULL` rows.
- **§2 / §3.** No failure query changes. The Scheduler stays the sole `Task.next` caller; the retry
  insert, the `retried` mark and their commits are untouched. `apply_results` unchanged: a late result
  for a reverted row is still dropped.
- **§4 modes.** `revert_orphaned` stays behind `not in_memory and not no_confirm`; `in_memory` has no
  Scheduler. R5 covers the upstream with and without a database; R7 applies in every mode (it reads no
  database).
- **§5 concurrency.** No new thread or state. The heart monitor and puller keep their finite-timeout
  waits; R5's extra request uses the puller's existing bounded request wait.
- **§6 shutdown.** Order unchanged. The staged reverts still use `Task.revert_staged`. R5 relies on,
  and does not change, the dispatcher serving a zone's staged rows before its shutdown reply. R8's
  optional fix (ii) changes latency only and keeps the order; gated (§5).
- **§8 signals.** Untouched.
- **§9 transport/security.** TLS stays on by default; auth unchanged; no key is sent by either probe;
  the TLS probe trusts nothing and is detection only (the real dial verifies as before, fingerprint
  check kept); no change to `core/queue.py` / `core/tls.py` or a plain client's or server's TLS.
- **§10.** No configuration change; the relay process rules untouched.
- **§11.** The topology publisher reads no heartbeat time (research 06); launches untouched.
- **§12.** Docs in the same commit as each user-visible change, `versionchanged` where a released
  behaviour changes; every new test tagged `@mark.unit` / `@mark.integration`; exit status from
  `cmdkit.app.exit_status`; supported Python 3.11–3.14 (the stale-id text path is identical there).

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| R10's "existing suites pass unchanged": four tests change how they make a client stale (`test_relay_roster.py:223`, `:500`; `test_topology_server.py:104`, `:435`) — a back-dated receive stamp instead of a back-dated sender time; assertions unchanged | They encode exactly the sender-clock aging R7 removes; no R7 design can pass them (R10 itself exempts what R7 states) | Keeping sender-time aging alongside (e.g. `min` of both ages) fails R7's second clause for a dead client with a fast clock |
| R10's "existing suites pass unchanged": three assertions of the zones that sent a request (`test_relay_connector.py:566` ×2 params, `:607`) become `['cpu', 'gpu']` | The idle zone's one request at a finish is R5's mechanism; R5 already orders that test's docstring rewritten. R10's exemption list names R1, R2, R7 but not R5 — **needs the foreman's nod** | Option (c) (a shutdown reply listing staged zones) is a wire change on every server and still needs the zone to ask; option (a) (a time window) fails R5 when a fleet launches slower than the window |
| `__row_statements` gains a bind-prefix parameter (`d_` for the stamp) | Two existing `test_contended_recovery.py` tests key on `params['r_id']` after a dispatch and would count the stamp's UPDATE; R10 keeps them unchanged | Renaming their binds or editing those tests breaks R10 |
| R8 runs log at TRACE, not DEBUG | The shutdown segments' key lines (`Waiting (heartbeat)`, the scheduler's `backing off`, `Client disconnected`) are TRACE; TRACE is a superset of DEBUG | DEBUG to the letter cannot attribute segments s3/s8 |

## 4. Rabbit holes (resolved)

- Revert SQL shape and its interplay with Confirm and the writeback → `select_orphaned`'s predicate
  per row; every interleaving tabled; existing SQLite/PostgreSQL/TimescaleDB suites pass with it
  patched in ([`research/01-conditional-revert.md`](research/01-conditional-revert.md)).
- Stamp shape, bundles with dropped rows, wire `schedule_time` → per-row conditional stamp, no
  attribute set (reload keeps the stored value), short chunks already normal; ~+0.07 ms/row
  ([`research/02-dispatch-stamp.md`](research/02-dispatch-stamp.md)).
- Stale proxy: detection vs proxy rebuild → detection only; rebuild cannot know the server changed
  ([`research/03-reconnect-proxy.md`](research/03-reconnect-proxy.md)).
- TLS mismatch both ways within one deadline → positive proofs in milliseconds each way; no gate
  return needed ([`research/04-tls-mismatch.md`](research/04-tls-mismatch.md)).
- Idle-zone rule → (b), one request per idle zone at the finish; probes with and without an upstream
  database ([`research/05-idle-zones.md`](research/05-idle-zones.md)).
- Receive-time clock → monotonic; covers the converse defect (a dead client with a fast clock kept
  for skew + evict) ([`research/06-receive-time.md`](research/06-receive-time.md)).
- Tail measurement and staging → wrapper + judge + cluster recipe; the designed chain already exceeds
  5 s ([`research/07-tail-investigation.md`](research/07-tail-investigation.md)).

## 5. Risks & open questions

**For the foreman at the plan gate:**

1. **R5 vs R10 (deviation row 2):** accept that three request-list assertions in
   `test_relay_connector.py` change with R5's mechanism.
2. **R8 cluster trigger.** The designed chain alone gives > 5 s tails (smoke: 5.15 s on a tiny healthy
   run), so read literally "cluster only if no laptop run is over 5 s" would never fire while H0 is in
   place, and the Linux anomaly (excess beyond the design bounds, and the ~60 s run) would go unprobed.
   Proposed reading: at most 5 diagnostic laptop runs; if the segments name H0, fix it (below); then
   the remaining laptop runs on the fixed head; if none of those is over 5 s, the foreman submits the
   one cluster job (≤ 10 runs) on the fixed head; outcome (a) if it shows none over 5 s, else (b) with
   its logs. If the laptop runs show a segment `OVER` its bound (an anomaly), the cause is chased from
   those logs and no cluster job runs. Every started run counts.
3. **R8 fix (ii)** changes every server's shutdown latency (the shutdown reply no longer waits up to
   2 s for a heartbeat `get`), order kept. Pre-authorise it as within R8, or limit outcome (a) to fix
   (i) (relay only) and record (ii) under (b).
4. **TRACE logging for R8** (deviation row 4).
5. **R4's premise:** `hs client --no-tls` against a TLS server hangs; it does not fail at once. R4
   stands; `hs-review` should not grade the client against it.

**Residual risks:**

- The TimescaleDB 2.29–2.31 0-row miss does not reproduce on the local 2.32: R1/R2's re-run arms are
  proved by SQLite fault injection and the lock-wait cases on local PostgreSQL/TimescaleDB; the
  merely-contended hypertable "fails on `2ffb5cc`" leg is shown only by CI's TimescaleDB 2.31 jobs.
- R9's "contended case in `test_contended_write.py` on SQLite": that file needs a server database;
  the SQLite leg is `test_contended_recovery.py`.
- R1 residue (accepted, Non-goals): a row reverted, re-dispatched to the same reconnected client and
  re-confirmed inside the gap is reverted again; its result reads STALE and the task re-runs.
- R3: a restarted server minting the same id string for a different object would not be caught (not
  observed; out of scope).
- R5: a zone whose fleet never connects holds one task, and the relay and a non-forever upstream
  wait until a drain or stop (today's behaviour for a zone that holds work). An upstream restarted
  after a zone heard shutdown can see one extra size-1 request per idle zone.
- R2 writes one `schedule_time` per chunk instead of one per row.

## 6. Verification strategy

- **R9 red/green.** Every new regression test is listed (node id) in
  `spec/relay-hardening/drive/red.txt`; `drive/red_check.sh` exports `2ffb5cc`'s `src` with
  `git archive` into `$TMPDIR`, runs each listed test with that tree first on `PYTHONPATH` (the
  editable install comes after it), and fails unless **every** listed test fails there; the same tests
  pass on the head in each phase's own run. Tests that need a server database are listed with a
  `pg:` prefix and run only when `HS_TEST_POSTGRES` is set.
- **Unit/integration per phase** on SQLite; PostgreSQL and TimescaleDB legs
  (`HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres`,
  `HS_TEST_TIMESCALE=community` and empty; sandbox off) for the model phase.
- **Laptop drives** (`spec/relay-hardening/drive/hardening_drive.sh MODE`, throwaway site): `restart`
  (R1–R3: an upstream with a database and a backlog restarted on its port mid-run; the relay never
  exits 5, every task completes, the relay exits 0), `tls-on` and `tls-off` (R4: exit 5 within the
  deadline, the error text, no launch), `zones` and `zones-nodb` (R5: a two-zone relay, the gpu fleet
  delayed by its shim until after the cpu zone hears the shutdown; every task runs at the site; the
  relay and the upstream exit 0), `pair-relay` / `pair-server` (R10: a `2ffb5cc` relay against this
  server, this relay against a `2ffb5cc` server, `DRIVE_BASE_REV` as in `retire_drive.sh`: run
  completes as today). Plus the `relay`, `relay-topology` and `relay-retire` laptop drives on the
  head (R10), evidence in `evidence/laptop/`.
- **Docs** build with `-W` in a separate environment (the worktree's `.venv` stays intact):
  `UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs $TMPDIR/docs-html`
  (sandbox off for `sphinx_sitemap`).
- **Full suite** (`uv run pytest`, ~19 min) once, in the evidence phase.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*

## Plan-gate rulings (2026-11-13)

Foreman, gate judge under the maintainer's mandate. Approved as planned, with these rulings.

1. **R5 against R10.** The three `tests/test_relay_connector.py` request-list assertions change to
   `['cpu','gpu']`, as R5's mechanism requires. R10's exemptions read as R1, R2, R5's extra
   per-zone request, and R7.
2. **R7.** The four tests that back-date the receive stamp, with their assertions unchanged, are
   accepted.
3. **R8 trigger.** The proposed reading is adopted:
   - at most 5 diagnostic laptop runs;
   - if they name the designed chain, fix it and run the rest of the 10 on the fixed head;
   - one cluster job on the fixed head only if none of those exceeds 5 s;
   - if instead a segment exceeds its design bound, chase that cause from the logs, with no cluster
     job.
4. **R8 fix (ii)** is pre-authorised: the shutdown reply no longer waits out the heartbeat monitor's
   2 s queue read. It may only shorten every server's shutdown, with order kept and the existing
   suites passing; `hs-review` grades it under R10. Fix (i) is authorised too.
5. **TRACE** for the R8 runs is accepted.
6. **R4's premise.** Noted for `hs-review`: `hs client --no-tls` against a TLS server hangs; it does
   not fail at once.
7. **Evidence limits** are accepted as stated: CI's TimescaleDB 2.31 jobs carry R2's contended leg,
   and `test_contended_recovery.py` carries R9's SQLite contended leg.

Build on a supported Python: the worktree's `.venv` is CPython 3.15; re-sync it with
`uv sync --python 3.14` before verify.

## Review-cycle-1 rulings (2026-11-14)

Foreman, gate judge under the maintainer's mandate. Every finding is fixed this cycle; RV1 is triaged
as a fix, not a seed.

- **RV1, a zone with no client whose topology target is 0 takes a task it cannot run.** R5's
  one-task request is made only by a zone whose fleet would launch a client for it: under a
  published topology, a zone whose target at this relay is above 0; without one, a zone whose local
  fleet can launch (as today's fallback, `min` ≥ 1 or launches on demand). A zone whose target is 0
  ends as before. Rows staged for the zone stay with the upstream server for the relay that serves
  it. Pin it with a test that fails on `a741340`: the `plan_sites` probe shape (A gpu at 0, B
  holding gpu) at the puller level. Add a two-relay drive mode on the `relay-topology` fill harness
  if one fits the appetite. Otherwise record why the unit-level test suffices.
- **RV2.** Scope the added §1 sentence in `.agents/factory/invariants.md` to the conditional writes
  (the eviction revert and the dispatch stamp). `confirm_all` (strict) and `revert_all` still raise
  by design. Keep `AGENTS.md` and the invariants in lockstep.
- **RV3.** Each `red.txt` entry must fail on `2ffb5cc` for the defect itself, not for a missing API
  or a harness that cannot observe the base. Rework or drop the eight entries the review names.
  - Where a defect has no such red on any local backend, keep the entry out of `red.txt`, and say in
    TECH which test shows the defect and where. The contended stamp is shown on CI's TimescaleDB
    2.31 jobs only, per plan-gate ruling 7.
  - Make `red_check.sh` require the failure to come from an assertion, not a collection, import,
    attribute or type error, if that can be done reliably. Otherwise document the limit.
- **RV4.** `hardening_judge.py` must accept the documented `DRIVE_BASE_SRC` override.
- **Foreman's note (not a review finding).** `PLAIN_CHECK_WAIT` drops from 5 s to 1 s. A cleartext
  upstream sends its challenge as soon as it accepts (about 1 ms measured), so a 1 s wait loses
  nothing. It keeps R4's probe from holding a connection to an upstream longer than it needs.
