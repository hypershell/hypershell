# Research 02 — Upstream connector and outage ride-through (R2, R3, R5, R9, DQ2)

Base: post-zone tree. Line refs are `feature/zone-dispatch` for `client.py`, the post-zone preview
for `core/queue.py`, `server.py`, and `origin/feature/zone-queue-primitive` for `data/model.py`
(cited as Z1). The outage behaviour below comes from a stdlib probe (`BaseManager` and a queue in a
subprocess; Python 3.14), scratch `/tmp/claude-501/relayplan/a02/exp_*.py`.

## 1. Client machinery post-Z2: what the relay can reuse

`ClientThread` (client.py:1121-1340) builds one `QueueClient` and four roles over it:
`ClientScheduler` (START→REQUEST→UNPACK→PUT_CONFIRM→POP_TASK→PUT_LOCAL, :146-283), executors,
`ClientCollector` (bundlesize/bundlewait; `put_remote` :419-432), and `ClientHeartbeat` (:1009-1074).
Shutdown order is scheduler→executors→collector→heartbeat. A child thread's exception surfaces only
at its `join()` (core/thread.py). If the scheduler dies, `ClientThread` leaves its `with self.client`
and the other daemon threads die at process exit. Idle `client_timeout` is measured from
`previous_received` in `request_bundle` (:236-241).

**The one-identity assumption is baked in.** `request_text` hard-codes `INSTANCE`/`HOSTNAME`
(:215-218). So do `ClientInfo.from_tasks` (:135-138), `TaskExecutor.create_task`
(`client_id = INSTANCE`, :755) and `Heartbeat.new` (default `uuid=INSTANCE`). `Heartbeat.new` and the
`ClientInfo(...)` constructor take explicit ids. `request_text` and `from_tasks` don't.

| Need | Reuse as-is | Relay-specific |
|---|---|---|
| (i) request per zone/size/id | reply validation + `QueueVersionError` text (:225-230); `QueueClient.dispatcher` | the request text must name the per-zone id: factor `request_text(client, host, zone, size, wait)` into a module function that `ClientScheduler` calls with `INSTANCE` |
| (ii) confirm | `ClientInfo(client_id=<zone id>, …).pack()` → `confirmed.put` | sent only after the durable local insert (R3) |
| (iii) return results | `serialize_tasks`; `completed.put(timeout=…)` | the source is the local-DB outbox, not an in-memory `Queue`; the upstream `schedule_time` is restored before the put |
| (iv) heartbeat | `Heartbeat.new(uuid=…, state=…)`, `pack()` | one beat per zone identity, plus the roster (DQ3) |

**Recommendation: build new thin FSMs in `src/hypershell/relay.py`. Don't subclass the client FSMs.**
The relay puller runs GATE(R8 backlog, signal poll) → WAIT_LINK → REQUEST(zone i) → INSERT(durable)
→ CONFIRM → GATE, which is a different graph from the client's local-queue hand-off. The returner
reads the DB, and the heartbeat iterates identities. Subclassing would override every action anyway.
Multiplexing (DQ1(a)) fits the proxy model: all proxies to one address share **one connection per
thread** (`BaseProxy._address_to_local[address]`). So the upstream descriptor cost is the number of
upstream threads, not the number of identities. Prefer a single puller thread that cycles zones,
sending a short `wait` (the client chooses it and the server caps it; dispatch.py:320-321), over one
thread per zone. Demand persists in the map between requests (`_register`, dispatch.py:259-269).

## 2. Upstream outage today (probe-confirmed)

- **Connect while down:** `ConnectionRefusedError`. The dial has **no timeout**:
  `raw.setblocking(True); raw.connect(address)` (queue.py:268-271), and the stock cleartext
  `SocketClient` is the same. Against a blackholed address, the call waits out the OS SYN timeout.
