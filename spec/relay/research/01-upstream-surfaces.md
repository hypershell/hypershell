# 01 — Upstream surfaces a relay rides (confirm, result, heartbeat, counts, DQ1/DQ3/R19)

Tree: the post-zone preview at `/tmp/claude-501/relayplan/postzone/` (**PZ**). Where it has conflict
markers, Z1 = `origin/feature/zone-queue-primitive` `9f36de4`, Z2 = `feature/zone-dispatch` `b6fcb55`.
PZ `server.py` auto-merged `Confirm` from Z1 (`Task.confirm_all`); Z2 alone wrote confirms with
`Task.update_all(client_info.transpose())` (Z2 `server.py:434`).

## 1. Confirm path and R19

- **Payload.** `ClientInfo` (PZ `client.py:105-145`): JSON `{client_id, client_host, task_ids}`, one
  identity per message, chosen by the sender (`from_tasks` defaults to `INSTANCE`, but the
  constructor takes any id). No `schedule_time`, so a confirm can't be matched to a dispatch.
- **Server.** `Confirm.update_info` (PZ `server.py:512-517`) calls `Task.confirm_all(ids, id, host)`.
  It ignores the return value and sends nothing back.
- **`confirm_all`** (PZ `model.py:925`; Z1 `831`) goes through `__write_rows` (PZ `886`). That
  issues one `UPDATE … WHERE id = :r_id` per row from `__row_statements` (PZ `869-884`), with **no
  condition**. On 0 rows it re-reads `SELECT id`. If the row is missing it raises `StaleDataError`.
  If the row exists it re-runs up to `CONTENDED_RERUNS` (=3, PZ `76`), then logs a warning and
  moves on ("client not recorded").
- **R19 fix (minimal).** Give `__row_statements` an optional guard. For the confirm column set, add
  `completion_time IS NULL` to the `UPDATE`, and make the re-read `SELECT completion_time … WHERE id`.
  In `__write_rows`, a 0-row result is then classified three ways:
  - **missing**: raise `StaleDataError`, as today;
  - **complete** (`completion_time` not NULL): a settled, non-retry outcome. Break with no re-run
    and no warning, log at trace/debug, and leave the id out of `landed`;
  - **still open**: contended, so re-run, as today.

  Revert keeps its unguarded statement. Without the new branch, every late confirm would cost 4
  UPDATEs and a false "Contended confirm write did not land" warning. That is exactly what
  `test_contended_confirm_exhausted_keeps_running` would flag. Side effect: a confirm that lands
  after a cancel (cancel sets `completion_time`) no longer stamps `client_id`. This is benign, and
  no existing test depends on the old behaviour.
- **Existing tests.**
  - `tests/test_contended_recovery.py` (Z1, in PZ): `test_contended_confirm_lands_on_rerun:216`,
    `…_exhausted_keeps_running:232`, `test_confirm_of_missing_row_raises_and_writes_nothing:301`,
    `test_uncontended_confirm_and_revert_cost_one_update_per_row:316`.
  - `tests/test_contended_write.py` (PG/Timescale, real row locks):
    `test_contended_confirm_records_client:231`, `test_contended_orphan_revert_lands:290`.
  - `tests/test_writeback.py` has the `confirm()`/`finish()`/`receive()` helpers (`:93`).
- **Red-first test** (`test_writeback.py`, SQLite, deterministic):
  1. `submit`, then `dispatch_one`;
  2. `receive([finish(copy, 0, LEAF)])` with no confirm yet;
  3. `confirm(RELAY, [id])`;
  4. assert `row(id)[client_id, client_host] == LEAF`. This fails today with RELAY.

  Companions:
  - In `test_contended_recovery.py`, run the late confirm with `contention.lose(id, 1)`. Assert
    `contention.kinds(id) == ['UPDATE', 'SELECT']`, no WARNING, and attribution unchanged.
  - A PG variant in `test_contended_write.py`: a holder transaction sets `completion_time` while the
    confirm waits, and the confirm must classify "complete".
  - Regression: confirm-before-result for the same client is unchanged (the existing tests cover it).

## 2. Receiver and the result fence

- `result_columns` (PZ `model.py:437`): 13 columns, `client_id` … `memory_max`. `ResultOutcome`
  (PZ `323`) has APPLIED/DUPLICATE/STALE/DEFERRED (DEFERRED is Z1-only; Z2 lacks it).
- `__apply_result` (PZ `1094`) does
  `UPDATE … WHERE id AND schedule_time = :sent AND completion_time IS NULL`. A result with no
  `schedule_time` is STALE. On 0 rows it re-reads by (id, schedule_time): no row is STALE, a
  complete row is DUPLICATE, an open row is re-run up to 3 times and then DEFERRED. It commits once
  per list.
