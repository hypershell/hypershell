# PLAN — Serve the managed queues in-process on a threading primitive (zone, cycle 1)

> **Status:** Draft for review · **Last updated:** 2026-11-02
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (digest: [`research/00-digest.md`](research/00-digest.md)). Every design
> element traces to a GOAL R-ID.

## 1. Summary

`QueueServer.start` stops forking a manager child. It builds a `multiprocessing.managers.Server`
in the server process and runs HyperShell's own accept loop on a daemon thread. Per-connection
requests are still answered by the stock `Server.handle_request` / `serve_client`, so the
registered names, pickle framing, `SecureConnection`, the HMAC handshake and TLS are byte-for-byte
what a `b819de6` client expects. The four queues become `queue.Queue`s that the server threads and
the manager's handler threads share directly. The seam is small (`core/queue.py`, plus one
docstring each in `core/tls.py` and `core/logging.py`). Most of the appetite goes to proof: new
queue and FSM tests, a base-vs-feature parity, compatibility and throughput harness, and a
cluster-agnostic multi-node drive whose evidence comes back from outside the repo.

## 2. Design

### 2.1 `core/queue.py` — the in-process server

**`_InProcessServer(Server)`** (private, in `core/queue.py`). Construction is the stock
`Server.__init__`. It binds and listens (backlog 128 on 3.11–3.14) through
`listener_client[serializer][0]`, so `SecureListener` (TLS) or the stock `Listener` (`--no-tls`) is
chosen exactly as today. It adds:

- `stop_event = threading.Event()`. `serve_client` reads it between requests. It is **never set**:
  setting it breaks request/reply pairing (measured: a client's next call returned `None`, research
  01 §2).
- A halt flag, a live-connection set and a lock.
- `serve()`, the accept loop, which replaces `serve_forever()` and `accepter`. It runs a
  `selectors.DefaultSelector` on the listening socket with `POLL = 0.25` s. On readiness it calls
  `listener.accept()`, which for TLS includes the handshake, unchanged. Any exception from one
  accept is logged at debug level and the loop continues: an `OSError`, an `SSLError`, a `TLSError`
  or a closed listener all mean "drop that peer". Each accepted connection is added to the live set
  under the lock (or closed at once if halted). It then gets a daemon thread named
  `hypershell-queue-handler` running `handle_request`. If the loop itself dies, it logs at error
  level and does not exit the process.