- **Server dies:** the next proxy call raises `EOFError` (`SecureConnection._recv`, queue.py:106-118,
  or the stock `Connection`). Later calls on the same thread raise `EOFError`/`BrokenPipeError`, and a
  reset raises `ConnectionResetError`. A peer that dies mid-message gives
  `OSError('got end of file during message')`. Wrong key: `AuthenticationError`. TLS: `ssl.SSLError`
  (a subclass of `OSError`), `ssl.SSLCertVerificationError`, and `TLSError` on a fingerprint mismatch
  (tls.py:194-211; subclass of `Exception`, not `OSError`). The protocol check in `connect`
  (queue.py:1010-1031) maps **any** `RemoteError` to `QueueVersionError`.
- **Half-open or stalled peer: a call blocks forever.** The probe SIGSTOPped the server, and the
  `qsize()` call stayed blocked past 6 s until SIGCONT. Nothing sets a socket timeout or keepalive
  anywhere in `src/`. `put(timeout=2)` and `request` `wait` bound only server-side waits. TCP
  retransmission (~15 min on Linux) or nothing ends the hang.
- **Rebuild in-process is broken by the per-thread connection cache.** After a server restart on the
  same port, a **new** manager plus new proxies still fail with `BrokenPipeError` on any thread that
  had cached a connection to that address. A fresh thread works. Popping
  `BaseProxy._address_to_local[address]` (under `BaseProxy._mutex`) before reconnecting fixes it
  (probe). `_finalize_in_process` (queue.py:730) already does this, but only for in-process
  *servers*. **Test trap:** with the upstream server in the same process, its shutdown clears the
  client cache too and masks the bug. Dial through a TCP forwarder, or use a subprocess.
- **A Client factory applies only to new connections.** A cached connection made earlier on that
  thread is reused, whatever serializer key the new manager uses (probe).
- **After a receive timeout the connection is desynchronized.** The late reply is still in the
  buffer, so the next call would read it. A timed-out connection must be discarded, never reused.
- **Decref hazard.** Proxy finalizers (`_decref`, queue.py:641) dial the old address. In the relay
  process, which serves locally, many decrefs go to the single decref sender (`_send_decrefs`, :686).
  An unbounded dial during a partition stalls that thread for local decrefs as well.

## 3. DQ2 design

**Component: `UpstreamLink`** in `core/reconnect.py`, liftable by the resilience seed's R3/R8:
- Owns a factory → `QueueClient`, a state (UP/DOWN), an `epoch`, `last_ok` (monotonic), and a
  `Condition`.
- `call(fn)` runs `fn(client)` and classifies the failure. Transport errors (`EOFError`,
  `ConnectionError`, `TimeoutError`, `BlockingIOError`, non-verification `OSError`/`ssl.SSLError`)
  mark the link DOWN for that epoch and raise `LinkDown`. FSM states catch it and go to WAIT_LINK,
  which waits `link.wait_up(timeout=1)` and re-enters, so halt latency stays ≤1 s. Fatal errors
  (`QueueVersionError`, `AuthenticationError`, `TLSError`, `SSLCertVerificationError`) raise
  `LinkFatal`, and the relay exits non-zero with the client's error. Exiting is safe because the
  outbox is durable.
- **Reconnector:** full-jitter exponential backoff, `delay = uniform(0, min(cap, base·2ⁿ))`, with
  base 1 s and cap 60 s. It sleeps via `halt_event.wait(delay)`. Each attempt pops the address cache
  (both the dialed and the resolved address), builds a fresh `QueueClient`, connects, bumps `epoch`
  and notifies. The old client is kept in a graveyard until UP.
- **Logging:** one WARNING on the transition to DOWN. Then INFO on attempt numbers that are powers of
  two, and at least every 5 min ("unreachable 4m12s, 17 attempts"). One WARNING when the outage
  passes an `evict_hint` (default `server.evict`, 600 s): "buffered results will likely be refused as
  stale". One WARNING on reconnect, with the duration.
