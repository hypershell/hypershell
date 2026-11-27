# Research 04 — What a relay holds, what counts as started, and how upstream can take it back

Scope: R3, R4, R5, R6, R7. Source is `develop` at `fb89670` (no `src/` drift from `8403388`).

## 1. What a relay holds

A received bundle enters the relay database in one transaction (`store.insert_received`,
`relay/store.py:197-235`): a relay-local `task` row with the scheduling and client columns cleared
(`LOCAL_STAMP_COLUMNS`, `local_row`, `:172-194`), so the relay-local server schedules it as new, and a
`relay_task` row holding the upstream stamp verbatim (`schedule_time` text, `server_id`,
`server_host`) and the upstream `identity` that confirmed it (`:100-110`). Only after that commits is
the bundle confirmed upstream (`UpstreamPuller.confirm`, `relay/upstream.py:429-443`), which stamps
the upstream row's `client_id` with that zone identity.

At the site the relay-local server's scheduler stages unscheduled rows into its dispatcher pool, and
staging stamps the local `schedule_time` (`Dispatcher.stage`, `core/dispatch.py:385-403`, rows
`stamped=True`). A site client's request cuts rows out of the pool under the dispatcher lock
(`Dispatcher.request`, `:313-356`); its confirmation then writes the local `client_id`. Completed
rows leave the task table for the outbox (`store.collect_results`, `:316-347`) and go upstream.

So a relay-local row is in one of three states while incomplete:

| Local state | Meaning | Who can take it next |
|---|---|---|
| `schedule_time IS NULL` | received, never staged (or staged and reverted) | the local scheduler |
| `schedule_time` set, row in a local pool | staged, not delivered | a site client's request, or `take_staged` |
| `schedule_time` set, row not in any pool | delivered to a site client (confirmed or about to be) | that client only |

## 2. What "started" means (R5)

The relay already has a definition: `store.drop_superseded` (`:238-257`) drops held tasks "not yet
started here" as those with no local `schedule_time` and no `completion_time`. That definition counts
a staged-but-undelivered row as started, which is conservative but not exact. The exact line for this
cycle is **delivered**: a row cut from a relay-local pool by a site client's request. The relay cannot
see inside a client's bundle which task is executing, and a client never gives a task back, so every
delivered row is the client's to run (R5). A staged row that no request has cut can be withdrawn
atomically with `Dispatcher.take_staged` (`:480-494`, under the dispatcher lock, the same call
`ServerThread.revert_staged` makes at shutdown, `server.py:1207-1216`): each staged row goes either to
exactly one request or to the take, never both.

Withdrawal is race-free only once the local scheduler no longer stages: stop it first (as
`RelayThread.stop_site` already does, `relay/__init__.py:670-691`), then take what is staged, revert
those rows locally (`Task.revert_staged`), and the unstarted set is every incomplete local row with no
local `schedule_time`. Rows the relay-local scheduler already reverted when a zone's last client left
(`Scheduler.revert_unstaged`, `server.py:390-398`, the RV5 path) are in that set too.

## 3. Upstream revert paths, and why none fits as is

| Path | Trigger | What it reverts | Fit |
|---|---|---|---|
| `Task.revert_orphaned(client_id)` (`data/model.py:1154-1159`) | eviction only (`HeartMonitor.check_clients`, `server.py:902-920`) | every incomplete row with that `client_id` | No: a retired relay's site clients are still running some of them (R5); and a relay is evicted only after 600 s (R7) |
| `Task.revert_staged(ids)` (`:1010-1025`) | `Scheduler.revert_unstaged`, `ServerThread.revert_staged` | rows by id while `completion_time IS NULL` | Close, but by id alone: a row the relay held under an assignment the server has since reverted and given to another client would be reverted under that client (a double run) |
| FINISHED heartbeat (`update_client`, `server.py:796-806`) | final beat | nothing: it disconnects only | No, and a new `ClientState` value kills older servers' heart monitors (research 01 §3) |
| `Task.revert_interrupted()` | server start | everything in flight | No |

A "final heartbeat variant that releases unstarted tasks" would need to name the tasks anyway (an
identity-wide release breaks R5 while site clients run), and must happen before the clients finish
(R7 counts from the mark, not from the clients' exit), so the identity is still beating. The release
is therefore a list of tasks, sent while the relay is still live.

## 4. The match a hand-back needs

The server already identifies an assignment by the `schedule_time` it stamped and the client returns
unchanged (`Task.apply_results`, `data/model.py:1063-1146`: one conditional UPDATE matching `id` and
`schedule_time` with `completion_time IS NULL`, contention handled per invariants §1). The relay keeps
that stamp verbatim (`relay_task.schedule_time`). A hand-back revert matching **id + schedule_time as
dispatched + no completion_time** reverts exactly the assignment the relay holds and nothing a later
dispatch made; adding **`client_id` is the identity or NULL** (NULL: the relay's confirmation was lost
with the link, `UpstreamPuller.wait_link`, `upstream.py:445-463`) guards against a row reassigned
under the same stamp, which the server never does but costs nothing to check. A row that matches
nothing is settled (completed, cancelled, already reverted by eviction and re-dispatched, or deleted):
the relay drops it either way.

## 5. Where the server does the write

`core/dispatch.py` is database-free by design ("Only the scheduler thread touches the database",
`:14`), and `Dispatcher.topology` is a pure read. The precedent for a dispatcher-side request that
ends in a database write is the zone unstaging: `Dispatcher.forget` marks zones, the scheduler takes
them (`take_unstaged`, `:468-478`) and writes the revert at the top of its loop
(`Scheduler.load_bundle` → `revert_unstaged`, `server.py:242-249`), woken by `_signal_need`. A
hand-back follows the same shape: the dispatcher validates and queues it, signals need, and the
scheduler writes it on its next pass (immediately, since `wait_need` returns on the signal). The
reply acknowledges what was queued; the relay may delete its local copies on that acknowledgement,
because every queued row either is reverted or no longer belongs to the relay. A server that stops
before writing it reverts every in-flight row at its next start (`revert_interrupted`).

## 6. Timing against R7 (defaults)

Upstream heartbeat every 10 s (`[relay.upstream] heartrate`), so R7 allows 30 s from the first read
of the mark. The relay's main loop checks every `STOP_CHECK_INTERVAL` (1 s); the puller ends at its
gate within `GATE_WAIT` (0.1 s) or after one in-flight request (at most `DEFAULT_REMOTE_TIMEOUT`
for one zone, `ZONE_REQUEST_WAIT` = 1 s for several); the relay-local scheduler halts within one
`wait_need` (its poll is `[relay] poll`, 0.1 s); the hand-back call is one round trip; the upstream
scheduler reverts on its next pass. Expected total: a few seconds, well inside 30 s.

The mark itself is computed at the publisher's check (`[autoscale] period`, 60 s by default), so the
mark reaches the old relay up to one period plus one beat after the newer instance registers. R7
measures from the old relay's first read, so that latency is outside the bound, and the duplicate
warning (R1) is logged at the same check.
