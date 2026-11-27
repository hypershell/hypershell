# Research 03 — The manager child's bootstrap duties, and what else leaves with the child

Base: `b819de6`. CPython line numbers are from 3.14.6 `multiprocessing/managers.py` (`mp/managers.py`
below); the shape is the same on 3.11+. Experiments ran on macOS with the project venv (3.14).

## 1. TLS context installation

**Storage.** `core/tls.py` keeps one process-global slot: `_PROCESS_CONFIG`, `_PROCESS_SERVER_CONTEXT`,
`_PROCESS_CLIENT_CONTEXT` (L215–217), plain module globals, no lock. `install_process_context(cfg)`
(L220–231) resets both cached contexts to `None` and stores `cfg` only when active (else `None`).
`get_tls_config()` (L239–241) returns the slot. `get_server_context()` / `get_client_context()`
(L244–261) raise `TLSError` if no config is installed, else build lazily from the slot and cache.
`clear_process_context()` (L234–236) is `install(None)`, used only by tests.

**Thread-safety.** Unsynchronized but benign under the GIL: the only race is check-then-build in
`get_*_context`, which can build a context twice (both valid, last write wins). The server context
is only fetched from `SecureListener.accept` (`core/queue.py` L184), i.e. serially on the one
accepter thread. An `install` racing an accept can at worst rebuild the context once.

**Who installs today.** Only two callers in `src/`: `_child_bootstrap` in the manager child
(`core/queue.py` L252–253) and `SecureManager.connect` (L289–293, only when `self._tls` is set).
Nothing in `server.py` or `cluster/*` installs. So a standalone `hs server` process has **no**
context installed in the parent. Measured consequence at `b819de6`: with TLS on, `QueueServer.shutdown()`
takes 1.00 s and the child exits `-15` (fork and spawn), while `--no-tls` shuts down in ≤0.07 s with
exit 0. Cause: the stock finalizer dials the child with `_Client` = `secure_client`
(`mp/managers.py` L663–672), which calls `get_client_context()` → `TLSError`, swallowed by
`except Exception: pass`; then `join(timeout=1.0)` → `terminate()` (L677–683). The in-process design
removes this path, so TLS shutdown should get faster, not slower (relevant to R5/R8 timing).

**Where to install after the change.** `SecureListener.__init__` binds without a context (L148–169);
the context is fetched on each accept (L184). So installation must happen before the **first
accept**, not before listener creation. Recommended: in the in-process start, when `self._tls` is
active, call `install_process_context(self._tls)` before the accepter thread starts (simplest: before
constructing `Server`, mirroring `connect` L291–292). Do not call it with `None` for `--no-tls`: with
the `pickle` serializer `SecureListener`/`secure_client` are never used, and clearing would clobber
any other user of the slot in the process.

**Repeated installs.** Idempotent in effect: each install drops the cached contexts and the next
accept/connect rebuilds from the new config (one extra cert-chain load). Cases:

- `LocalCluster` / `hsx` (`hsx` = `hs cluster`, `__init__.py` L144–146): `cluster/local.py` passes the
  **same** `TLSConfig` object to `ServerThread` (L233) and `ClientThread` (L252). The in-process
  client's `SecureManager.connect` re-installs it after the server is ready → server context rebuilt
  once, identical. No conflict.
- `RemoteCluster` / `SSHCluster` / autoscale: clients are separate processes (`Popen`), no in-process
  client, no conflict.
- Sequential servers with different certs in one process (tests): each start's install resets the
  caches, so the next accept uses the new cert — correct **provided the previous server is shut
  down** first.

**Conflict case (new with this cycle).** Before, the server's slot lived in the child and the parent's
slot belonged to any in-process client. Now they share one slot. If an in-process client installs a
config that differs from the server's — e.g. a client-only config without `cert`/`key` — the next
accept's `build_server_context` raises `TLSError` (`core/tls.py` L152–153). No current `src/` path
does this (only `LocalCluster` runs both, with one object), but concurrent servers or a leaked server
in tests would hit it. Note also that the stock `Server.accepter` only survives `OSError`
(`mp/managers.py` L190–198); `TLSError` is not an `OSError`, so it would end the accepter thread.
Options for the plan:

- **A (minimal, matches today):** install in start as above; HyperShell's own accept loop catches
  per-accept exceptions broadly (log, continue) instead of inheriting the stock `except OSError`.
- **B (pins per server):** build the server context once at start from `self._tls` and hand it to
  the listener (e.g. an optional per-instance context on `SecureListener`, falling back to
  `get_server_context()` when unset so the existing listener tests keep their behaviour). Still
  install the slot so client-side calls in the server process work. Removes the shared-slot
  coupling. Wire and handshake unchanged either way.

Building eagerly at start (either option) also surfaces a bad cert/key in the caller's thread at
`start()` instead of at first accept; a behaviour nuance the plan may adopt or defer.

**`tests/test_queue_tls.py` (must pass unmodified).** Fixture `install_tls` installs/clears the slot
(L50–57). `TestSecureListenerSecureClient` (L441–504) runs `SecureListener` + `secure_client` in one
process, relying on the global slot → any listener change must keep the `get_server_context()`
fallback. `test_secure_manager_subprocess_roundtrip` (L507–575, `integration`) calls
`SecureManager(address=('127.0.0.1', 0), …, tls=cfg)`, `register('get_value', callable=…)`,
`start()`, reads `manager.address`, then `manager.shutdown()`, inside a **spawned** subprocess that
already installed the context (L510). It therefore constrains `SecureManager.start` itself: whether
start stays forking (in-process serving only in `QueueServer.start`) or becomes in-process, `address`
must be the real bound port after `start()`, `shutdown()` must return, and the subprocess must exit
(serve threads daemon). Both options above satisfy it.

## 2. Log-slot locks

`core/logging.py`: file logging claims a per-host "slot" by taking a non-blocking exclusive `flock`
on a `<log>.lock` sidecar (`acquire_lock` L810–825, `try_lock` L835–848, `claim_file_slot` L861–879,
called from `initialize_logging` L1028/L1043, itself called once from `hypershell/__init__.py` L139).
Held handles live in the module list `slot_locks` (L854) for the process's life;
`finalize_logging` (atexit, L884–911) unlinks sidecars while still holding the lock.
`close_inherited_slot_locks()` (L914–930) pops and closes handles **without** unlinking.

Why only the fork needed it: a `flock` belongs to the open-file-description, which a fork-without-exec
child shares; the lock is released only when the last descriptor closes, so a forked manager child
would keep the slot held after the true owner died (comment L771–773). Under spawn the child is a
fresh interpreter, `initialize_logging` is never called there, `slot_locks` is empty → already a
no-op. With no child, **nothing needs to run in its place**.

Other fork users in `src/`: none without exec. `subprocess.Popen`/`run`/`check_output` (task
execution `client.py` L772, `cluster/ssh.py` L311, `cluster/remote.py` L314/L533, `config.py` L91,
`core/template.py` L201) exec immediately, default `close_fds=True`, no `pass_fds`/`preexec_fn`, and
Python opens files non-inheritable — so slot fds never reach them. No other `multiprocessing`
process/queue/pipe use remains in `src/` after `JoinableQueue` goes (`submit.py`/`client.py` import
only `AuthenticationError`, `cpu_count`).

**Keep or delete.** Keep `close_inherited_slot_locks`: it is in `core/logging.py`'s `__all__` (L53)
and `tests/test_logging.py` `_fork_lock_probe` calls `log.close_inherited_slot_locks()` (L352; R8
forbids changing it). Its docstring (L916–922) names the queue-manager subprocess; reword to a generic
"fork-without-exec child" helper. `_child_bootstrap` (`core/queue.py` L243–255) is private: delete it
if `SecureManager.start` becomes in-process; if `SecureManager.start` keeps forking for direct
`SecureManager` users (the TLS integration test), it stays as that path's initializer. The import of
`close_inherited_slot_locks` into `core/queue.py` (L28) goes with it.