- Inject `clock`, `rng` and `sleep` for tests.
- **Bounded I/O (needs a small general change in `core/queue.py`):** a process-wide
  `set_dial_options(connect_timeout, io_timeout, keepalive)`, defaulting to off, modelled on
  `install_process_context`. `secure_client` would apply it (`settimeout` before connect and on the
  SSL socket), and so would a cleartext twin that sets `SO_KEEPALIVE`, `SO_RCVTIMEO`/`SO_SNDTIMEO`,
  and `TCP_USER_TIMEOUT` where available. The probe shows `SO_RCVTIMEO` turns a stall into
  `BlockingIOError` after the bound. Set `io_timeout` well above the request `wait` (e.g. 30 s vs 2 s).
  Only the relay enables it, so ordinary clients see no change. Its only dialer is the upstream link.

**While DOWN:**
- The puller stops pulling. The embedded server, the fleet and the local Receiver keep running, so
  completions land durably in the local DB.
- The returner and the heartbeat park in WAIT_LINK.
- The idle `client_timeout` clock is paused, so a long outage does not end the relay.

**On UP, in order:**
1. Heartbeat every identity immediately. Each takes the #29 re-registration branch
   (server.py:774-781).
2. Replay pending confirms, under the policy below.
3. Drain the outbox, oldest first, in bundles.
4. Resume pulling.

**Eviction is invisible to the relay.** The heartbeat put is one-way, and the reply has no eviction
flag. While evicted, rows confirmed to the relay are reverted (`revert_orphaned`, Z1 model.py:973-991)
and may be re-dispatched elsewhere. Their late results are STALE by design.

**Rule: never drop local tasks on inference.** Unconfirmed rows are not reverted by eviction:
`select_orphaned` filters `client_id ==`. If the relay dropped tasks upstream had *not* reverted, they
would stay in flight until the upstream server restarts. Running everything and accepting STALE is
correct, and the waste is bounded by R8.

**One reliable signal: an upstream restart.** Every task record carries the upstream `server_id`
(stamped in `Task.next`, Z1 model.py:696). A restarted server calls `revert_interrupted` at start
(Z1 server.py:203-206). So when a received bundle names a new `server_id`, every unreturned local row
with the old one is already reverted upstream. The relay may cancel those rows if unstarted and
discard their results, which would be STALE. The relay can't see this until it receives a bundle.
Optionally, an additive `server` field in the reply would make it immediate: a general change, with
no `PROTOCOL` bump because the client ignores extra keys. A restart also re-dispatches those ids,
possibly **to the same relay**, so the local insert (R3) must upsert by id and refresh the stored
upstream stamps. Flag this for the insert design.

**Confirm-replay policy.** `confirm_all` is unconditional by id (Z1 model.py:831-834). A replayed
confirm after a revert and re-dispatch can stamp the relay onto a row that another site is running,
and that row then escapes the other site's eviction. Replay only if `server_id` is unchanged and the
outage is shorter than `evict_hint`. Otherwise drop the confirm and log it. Results still land,
because the fence is `schedule_time`, not `client_id`. A complete fix is a confirm conditioned on
`schedule_time` (optional stamps in `ClientInfo`). Consider it alongside R19.

**Duration vs `evict_after`:**
- An outage shorter than `evict_after` is seamless: results land.
- A longer one gets results back STALE, after the #29 re-registration.
- An upstream *restart* makes every pre-restart result STALE, whatever the duration. R9's "hands the
  results over" yields landed results only when the upstream *process* survived the outage.

## 4. R5 outbox and prune

Local row states: `inserted` → `confirmed` → `completed` (local Receiver) → `returned` → pruned.
1. The returner selects `completed` rows (bundlesize/bundlewait) and restores the upstream stamps.
2. It runs `link.call(completed.put)`.
3. Only after the put returns does it mark `returned` in one commit.

A crash or ambiguous failure between steps 2 and 3 re-sends the bundle, which comes back DUPLICATE
(harmless). **A successful `put` is a sufficient hand-over point.** After it, the bundle sits in the
upstream memory queue. The Receiver holds DEFERRED results and re-presents them itself (Z1
server.py:529-560). Their only loss mode is an upstream crash, and there `revert_interrupted`
re-runs the tasks, while a re-send from the relay would be STALE anyway. So keeping `returned` rows
for re-presentation buys nothing.

