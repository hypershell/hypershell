# Research 01 — The request callable (R2–R5, R14)

Codebase-first; line numbers are at `b54d685` unless noted. CPython checks were run on 3.12.13,
3.13.16 and 3.14.6 (no local 3.11; Z1's gate ran the pinned internals on 3.11.18).

## 1. Shape and registration

**Register beside the queues.** `QueueServer.start` builds a per-instance registry
(`core/queue.py:784-785`). Add one entry: `'_get_dispatch': (_provider(self.dispatcher),
('request',), None, AutoProxy)`, with a per-server `Dispatcher` object built before
`_serve_in_process` (`:788`). `_provider` stays lock-free, which matters because `Server.create`
runs the registry callable under `self.mutex` (stdlib `managers.py` `Server.create`, 3.14 l.377-417).

**Where the call runs.** `Server.serve_client` (3.14 l.242-310) looks up the referent, calls
`function(*args, **kwds)` on the handler thread of the connection that sent the request, and
`send`s the reply on that same `conn`. It does not take `self.mutex`, so a long-poll in `request`
holds back no other connection. Each connection has its own handler thread
(`_InProcessServer.serve`, `core/queue.py:467-468`), so the reply reaches only the client that made
the request (R4). `_handle_request`/`serve_client`/`create`/`_callmethod` are identical on 3.12,
3.13 and 3.14, apart from a `del result` in `_callmethod` on 3.13+.

**Connections per client.** `BaseProxy._callmethod` reuses `self._tls.connection`, a
`ForkAwareLocal` keyed by server address (`managers.py` l.766-772, 810-836). So a client process
holds **one persistent connection per thread that makes proxy calls**, shared by all its proxies.
A scratch prototype confirmed this: three threads plus the main thread each called `request` and
`put` on two referents. Each thread got its own handler thread (named `MainProcess|tN` by
`accept_connection`). The main thread's two calls shared one connection. A thread's connection
closes when the thread ends.

| | today (Z1) | Z2, `_get_dispatch` replacing `_get_scheduled` |
|---|---|---|
| Persistent connections (= server handler threads) | 3: scheduler thread (`scheduled.get`/`task_done`, `confirmed.put`, `client.py:212-213, 241`), collector (`completed.put`, `:389`), heartbeat (`heartbeat.put`, `:1014`) | **3**, if `request` is called from the scheduler thread |
| Short-lived at connect, one at a time | 1 handshake + 4 × (create, incref, decref from `temp`) = 13 | 13 (or 16 if `_get_scheduled` were also kept) |

The count stays at 3, so C3's "(soft limit − 32 − own files) / 3 clients" sizing
(`spec/zone-queue-primitive/TECH.md:273-276`) still holds. That depends on `request` running on
the scheduler thread; a dedicated request thread would make the count 4. C5 is unaffected: wrap
the new proxy with `_defer_decref` like the others (`core/queue.py:837-840`). A long-poll holds
its handler thread, not the accept loop, so the decref sender's fresh connections still get
accepted.

**Thread safety and FCFS.** Concurrent requests run on different handler threads, so the
dispatcher needs its own lock. `threading.Lock` is not FIFO, so R4's first-come-first-served
needs an explicit waiter FIFO: per zone, a deque of tickets under one `Condition`. A request
waits only until it is at the head and its zone's pool is non-empty, or its deadline passes. The
cut happens under the lock, which guarantees no task goes to two clients (R4).

## 2. JSON-only contract (R3)

The RPC framing stays pickle (AGENTS.md § Queue transport), so R3 is about what our code
accepts and returns. **Recommend `request(text: str) -> str`, both JSON text:**
- It is JSON by construction: the server `json.loads` and checks the fields of a `str`, and the
  reply is built as JSON text, so no application object can reach the reply.
- It keeps today's shape: today's bundle is JSON bytes inside the pickle frame
  (`serialize_tasks`, `data/model.py:237-239`). Pickling one `str` is a memcpy; pickling a dict
  of N task dicts on the handler thread costs GIL time in the process that now also runs the
  scheduler (R1).
- Staging can pre-encode each task's JSON on the scheduler thread, so the cut on the handler
  thread is `'{"protocol":1,"tasks":[' + ','.join(chunks[:n]) + '],"shutdown":false}'`. The
  reply length is then known before sending, which bounds the frame along with the cap (R3).

`bytes` is ruled out because R3's list of allowed types has no `bytes`. A plain `dict` would
also meet R3, but it loses the three points above.

**Request:** `{"protocol": 1, "client": "<uuid>", "host": "<name>", "zone": "<name>" | null,
"size": <int> | null, "wait": <seconds>}`. The client sends the zone **name**: remote clients
have no database, so the server resolves names (R8 get-or-create, cached per name). `size: null`
means "use the server's `-b`" (R2). The server clamps `size` to `[1, cap]` and `wait` to
`[0, DEFAULT_REMOTE_TIMEOUT]`, caps the request text's length, and answers a malformed request
with an error reply rather than raising.

**Reply:** `{"protocol": 1, "tasks": [<Task.to_json()>…], "shutdown": <bool>}`. An empty reply is
`tasks: []`, with `shutdown: false` until the server is draining, and `shutdown: true` once the
pool is empty and the server is finishing. An error reply is
`{"protocol": 1, "error": "protocol" | "request", "message": "…", "server_version": "…"}`. The
client turns `tasks` into `Task.from_json` (mirroring `deserialize_tasks`, `data/model.py:242-245`).

## 3. Blocking discipline

A remote `get(timeout=2)` works today because the timeout runs **server-side**: `Queue.get`
blocks the handler thread for up to 2 s, raises `Empty`, `serve_client` sends `('#ERROR', e)`, and
the client re-raises `queue.Empty` (`managers.py` l.272-281). The proxy call itself has no
client-side timeout. **Recommend a bounded long-poll in the same way:** `request` waits up to
the clamped `wait` (default `DEFAULT_REMOTE_TIMEOUT`, 2 s), then returns empty. It never raises
for "nothing ready". The client re-enters `GET_REMOTE`, polling `check_signal()` and its
`client_timeout` exactly as `get_remote` does now (`client.py:206-222`).
- Handler occupancy matches today's (the same thread blocked in `scheduled.get` for up to 2 s).
- Returning empty immediately would turn every idle client into a stream of round trips. That
  costs GIL time in the server process and risks R1.
- Shutdown latency: `stop()` cuts sockets (`core/queue.py:507-522`), but that does not wake a
  thread waiting on a `Condition`. Give the dispatcher an idempotent `close()` that sets a draining
  flag and calls `notify_all`. Waiters then reply at once, with `shutdown: true` once their pool is
  empty. Call `close()` where `HeartMonitor.signal_clients` now puts sentinels (`server.py:640-647`),
  which runs after the scheduler is done (`server.py:844-852, 893-898`), and again from
  `QueueServer.__exit__`. Without `close()`, a waiter wakes at its deadline of 2 s or less, its send
  to the cut socket fails, and `serve_client` exits through `sys.exit`, which `handle_request`
  absorbs (`core/queue.py:488-498`).
- Improvement: a client that connects after shutdown begins gets `shutdown: true` at once, where
  today it waits for a sentinel that was sized to the clients already known.

## 4. Version handshake (R5)

**(a) Old client to new server.** Every old line (2.8.3, `b819de6`, `d996579` = 2.9.0a2) runs
the same `QueueClient.connect`: it registers four typeids, does the handshake, then calls
**`_get_scheduled()` first** (`git show d996579:src/hypershell/core/queue.py` l.427-438). If the
server's registry callable raises, `_handle_request` replies `('#TRACEBACK', format_exc())`
(`managers.py` l.200-228) and closes the connection. The old client then raises
`RemoteError(<server traceback>)` from `connect()`, inside `with self.client:`
(`d996579 client.py:1241`), before any thread starts or any heartbeat is sent.
`ClientThread.join()` re-raises it in `run_client`, and the shared mapping's `Exception →
write_traceback` logs one CRITICAL line ending in our message and exits `runtime_error`
(`d996579 core/exceptions.py:73-82`). The prototype showed the message arriving intact:
`TooOld: client too old: …`.

**Design:** register `_get_scheduled` with a refusal callable that raises
`ClientTooOld("this server requires the work-request protocol (HyperShell ≥ <version>)")`.
- Log "client too old" **outside the server mutex**: the callable sets a thread-local flag, and
  `_InProcessServer.handle_request` logs one WARNING with the peer's host after the connection
  closes. Each old process calls `_get_scheduled` once, so that is one line per client.
- This is better than an always-empty or always-sentinel queue. An empty queue leaves the old
  client waiting forever (fails R5). A sentinel queue lets it connect, heartbeat (writing a
  `Client` row) and then exit 0 silently.
- **Catch:** `_get_scheduled` has new-code remote users. `LiveSubmitThread`/`QueueCommitter`
  put bundles on the remote `scheduled` queue (`submit.py:744, 861`). They are used by
  `hs submit --queue` (`submit.py:1290, 1350, 1359`) and by the server's own in-memory mode,
  which connects to itself (`server.py:823-826`). So the live-submit path must move to a **new
  typeid** (e.g. `_get_submitted`) before `_get_scheduled` can be refused. An old
  `hs submit --queue` is refused the same way, which is consistent with the no-2.8-clients
  non-goal.
- Internally, the server still uses the `scheduled` object today in `Scheduler.post_bundle`
  (`server.py:254`) and `HeartMonitor.signal_clients` (`:644`). Both become staging and
  `dispatcher.close()` in Z2. In in-memory mode the dispatcher must serve from what live submit
  delivers, re-cut to each client's size. Refusing *remote* access needs none of these to go
  away.

**(b) New client to old server.** `_get_dispatch` is unknown there, so `Server.create` raises
`KeyError` under `_handle_request`, and the client gets `RemoteError` whose last line is
`KeyError: '_get_dispatch'` (the prototype showed this on all three versions). **Design:**
`QueueClient.connect` creates `_get_dispatch` **first**, catches any `RemoteError` from it (that
create can fail no other way), and raises `QueueVersionError(RuntimeError)`: "server at H:P is
older than this client (no work-request support; 2.8.x or 2.9.0a2?); run matching versions". The
shared mapping turns `RuntimeError` into one CRITICAL line and a non-zero exit
(`core/exceptions.py:133`). This is immediate, since nothing else is created. A new
`hs submit --queue` against an old server fails the same way on `_get_submitted`.

**Protocol integer: yes.** Every request carries `"protocol": 1`. On a mismatch the server
returns the error reply above (logged once per client UUID), and the client raises
`QueueVersionError` with both numbers. This makes later reply-schema changes explicit instead of
showing up as a `KeyError`.

## 5. CPython private surface to pin (`_INTERNALS` / `TestPrivateInternals`)

All of these hold on 3.12–3.14 (checked with a scratch script):
- `'mutex' not in co_names(Server.serve_client)`: an exposed method runs without the server
  mutex, so a long-poll never serialises clients or `create`/`incref`.
- `Server._handle_request` turns an exception into `'#TRACEBACK'` (`'format_exc'` in co_names,
  `'#TRACEBACK'` in co_consts): the old-client refusal reaches the client as text.
- `{'registry', 'mutex'} <= co_names(Server.create)`: the registry callable runs in the
  requesting handler thread (needed by the thread-local refusal flag).
- `'RemoteError' in co_names(managers.convert_to_error)`, and `managers.RemoteError` imports:
  new-client version detection.
- `{'_tls', 'connection', '_connect'} <= co_names(BaseProxy._callmethod)`: one connection per
  client thread, which the per-client descriptor count above depends on.

Back the pins with behavioural tests: an old-shaped client gets the refusal and exits; a new
client against a stock manager without `_get_dispatch` exits with `QueueVersionError`; and a
client holds 3 connections.

## Recommendation

- **Surface:** typeid `_get_dispatch`, exposed `('request',)`, `AutoProxy`, one `Dispatcher`
  per `QueueServer`, wrapped by `_defer_decref`. `request(text: str) -> str`.
  - Request JSON: `{protocol, client, host, zone|null, size|null, wait}`.
  - Reply JSON: `{protocol, tasks: [Task.to_json…], shutdown}` or
    `{protocol, error, message, server_version}`.
  - Called only from the client's scheduler thread, which keeps 3 connections per client.
- **Blocking:** a server-side long-poll of at most `DEFAULT_REMOTE_TIMEOUT`, with FIFO waiters
  per zone, returning a partial bundle as soon as any task is ready, empty at the deadline, and
  `shutdown: true` once drained after `close()`. It never raises for "empty". The client re-enters
  `GET_REMOTE` and polls signals.
- **Handshake:**
  - The new client creates `_get_dispatch` first; a `RemoteError` there becomes
    `QueueVersionError`, a non-zero exit.
  - The server refuses `_get_scheduled` with `ClientTooOld` and logs it once per connection,
    outside the mutex.
  - Live submit moves to `_get_submitted`.
  - `protocol: 1` goes in every request.
- **Risks:**
  - Moving live submit to `_get_submitted` changes the in-memory path and `hs submit --queue`,
    and needs tests.
  - A reply whose send fails (the client died mid-poll) leaves its tasks scheduled but never
    confirmed, as today's get-then-crash window does. The plan may want to record each cut
    against the requesting client id so eviction can revert it.
  - FIFO fairness needs its own test. A plain lock reorders waiters.
  - Large replies are bounded only by the cap. Pick the cap with today's largest bundle in mind
    (R3).
  - `test_queue.py` uses `client.scheduled` remotely (l.182-342) and needs rework.
  - 3.11 was not re-checked locally.
