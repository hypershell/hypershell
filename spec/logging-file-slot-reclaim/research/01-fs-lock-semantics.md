# Filesystem advisory-lock semantics and log-slot proliferation

## Summary

The slot-claim code in `src/hypershell/core/logging.py` (`_try_lock` → `claim_file_slot`)
catches a **bare `OSError`** from `fcntl.flock(LOCK_EX|LOCK_NB)` and collapses every failure
into one meaning: "contended, try the next slot." That is wrong. For `LOCK_NB`, **only
`EWOULDBLOCK`/`EAGAIN` means "a live sibling holds the lock."** Every other `OSError`
(`ENOSYS`, `ENOLCK`, `EOPNOTSUPP`/`ENOTSUP`, `EINVAL`) means "locking is unavailable or
failed on this filesystem" — a condition under which falling through to a new slot is exactly
the wrong response. On Lustre and NFS-backed filesystems these non-contention errnos are the
normal case, which is why the canonical `client-<host>.log` is never reclaimed and `.lock` /
`-N.log` files grow without bound.

The in-source comment (logging.py:663) — *"same-host advisory locks are reliable even on
shared network filesystems"* — is the load-bearing false assumption. It is false on **both**
of Gautschi's filesystems, for two *different* reasons.

## How Python surfaces each errno

