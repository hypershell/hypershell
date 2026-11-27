# Research 02: Thread model, GIL, deadlock, signals and R7 test seams

Base: `b819de6`. CPython references are to 3.14.6 `multiprocessing/managers.py` (`mp/managers.py`).
Experiments were run on macOS with the project venv (3.14, `sys.getswitchinterval()` = 0.005 s).
TLS context install, log-slot locks and SIGINT in the old child are covered in `03-bootstrap-duties.md`.

## 1. Queue operations

Defaults matter here: `queuesize` = 1 (`core/config.py` L133), so `scheduled` and `completed` each hold
**one** bundle.

| Site | Queue · op | Timeout | Thread |
|---|---|---|---|
| `server.py` L254 `post_bundle` | scheduled · put | 2 s → `POST` | hypershell-scheduler |
| `server.py` L345–346 `unload_info` | confirmed · get + task_done | 2 s → `UNLOAD` | hypershell-confirm |
| `server.py` L443–444 `unload_bundle` | completed · get + task_done | 2 s → `UNLOAD` | hypershell-receiver |
| `server.py` L570–571 `get_next` | heartbeat · get + task_done | 2 s → `SWITCH`→`NEXT` | hypershell-heartmonitor |
| `server.py` L644 `signal_clients` | scheduled · put sentinel ×`len(beats)` | **none** | hypershell-heartmonitor |
| `server.py` L898 `wait_receiver`, L916 `stop` | completed · put sentinel | **none** | hypershell-server / stop caller |
| `server.py` L905 `wait_confirm`, L919 `stop` | confirmed · put sentinel | **none** (unbounded: never blocks) | same |
| `client.py` L212–213 / L241 | scheduled get+task_done / confirmed put (proxy) | 2 s | client scheduler |
| `client.py` L389 / L1014 | completed put / heartbeat put (proxy) | 2 s | client collector / client heartbeat |
| `submit.py` L744 `QueueCommitter.commit` | scheduled put (proxy) | 2 s; `finalize` L754 loops until it lands | committer (`hs submit` queue mode, and `LiveSubmitThread` L783 inside an in-memory server) |

Nobody calls `join()` on a managed queue; `task_done` always follows a successful `get`.

**Untimed ops, base vs `queue.Queue`.** Blocking semantics are identical. `JoinableQueue.put` takes the
cross-process `_sem` (bounded count of items not yet read by a consumer) and blocks indefinitely without
a timeout. `queue.Queue.put` waits on `not_full` the same way. Both raise `Full`/`Empty` on expiry, and
both raise `ValueError` on a surplus `task_done`. Differences:

- `JoinableQueue.put` returns before the item reaches the pipe (feeder thread), while `queue.Queue.put`
  makes the item visible at once. `qsize()` works on macOS for `queue.Queue`.
- AutoProxy exposes `public_methods(referent)`. For JoinableQueue that is `get put get_nowait put_nowait
  task_done join qsize empty full close join_thread cancel_join_thread`; for `queue.Queue` on 3.13+ it
  is the same minus the last three, **plus `shutdown`**. A queue shut down that way raises `ShutDown`,
  not `Empty`, in every FSM. Recommend registering with an explicit `exposed=` of the common nine so the
  method surface does not change.
- **Pre-existing hang, unchanged:** L644 posts one untimed sentinel per registered client into a
  1-slot queue. If two or more registered clients are dead but not yet evicted, the extra puts block
  forever. HeartMonitor then never evicts, `wait_heartbeat` (L888) never returns, and the server hangs.
  Likewise L898 blocks forever if the Receiver died with `completed` full. This is out of scope (R8), but
  R7 tests must size the queue or the beats so they never trip it.

## 2. Thread inventory (server process)

`with self.queue:` is entered in **hypershell-server**, which is `ServerThread.run_with_exceptions`
(L848). So `QueueServer.start` and `shutdown` run on that thread. The caller (main thread, or
hypershell-cluster in `hsx`) polls `queue.ready` in `ServerThread.start` (L857–862).

