# Research 02 — Server-side staging (zone-dispatch, Z2)

Codebase-first; line numbers are this worktree (Z1 head) unless noted.

## 1. Today's dispatch path and footprint

- `Scheduler.load_bundle` (`server.py:206-244`) calls `Task.next(limit=bundlesize, attempts, eager,
  group=current_group)`. `Task.next` (`data/model.py:624-640`) selects (new first, or failed first
  when `eager`; `__next_not_eager` only takes failures if `attempts > 1`, `:653-660`), stamps
  `server_id`, `server_host`, `schedule_time` and commits. Retries are new rows created *inside*
  selection (`__schedule_next_failed_tasks`, `:663-681`).
- PACK serializes (`:246-249`); POST does `scheduled.put(timeout=2)` and re-enters on `QueueFull`
  (`:251-260`). `scheduled` has `maxsize = config.server.queuesize` (`core/queue.py:778`), default
  1 (`core/config.py:133`); server `-b` default 1 (`:134`).
- **Footprint:** stamped-but-undelivered rows ≤ `(queuesize + 1) × bundlesize`: `queuesize` bundles
  in the queue plus one loaded bundle held in POST. Default: 2 rows.
- Nothing loaded: FINAL if all done (`:219-226`), else `increment_group` and exponential backoff
  `time.sleep(poll)` from 0.5 s up to `poll` (30 s) (`:228-244`).
- Session is a thread-local `scoped_session` (`data/core.py:268-269`) with expire-on-commit. Rows
  returned by `Task.next` must be turned into dicts (`to_json`) **in the scheduler thread**. A
  manager handler thread must never touch those ORM objects.

## 2. Per-zone staging design

`Staging` object, owned by `ServerThread`, shared by the Scheduler thread and the request handler
threads:

- `lock: threading.Lock`; `pools: dict[zone_id, deque[Entry]]`, where `Entry = (task_id, json_dict,
  stamped: bool)`; one `Condition(lock)` per zone (`ready`), so staging zone A wakes only A's
  waiters; one `Condition(lock)` for the scheduler (`need`); `clients: dict[client_id, (zone,
  size)]`; `finishing: bool`.
- **Demand registration:** each request registers or refreshes `clients[client_id] = (zone,
  size)`. Request recency is not a liveness signal, because a client running a long bundle doesn't
  request for hours. Liveness comes from the client's lifetime instead: HeartMonitor calls
  `staging.forget(uuid)` on a FINISHED heartbeat (`server.py:601-607`) and on eviction
  (`:630-637`). The heartbeat uuid and the request's client id are both the client's `INSTANCE`.
- **Size per zone:** `S_z = min(cap, max(advertised size, or server -b for a client that advertises
  none) over the zone's registered clients)`. **The default zone is always demanded** with `S ≥
  server -b`, even before any request arrives, so it pre-stages exactly as today.
- **Fill** (Scheduler LOAD, scheduler thread only): for each demanded zone with `len(pool) ≤
  queuesize × S_z`, run `Task.next(limit=S_z, group=current_group, zone=z, …)` and stage the result
  as one chunk of `S_z` (`to_json` in PACK). The maximum depth is `(queuesize + 1) × S_z`, the same
  as today's bound. The GOAL's "queuesize × size" becomes the refill mark, and the one chunk above
  it stands in for today's bundle held in POST. With no zone below the mark, the scheduler waits on
  `need` (finite timeout, about 2 s) instead of blocking in POST, and every cut notifies `need`. The
  backoff `time.sleep(poll)` becomes `need.wait(poll)`, woken when a *new* zone first registers, so
  a new zone's first client doesn't wait out a 30 s backoff.
- **Cut** (handler thread): `n = min(advertised or server -b, cap)`. Under `lock`, pop up to `n`
  from the front of the zone's pool. If the pool is empty, wait on `ready[z]` with a finite timeout
  below the client's RPC timeout, then return empty. Return a partial bundle at once if fewer than
  `n` are staged (R4), notify `need`, and release the lock before serializing. Popping under the
  lock is what makes "no task delivered twice" (R4) hold.
- **Zone filter sketch:** `Task.next(…, zone=DEFAULT_ZONE_ID)`, applied in `select_new` (`:586-591`)
  and `query_retryable` (`:594-604`) only when `zone` is given:
  `or_(Task.zone.is_(None), Task.zone == DEFAULT_ZONE_ID)` for the default zone, else `Task.zone ==
  z`. NULL counts as default, so no rows are rewritten (R11). `count_retryable` and
  `increment_group` stay zone-free. The data-model brief owns indexing; the residual zone predicate
  rides on `index_tasks_unscheduled` (`:901`).

