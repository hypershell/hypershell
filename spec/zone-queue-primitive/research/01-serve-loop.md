# Research 01 — An in-process serve loop around `multiprocessing.managers.Server`

Base `b819de6`. CPython lines are 3.14.6 `multiprocessing/managers.py` (`mgr`) and `connection.py`
(`conn`). TLS install and log-slot locks are in [03](03-bootstrap-duties.md), not repeated here.
Prototype: `$TMPDIR/zqp-proto/proto3.py` (loop + checks), `sm_check.py`, `stock_accepter.py`,
`proto.py`/`base_check.py`/`foot.py` (base comparisons). Run with the project venv (3.14.6) and
throwaway 3.12.13 / 3.13.16 venvs on macOS. All numbers below are measured.

## 1. CPython facts, and version drift

- **No relevant drift, 3.11–3.14.** `Server` and `BaseManager` are byte-identical on 3.12, 3.13 and
  3.14 (3.12 is 4 lines above 3.14, 3.13 is 1 line above). 3.11 differs only by a dead assignment in
  `Server.incref`. `BaseProxy` differs: 3.11 tracks ids (`tls_idset`), 3.12+ tracks per-proxy
  serials (`_next_serial`, `_decref(token, serial, …)`), and 3.13+ adds `del result` in
  `_callmethod`. None of that touches the loop. Listener backlog is `128` in every version
  (`mgr` L160). `conn` changes 3.12→3.13 are Windows named-pipe only.
- `Server.__init__` (L150–166) binds and listens at construction through
  `listener_client[serializer][0]`, so `SecureListener` vs stock `Listener` is chosen by the
  serializer alone. `address` is the listener's `getsockname()`.
- `serve_forever` (L168–188) is the only place that sets `self.stop_event` (L172) and
  `current_process()._manager_server` (L173). It ends by resetting stdout/stderr (L184–187) and
  `sys.exit(0)` (L188). `accepter` (L190–198) loops `accept()`, `continue`s on `OSError`, and starts a
  daemon thread per connection.
- `handle_request` (L230–240) swallows the `SystemExit` that `serve_client` raises inside handler
  threads: `sys.exit(0)` on `EOFError` (L305) and `sys.exit(1)` after a failed send (L321).
  `serve_client`'s loop is `while not self.stop_event.is_set()` (L253), checked **between** requests.
- Remote `shutdown` RPC (L364–375) replies and sets `stop_event`. `create` (L377–425) calls the
  registry callable under `self.mutex`, takes `exposed = public_methods(obj)` (L133–137) when the
  registry gives `None`, and keys objects by `'%x' % id(obj)`.
- `BaseManager._registry` is class-level (L499). `register` is a classmethod that copies the
  registry once per class (L708) and, with `create_method=True`, does `setattr(cls, typeid, temp)`
  (L739). **On base this replaces `QueueServer._get_scheduled` etc. with proxy-creating methods at
  class level.** Measured: a second `QueueServer` started in the same process under `fork` fails
  with `RemoteError: AssertionError: server not yet started` (it works under `spawn` only because
  pickling the bound method re-resolves it by name in the child). `get_server()` (L515–528) passes
  `self._registry` by reference.
- `start` (L539–580) sets `self.shutdown = util.Finalize(...)` (L575) only there.
  `_finalize_manager` (L663–694) RPCs `shutdown`, joins, terminates, kills, sets `SHUTDOWN`, and
  pops `BaseProxy._address_to_local[address]` (L693), where `address` is the **listener's**
  address.
- `BaseProxy._address_to_local` (L759) is keyed by `token.address`. For a `QueueClient` that is
  the **client's dial address** (`BaseManager._create` builds `Token(typeid, self._address, id)`, L615).
  Its value is a thread-local connection plus a serial set (L766–773), and `_callmethod` reuses
  `self._tls.connection` (L819–828). `RebuildProxy` (L944–960) reads `_manager_server` only when a
  proxy is **unpickled** in the serving process.

## 2. Design

**Construct.** In `QueueServer.start`, create the four `queue.Queue`s and a **per-instance
registry dict**. Do not call `self.register`, so there is no class mutation:
`{'_get_scheduled': (lambda q=self.scheduled: q, EXPOSED, None, AutoProxy), …}`. Then build
`_InProcessServer(registry, self._address, self._authkey, self._serializer)`. Two instances in
one process then serve their own queues (measured). `EXPOSED` should be pinned to the methods
that `JoinableQueue` and `queue.Queue` share (§3).

**Attributes.** Set `stop_event = threading.Event()` and **never set it**. `serve_client` needs
the attribute (L253). Setting it to stop (the variant tested in `proto_v1.py`) breaks
request/reply pairing. The handler leaves `serve_client` after one more reply, then
`_handle_request` sends the stale `('#RETURN', None)` for `accept_connection`. Measured result: a
blocked `get(timeout=2)` returned `Empty` after the full 2.01 s, and the client's **next call
returned `None`**. Do **not** set `_manager_server`. It is process-global, two servers would
fight over it, and HyperShell never unpickles a proxy in the server (the payloads are bytes).