## 3. Other things the child did implicitly

| Child behaviour | Needed after? | Where |
|---|---|---|
| `_run_server` sets `SIGINT` → `SIG_IGN` (`mp/managers.py` L588–589) so Ctrl-C to the group does not kill the manager | No. Signals are delivered to the main thread only; serve threads never see `KeyboardInterrupt`. Make accepter/handler threads daemon (as stock, L175–177, L196–197) so an interrupted exit is not blocked by them; the main thread's existing path (`ServerThread.stop` → `with self.queue` exit) drives shutdown. Do not touch process signal dispositions. | serve loop |
| Parent's `util.Finalize` `shutdown` (L575–580, `_finalize_manager` L663–694): RPC `shutdown`, join, terminate, kill, `state = SHUTDOWN`, `del BaseProxy._address_to_local[address]` | Partly. `QueueServer.__exit__` calls `self.shutdown()` (L416), which today exists only because `start()` set it → the new start must provide a `shutdown` (method or attribute) that stops the loop directly (no self-RPC), closes the listener (R5 port reuse), sets `_state.value = State.SHUTDOWN`, and pops `BaseProxy._address_to_local[server address]` — in `LocalCluster` the in-process client's per-thread proxy connections are cached there, and a later server on the same port in the same process would otherwise reuse dead connections. Join/terminate/kill have no equivalent. An atexit `Finalize` is optional (daemon threads and socket close at exit already cover it). | `SecureManager`/`QueueServer` start/shutdown |
| Child re-imports under spawn: `core/sys.py` `HYPERSHELL_PYTHONPATH` rewrite of `sys.path` (L37–69), config load, `data/core.py` module-level engine config (L199ff), logging module import | No — duplicates of what the server process already did at startup. Nothing to replace. | — |
| Under fork, the child inherited the logging `FastQueueHandler` but not its `QueueListener` thread (`core/logging.py` L646, L1089–1090), so file-log records emitted in the child were never written | Goes away; serve-loop log records now reach the real handlers. Avoid logging from the serve loop at high rates (it shares the server's log queue). | — |
| `multiprocessing.active_children()` | Measured at base: 0 → 1 after `start()` under fork and spawn. After the change it must stay 0 (R1). No code depends on it; `BaseManager.join` uses `self._process` — set `_process = None` or leave `join` unused. | R1 test |
| `JoinableQueue` named semaphores and the `resource_tracker` | Under a non-fork start method (spawn: macOS default; forkserver: Linux default on 3.14), `JoinableQueue` uses named `SemLock`s registered with the resource tracker (`mp/synchronize.py` L52–53, L74–79). Measured: creating one `JoinableQueue` under spawn launched the tracker process (pid set, semaphore name `/mp-…`); under fork, no tracker and unnamed semaphores. Spawn/forkserver `Process.start` also needs the tracker, and forkserver starts its own server process. Removing both the child and `JoinableQueue` therefore removes the tracker (and, on Linux 3.14, the forkserver) from the server process — these are OS children not counted by `active_children()`; count them for R9's process/fd footprint. Each `JoinableQueue` also held a pipe pair and, once used, a feeder thread. | R9 benchmark |

## 4. `initializer` / `initargs`

No caller anywhere in `src/`, `tests/`, or `docs/` passes them. `QueueServer.start` (L379–392) already
accepts and silently drops them (calls `super().start()` bare at L391); `SecureManager.start`
(L281–287) forwards them to the child via `_child_bootstrap`. Keep both signatures for compatibility;
in-process, run a given `initializer(*initargs)` once in the calling thread before serving (the
closest analogue to "before the manager serves"), or document it as accepted-and-ignored as
`QueueServer` does today. Also refresh the "server subprocess" wording in `core/tls.py` docstrings
(L80–82, L214, L224) and `SecureManager.start`'s docstring.