## 3. R1 parity

In the homogeneous case every client is default-zone and advertises either nothing or the cluster's
`-b`. `hsx` passes `-b` explicitly (`cluster/local.py:219,236`, `ssh.py:298`, `remote.py:305`), and
it equals the server's `-b`. So `S = server -b`, loads are exactly `S`, the pool is FIFO in
`Task.next` order and each cut takes `S` from the front. The result is the same tasks in the same
order with the same `≤ (queuesize+1)·S` stamped-undelivered bound. Bundle boundaries can differ only
after a partial load (`k < S` rows ready) that isn't cut before the next load. Final state and group
order are unaffected.

**Overhead:** each cut costs two lock operations, a `notify` and O(n) `popleft`: a few µs, and about
50 µs at n = 1000. A TLS manager round trip costs about 100-500 µs and a DB commit is ms. Today's
client does `get()` **and** `task_done()`, two RPCs per bundle (`client.py:212-213`), where a
request needs one. JSON encoding moves from the scheduler thread to the handler thread under the same
GIL, so its cost is unchanged. No new threads or fds. **Risk:** an idle-pool wait that polls instead
of waiting on a condition.

## 4. Server cap (R3)

Recommend a config/env key `server.bundlecap` (`HYPERSHELL_SERVER_BUNDLECAP`) with default 1000 and
no CLI flag. The effective cap is `max(bundlecap, server -b)`, so no `-b` a user sets today is ever
shrunk. 1000 rows is about 1-2 MB per reply frame and up to `(q+1)·1000` dicts held per pool. Make it
config-only: no help snippet or completion work under R13, but list it in the config reference. A
relay that needs more raises the key.

## 5. Unstaging by id

`unstage(zone, ids=None) -> entries` pops under `lock`, so it can never race a cut. Revert the
stamped entries with `Task.revert_all(ids)` (`model.py:714-736`) **in the scheduler thread**, and
never with a bespoke UPDATE.

- **A non-default zone's last client leaves:** LOAD sees a non-empty pool with no registered clients,
  then unstages and reverts it. Without this its rows read as in-flight until restart.
- **Shutdown:** see §8. Any pool left after HeartMonitor joins is reverted; a crash leaves them for
  `revert_interrupted` at START (`server.py:195-198`, `model.py:744-747`), which already covers
  them.
- **Eviction:** staged rows have no `client_id`, so `revert_orphaned` (`model.py:786-789`) never
  touches them. That's correct, since they were never delivered. `forget()` only drops demand.
  `no_confirm` is unchanged: delivered rows are still never orphan-recovered (invariants §4).