**Accept loop: select with a timeout.** CPython's `accepter` cannot be stopped.
`stock_accepter.py` closed the listener under a running `accepter`. With the stock `Listener`,
`accept()` raises `OSError('listener is closed')` (`conn` L511), and the thread spins at 100% of a
core forever. With `SecureListener` (`_socket = None`), it raises `AttributeError` and the thread
dies with a traceback. Closing a socket is also not a portable way to wake a blocked `accept()`.
The loop therefore uses `selectors.DefaultSelector` on the listening socket with `POLL = 0.25`
and catches **any** exception per accept, so `SSLError`, a closed listener and `TLSError` all
mean "drop this peer". It never relies on close to wake it, and stop never waits on it.
`accept()` can block (it runs the TLS handshake), so stop never joins the accept thread. The
socket to watch is `listener._socket` (`SecureListener`) or `listener._listener._socket` (stock
`Listener` → `SocketListener`). The second is a private attribute, but it is present on 3.11–3.14.
A cleaner option is a `fileno()` method on `SecureListener`; the stock listener still needs the
private path.

**Shutdown.** `shutdown` keeps BaseManager's shape: a `util.Finalize(self, _finalize,
exitpriority=0)` set in `start`. It is idempotent, it also runs at exit, and `__exit__` keeps
working. `_finalize`:

1. `server.stop()`. Under the lock: set the halt flag, `listener.close()` (port released
   immediately), and `shutdown(SHUT_RDWR)` each live connection. Do this through a
   `socket.fromfd` dup, since `Connection` objects have no socket.
2. `state.value = State.SHUTDOWN`. Set `_process = None` in `start` so `join()` is a no-op.
3. Pop `BaseProxy._address_to_local` for **both** the requested `(host, port)` and
   `server.address`.

Handler threads are never joined. A handler blocked in `queue.get(timeout)` finds its socket dead,
fails its send, and exits through `sys.exit(1)` → `SystemExit`, which is swallowed. The sweep
happens under the same lock that `handle_request` takes before it closes a connection, so a
listed fd cannot have been reused. Override `Server.shutdown(c)` (the remote RPC) to reply and
call `stop()` in a thread, never exiting.

**Why pop both addresses (new finding; base has the same gap).** `LocalCluster` uses
`('localhost', port)` on both sides, but the listener reports `('127.0.0.1', port)`, so the stock
pop misses the client's cache key. Measured (3.12, 3.13, 3.14): with only `server.address`
popped, a same-thread client of a second server on the same port gets `EOFError`. With both
popped, it gets `b'b'`.

**Should open connections be cut at shutdown? Yes, for parity.** On base, the child's exit closes
them. Measured on base: a client blocked in `get(timeout=2)` sees `EOFError` the moment
`shutdown()` runs, with shutdown taking 2 ms under fork and 114 ms under spawn. The prototype
matches that: `EOFError` at shutdown, and the next call raises
`EOFError`/`ConnectionResetError`/`SSLEOFError`. If connections are not cut, old clients keep
using the dead server's queues.

**Where it lives.** Put `_InProcessServer` and a `SecureManager._serve_in_process(registry)`
helper in `core/queue.py`. The helper does the TLS install from 03 §1, the bind and the
thread/Finalize wiring. Call it only from `QueueServer.start`. Leave `SecureManager.start`
(forking, `_child_bootstrap`) unchanged. That keeps `SecureManager`'s generic semantics and the
`tests/test_queue_tls.py` integration path exactly as they are. `SecureManager.start` could also
go in-process: `sm_check.py` replayed the TLS integration test's flow that way, in a spawned
child with `dict(self._registry)`. Children went 0→0, `_getvalue()` returned the value, shutdown
took 0.0 ms and the subprocess exited 0 in 0.09 s. So it is viable, but it changes isolation
semantics for a public class (callables run in the caller's process). It is optional, not
needed for R1.

**Condensed loop (from `proto3.py`):**

```python
class _InProcessServer(Server):
    def __init__(self, *args):
        super().__init__(*args)                       # bind + listen (backlog 128)
        self.stop_event = threading.Event()           # read by serve_client(); never set
        self._halted, self._live, self._lock = False, set(), threading.Lock()
    def serve(self):                                  # daemon thread; replaces serve_forever/accepter
        with selectors.DefaultSelector() as sel:
            sel.register(_listening_socket(self.listener), selectors.EVENT_READ)
            while not self._halted:
                if not sel.select(timeout=POLL):
                    continue
                try:
                    conn = self.listener.accept()
                except Exception:                     # OSError, SSLError, closed listener
                    continue
                with self._lock:
                    if self._halted:
                        conn.close(); break
                    self._live.add(conn)
                threading.Thread(target=self.handle_request, args=(conn,), daemon=True).start()
    def handle_request(self, conn):
        try: self._handle_request(conn)
        except SystemExit: pass                       # serve_client: EOF / failed send
        finally:
            with self._lock:
                self._live.discard(conn)
            conn.close()
    def shutdown(self, c):                            # remote RPC: stop serving, never exit
        c.send(('#RETURN', None)); threading.Thread(target=self.stop, daemon=True).start()
    def stop(self):
        with self._lock:
            if self._halted: return
            self._halted = True
            self.listener.close()                     # port free now
            for conn in self._live:                   # recv -> EOF, send fails; no joins
                try:
                    with socket.fromfd(conn.fileno(), socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.shutdown(socket.SHUT_RDWR)
                except OSError: pass
```

`fromfd`/`select` are not tested on Windows, which is a listed platform. CI should cover it.

## 3. Exposed methods

`public_methods`: `JoinableQueue` only: `cancel_join_thread, close, join_thread`. `queue.Queue`
only, on 3.13+: `shutdown`. Common: `empty, full, get, get_nowait, join, put, put_nowait, qsize,
task_done`. The base client (`client.py` L212–213, L241, L389, L1014; `submit.py` L744) calls
only `get, put, task_done`, all in the common set. `client.py` L1292 `heartbeat.join()` is a
local thread join. **Pin `exposed` to the common nine.** That stops a remote `Queue.shutdown()`
on 3.13+ and gives the same proxy surface on every version (measured: the proxy `_exposed_` is
exactly those nine).

`multiprocessing.queues` re-exports `queue.Empty` and `queue.Full` (verified `is`). The server
sends `('#ERROR', e)` (L279), and the client unpickles `_queue.Empty` / `queue.Full`, which are
identical to the classes `client.py`, `submit.py` and `server.py` import from `queue`. Measured:
`is queue.Empty` and `is queue.Full` are both True, each raised in 0.31 s for `timeout=0.3`.

## 4. Prototype results (3.12.13, 3.13.16, 3.14.6, macOS; same on all three unless noted)

| Check | Base `b819de6` | In-process |
|---|---|---|
| (a) `active_children()` before→after `start()`, fork / spawn | 0→1 / 0→1 (spawn start 277–539 ms) | 0→0 / 0→0 (start ≈0 ms) |
| (b) round trip ×4 queues with unmodified `QueueClient`, TLS off and on | — | all True. Remote `join()` returned once `task_done` ran (0.31 s); sentinel arrived |
| (c) remote `get(timeout)` empty / `put(timeout)` full | — | `queue.Empty` / `queue.Full` within timeout + 0.01 s |
| (d) shutdown; stdio identity; same-port rebind; same-thread client on 2nd server | — | returns; `sys.stdout`/`stderr` unchanged; rebind OK at once; client OK (`127.0.0.1`; `localhost` needs the double pop) |
| (e) shutdown with a client blocked in `get(timeout=2)` | fork 2 ms / spawn 114 ms; client `EOFError` | 0.1–0.2 ms (TLS off and on); client `EOFError` at once |
| (f) server-process threads, 50 connected clients | 0 extra (handlers live in the child) | +1 accept thread, +1 handler per client (53 / 52), back to baseline after shutdown |
| Footprint after `start()` + one put per queue (fork, 3.14) | threads 1→5, fds 5→47, 1 child | threads +1, fds +2 (listener + selector) |

An earlier variant that joined the accept thread made shutdown take 84–135 ms (the poll
interval). Not joining removes that wait.

## 5. R6 tests

- **Red on base:** R1 child count. Parametrize `fork`/`spawn` with a fixture that calls
  `mp.set_start_method(m, force=True)` and restores `get_start_method(allow_none=True)`. Also red:
  R2 `isinstance(server.scheduled, queue.Queue)`; two sequential `QueueServer`s in one process
  under `fork`; and the `localhost` same-thread reuse case.
- **Green on base, kept as regression guards:** round trip ×4 with TLS on/off, Empty/Full, `join`
  after `task_done`, sentinel delivery, stdio identity, port reuse, and `'shutdown' not in
  proxy._exposed_`.
- **Speed and stability:**
  - Bind `('127.0.0.1', 0)` and read the real port from `server.address`; this avoids the
    `free_port` race. Reuse that port for the rebind test. Use `free_port` only where a fixed
    port is the point.
  - Use 0.2 s operation timeouts and assert `elapsed < timeout + 1.0`.
  - Wrap servers in `with`, so a failing assert still shuts them down.
  - TLS: use a module-level fixture modelled on `test_queue_tls.py` L44–57 (generated certs,
    `clear_process_context()` on teardown). Do not import from that test module.
  - For "no leaked threads", poll `threading.active_count()` back to baseline with a 2 s deadline
    rather than asserting it immediately.
  - In-process start is ≈0 ms, so the whole R6 set should run in a few seconds. Mark it `unit`.
