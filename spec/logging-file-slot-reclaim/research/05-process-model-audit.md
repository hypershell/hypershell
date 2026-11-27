# Research 05 — Process-model / FD-inheritance lock-leak audit (H-leak)

## SUMMARY (verdict up front)

**H-leak does NOT explain the observed `client-<host>` proliferation, but it is a real *latent*
bug for the `server`/`cluster` roles.**

`initialize_logging()` runs in `main()` (`__init__.py:139`) — *before* any subcommand dispatch —
so the flock'd slot fd from `_try_lock`'s `open(path,'w')` (`logging.py:671`) is open before any
process creation. `open()` fds carry `O_CLOEXEC` (PEP 446), which closes on **exec** but **not on
fork**.

There is exactly **one fork-without-exec** in the codebase: `QueueServer.start()`
(`queue.py:385`) → `SecureManager.start` → `BaseManager.start()`, which spawns the manager
subprocess via multiprocessing's default context. No `set_start_method` exists anywhere (grep), so
on the Linux HPC target the method is **`fork`** (default through 3.13; 3.14 flips to `forkserver`).
The forked manager child runs a pure-Python loop (no exec) and therefore **inherits and shares the
role's log-slot OFD**, so that flock is released only when *both* parent and manager child close it.
This fires only in roles that run a `QueueServer`: `server`, `cluster`, and in-process
`RemoteCluster`/`AutoScalingCluster` (`remote.py:312` → `server.py:845 with self.queue:`).

**The client never forks a manager** — it uses `QueueClient.connect()` (`client.py:1219`, no fork);
its executors/collector/heartbeat are threads; its only child processes are task subprocesses
(`client.py:772 Popen(shell=True)`, default `close_fds=True` + exec) which drop the fd. So a
standalone/launched `hs client` holds its `client-<host>.log.lock` OFD solely in itself; on exit the
kernel drops the flock. FD inheritance therefore **cannot** cause non-reclaim of client slots — that
symptom points at H-conc (≥2 concurrent clients/host) and/or H-stale. The manager-child leak only
threatens `server`/`cluster` slots, and only when that child is orphaned (parent SIGKILL/OOM). The
evidence (clean `cluster-login01.log`, no `-2`) shows clean shutdowns normally release it.

---

## 1. WHEN `initialize_logging()` runs vs. process creation

- `main()` calls `initialize_logging(role=role_from_command(argv[0]...))` at
  **`__init__.py:139`**, *then* `register_handlers()` (`:140`), *then* `HyperShellApp.main(argv)`
  (`:141`). `main_x`/`hsx` (`__init__.py:146`) prepends `cluster` and routes through `main`.
- Consequence: the slot fd is acquired **before** any subcommand runs, hence **before** every
  `Popen`/fork below. Any fork that happens during a subcommand inherits an already-open slot fd.
- The lock fd is created by `_try_lock`: `handle = open(path, mode='w')` (**`logging.py:671`**),
  `fcntl.flock(handle.fileno(), LOCK_EX|LOCK_NB)` (`:673`). The handle is appended to the
  module-global `_slot_locks` (`logging.py:698`, `:710`) and **held for the life of the process** —
  never closed. There is only one live-ness signal: the flock on that OFD.

## 2. Python defaults that matter here

- **PEP 446:** fds from `open()` are non-inheritable (kernel `O_CLOEXEC` / `FD_CLOEXEC` set).
  `O_CLOEXEC` acts on **exec only**; a plain **fork** leaves the fd open in the child, and the child
  **shares the same OFD**. A flock is a property of the OFD and is released only when **all** fds
  referring to it are closed. So a fork-without-exec descendant keeps the flock alive after the
  logical owner exits.
- **`subprocess.Popen` defaults `close_fds=True`** → in the child, fds ≥3 (except stdio/`pass_fds`)
  are closed before exec; combined with `O_CLOEXEC` this guarantees the slot fd is gone in any
  exec'd child.
- **multiprocessing default start method** (no override in this repo): Linux `fork` through 3.13
  (3.14 → `forkserver`); macOS/Windows `spawn`. `fork` is the leak-relevant case and is the Gautschi
  (Linux) default.