**Before:** MainThread, hypershell-server, submit (+ loader + committer), scheduler (DB mode only),
confirm (unless `--no-confirm`), heartmonitor, receiver, the log-compression thread
(`core/logging.py` L544). There are also up to **3** mp feeder threads, started lazily by the first put:
`scheduled` at the first post, `completed` and `confirmed` at the shutdown sentinels. The parent never
puts on `heartbeat`. The child holds the accepter, the per-connection handlers and up to 4 feeders.

**After:** the same minus all feeders, plus 1 accepter (serve loop) plus handler threads, each a daemon
running stock `Server.handle_request`. A prototype of an in-process `Server` measured 20 accepts per
`hs client` lifetime:

- **Long-lived, 3 per `hs client`.** A proxy connection is per *(client thread, server address)*,
  shared by all four proxies (`BaseProxy._address_to_local`, mp/managers.py L766–777). That gives one
  connection for the client scheduler (scheduled get/task_done + confirmed put), one for the collector,
  and one for the heartbeat. Only the scheduler's connection sits in `get(timeout)`. The other two sit
  in `recv()` with the GIL released.
- **`hs submit` queue mode and in-memory `LiveSubmitThread`:** 1 long-lived connection (the committer).
  `LiveSubmitThread` connects back into its own process.
- **Short-lived, 13 at connect and 4 at teardown.** Connect costs `dummy` plus, for each queue,
  `create`, `incref` and `decref` (L530–537, L605–616, L728–736, L858–877). Teardown is one finalizer
  `decref` per proxy.
- **`hsx` local mode:** server and client share one process (`cluster/local.py` L255–260), so its 3
  handler threads land beside the client's own threads. Remote, ssh and autoscale clients are `Popen`
  children, so no client runs in the server process.

At 1,000 clients that is about 3,000 handler threads plus a burst of ~13,000 short connections at
startup. The total is the same as base; it has only moved from the child into the server process.

## 3. GIL and starvation

**Moves into the server GIL:** for every client RPC, request unpickle (`conn.recv`), response pickle,
`SecureConnection` framing (`core/queue.py` L87–125: `BytesIO`, `bytes()` copies, `struct`), the HMAC
challenge on each new connection, the `Thread.start` per connection, and the Python side of
`wrap_socket` in `accept`.

**Leaves it:** feeder pickling and the pipe write for every server-side put. Server threads now pass
object references.

**Calls that release the GIL:** `Condition.wait` and `lock.acquire(timeout)` inside `queue.Queue`,
socket `recv`/`sendall`/`accept`, `select` (`_poll`), and OpenSSL read/write/handshake. sqlite3 releases
it during statement steps; SQLAlchemy's ORM work does not.

**Measured.** An in-process `Server` with N remote threads looping on `scheduled.get(timeout=2)` on an
empty queue (the idle-client pattern: about 0.5 RPC/s each) ran alongside a pure-Python CPU thread and
a 10 ms sleep probe:

| idle getters | CPU-thread rate | probe overshoot p99 |
|---|---|---|
| 0 | 133/s | 13–16 ms |
| 250 | 133/s | 25 ms |
| 1,000 | 115/s (about −14%) | 45 ms |

Process CPU did not rise; the cost is a share of the GIL. Heartbeats add about 100 RPC/s at 1,000
clients (`heartrate` 10 s); data RPCs scale with the task rate.

**Starvation risks:**

- Receiver and Scheduler: ORM work is preempted every 5 ms when handlers are runnable. Expect a
  throughput tax of roughly 10–15% at 1,000 idle clients, not a stall. R9's single-host benchmark has
  few clients and will not see it; the pre-release multi-node run should.
- HeartMonitor: eviction granularity is `evict_after/10` (60 s by default), so millisecond GIL delays
  are irrelevant. The real risk is backlog. Ages are measured against `hb.time`, the client's stamp
  (`check_clients` L624). If the monitor drains `heartbeat` (unbounded) slower than about 100/s, the
  processed beats are stale and false evictions follow. This mechanism exists today; the monitor now
  shares its GIL with the handlers.