R5's DEFERRED clause is met because the relay records *handed over*, never *landed*. TECH should
state this reading. Prune `returned` rows past a short retention (minutes, for diagnostics) in
batched deletes inside the returner loop. That keeps the DB size-stable, and a restart re-presents
exactly the unreturned rows.

## 5. Deterministic tests

- **FSM units:** step actions one at a time against a `FakeUpstream` that raises scripted errors on
  the Nth call (pattern from `tests/test_client_request.py`). Assert WAIT_LINK, re-entry and halt.
- **`UpstreamLink`:** use an injected clock, rng and sleep. Assert the backoff sequence, error
  classification, logging cadence and `epoch` bump with no real sleeps.
- **Integration:** run an in-process `QueueServer` on `draw_port()`. Stopping it cuts live
  connections (`_InProcessServer.stop` → `_cut`, queue.py:448-461, 569-586), and `SO_REUSEADDR` allows a restart on the same port. Put a
  **test TCP forwarder** in front of it with three modes: pass, close (EOF) and blackhole (stall).
  This reproduces the stale-cache bug and the `io_timeout` path (io_timeout = 0.5 s).
- **Synchronization:** wait on `link.wait_state(DOWN|UP, timeout)` and on deadline-bounded polls of
  local-DB counts.
- **Eviction:** `evict_after ≥ 10` s is a floor, so call `HeartMonitor.check_clients` with a moved
  `last_check`, or call `Task.revert_orphaned` directly. Then assert `apply_results` outcomes are
  STALE.
- **Restart durability:** stop the relay components without draining, start new ones on the same
  SQLite file, and assert the outbox is re-sent (APPLIED or DUPLICATE).

## Surprises worth carrying into PLAN/TECH

- `HeartMonitor` evicts on `last_check - hb.time` (server.py:813), and `hb.time` is the **sender's
  clock**. Cross-site skew beyond `evict_after` causes a false eviction every check, with a revert
  each time. Stamping receipt time server-side is a small, general fix.
- The #29 re-registration (server.py:778-779) does not refresh `server_id`/`server_host`. After an
  upstream restart a surviving relay, and its roster, keep the old `server_id`, so `count_connected()`
  (Z1 model.py:1175-1180) misses them. That breaks R6/R7 coherence.
- A dropped reply to `request` after the cut loses up to `bundlemax` rows in flight until the
  upstream restarts (the documented cut-to-confirm window). Outages make it likelier.

## Recommendations

- New thin FSMs (puller, returner, heartbeat) in `relay.py`; don't subclass the client FSMs.
- Factor `request_text` into a function taking the client id; reuse `ClientInfo`, `Heartbeat.new(uuid=)`, `serialize_tasks`.
- One puller thread cycling zones with a short `wait`; descriptors scale with threads, not identities.
- `core/reconnect.py::UpstreamLink`: transport errors vs fatal, full-jitter backoff (1 s→60 s cap), halt-responsive waits, `epoch`, injected clock.
- Pop `BaseProxy._address_to_local[addr]` and build a fresh `QueueClient` on every reconnect.
- Add opt-in `set_dial_options` (connect/io timeout, keepalive, `TCP_USER_TIMEOUT`) to `core/queue.py`; discard a connection after any timeout.
- While DOWN: keep the site running, stop pulling, pause the idle timeout. On UP: heartbeat → confirm replay (policy) → drain outbox → pull.
- Never drop local tasks on inferred eviction; treat a `server_id` change as a proven revert.
- Insert upserts by id and refreshes the upstream stamps (re-dispatch to the same relay).
- Outbox `completed→returned` marked after `put` returns; prune `returned` after a short retention; TECH records the DEFERRED reading.
- Flag to plan: sender-clock eviction, re-registration keeping a stale `server_id`, unconditional `confirm_all` vs replay.
- Test through a pass/close/blackhole TCP forwarder; never sleep to synchronize.