## 3. Fork/exec inventory after logging init

| Site | Mechanism | Execs? | Inherits slot fd past owner? |
|------|-----------|--------|------------------------------|
| `queue.py:385` `BaseManager.start()` (via `QueueServer.start`) | multiprocessing `fork` (Linux) | **No** (Python loop) | **YES — shares the role's slot OFD** |
| `client.py:772` task `Popen(shell=True)` | fork+exec, `close_fds=True` | Yes | No (closed at exec) |
| `remote.py:314` `Popen(client_argv)` (RemoteCluster) | fork+exec, default `close_fds` | Yes | No |
| `remote.py:533` `Popen(launcher)` (AutoScaler.scale) | fork+exec, default `close_fds` | Yes | No |
| `ssh.py:311` `Popen(ssh argv)` (SSHCluster) | fork+exec, default `close_fds` | Yes | No |
| `config.py:91`, `template.py:201` | fork+exec | Yes | No |

Only the multiprocessing `BaseManager` child fork-without-exec retains the fd.

## 4. Role-by-role

- **`server`:** `initialize_logging('server')` claims `server-<host>.log.lock`; `ServerThread`
  runs `with self.queue:` (`server.py:845`) → `QueueServer.start()` (`queue.py:385`) forks the
  manager child, which **inherits `server-<host>.log.lock`'s OFD**. Released on clean shutdown when
  BaseManager terminates the child; leaked if the child is orphaned (parent SIGKILL/OOM).
- **`cluster` (Local):** single process, role `cluster`. Server runs in-process, so the same
  BaseManager fork inherits `cluster-<host>.log.lock`. Clients here are **threads**, so there is
  **no** `client-<host>.log` from `LocalCluster` at all.
- **`cluster` (Remote/AutoScaling):** cluster process runs `ServerThread` in-process (same manager
  fork, inherits `cluster-<host>.log.lock`) and then `Popen(launcher/client_argv)` — those exec, so
  launched `hs client` processes get **clean fd tables** (no cluster-slot fd).
- **`client`:** `QueueClient.connect()` (`client.py:1219`) — **no fork**. Threads only
  (`client.py:1263-1267`). Task subprocesses (`client.py:772`) exec with `close_fds=True`. The
  `client-<host>.log.lock` OFD lives **only** in this process → released on its death.
- **`submit`:** no fork; `submit.py` only imports `AuthenticationError`.

## 5. VERDICT and minimal fix

**No fork/lingering-parent/shared-OFD path exists that would prevent release of a *client* slot
lock.** Client processes never fork a manager and their only children exec with `close_fds=True`.
This refutes H-leak as the cause of the reported `client-<host>` non-reclaim/proliferation and
shifts weight to **H-conc** (genuinely ≥2 concurrent clients per host → correct `-2`, never
consolidated/reaped) and **H-stale**.

**However, a genuine FD-inheritance leak exists for `server`/`cluster` slots:** the fork-without-exec
`BaseManager` manager child (`queue.py:385`) shares the role's `*.log.lock` OFD. If that child is
orphaned (parent OOM/SIGKILL before BaseManager's `Finalize` terminates it), the flock is **not**
released though the owner is gone → next generation falls to `server-<host>-2.log`. The evidence
(clean `cluster-login01.log`, no `-2`) is consistent with clean shutdowns normally releasing it, so
this is latent rather than the observed symptom.

**Minimal hardening (recommended, not the root-cause fix):** close the inherited `_slot_locks`
handles inside the manager child. `SecureManager.start` already injects the `_tls_bootstrap`
initializer that runs *in the child after fork* (`queue.py:243-247, 278`); extend that initializer
(and add one for the non-TLS branch) to `for h in _slot_locks: h.close()`. Since the child runs it
post-fork, only the parent then holds the flock, so the lock releases exactly when the logical owner
exits. **`os.set_inheritable(False)` is useless here** — it only acts on exec, and the manager child
is fork-without-exec; the fd must be actively closed in the child (or the OFD kept single-holder).