- If R9 regresses, there are two cheap levers. One is `sys.setswitchinterval` (process-wide; not
  recommended blind). The other is `threading.stack_size`, which matters for RSS and virtual memory at
  3,000 threads, not for the GIL.

## 4. Deadlock analysis

- **`queue.Queue.mutex` is a leaf.** Its three Conditions share it, and `_put`/`_get` call nothing
  external.
- **`Server.mutex`** is held only in `create`/`incref`/`decref`/`debug_info` (L341–470). `create` calls
  the registered callable under it, so the `_get_*` callables must stay lock-free and must not log.
  Since `QueueServer` keeps a strong reference to each queue, dropping a refcount to 0 in `decref`
  frees nothing, and a later `create` re-adds the same id.
- **Logging locks and the SQLAlchemy scoped session** are never touched by handler threads (they log
  only via the unconfigured `multiprocessing.util`). **`executor_lock`** is client-only and never held
  across a managed-queue op. No cycle exists among these locks.
- **Handlers at shutdown:**
  - `serve_client` checks `stop_event` only *between* requests (L251). A handler sits in `recv()` until
    its client closes, and in `get` for at most the timeout the client chose.
  - Handlers are daemon threads, so they never block exit. For R5 they keep accepted sockets and queue
    references alive. Port rebind still works because the listener sets `SO_REUSEADDR`.
  - The serve loop must create `stop_event` itself. `serve_client` reads it, but only `serve_forever`
    creates it (L172).
  - Do not set `process.current_process()._manager_server`; it is process-global.
  - The remote `shutdown` request (L364) only sets `stop_event`; decide that it never ends the server
    process.
  - To close live connections at shutdown, track them and call `shutdown(SHUT_RDWR)` on the socket.
    A cross-thread `close()` does not reliably wake `recv`.
- **Put on a full `completed` after the Receiver exits.** The client's 2 s timeout returns `Full`, and
  the collector retries until it is stopped. That is a livelock-free retry identical to base. The
  ordering prevents it in normal runs: the Receiver sentinel is posted only after the heartmonitor has
  seen every client FINISHED.
- **Barging.** `not_full.notify` wakes one waiter, and a fresh putter can take the slot first. The
  untimed server sentinel at L898 can lose a few rounds to client puts, but clients have finished by
  then.
- **Serialised accepts (unchanged).** `SecureListener.accept` completes the TLS handshake on the
  accept thread (`core/queue.py` L180–189), so accepts are handled one at a time, as on base. The backlog is 128
  (mp/managers.py L160). During the prototype's simultaneous 1,000-thread connect, macOS reset
  connections even without TLS. A staggered start was clean. The handshake is mostly GIL-released.
- **Serve-loop exit (R5).** On macOS, `close()` of the listener wakes a blocked `accept()` (measured).
  On Linux it is known not to: the in-flight `accept` keeps the socket in LISTEN, so the rebind fails.
  A self-connect to wake it would hang in the TLS handshake. **Recommend** that the loop `select()`
  the listening fd with a short timeout (≤0.5 s), check a stop event, and close the listener on its own
  thread. `shutdown()` then sets the event and `join`s the loop.

## 5. Signals

Python handlers run only in the main thread. `register_handlers()` runs in `main()`
(`__init__.py` L140) before any app code, and therefore before the manager is started.

- **Main thread.** In `hs server` it is blocked in `thread.join()` (`server.py` L1033 / L1224). In
  `hsx` it is in `run_local`'s `join` (`cluster/local.py` L47). Lock waits are signal-interruptible on
  POSIX, so the handler runs promptly. EINTR in other threads (accept, recv, select, lock waits) is
  retried under PEP 475.