`fcntl.flock()` raises `OSError` on any failure
(https://docs.python.org/3/library/fcntl.html). The concrete subclass is chosen by errno:
`OSError`'s constructor "often actually returns a subclass … depend[ing] on the final errno"
and **`BlockingIOError` corresponds to `EAGAIN`, `EALREADY`, `EWOULDBLOCK`, `EINPROGRESS`**
(https://docs.python.org/3/library/exceptions.html). So genuine contention arrives as a
`BlockingIOError` (itself an `OSError`); unsupported/failed-locking arrives as a plain
`OSError` with a different `.errno`. Empirically confirmed on this macOS host: a contended
`flock(LOCK_EX|LOCK_NB)` raises `BlockingIOError`, `errno == EAGAIN (35)`, and here
`EWOULDBLOCK == EAGAIN`. **errno *numbers* differ across OSes** (macOS `ENOSYS`=78, `ENOLCK`=77,
`EOPNOTSUPP`=102, `ENOTSUP`=45; Linux `ENOSYS`=38, `ENOLCK`=37, `EOPNOTSUPP`=95, `EAGAIN`=`EWOULDBLOCK`=11)
— so any fix MUST compare symbolic `errno.*` constants, never integer literals.

## Filesystem × flock behavior × errno

| Filesystem / mount | flock(LOCK_EX\|LOCK_NB) behavior | errno on failure | Reclaim safe? |
|---|---|---|---|
| Local (ext4/xfs/APFS, ZFS-on-Linux local) | Works; genuine advisory lock | `EWOULDBLOCK`/`EAGAIN` only when truly held | Yes — fall-through = real contention |
| **Lustre, default (`noflock`)** | **Disabled entirely; call is rejected** | **`ENOSYS`** (every call, always) | **No** — never contention |
| Lustre `-o localflock` | Node-local kernel locks; reliable same-host | `EWOULDBLOCK`/`EAGAIN` | Yes |
| Lustre `-o flock` | Cluster-coherent across clients (NLM-like overhead) | `EWOULDBLOCK`/`EAGAIN` | Yes |
| NFS, default (`local_lock=none`) | flock **emulated as whole-file fcntl byte-range lock**, coordinated to the server via NLM/NFSv4 — NOT node-local | `EWOULDBLOCK`/`EAGAIN` if held; **`ENOLCK` if the remote lock protocol fails** | Only if NLM healthy |
| NFS `local_lock=flock` / `=all` | flock handled node-locally | `EWOULDBLOCK`/`EAGAIN` | Yes |
| NFS < 2.6.11 | flock was local-scope only (historical) | — | n/a on modern kernels |
| Windows (`msvcrt.locking`) | byte-range lock | `OSError` (`EACCES`/`EDEADLOCK`) on contention | separate taxonomy needed |

**Lustre (authoritative, Lustre Operations Manual §40.4 / mount.lustre, https://doc.lustre.org/lustre_manual.xhtml):**
- `flock` — "Enables advisory file locking support … using the flock(2) system call. This
  causes file locking to be coherent across all client nodes also using this mount option …
  imposes communications overhead."
- `localflock` — "Enables client-local flock(2) support, using only client-local advisory
  file locking. This is faster … for applications that … run only on a single node. It has
  minimal overhead using only the Linux kernel's locks."
- `noflock` — **"Disables flock(2) support entirely, and is the default option. Applications
  calling flock(2) get an ENOSYS error."**

**NFS (nfs(5), https://man7.org/linux/man-pages/man5/nfs.5.html; flock(2),
https://man7.org/linux/man-pages/man2/flock.2.html):**
- Since Linux 2.6.12, "NFS clients support flock() locks by emulating them as fcntl(2)
  byte-range locks on the entire file" (flock.2).
- `local_lock` = `none` (the default): "the client assumes that the locks are not local" —
  i.e. flock is coordinated to the server, **not** node-local (nfs.5). It can be forced
  node-local with `local_lock=flock`/`all`.
- **`ENOLCK` = "Too many segment locks open, lock table is full, or a remote locking protocol
  failed (e.g., locking over NFS)"** (fcntl(2),
  https://manpages.ubuntu.com/manpages/jammy/man2/fcntl.2.html). This is the errno a flaky/
  disabled NLM (rpc.statd/lockd) or a lock-averse export produces.
- NFSv4 lock-staleness footgun: pre-3.12 an NFSv4 client that lost contact "might lose and
  regain a lock without ever being aware"; ≥3.12 subsequent I/O fails until reopen
  (`nfs.recover_lost_locks`) — relevant to same-node release *visibility* delays (fcntl(2) NOTES).

**flock vs POSIX (fcntl/lockf F_SETLK) on network FS (Q5):** flock is tied to the *open file
description* (survives `fork`, released on last close); POSIX `F_SETLK` locks are per-process
and are **released when the process closes *any* fd referring to the file**, and are not
inherited across `fork` (fcntl.2). Over NFS the "native" coordinated lock is the POSIX one
(flock is merely emulated on top of it), so `fcntl`/`lockf` is marginally more portable on
NFS — but on Lustre-default **both are governed by the same `flock` mount option and both fail
with `ENOSYS`**, so switching lock APIs does not rescue Lustre. ZFS-on-Linux is an in-kernel
POSIX filesystem and supports flock/fcntl locally via the standard VFS lock layer; when a ZFS
dataset is the backing store for an NFS-exported HPC `/home`, **the client sees NFS lock
semantics, not ZFS's** — so the NFS row above governs `/home`.

## errno-classification recommendation

Replace the bare `except OSError` with errno inspection:

- **"Genuinely held by a live sibling → advance to the next slot":** `errno` in
  `{EWOULDBLOCK, EAGAIN}` — equivalently, catch `BlockingIOError`. This is the *only* class
  that should ever trigger fall-through to `-N.log`.
- **"Advisory locking unavailable/unreliable on this FS → do NOT walk slots":**
  `errno` in `{ENOSYS, ENOLCK, EOPNOTSUPP, ENOTSUP, EINVAL, EPERM, EACCES}`. On the *first*
  such result, stop iterating and return the **canonical** path (n==1) directly (best-effort
  single-writer, accepting the lock is a no-op), rather than manufacturing 100 `.lock` files
  and a PID-suffixed log. Optionally emit one `warn` that locking is unsupported.
- **Transient:** `EINTR` → retry the same candidate.
- **Portability:** compare `e.errno` against `errno.*` symbolic names (numbers differ
  macOS↔Linux); guard `ENOTSUP`/`EOPNOTSUPP` with `getattr(errno, 'ENOTSUP', None)` since
  they are not universally distinct. Windows (`msvcrt`) needs its own contention-vs-error
  discrimination.

## Why this explains proliferation on BOTH ZFS and Lustre

The bug is a **one-way collapse of two orthogonal outcomes into one branch.** `claim_file_slot`
walks `client-<host>.log`, `-2.log`, …, taking the "first lockable slot," and treats *any*
`OSError` as "this slot is taken."

- **Lustre `/scratch` (default `noflock`):** *every* `flock` call returns **`ENOSYS`**, on
  *every* candidate, for *every* process generation. The loop therefore rejects all 100 slots
  and falls to the `-<pid>.log` branch — so no process ever "holds" the canonical slot, the
  canonical name is never reclaimed, and each process spawns fresh `.lock` + PID-suffixed
  files. Unbounded growth, exactly as observed.
- **ZFS `/home` (NFS-exported):** with the default `local_lock=none`, flock is emulated as a
  server-coordinated POSIX byte-range lock; when NLM (rpc.statd/lockd) is disabled, unhealthy,
  or the export is lock-averse, the call returns **`ENOLCK`** ("a remote locking protocol
  failed (e.g., locking over NFS)"). `_try_lock` again reads this as "contended," walks and
  fails all slots, and PID-suffixes — same unbounded growth, different errno. (Even where NLM
  works, cross-node/same-node release-visibility staleness can make a just-freed canonical
  slot briefly appear held, pushing new processes onto `-N.log` and defeating reclaim.)

Both filesystems break the same invariant — that a failed `LOCK_NB` means "a live sibling has
it" — but via `ENOSYS` (unsupported) and `ENOLCK` (protocol failure), neither of which is
contention. Because the code cannot tell "no lock service here" from "lock is held," it
manufactures a new file on every launch. Distinguishing the errno classes (fall through only
on `EWOULDBLOCK`/`EAGAIN`; otherwise reuse the canonical path) is the fix.

### Sources
- flock(2): https://man7.org/linux/man-pages/man2/flock.2.html
- fcntl(2) (ENOLCK, NFS notes): https://manpages.ubuntu.com/manpages/jammy/man2/fcntl.2.html · https://man7.org/linux/man-pages/man2/fcntl.2.html
- nfs(5) (local_lock): https://man7.org/linux/man-pages/man5/nfs.5.html
- Lustre Operations Manual (flock/localflock/noflock, default=noflock→ENOSYS): https://doc.lustre.org/lustre_manual.xhtml
- Python `fcntl`: https://docs.python.org/3/library/fcntl.html
- Python exceptions (BlockingIOError↔EAGAIN/EWOULDBLOCK; OSError errno→subclass): https://docs.python.org/3/library/exceptions.html
