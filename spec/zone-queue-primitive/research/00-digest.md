# Research 00 — Digest: one recommendation per question

Four briefs were fanned out (appetite `big`, high blast radius: `core/queue.py`, `server.py`, and
how they interact with `core/fsm.py`, `core/thread.py` and `core/signal.py`):
[01 serve loop](01-serve-loop.md), [02 thread model](02-thread-model.md),
[03 bootstrap duties](03-bootstrap-duties.md), [04 benchmark and drive](04-benchmark-and-drive.md).
The prototypes they ran are kept in [`prototypes/`](prototypes/) so the build can adapt them:
`serve_loop/proto3.py` (with its helper `proto.py`), `bench/bench.py`, `bench/drive.sh` and
`bench/compare.py`. None of them is collected by pytest. All measurements are from macOS (3.12–3.14);
Linux behaviour is reasoned from source and confirmed only by CI and the cluster run.

## Decisions (contradictions resolved)

1. **Serve loop.** `QueueServer.start` builds an `_InProcessServer` (a subclass of
   `multiprocessing.managers.Server`) in the server process and runs HyperShell's own accept loop on
   a daemon thread. It never calls `serve_forever()` or the stock `accepter`. (01 §2)
2. **Where it lives.** A `SecureManager` helper is called only from `QueueServer.start`.
   `SecureManager.start` keeps its forking path, so `tests/test_queue_tls.py`'s subprocess test runs
   exactly the code it runs today. 01 showed that moving `SecureManager.start` in-process also
   works; we don't do it, because R1 doesn't need it and it would change a generic class. (01 §2, 03 §1)
3. **Joining the accept thread: 01 and 02 disagree.** 02 has `shutdown()` join the loop thread. 01
   says don't, because `accept()` runs the TLS handshake and can block. **Decision:** `stop()` never
   blocks on the loop. It sets the halt flag, calls `shutdown(SHUT_RDWR)` and then `close()` on the
   listening socket, and returns. `shutdown` takes the socket out of LISTEN at once on Linux, where
   a bare `close()` would not wake a thread in `select`/`accept`; `close` is enough on macOS (02
   §4). A best-effort `join(timeout=2*POLL)` follows, for tidiness only: correctness and R5 never
   wait on it.
4. **`stop_event`.** Create it, because `serve_client` reads it. Never set it. Setting it breaks
   request/reply pairing: a client's next call returned `None`. (01 §2; 02 §4 agrees it must exist)
5. **Live connections at shutdown.** Track them in a set under a lock, and `shutdown(SHUT_RDWR)`
   each one when stopping. Base behaves the same way when the child exits. Never join handler
   threads. Use the TLS socket where there is one; otherwise use a socket duplicated from the
   connection's fd. Where the platform offers neither, skip the sweep, and clients fail on their
   next call. (01 §2, 02 §4)
6. **Registry.** One registry dict per instance, using the existing typeids `_get_scheduled`,
   `_get_completed`, `_get_heartbeat` and `_get_confirmed`. Don't call the `register` classmethod:
   it mutates the class, and on base a second `QueueServer` in the same process fails under fork.
   (01 §1–2)
7. **Exposed methods.** Pin them to the nine that `JoinableQueue` and `queue.Queue` share: `put get
   put_nowait get_nowait task_done join qsize empty full`. This keeps the remote surface identical
   on 3.11–3.14; on 3.13+ the default would add `shutdown`. (01 §3, 02 §1)
8. **Proxy cache.** At shutdown, pop `BaseProxy._address_to_local` for both the configured
   `(host, port)` and `server.address`. `localhost` versus `127.0.0.1` otherwise leaves a stale
   same-thread connection. (01 §2, 03 §3)
9. **`_manager_server`.** Do not set it. (01, 02)
10. **TLS context (bootstrap duty 1).** When TLS is active, call `install_process_context(self._tls)`
    in the in-process start, on the starting thread, before the accept loop starts. The slot is
    process-global and is read by the accept thread on each accept. Don't install `None` for
    `--no-tls`. Accept errors are caught per connection (03 option A). Option B, a per-server pinned
    context, is rejected for this cycle: it adds a listener attribute that no current path needs.
    (03 §1)
11. **Log-slot locks (bootstrap duty 2).** Nothing replaces them on the in-process path, because
    there is no fork without exec. `close_inherited_slot_locks` stays (`__all__`, and
    `tests/test_logging.py` calls it). `_child_bootstrap` stays as the initializer of the unchanged
    `SecureManager.start` fork path. Only docstrings change. (03 §2)
12. **Serve-loop thread type.** A plain daemon `threading.Thread`, not a HyperShell `Thread`/FSM. It
    never polls `check_signal()`: the cooperative drain needs it serving until `shutdown()`. It logs
    loudly if it dies. (02 §5)
13. **Signals.** No handler change. On base, a USR1/USR2/HUP sent to the process group killed the
    spawn/forkserver manager child. That failure mode goes away; record it as an intended
    observable difference, not an R-ID change. (02 §5)
14. **Benchmark gate.** Interleave the builds. Per config, the feature passes when its median is ≥
    the base median − max(base spread, feature spread, 5% of the base median). Footprint is measured
    over the server's whole process tree: children and FDs must be ≤ base. Use M ≥ 5000 and 5
    repeats. (04 §A)
15. **Multi-node drive.** `tests/cluster/drive.sh` is POSIX sh with `HSD_*` environment variables
    only. It stages one relocatable archive per node, keeps everything node-local, and writes
    results only to shared storage. The productionised version must record **no host names**. The
    prototype records `hostname`, which has to go. Groups and retries stay out of the 2.8.3
    comparison. (04 §B)

## Measured facts the plan relies on

- Base: `active_children()` goes 0→1 at `start()` under fork and spawn. In-process stays 0→0 on
  3.12, 3.13 and 3.14. (01 §4)
- In-process shutdown takes 0.1–0.2 ms, even with a client blocked in `get(timeout=2)`, and that
  client gets `EOFError` at once. On base, TLS shutdown takes 1.0 s and ends the child with SIGTERM.
  (01 §4, 03 §1)
- Footprint: the server process gains one accept thread, plus one handler per connection: about 3
  per `hs client`, moved over from the child. Base tree: 2 children under spawn (manager and
  resource tracker). (02 §2, 04 §A)
- GIL tax, synthetic: 1,000 idle remote getters cost a pure-Python thread about 14%; 250 cost
  nothing measurable. (02 §3)
- Base throughput: 4×4 b=1 TLS gives 240.8 tasks/s [231–243]; 16×1 b=1 TLS gives 224.3
  [200–233]; 4×4 b=50 gives 836.5. Spread runs 2–18% at 3 repeats. (04 §A)