- `Receiver.unload_bundle` calls `completed.task_done()` **immediately after `get`, before
  applying** (PZ `server.py:598`). `update_tasks` (PZ `~620-650`) applies `held + tasks`. DEFERRED
  results go back into the in-memory `self.held` list and are presented again on every cycle, even
  when the queue is empty. At shutdown, held results are logged "not written … task left
  interrupted".
- **No per-result acknowledgement exists.** `completed.put` (PZ `client.py:430`) returns once the
  bundle is in the server-process `Queue(maxsize=queuesize)` (PZ `queue.py:956`), and it can raise
  `QueueFull`. Outcomes appear only in the server log.
- **What a relay can observe:** only "the put returned" (custody transferred) or "the put
  raised/timed out" (not transferred). It cannot tell APPLIED from DEFERRED or STALE.
- **R5's deferred clause.** Taken literally ("don't prune a DEFERRED return"), it is unobservable.
  Once the put returns, a DEFERRED result is in the upstream Receiver's custody. The Receiver
  re-presents it until it lands, or until the row is reverted and it turns STALE. If the server
  dies first, `revert_interrupted` re-runs the task. Keeping the result at the relay buys nothing:
  after an upstream restart a replay is STALE by construction. So the implementable rule is
  **prune only after `completed.put` returns without exception; never on `QueueFull`, timeout or
  connection error.** Delivering a real ack would need a new general server→client reply path,
  which is outside R2's "no relay-specific change". Record this as a GOAL wording clarification for
  R5, not as a design gap.

## 3. Heartbeat, HeartMonitor and DQ3

- `Heartbeat` (PZ `core/heartbeat.py:44`) has `uuid, host, time, state`. `pack` (`64`) writes JSON
  (`core/types.py:75`). `unpack_or_none` (`72`) reads exactly those four keys. **Extra keys are
  ignored** (an old server tolerates new fields). **Missing keys raise `KeyError`**, so new fields
  must be read with `.get(…)` and have defaults. `time` is the *sender's* clock, and eviction age
  is `last_check - hb.time` (PZ `server.py:~812`).
- `update_client` (PZ `765`):
  - On a first beat it calls `Client.from_heartbeat` (`model.py:1312`, `server_id=INSTANCE` of the
    upstream). The #29 branch (`776`) re-registers an existing id with `disconnected_at=None`,
    `evicted=False`; otherwise it calls `Client.add`. The id then goes into `self.beats`.
  - On FINISHED it does `beats.pop`, then `Client.update(disconnected_at)`, then
    `dispatcher.forget(uuid)` (`790`).
- `check_clients` (PZ `808`) handles any id in `beats` past `evict_after`: `Client.update(…,
  evicted=True)`, then `Task.revert_orphaned(uuid)` (unless `in_memory`/`no_confirm`, `821`), then
  `dispatcher.forget(uuid)` (`822`).
- `Client` columns (PZ `1271-1291`): `id, host, server_id, server_host, connected_at,
  disconnected_at, evicted`.