- **Old child.** Under **fork** (Linux ≤3.13) it inherits HyperShell's handler, so a USR1/USR2/HUP sets
  the child's own `RECEIVED`, which nothing polls: a no-op. Under **spawn** (the macOS default) and
  **forkserver** (the Linux default on 3.14+) the child has default dispositions. Measured under spawn:
  `SIGUSR1` killed the manager child (exit −30). Forkserver is inferred from its handler reset, not
  measured here because of sandbox limits.
- **Consequence today.** A signal sent to the whole process group, as batch systems do, kills the queue
  manager. The cooperative drain then cannot deliver sentinels, and clients run on until eviction.
- **After the change.** There is no child, and the drain works on every start method. This is a strict
  improvement; the plan should record it as an intended difference under R8.
- **Serve-loop thread design.** It must **not** poll `check_signal()`: the cooperative drain needs it
  serving until `shutdown()`. Sticky `RECEIVED` and the vestigial `Thread.__should_halt` are therefore
  irrelevant to it. It must not be an FSM, because `accept` has no finite timeout to re-enter on.
  Recommended shape: a plain daemon `threading.Thread`, with its own stop `Event`, its own
  `select`-gated accept loop, and a broad per-accept `except` (see 03, option A). If it subclasses
  `core.thread.Thread` to get `join()` re-raise, it must override `stop()` to set the event. Note that
  `Thread.run` swallows the exception until `join`, so a dead loop would go unnoticed while clients
  hang. Log loudly instead.

## 6. R7 test seams (validated by a scratch script)

- **Stand-in queues.** The FSMs only touch `self.queue.<name>`, so
  `SimpleNamespace(scheduled=Queue(1), completed=Queue(1), heartbeat=Queue(), confirmed=Queue())` works
  without sockets. Add one integration test per FSM through a started `QueueServer` and a `QueueClient`
  for R3.
- **Short timeouts.** `server.py` imports `DEFAULT_REMOTE_TIMEOUT` by name and reads it at call time,
  so `monkeypatch.setattr(hypershell.server, 'DEFAULT_REMOTE_TIMEOUT', 0.05)` works. `Final` is not
  enforced. An Empty round trip measured 0.06 s.
- **No DB needed.** Receiver, Confirm and HeartMonitor take `in_memory=True`. For the Scheduler,
  `post_bundle` needs only `tasks`/`bundle`, and `load_bundle` checks the signal before any query
  (L208). Full `load_bundle` runs need the `task_db` fixture from `tests/test_retry_gate.py`
  (L35–46; consider moving it to `conftest.py`) plus `temp_site`.
- **Sketches** (one step = call the action method and assert the returned state):
  - *Receiver:* empty → `unload_bundle()` is `UNLOAD`. Put `serialize_tasks([Task.new(args='echo')])` →
    `UNPACK`, `UPDATE`, `UNLOAD`. Put `make_sentinel()` → `unpack_bundle()` is `FINAL`. Thread form:
    start `ReceiverThread`, put the sentinel, `join(2)`, assert not alive.
  - *Confirm:* empty → `UNLOAD`. Put `ClientInfo.from_tasks(...).pack()` → `UNPACK`, `UPDATE`. Sentinel
    → `FINAL`.
  - *HeartMonitor* (`evict_after=10`): empty → `SWITCH`, then `switch_mode()` is `NEXT`. Put
    `Heartbeat.new().pack()` → `UPDATE`, `SWITCH`, one beat registered. Set `should_signal` →
    `SIGNAL`, and `scheduled` holds one sentinel. Sentinel on `heartbeat` → `FINAL`. Keep
    `len(beats)` ≤ free slots (see §1).
  - *Scheduler:* `post_bundle()` on an empty queue → `LOAD`; on a full one → `POST` after the timeout,
    and the bundle is gettable. Halt: `monkeypatch.setattr(hypershell.core.signal, 'RECEIVED',
    SIGUSR1)` → `load_bundle()` is `FINAL`. Always restore via monkeypatch, since the flag is sticky.
    The Scheduler consumes no sentinel, so its "halt" is the signal path or `halt()`.