- `handle_request(conn)`: the stock body (`_handle_request`, swallow `SystemExit` from
  `serve_client`'s in-thread `sys.exit`, `conn.close()`), with live-set removal under the lock.
- `shutdown(c)` (the remote RPC) is overridden. It returns `None`, so the stock reply is sent, and
  it schedules `stop()` on a short daemon thread. It never exits the process. On base, the same RPC
  ended the child.
- `stop()` is idempotent and non-blocking. Under the lock it sets the halt flag. It calls
  `shutdown(SHUT_RDWR)` on the listening socket, ignoring `OSError`: on Linux this takes the socket
  out of LISTEN even if the loop is inside `select`/`accept`. It then calls `listener.close()`, which
  releases the port; the listener sets `SO_REUSEADDR`. Finally it calls `shutdown(SHUT_RDWR)` on
  each live connection's socket: the `ssl.SSLSocket` for `SecureConnection`, else a socket
  duplicated from `conn.fileno()`, skipped where the platform can't. The handler threads blocked in
  `recv` or `get(timeout)` then fail their next send/recv and exit on their own. They are **never
  joined**, and neither is the accept thread. A best-effort `join(timeout=2*POLL)` on the accept
  thread follows, for tidiness only.
- The listening socket is `listener._socket` for `SecureListener`. For the stock listener it is the
  private `listener._listener._socket`, present on 3.11–3.14. A small `_listening_socket(listener)`
  helper isolates that dependency, and R6's tests run on every CI Python.

**`SecureManager._serve_in_process(registry)`** (new private helper):

1. If `self._tls` is active: `install_process_context(self._tls)`. This is bootstrap duty 1; see §2.3.
2. Build `_InProcessServer(registry, self._address, self._authkey, self._serializer)`. Set
   `self._address = server.address`, so a requested port 0 reports the real port, as base does, and
   set `self._process = None`.
3. Start the accept loop on a daemon thread named `hypershell-queue-server`.
4. Set `self._state.value = State.STARTED`.
5. Set `self.shutdown = util.Finalize(self, _finalize_in_process, args=(server, configured_address,
   state), exitpriority=0)`. This keeps BaseManager's shape: `__exit__` calls `self.shutdown()`, it
   is idempotent, and it also runs at interpreter exit. `_finalize_in_process` runs `server.stop()`,
   sets `state.value = State.SHUTDOWN`, and under `BaseProxy._mutex` pops
   `BaseProxy._address_to_local` for **both** the configured `(host, port)` and `server.address`.
   The second key matters because `localhost` and `127.0.0.1` cache differently (research 01 §2).

**`QueueServer.start(initializer=None, initargs=())`**: the signature is unchanged.

- It creates `queue.Queue(maxsize=self.config.size)` for `scheduled` and `completed`, and
  `queue.Queue(maxsize=0)` for `heartbeat` and `confirmed`. These are today's bounds.
- It builds a **per-instance** registry with the existing typeids:
  `{'_get_scheduled': (callable, QUEUE_EXPOSED, None, AutoProxy), …}`, where each callable is a
  lock-free closure returning that instance's queue. It does **not** call the `register`
  classmethod, which mutates the class.
- `QUEUE_EXPOSED` is the nine methods common to `JoinableQueue` and `queue.Queue`.
- If an `initializer` is given, it runs once in the calling thread before serving. Today
  `QueueServer` silently drops it; nothing passes one.
- It calls `self._serve_in_process(registry)`, then sets `self.ready = True`.

The `_get_*` instance methods and the `QueueInterface` annotations change from `JoinableQueue` to
`queue.Queue` (`multiprocessing.JoinableQueue` import removed). **`SecureManager.start`,
`_child_bootstrap`, `SecureListener`, `secure_client`, `SecureConnection`, `QueueClient` and
`QueueConfig` are unchanged.**

### 2.2 Unchanged callers

`server.py`, `client.py`, `submit.py` and `cluster/*` need no code change. Every server thread
already uses `self.queue.<name>.put/get/task_done` with the same signatures, and `queue.Empty` and
`queue.Full` are the same classes `multiprocessing` re-exports (research 01 §3). The
untimed sentinel puts (`server.py` L644, L898, L905, L916, L919) block exactly as they did with
`JoinableQueue` (research 02 §1). The shutdown and sentinel ordering in `ServerThread` is not
touched. `with self.queue:` still runs on the `hypershell-server` thread, and `QueueServer.__exit__`
still calls `self.shutdown()` after every sentinel has been consumed.

### 2.3 The two bootstrap duties, now that the child is gone

| Duty | Where it lives now | Why it is safe |
|---|---|---|
| **Per-process TLS context install** | `SecureManager._serve_in_process`, step 1, on the thread that starts the server (`hypershell-server`), before the accept loop starts. Only when TLS is active; `--no-tls` never installs `None`. | The context slot (`core/tls.py` L215–231) is process-global, and `SecureListener.accept` reads it lazily on every accept, from the single accept thread. Repeat installs reset the cached contexts and rebuild them from the new config, so they are idempotent in effect. `LocalCluster`/`hsx` passes the **same** `TLSConfig` to its in-process server and client, so the client's `SecureManager.connect` re-install is a no-op in effect. Remote, ssh and autoscale clients are separate processes. Sequential servers in one process (the tests) each install their own config. A conflicting install, which no current path does, makes the next accept fail, and the loop logs that and keeps serving instead of dying the way the stock `accepter` does. Server-side TLS is still configured in the process that serves, which is the GOAL's requirement. |
| **Closing inherited log-slot locks** | Nowhere on the `QueueServer` path. `close_inherited_slot_locks()` stays in `core/logging.py` (exported, and called by `tests/test_logging.py`). `_child_bootstrap` stays as the initializer of the unchanged `SecureManager.start` fork path. | The duty existed only because a fork-without-exec child shares the parent's `flock` open-file-description (research 03 §2). With no child, no second holder exists. No other fork-without-exec remains in `src/`: every `Popen` execs at once with `close_fds=True`. The docstrings of `close_inherited_slot_locks`, `SecureManager.start` and `core/tls.py` (L80–82, L214, L224), which name "the server subprocess", are reworded. |

### 2.4 Thread model, GIL, signals, deadlock

**Threads in the server process after the change.**

- **Unchanged:** MainThread, `hypershell-server`, submitter (and its loader and committer),
  scheduler, confirm, heartmonitor, receiver and log compression.
- **Removed:** the up to 3 `multiprocessing` feeder threads.
- **Added:** `hypershell-queue-server` (the accept loop), plus one `hypershell-queue-handler` per
  client connection.
- **Handler count:** each `hs client` holds **3 long-lived connections** (one per client thread
  that touches a proxy: scheduler, collector, heartbeat), plus ~17 short incref/decref/create
  connections over its life. `hs submit` in queue mode and the in-memory `LiveSubmitThread` hold one.
  At the ~1,000-client ceiling that is ~3,000 handler threads. Base had the same number, in the
  manager child. Per-user thread limits count both processes alike, so the total is unchanged; only
  the owning process changes.

**GIL.**

- **Moves into the server's GIL:** unpickling requests, pickling replies, `SecureConnection`
  framing, the HMAC challenge per connection, thread start per connection, and the Python side of
  `wrap_socket`.
- **Leaves it:** feeder-thread pickling and pipe I/O for every server-side put. Server threads now
  pass object references.
- **What releases the GIL:** queue waits, socket `recv`/`sendall`/`accept`, `select` and OpenSSL
  I/O. Idle handlers therefore cost little.
- **Honest sizing:** a synthetic test with 1,000 idle remote getters cost a pure-Python thread
  ~14% and raised wake-up p99 from ~15 to ~45 ms. 250 getters cost nothing measurable (research 02
  §3). The Scheduler and Receiver (SQLAlchemy ORM work does not release the GIL) can expect a tax
  of that order at the ceiling: a slowdown, not a stall.
- **HeartMonitor:** eviction granularity is `evict_after/10` (≥1 s), so millisecond delays don't
  matter. The real exposure is backlog. If it drained `heartbeat` slower than ~100 beats/s at 1,000
  clients, ages computed from the client's stamp would grow and could cause false evictions. That
  mechanism exists today; the monitor now competes for the GIL.
- **Coverage:** R9's local benchmark (≤32 clients) cannot show this. The multi-node run is the
  check, and §5 makes it an explicit risk with a scale point near the ceiling.

**Signals.**

- Python runs handlers only in the main thread. In `hs server` that thread is blocked in
  `ServerThread.join()` (`server.py` L1033/L1224); in `hsx` it is in `run_local`'s join. Both are
  interruptible lock waits, so delivery is unchanged.
- The accept loop and the handlers never call `check_signal()`. The cooperative drain needs the
  queues served until `shutdown()`. So the sticky `RECEIVED` flag and the vestigial
  `Thread.__should_halt` never touch them, and no `reset_signal()` is added.
- The FSMs' manual polling is untouched.
- **One observable difference.** Under spawn (macOS) and forkserver (Linux 3.14+), a
  USR1/USR2/HUP sent to the **process group** used to kill the manager child (measured: exit −30
  under spawn). It then left the drain unable to deliver sentinels. With no child, that failure
  mode is gone. This is recorded as an intended difference (§5), not an R-ID change.

**Deadlock and starvation.**

- **Locks: no cycle.**
  - `queue.Queue.mutex` is a leaf: its Conditions share it, and `_put`/`_get` call nothing outside.
  - `Server.mutex` is held only in `create`/`incref`/`decref`. Inside it, `create` calls the
    registry callable, which is a lock-free closure and must stay so: no logging, no I/O.
  - Handler threads never touch logging handlers, the SQLAlchemy session or `executor_lock`.
- **Blocking at shutdown.**
  - A handler blocked in a client's `get(timeout)` or in `recv` cannot block shutdown: it is a
    daemon, it is never joined, and `stop()` cuts its socket.
  - A client `put` on a full `completed` after the Receiver has exited times out after 2 s and
    retries, as on base. Ordering prevents it in normal runs: the Receiver sentinel is posted only
    after the heartmonitor has seen every client finish.
- **Accepts.** The accept loop never blocks `stop()`. Accepts are serialised on that one thread,
  handshake included, exactly as on base, and the backlog is still 128. A simultaneous connect burst can still see
  resets, now in the server process; staggered launch, as the autoscaler does, avoids it.
- **Pre-existing hang (base), untouched and out of scope (R8).** The heartmonitor's untimed
  sentinel puts into the 1-slot `scheduled` queue (`server.py` L644) can hang shutdown if two or
  more registered clients die without being evicted. R7's tests size their queues so they never
  trip it.

### 2.5 Tests and harness (new files only; no existing test is modified)

- `tests/test_queue.py` (`unit`) covers R1, R2, R3, R5 and R6:
  - Child count under `fork` and `spawn`. Parametrised, restoring the start method.
  - Queue types and bounds.
  - A round trip on all four queues through the unmodified `QueueClient`, with TLS on and off.
  - `Empty`/`Full` raised within the timeout; `join` returns after `task_done`; the sentinel reaches
    a remote consumer.
  - Shutdown: returns, stdio identity is unchanged, the same port rebinds in the same process, and
    a same-thread client of the second server works under both `127.0.0.1` and `localhost`.
  - A second concurrent `QueueServer` serves its own queues.
  - The exposed surface is the nine methods.
  - Handler and accept threads drain to baseline within 2 s.
  - Setup: bind port 0 and read `server.address`; timeouts of 0.2 s; a TLS fixture defined in this
    module, modelled on `test_queue_tls.py`, which is not imported.
- `tests/test_server_fsm.py` (`unit`, plus one `integration` per FSM) covers R7:
  - `Scheduler.post_bundle`, `Confirm`, `Receiver` and `HeartMonitor`, against `queue.Queue`
    stand-ins (`SimpleNamespace`).
  - `hypershell.server.DEFAULT_REMOTE_TIMEOUT` is monkeypatched to 0.05, which works because it is
    read at call time.
  - The non-Scheduler FSMs run with `in_memory=True`. Scheduler halt goes through a monkeypatched
    `RECEIVED`, which is always restored.
  - One integration pass per FSM goes through a started `QueueServer` and a `QueueClient`.
  - Sketches: research 02 §6.
- `tests/test_queue_workload.py` (`integration`) covers R8: a server and two `hs client`s on
  loopback in a `temp_site`, running a grouped workload with one task that fails once and succeeds
  on retry. It asserts the final state of every task, no lost results, and every process exiting
  within a bound.
- `tests/bench/bench_queue.py` (never collected: no `test_` prefix) is the base-vs-feature harness:
  - It builds one locked, non-editable venv per git ref (`git archive` + `uv sync --frozen --no-dev
    --no-editable`).
  - **`parity`** runs R8's workload on both refs and diffs the normalised final task table
    (`args`, `group`, `attempt`, `exit_status`, `retried`, completed or not).
  - **`compat`** runs a feature server with clients from the base ref (R4).
  - **`gate`** is R9: interleaved, peaks over the process tree, and the verdict printed with a
    non-zero exit on failure.
  - Prototype: `research/prototypes/bench/bench.py`.
- `tests/cluster/` holds the multi-node drive (§2.6).

### 2.6 The multi-node drive (R9's cluster clause), cluster-agnostic

**Files.**

- `tests/cluster/drive.sh`: POSIX sh, role `server` or `client`.
- `tests/cluster/build_archive.sh`: builds one archive per version.
- `tests/cluster/compare.py`: stdlib only.
- `tests/cluster/smoke.sh`: a local loopback rehearsal.

Prototypes are in `research/prototypes/bench/`.

**Interface: environment only.** Any launcher (job script, `mpirun`, `ssh`) starts one `server`
and N `client`s with the same `HSD_RUN_ID`.

| Variable | Meaning (default) |
|---|---|
| `HSD_ROLE` | `server` \| `client` (required) |
| `HSD_ARCHIVE` | staged archive for the version under test (required) |
| `HSD_RESULTS` | shared results root; only results are written here (required) |
| `HSD_RUN_ID` | id shared by a server and its clients (required) |
| `HSD_AUTH` | queue key, ≥16 chars, passed as `HYPERSHELL_SERVER_AUTH` (required) |
| `HSD_HOST` / `HSD_BIND` / `HSD_PORT` | address clients dial / server bind (`0.0.0.0`) / port (`50001`) |
| `HSD_LOCAL` | node-local scratch root: archive unpack, venv, site, database, logs (`$TMPDIR`) |
| `HSD_DB` | database file (`$HSD_LOCAL/hsd-$HSD_RUN_ID/server/task.db`); must be node-local |
| `HSD_CLIENTS` / `HSD_CLIENT_INDEX` | expected client count (recorded) / this client's index (required for `client`) |
| `HSD_TASKS` / `HSD_FAIL_EVERY` / `HSD_COMMAND` | task count M (10000) / every k-th task exits 3 (100) / passing command (`true`) |
| `HSD_THREADS` / `HSD_BUNDLESIZE` / `HSD_CLIENT_BUNDLESIZE` / `HSD_CLIENT_BUNDLEWAIT` | executors per client (1) / server bundle (1) / client bundle (1) / client wait (1) |
| `HSD_TLS` | `1` (default; clients pin the server's `<auto>` cert via `--tls-ca`) or `0` (`--no-tls`) |
| `HSD_IDLE` / `HSD_TIMEOUT` / `HSD_WAIT` | client idle backstop (120 s) / server wall limit (3600 s) / client wait for readiness (600 s) |

**Node-local discipline** (the stall lesson).

- The archive is a relocatable standalone interpreter (`python/`), a `--target` install (`site/`)
  and a `LABEL`. It has no absolute paths and no console-script shebangs. It is copied once to each
  node's `HSD_LOCAL` and unpacked behind a `mkdir` lock.
- `PYTHONPATH`, the site, the database and logs are all node-local. Imports and SQLite never touch
  the shared filesystem.
- Only `server.ready`, `server.crt` (the public certificate), `server.json` and `client-<index>.json`
  are written to `HSD_RESULTS`. Logs are copied there only on failure and are never committed.
- `build_archive.sh` takes `HSA_REF` (a git ref, e.g. `2.8.3` or the feature head), `HSA_PYTHON`,
  an optional `HSA_PLATFORM` for cross-building to the nodes' platform, and `HSA_OUT`.

**Flow.**

- The server submits M tasks into node-local SQLite.
- It then runs `hs server --restart`, which stops on the scheduler's own drained-DB rule and sends
  each registered client its disconnect sentinel.
- A psutil sampler publishes `server.ready` once the port is LISTENing, with no probe connection.
  It then records tree peaks until the server exits.
- Clients poll for `server.ready` (`hs client` doesn't retry a refused connect), run, and exit on
  the sentinel; `-T` is only a backstop.
- The CLI flags used are identical in 2.8.3 and the feature (research 04 §B).

**Records.**

- `server.json`: label; HyperShell and Python versions; `multiprocessing` start method; run id;
  TLS; M; N expected and seen; T; bundle sizes; server rc; submit and wall seconds; DB span; full
  and p10–p90 tasks/s; rows; retried; incomplete; exit-status histogram; and peaks (own and tree:
  children, threads, FDs, RSS).
- `client-<index>.json`: rc, start and end.
- **No host names, user names or paths are recorded anywhere** (the prototype's `hostname` field
  and host-based client names are removed). So the JSONs can be committed as evidence.

**Task mix.** `true` plus 1% `exit 3`, with no groups or retries. #68, in `b819de6`, legitimately
changed group and retry behaviour since 2.8.3. R8's grouped retry workload is checked locally
against `b819de6` instead.

**Comparison.** `compare.py RESULTS --baseline 2.8.3 --candidate <label>` groups runs by (TLS, N,
T, M, bundle). It requires identical results (the same histogram for every run and label,
rows = M, 0 incomplete, every rc 0) and applies the R9 throughput rule to medians of ≥3
interleaved runs per label. It exits non-zero on failure.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `QueueServer.start` → `SecureManager._serve_in_process` → `_InProcessServer` on daemon threads. No `BaseManager.start()` and no fork. Test: `active_children()` unchanged across `start()` under fork and spawn (red on base). |
| R2 | `queue.Queue` × 4 with today's bounds. No pipes or feeders. Pinned 9-method surface keeps `put`/`get` with timeout raising `Full`/`Empty`, and `join`/`task_done`. |
| R3 | Stock `serve_client` dispatches proxy calls to the shared objects. The R6 round trip runs both directions on all four queues, TLS on and off. |
| R4 | Stock `Server` request handling, the unchanged `SecureListener`/`SecureConnection`/`secure_client`, the same authkey handshake, the same typeids, and the same exposed-method set. `test_queue_tls.py` is unmodified. `bench_queue.py compat` runs `b819de6` clients against the feature server. |
| R5 | `stop()`: halt, `SHUT_RDWR` and close the listener (port released), cut live connections, never join, never `sys.exit`, never touch stdio. A `Finalize`-backed `shutdown` is called from the unchanged `__exit__` after the unchanged sentinel ordering. Tested: same-port rebind, same-thread client of a second server, stdio identity. |
| R6 | `tests/test_queue.py` (§2.5). |
| R7 | `tests/test_server_fsm.py` (§2.5). |
| R8 | No change to `server.py`/`client.py`/`submit.py`/CLI. Full suite unmodified. `tests/test_queue_workload.py`. `bench_queue.py parity` against `b819de6`. `hs … --help` output diffed against base. |
| R9 | `bench_queue.py gate` with the thresholds in §6. The multi-node clause uses `tests/cluster/*` (§2.6): run A hands back, run B collects. |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research (gate 1: §5, §6, §8, §9, §12) and again against this design (gate 2).
§1–§4, §7, §10 and §11 are not touched: no model, query, retry, resource or argv change.

- **§5 Concurrency.**
  - No new FSM; no `StateMachine` or `Thread` subclass is changed.
  - The FSMs' queue ops keep their 2 s finite timeouts and re-entry, and R7 now tests them.
  - The serve loop is a plain daemon thread, deliberately not a `core.thread.Thread`, so the
    `stop()`/`machine.halt()` rule does not apply. Its blocking points (`select` with a 0.25 s
    timeout; `accept`) never block shutdown, because `stop()` never waits on them.
  - The handler threads are CPython's, unchanged from base, now in-process.
- **§6 Shutdown/sentinel ordering.**
  - `ServerThread`'s ordering and the submit flush are untouched.
  - `QueueServer.__exit__` still runs only after every sentinel has been consumed.
  - Payloads stay `serialize_tasks`/`heartbeat.pack` bytes and `make_sentinel()`.
- **§8 Signals.**
  - No handler change and no `reset_signal()`.
  - The serve loop does not poll, so the sticky `RECEIVED` cannot halt it mid-drain.
  - SIGINT/SIGTERM are still not captured.
- **§9 Queue transport & security.**
  - TLS stays on by default; `--no-tls` stays `tls=None`.
  - The authkey is always required, and the placeholder/length/charset checks are untouched
    (they live in the app layer, `server.py` L1412–1417, not in `core/queue.py`).
  - The pickle framing and `SecureConnection` byte layout are unchanged: the same classes are used.
  - No mTLS, as today. The post-handshake fingerprint check in `secure_client` is untouched.
  - TLS material handling is unchanged.
- **§12 Conventions.**
  - No CLI change, so no `docs/_include` or `share/` change.
  - No dependency floor change: psutil is already a dependency, and the harness uses stdlib +
    psutil.
  - New tests are tagged `unit`/`integration`.
  - Comments are declarative and carry no R-IDs.
  - 3.11–3.14 are covered by CI.

### Deviation justifications

**No row changes what any R-ID SHALL do.** The rows below record added complexity only.

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| Own accept loop in an `_InProcessServer` subclass. It relies on CPython internals (`Server._handle_request`, `serve_client`'s `stop_event` attribute, the stock listener's private `_listener._socket`). | R1 and R5: serve in-process, stop cleanly, release the port, keep stdio. | `serve_forever()` on a thread ends with `sys.exit(0)` and resets `sys.stdout`/`sys.stderr`. The stock `accepter` cannot be stopped: after close it spins at 100% CPU (stock listener) or dies (`SecureListener`). Replacing `BaseManager` is a GOAL non-goal. The internals are identical on 3.11–3.14 (research 01 §1), and R6 runs on all four in CI. |
| Two serve paths remain. `SecureManager.start` keeps forking (only `test_queue_tls.py` uses it); `QueueServer.start` serves in-process. | Keeps the generic `SecureManager` semantics and the existing TLS subprocess test on exactly the code it runs today (R4: "existing TLS queue tests SHALL pass unmodified"). | Making `SecureManager.start` in-process too works (research 01 §2), but it changes a public class's isolation semantics for no R-ID. It can be done later with `_child_bootstrap`'s removal. |
| Pinned 9-method `exposed` list instead of `public_methods()`. | R4: the remote surface stays identical. On 3.13+, `queue.Queue` would also expose `shutdown`. | The default exposure differs by Python version and from base. |
| Per-instance registry dict instead of the `register` classmethod. | Two `QueueServer`s in one process (tests; R5's same-process rebind) must each serve their own queues. | `register` mutates the class. On base a second server already fails under fork (research 01 §1). |
| `stop()` cuts live client connections with `SHUT_RDWR`. | R5 parity: on base the child's exit cut them. Without this, an old client would keep using a dead server's queues. | Leaving them open strands clients until their own timeouts. |

## 4. Rabbit holes (resolved)

- **Can `BaseManager`'s server run in-process at all?** Yes. A prototype passes R1, R3, R4 (an
  unmodified `QueueClient`), R5 and the `Empty`/`Full`/`join` semantics on 3.12, 3.13 and 3.14, TLS
  on and off ([01](research/01-serve-loop.md) §4).
- **Stopping without `stop_event`.** Setting it desynchronises request and reply, so the design
  stops by cutting sockets instead ([01](research/01-serve-loop.md) §2).
- **Stale proxy connections after a same-port restart.** Pop both cache keys
  ([01](research/01-serve-loop.md) §2). Base has the same gap with `localhost`.
- **Where the TLS install and the lock hygiene go.** See §2.3 ([03](research/03-bootstrap-duties.md)).
- **Exposed-surface drift on 3.13+** is pinned ([01](research/01-serve-loop.md) §3,
  [02](research/02-thread-model.md) §1).
- **GIL at scale.** Measured synthetically; the multi-node run is the real check
  ([02](research/02-thread-model.md) §3).
- **Benchmark noise.** M ≥ 5000, interleaved builds and 5 repeats; ±10% at 3 repeats is noise
  ([04](research/04-benchmark-and-drive.md) §A).
- **Parallel-filesystem stalls on the cluster.** One archive, node-local unpack, node-local DB;
  results only on shared storage ([04](research/04-benchmark-and-drive.md) §B).
- **2.8.3 comparability.** Identical server and client flags. Groups and retries are excluded
  because #68 changed them since 2.8.3 ([04](research/04-benchmark-and-drive.md) §B).

## 5. Risks & open questions

- **GIL tax near the ~1,000-client ceiling (main risk).** Expect up to ~10–15% on scheduler and
  receiver work at the ceiling: a slowdown, not a stall. The local gate cannot see it. The
  multi-node run must include a high-N point. Recommendation: N ≥ 256 clients, and the largest N
  the allocation allows, up to the ceiling. If it regresses beyond R9's tolerance there, this
  design has no in-cycle lever. `sys.setswitchinterval` is process-wide and is not adopted blind.
  The finding returns to the foreman before the pre-release.
- **Linux socket semantics are verified only by CI and the cluster run.** Prototypes ran on macOS.
  The design avoids relying on `close()` waking `accept()`; it uses `shutdown(SHUT_RDWR)` on the
  listener plus a select timeout. R6's port-reuse test is the Linux check in CI (ubuntu, 3.11–3.14).
  Windows (a listed classifier, not in CI) falls back to skipping the connection sweep.
- **R9 footprint is read over the server's process tree.** On base the server's queue sockets and
  handler threads live in its manager child. Comparing only the server's own process would count
  them on one side and not the other. The gate therefore compares tree children and tree FDs. The
  server process's own thread and FD counts rise by about 3 per connected client, by design. They
  are reported, not gated. *If the foreman reads "its process count and open file descriptors" as
  the server process alone, this is a GOAL question, not a plan choice.*
- **Intended observable differences (no R-ID changes; listed so review doesn't read them as
  regressions):**
  - A process-group USR1/USR2/HUP no longer kills a manager child under spawn or forkserver.
  - TLS shutdown takes milliseconds instead of ~1.0 s plus a child SIGTERM.
  - The `multiprocessing` resource-tracker process no longer appears under spawn or forkserver.
  - The server process's own thread and FD counts rise as above.
  - A remote `shutdown` RPC stops serving but no longer exits a process.
- **Distributed TLS on the cluster.** Clients pin the server's `<auto>` certificate via `--tls-ca`.
  If hostname verification fails for the address clients dial, the same remedy must apply to every
  label, or `HSD_TLS=0` is used for the comparison. Both versions behave identically here.
- **The multi-node evidence is external.** P7 can only be completed with results the foreman
  supplies. GOAL R9 requires it "before the pre-release" (2.9.0a2), not before review. P7 can
  therefore stay `blocked` while `hs-review` runs on P1–P6, at the foreman's call.

## 6. Verification strategy

- **Per phase:** the `verify:` commands in `TECH.md`.
  - Red-first: P1's child-count, two-servers and `localhost`-reuse tests, and P2's type and
    exposed-surface tests, are written and observed red on base before the code change.
  - Every code phase also drives the CLI in a throwaway site:
    `.agents/factory/bin/temp_site.sh sh -c "seq 200 | uv run hsx -t 'echo {}' -N4 && uv run hs
    list --count"`, with TLS default and with `--no-tls`.
- **R8:**
  - The full suite (`uv run pytest -v`) passes with `git diff b819de6 -- tests/` showing additions
    only.
  - `hs server|client|submit|cluster --help` is byte-identical to base.
  - `tests/test_queue_workload.py` passes.
  - `bench_queue.py parity --base b819de6 --feature HEAD` reports identical final task tables, with
    every process exiting within 60 s of the last completion.
- **R4:** `bench_queue.py compat --server HEAD --client b819de6` completes a workload: every task
  exits 0 and every process rc is 0, TLS on and off.
- **R9, local gate thresholds** (`bench_queue.py gate --base b819de6 --feature HEAD`).
  - **Runs:** builds interleaved, M = 5000 (20000 for b=50), 5 repeats per config.
  - **Configs:** 4×4 b=1 (TLS on, TLS off); 16×1 b=1 (TLS on, TLS off); 4×4 b=50 (TLS on); 32×1
    b=1 (TLS on).
  - **Throughput:** PASS when the feature's p10–p90 tasks/s median ≥ the base median −
    max(base spread, feature spread, 5% of the base median).
  - **Footprint:**
    - Peak tree child processes: feature ≤ base. Expected 0, against 1–2.
    - Peak tree open FDs: feature ≤ base.
    - Peak RSS and threads: reported only.
  - The harness exits non-zero on any failure.
  - Base reference: 4×4 b=1 TLS 240.8 tasks/s [231–243]; 16×1 b=1 TLS 224.3 [200–233]; 4×4 b=50
    836.5 [820–840] (research 04 §A).
- **R9, multi-node (external).**
  - **Run A:** `tests/cluster/smoke.sh` rehearses the drive locally.
  - **Foreman, outside the repo:** build archives for `2.8.3`, `b819de6` and the feature head. Run
    each (TLS on) at N ∈ {16, 128, ≥256 up to the ceiling} clients, M ≥ 20000, ≥3 interleaved runs
    per label.
  - **Run B:**
    - The sanitized JSONs go under `spec/zone-queue-primitive/evidence/cluster/`.
    - Then `compare.py … --baseline 2.8.3 --candidate feature` must exit 0. That means identical
      results and the same throughput rule.
    - Tree children and FDs must be ≤ 2.8.3.
    - `b819de6` is the attribution control.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