- **Cancel:** `hs update --cancel` (`task.py:1080-1092`) is another process, and it already warns
  when tasks are "already scheduled" (`:1085-1086`). Today a cancelled row sitting in `scheduled` is
  still delivered, so keep parity. Once the writeback fence lands, its result is refused (that
  cycle's R7). An optional idle-time sweep could drop pooled ids whose `completion_time` is set;
  **drop, not revert**. It's deferrable.
- **Group advance:** no unstage needed (§6).

## 6. Group gating (R10)

There is one global `current_group`, every per-zone `Task.next` is called with it, and
`increment_group` (`model.py:538-583`) counts incomplete rows across zones. Staged rows are stamped
and not complete, so they hold the group, and a later group can't be staged while any zone still
holds current-group work. When the group advances, every pool is empty, apart from cancelled-staged
rows as in §5. Call `increment_group` only when *every* zone below its refill mark loaded nothing.
FINAL keeps the global `count_remaining()==0` test.

**Log line:** while backing off with reason "waiting on N tasks", count the incomplete current-group
tasks per zone that has no registered client. Log once per (group, zone) at INFO, and again each time
the backoff reaches its ceiling: `Task group {g} held: {n} tasks in zone '{name}' have no connected
client`.

## 7. Retries (R9)

The retry row is built at `model.py:669-675`; add `zone=task.zone` there (NULL stays NULL, which is
still default). `Task.new` (`:380`) gains `zone`. `select_failed` and `query_retryable` take the
optional zone filter; `count_retryable` passes none.

## 8. Shutdown and end-of-work

Today `wait_heartbeat` (`server.py:893-898`) sets `should_signal`, and `signal_clients` puts one
sentinel per connected client on `scheduled` (`:640-647`). FIFO puts those sentinels behind the
remaining bundles. Replace the sentinels with `staging.finish()`: set `finishing` and `notify_all`
every zone. A reply carries `done` only when `finishing` is set, its zone pool is empty and
`scheduled` is empty, so staged work still drains first, as today. Order:

1. Submitter join.
2. Scheduler join: nothing refills the pools.
3. `finish()` at the point where `signal_clients` runs today.
4. HeartMonitor exits once clients are gone (`:615`).
5. **New:** revert what's left in the pools (stamped entries only, `if not in_memory`), logged as
   "reverted N staged tasks never delivered".
6. Sentinels to the receiver, then to confirm, both unchanged (`:900-909`).

A flag also serves clients that connect after the signal, which today's one-shot sentinel count
misses. Scheduler FINAL on SIGUSR1/2 or a failed group drains in the same way (parity: today the
POST-held bundle and the queued bundle are still delivered).

## 9. in_memory mode

`Scheduler` is None and `LiveSubmitThread` puts bundles on `scheduled` over a loopback `QueueClient`
(`server.py:823-827`, `submit.py:740-744`, `866-878`). `hs submit --queue` does the same in either
mode (`submit.py:1290,1350-1361`). **Minimal design:** keep `scheduled` as the *inbound* channel in
both modes. A default-zone cut that finds fewer than `n` staged rows drains `scheduled.get_nowait()`
under `lock`, extends the default pool with `stamped=False` entries, then cuts. Leftovers are served
first next time; this re-chunks to the client's size. If both are empty, it waits on `scheduled`
outside the lock with a finite timeout. In-memory mode has no Zone table, so zones are default-only:
a zoned client gets empty replies (warn once) and then `done`. The `queuesize` back-pressure on
`scheduled` stays. Unstage never reverts `stamped=False` entries.

## 10. Overlap with `fix/writeback-scoped-conditional`

That branch has only GOAL/META, with no PLAN. Its "assignment" runs from scheduling to
completion, cancellation or revert, so staging **starts** an assignment at stamp time in
`Task.next`, before delivery. Whatever token the fence introduces must be set in `Task.next` and be
present in the staged `to_json` dict. Unstaging **must** go through `Task.revert_all` so it ends the
assignment exactly as a revert does. A re-stage must mint a fresh token, because that GOAL's R2
counts "rescheduled to the same client" as a new assignment.

Shared touchpoints:

- `Task.next`
- `Task.__schedule_next_failed_tasks` (Z2 adds `zone`)
- `Task.revert_all`, `revert_orphaned`, `revert_interrupted`, `cancel_all`
- `Task.update_all` callers `Receiver.update_tasks` (`server.py:459-469`, its fence) and
  `Confirm.update_info` (`:361-365`)
- `HeartMonitor.check_clients` (`:624-638`)

If Z2 moves confirmation into the request path (the server knows the client at cut time), coordinate
with the fence: that GOAL's R4/R8 forbid keying on the returned `client_id`, and `no_confirm` results
must still apply.

## Recommendation

- **Structure:** `Staging` holds one lock, per-zone `deque[(id, json_dict, stamped)]`, per-zone
  `ready` conditions, a scheduler `need` condition, a `client_id → (zone, size)` demand map
  (registered by requests, cleared by HeartMonitor on FINISHED or eviction) and a `finishing` flag.
- **Fill:** the scheduler thread loads exactly `S_z` per demanded zone while `len(pool) ≤
  queuesize·S_z`, so the maximum is `(queuesize+1)·S_z`, equal to today's footprint. The default
  zone is always demanded at `≥ server -b`. Condition waits replace POST-blocking and `sleep`.
- **Cut:** under the lock, pop up to `min(size or server -b, cap)`. Return partial results
  immediately, or empty after a finite wait. Serialize outside the lock.
- **Cap:** `server.bundlecap`, config/env only, default 1000, effective `max(cap, server -b)`.
- **Unstage:** a zone losing its last client (non-default zones), and leftovers after HeartMonitor
  joins. Always `Task.revert_all` in a single owner thread; drop (not revert) cancelled rows.
- **Shutdown:** `finish()` flag at today's `signal_clients` point. `done` is sent only once the
  zone pool and `scheduled` are empty. Receiver and confirm sentinels are unchanged.
- **in_memory:** the default zone only, cut from a pool fed lazily from `scheduled`; `hs submit
  --queue` keeps working.
- **Risks:**
  1. Strict FCFS (R4): a newly arriving request can grab rows ahead of a waiter that was woken but
     hasn't reacquired the lock. Use a per-zone waiter ticket queue if graded.
  2. A missed `need` notify degrades to a 2 s poll and fails the 95% bar.
  3. Touching ORM objects off the scheduler thread.
  4. Deeper pools (relay-sized) widen the cancelled-but-delivered window. The writeback fence
     neutralises its result.
  5. Default-zone rows stay staged while only zoned clients run, as today. The §6 log line
     explains the resulting group hold.
