# Research digest — logging-file-slot-reclaim

Consolidated decisions from briefs [01](01-fs-lock-semantics.md) (FS lock semantics),
[02](02-code-trace-signatures.md) (code trace + failure signatures),
[03](03-fix-surface-and-tests.md) (fix surface + tests). Where briefs disagreed, the single
recommendation is stated here.

## Root cause (R1) — confirmed

`_try_lock` (`core/logging.py:669-677`) catches a **bare `OSError`** from
`fcntl.flock(LOCK_EX|LOCK_NB)` and returns `None`, which `claim_file_slot` treats as "slot
contended → advance to the next `-N` slot." This **conflates two orthogonal outcomes**: a
genuine lock *conflict* (a live sibling holds it) vs. locking being *unavailable/broken* on the
filesystem. For a non-blocking flock, **only `EWOULDBLOCK`/`EAGAIN` means "a live sibling holds
it"** (surfaced by Python as `BlockingIOError`, an `OSError` subclass). Every other errno means
"no usable lock here."

On the two Gautschi filesystems the non-contention errno is the *normal* case:
- **Lustre `/scratch`** default mount is `noflock`; the Lustre manual states flock(2) then
  returns **`ENOSYS`** on *every* call. → every generation, every candidate errors.
- **NFS-exported ZFS `/home`** (default `local_lock=none`) emulates flock as a server-coordinated
  POSIX byte-range lock; a disabled/flaky NLM (rpc.statd/lockd) returns **`ENOLCK`** ("a remote
  locking protocol failed (e.g., locking over NFS)"). Even where NLM works, release-visibility
  staleness can make a just-freed slot briefly appear held.

Both are swallowed as "contended," so the canonical `client-<host>.log` is never reclaimed and
files pile up. The in-source comment at `logging.py:663` — *"same-host advisory locks are
reliable even on shared network filesystems"* — is the load-bearing false premise.

**Does it reproduce locally?** No. On a working-flock FS (ext4/xfs/APFS/local ZFS) the OS
releases the held flock on process exit, and the existing loop already reclaims the canonical
slot and appends (`mode='a'`) — proven by `tests/test_logging.py::test_slot_reclaimed_after_owner_dies`.
**The defect only surfaces where flock errors or goes stale**, which is why it shows up on the
cluster and not on the maintainer's laptop.

## Failure signature (to confirm against the real `ls` on Gautschi)

Same defect, two distinguishable signatures (brief 02):
- **Unsupported (Lustre `ENOSYS`, NFS `ENOLCK` every call):** all 100 candidates error →
  loop exhausts → PID-suffix branch. On disk: a growing pile of non-sequential
  `client-<host>-<pid>.log`, a **fixed 100** `.lock` sidecars, and an *"Exhausted 100 log-file
  slots"* warning on **every** launch.
- **Stale/spurious-conflict:** gen K lands on slot K → growing **sequential** `client-<host>-2.log`,
  `-3.log`, … plus growing numbered `.lock` files, canonical present but never reused.

Both are cured by the same fix. (A quick `ls "$log_dir"` on Gautschi will tell us which — useful
confirmation, not a blocker.)

## Second proliferation source (R5/R8)

Both the `_LOCKING = False` branch and the "exhausted `max_slots`" branch currently return
`f'{root}-{os.getpid()}{ext}'` — **one file per PID**. The no-locking fallback is itself a
proliferation bug. Fix: the `_LOCKING = False` and *unsupported-errno* paths degrade to the
**canonical** per-host path (reuse + append), not a PID suffix. (The genuine-exhaustion case —
100 *real* concurrent live siblings — legitimately keeps the PID suffix; with errno
discrimination, an unsupported FS degrades at n=1 and never reaches exhaustion, so that branch
is now only ever hit by true 100-way concurrency, where a distinct file is the correct
single-writer choice. This refines brief 03's "replace both PID-suffix returns".)

## The fix (hybrid — resolves GOAL Q2 / R5) — all in `core/logging.py`, no new config knob

1. **Discriminate errno in `_try_lock`** → three outcomes: LOCKED (return handle) / CONFLICT
   (`errno ∈ {EAGAIN, EWOULDBLOCK}`, i.e. catch `BlockingIOError`) → advance the slot loop,
   preserving strict single-writer (**R7**) / UNSUPPORTED (any other `OSError`) → stop walking.
   Compare **symbolic `errno.*`** constants (numbers differ macOS↔Linux). `EINTR` → retry same
   candidate. Keep the `msvcrt` branch (conflict-only discrimination) and the `_LOCKING=False`
   guard.
2. **Degrade to canonical on UNSUPPORTED / `_LOCKING=False`** — return the n=1 per-host path
   (append), emit **one** warning that advisory locking is unavailable. Bounded to one file per
   host; accepts possible interleave — exactly the GOAL's resolved R5 (**R5**, **R8**).
3. **Reclaim canonical (R2/R3)** — the existing loop is already correct on working flock; the
   degrade extends the same reuse-and-append behavior to lockless filesystems. No new mechanism.
4. **Opportunistic orphan reap (R6)** — performed **only by the process that just won the
   canonical (n=1) lock** (natural one-reaper-per-host serialization). Scan siblings matching the
   **exact `-N` slot shape** (`client-<host>-<int>.log`); for each, `_try_lock` its sidecar — if
   LOCKED it is an orphan → `os.remove` the orphaned `-N.log` data file; if CONFLICT/UNSUPPORTED,
   skip. Mirror `recover_interrupted_compression`'s prefix-scoping discipline; **never** touch
   rotated forms (`client-<host>.<N>` / `.YYYYMMDD`, dot-separated), the `.lock` sidecars, or the
   `main` role.

### The one real footgun (brief 03) — carried into design, not hand-waved

Unlinking a `.lock` sidecar is a **POSIX inode-reuse race**: process A can hold `flock` on the
old inode while B does `open(mode='w')` and gets a *new* inode + a *new*, independent lock →
two writers to one log. **Decision:** reap only the `-N.log` **data** file (the real disk cost);
**leave the 0-byte `.lock` sidecars** — their count is bounded once `-N` proliferation stops.
(Reaping an orphan slot's *own* rotated children, e.g. `client-<host>-2.3`, and GC of stale
sidecars are explicitly deferred — see GOAL Non-goals / this plan's Risks.)

## Test technique (brief 03)

- **Unit:** `monkeypatch.setattr('hypershell.core.logging.fcntl.flock', …)` (or patch `_try_lock`)
  to raise `OSError(errno.EOPNOTSUPP)` / `OSError(errno.ENOSYS)` vs `OSError(errno.EAGAIN)`, and
  assert the claim **degrades to canonical** vs **falls through to `-2`**. Toggle the no-lock
  path via `monkeypatch.setattr('hypershell.core.logging._LOCKING', False)` (patch the **module
  attribute**, not a test-module `from … import` copy).
- **`_slot_locks` leaks:** it is a never-cleared module global; add an **autouse fixture** in
  `test_logging.py` that closes handles and clears it after each test (existing tests only dodge
  this via unique `tmp_path`).
- **Reap:** seed orphan `-2.log`/`-3.log` (no live locker) → assert removed after a canonical
  claim; seed a *locked* `-2.log` → assert retained; assert rotated (`client-host.1`,
  `client-host.20260723`) and `main.log` untouched.
- **Bound (R4) / integration:** N serial subprocess generations (or the CLI in `temp_site` with
  `HYPERSHELL_LOGGING_FILE=enabled`) → `glob('client-*.log')` count stays bounded (==1 on the
  degrade/reclaim path). Markers `@mark.unit` / `@mark.integration`.

## Invariants (brief 02/03)

- **§11** — no client-argv change: the log path is **not** forwarded to launched clients (all
  three argv builders omit it; each client derives `client-<own-host>.log`). The whole fix lives
  in `core/logging.py`; the per-mode argv builders are untouched.
- **§5** — reap runs at claim time (in `initialize_logging`, beside `recover_interrupted_compression`);
  it must touch only exact `-N.log`, never the rotation namespace managed under `FILE_LOCK` by the
  compression thread, nor the freshly-claimed file.
- **§12** — internal-only fix ⇒ **no** new config knob ⇒ no `docs/_include` / `share/` churn.
- **R8** — keep `msvcrt`; `main` role stays un-host-scoped and un-reaped; degrade path is still
  host-scoped, preserving cross-host safety.