- **Option A (recommended): roster piggyback.** The relay heartbeat gains optional `site` and a
  `roster: [{uuid, host, state}]`, sent as a full snapshot each beat (idempotent and self-healing).
  On a beat with a roster, HeartMonitor diffs it against `self.rosters[relay]`:
  - it adds leaf `Client` rows (reusing the #29 branch) with a nullable `site` and a nullable
    `relay` UUID column (#69 upgrade);
  - it disconnects leaves that dropped out or are FINISHED;
  - it **never puts leaves in `beats`**, so they can't be evicted on their own and don't hold
    shutdown (`not self.beats`).

  Relay eviction or FINISHED then gets one more statement:
  `UPDATE client SET disconnected_at=now WHERE relay=:id AND disconnected_at IS NULL`. Leaves hold
  no in-flight rows (those carry the relay id), so they need no revert or `forget`. An old server
  ignores the fields and the relay still works, but no leaf rows appear (see §4). Cost is about
  10 KB per beat for 100 leaves.
- **Option B: per-leaf heartbeats** put by the relay with the leaf uuid. This works with the
  upstream unchanged for registration, but:
  - leaves enter `beats` and are evicted independently;
  - a relay eviction doesn't cascade;
  - the server waits for a FINISHED beat from every leaf;
  - it costs N round trips over the WAN every 10 s on one connection.

  Meeting DQ3 would still need an optional `relay` field plus the same server change, so B is
  strictly more traffic for the same patch.

## 4. Counting and rate math (R6, R7)

- `latest_server` (PZ `1144`) returns the `task.server_id` with the newest `schedule_time`. Results
  can't write `server_id` (it isn't a result column), so upstream is safe as long as returns carry
  the upstream `schedule_time`.
  - **Pitfall:** don't store the upstream stamp in the relay DB's `Task.schedule_time`. The embedded
    server re-stamps it on local dispatch, and `revert_interrupted` on a relay restart nulls it.
    Keep it in a separate relay-side column or table.
- `count_connected` (PZ `1330`) counts `client` rows with `server_id = latest` and
  `disconnected_at IS NULL`. Leaf rows created by the upstream HeartMonitor get the upstream
  `server_id`, so they count. Relay identities count too: one per relay, or **K per relay** under
  DQ1(a). It is used only in the autoscaler's debug log (`cluster/remote.py:486,506`). For "correct",
  exclude relay identities, e.g. `relay IS NULL OR relay <> id`, if the relay row self-references.
- `effective_rate_by_client` (PZ `1158`) and `avg_duration` join `Task.client_id = Client.id` with
  `disconnected_at IS NULL`. Completed relay rows carry the **leaf** id.
  - Without roster rows, they silently drop out of the rate, so `task_pressure` (`1215`, used by the
    autoscaler at `remote.py:510`) under-reads.
  - Without R19, a late confirm moves them to the relay id, which gives a site-aggregate "client"
    with a bogus per-client rate.
  - Relay identities never own completed rows, so there is no double count.

  **Must hold:** results carry the leaf id and the upstream `schedule_time`, leaf rows exist and stay
  connected while the relay lives, R19 is in place, and relay rows are excluded from
  `count_connected`.

## 5. DQ1(a): one upstream identity per zone over one connection set

- **Nothing binds an id to a connection.**
  - `Dispatcher.request` (PZ `core/dispatch.py:307`) takes `client` from the JSON (`Request.decode`,
    `115-136`; unknown keys are ignored).
  - Confirms carry `client_id`, heartbeats carry `uuid`, results carry a `client_id` per task.
  - Z1's admission (`queue.py:531 _admits`) counts descriptors only.

  So one process can speak for K ids, building its own `request_text` (the stock one hard-codes
  `INSTANCE`, `client.py:222`), `ClientInfo(client_id=…)` and `Heartbeat.new(uuid=…)`.
- **Eviction is per id.** Each zone id heartbeats separately, is evicted separately, and
  `revert_orphaned(id)` reverts exactly the rows confirmed to it. The relay must therefore confirm
  each zone's bundle under the id that requested it. `forget(id)` pops only that id's demand
  (`dispatch.py:433`). Relay death means all K ids go silent, so every zone is reverted and
  unstaged.
- **Connections.** `BaseProxy` caches one connection **per thread** (`queue.py:397`). A request
  long-polls for up to `wait` on its handler, so K zones want K requester threads, plus the
  returner and heartbeat threads: K+2 connections per relay. The Z1 budget is about 3 per client
  (Z1 PLAN:119). That's acceptable.
- **Option (b) breaks Z2's tests.** `tests/test_zone_dispatch.py:197` assigns
  `dispatcher.demand['g2'] = (GPU, 1)` directly, and `_size`, `clients`, `zoned`, `take_unstaged`
  and `forget` all read `demand.values()` as `(zone, size)` keyed by client. Re-keying fails "Z2's
  tests pass unmodified".

## 6. `no_confirm`

- **Server `--no-confirm`** (PZ `server.py` `ServerApp`; `ServerThread` sets `confirm=None`; the
  HeartMonitor skips `revert_orphaned`, `821`): a relay's confirms then pile up in the unbounded
  `confirmed` queue (`queue.py:958`), and failover is gone. The same holds for an `in_memory`
  upstream.
- **Client `--no-confirm`** (`client.py:~255`) only skips the put.
- **Detection.** The relay can't detect the upstream's mode, because no reply field carries it.
  The upstream *can* detect a relay once heartbeats carry `site`/`roster` (§3). A cheap, general
  guard is for the HeartMonitor to log one ERROR when a roster beat arrives under
  `no_confirm`/`in_memory`. Hard refusal would need an error reply from `request`, which is a
  dispatch-surface change. Refusing on the relay CLI, plus that warning and docs, is enough.

## Recommendations

- **DQ1:** choose (a). Use one upstream id per zone, derived deterministically (e.g.
  `uuid5(relay INSTANCE, zone)`), each with its own requester thread, confirms and heartbeat. The
  upstream needs no change, and (b) would edit `test_zone_dispatch.py:197`.
- **DQ3:** choose roster piggyback (A). Add optional `site`/`roster` heartbeat fields read with
  `.get`. Leaves stay out of `beats`; a relay eviction or FINISHED disconnects `WHERE relay=:id`.
  Add nullable `client.site` and `client.relay` columns via #69.
- **R19:** guard the confirm UPDATE with `completion_time IS NULL`. On a 0-row re-read, a complete
  row is a settled outcome, with no re-run and no warning. Test red-first in `test_writeback.py`,
  then add contended and PG companions.
- **R5 deferred clause:** prune only after `completed.put` returns. DEFERRED is the upstream
  Receiver's custody, and no ack exists. Amend R5's wording rather than add an ack path.
