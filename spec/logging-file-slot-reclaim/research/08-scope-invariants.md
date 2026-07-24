# Research 08 — Re-scope, invariant gate, cross-platform & testability

Read-only survey for `/hs-plan`, reflecting the **post-refutation** evidence (identical behavior
on ZFS `/home` and Lustre `/scratch`; clean canonical name won, one `-2` slot, **no** PID pile,
**no** 100-lock pile, **no** "Exhausted" warning). That evidence kills the committed ENOSYS/
exhaustion diagnosis (PLAN §1, TECH P1) and points at an **FS-independent** cause — a lock/reclaim
leak — not errno conflation. This brief re-scopes the two architectures brief 07 frames
(**KEEP-slots** with pid-stamped liveness vs **SINGLE-file** per host), gates them against
`invariants.md`, and pins the compat + test surface.

## 0. The three questions, answered (they anchor both architectures)

- **Q1 — how do we know the canonical `.lock` is held by a *live* process?** Today: we do **not**
  track pids at all — the flock is the only signal, and the sidecar is written `open(mode='w')`
  and left 0-byte (`logging.py:671`, never written into). Proposal: keep **flock as the primary
  and authoritative signal** (it self-releases on death, works cross-generation), and *add* a
  **pid line written into the sidecar** as a **secondary/fallback** liveness signal used only
  where flock is unavailable/unreliable or to make reap trustworthy. Liveness check = a single
  injectable seam `_pid_alive(pid)` (POSIX: `os.kill(pid, 0)` → `ProcessLookupError` ⇒ dead,
  `PermissionError` ⇒ alive; optionally `/proc/<pid>` on Linux). Flock must stay primary so a
  new pid-stamping process and an old non-stamping one interoperate during a rolling upgrade.
- **Q2 — if we can't unlink `.lock` files, how do we ever reclaim?** Reclaim ≠ unlink. Reclaim =
  **re-acquiring the flock on the persistent sidecar** (and overwriting the pid stamp). This
  already works on a healthy FS: the sidecar file persists, the OS drops the flock on owner death,
  and the next `_try_lock('client-host.log.lock')` succeeds → canonical returned + appended
  (`mode='a'`, `logging.py:377`; proven by `test_slot_reclaimed_after_owner_dies`). Unlinking a
  sidecar is a **POSIX inode-reuse race** (A holds flock on the old inode while B `open('w')`s a
  new inode → two independent locks → two writers); brief 03 §2 already banned it. So the sidecar
  is never unlinked; only its lock is re-acquired.
- **Q3 — when we reap a `-N.log`, do we append its contents onto the canonical file?** Per the
  locked clarification *"reclaim ⇒ APPEND (preserve history)"* (GOAL Q1/R3), reaping must **concat
  the orphan `-N.log` onto the canonical file, then remove the `-N.log`** — a bare `os.remove`
  (as PLAN (d)/TECH P2 currently specify) *discards* that generation's history and contradicts
  R3's intent. This upgrades reap from *delete* to *concat-then-delete*, which is the §5 crux
  (below).

## 1. Revised phase breakdown (vertical slices per architecture)

### Architecture A — KEEP-slots + pid-stamped liveness + trustworthy reap

Keeps the `-N` scheme and R7 (distinct files for genuinely-concurrent same-host writers). No
GOAL change.

- **P1 — Close the fd-inheritance lock leak (likely root cause; ship-alone-able).**
  `SecureManager(BaseManager).start()` (`core/queue.py:273`, `:385`) forks a manager process
  **after** `initialize_logging` (`__init__.py:139`); on Linux's default **`fork`** start method
  the child inherits a **dup of the slot-lock OFD**, so the flock is *not* released when the
  original owner exits (a flock releases only when **all** fds on that OFD close) — FS-independent,
  and it explains "canonical never reclaimed" + `-2` persisting. This also explains **no local
  repro**: macOS multiprocessing defaults to **`spawn`** (exec ⇒ `O_CLOEXEC` closes the fd ⇒ no
  leak), Linux to **`fork`** (leak). Fix: `os.register_at_fork(after_in_child=…)` to close/clear
  the `_slot_locks` handles in forked children (and audit `LocalCluster`, resource/semaphore
  trackers). `Popen` sites (`remote.py:314,533`, `ssh.py:311`, `client.py:772`) exec → CLOEXEC,
  already safe. **Vertical slice: P1 alone may fully restore reclaim.** Appetite: **small**.
- **P2 — pid-stamped liveness sidecar (R5 lockless fallback + reap trust).** Write `pid` into the
  sidecar on claim; add `_pid_alive` seam. Used only where flock is unavailable/unreliable
  (`_LOCKING=False`, or a probe that can't distinguish stale-release) to arbitrate reclaim. Small.
- **P3 — trustworthy reap + concat-on-reclaim (R6/R3).** Canonical-lock winner scans exact
  `client-<host>-<int>.log` slots (regex on `basename_without_ext` prefix, mirroring
  `recover_interrupted_compression`, `logging.py:513-533`); an orphan = flock-probe LOCKED **or**
  pid-dead → **concat its bytes onto canonical, then remove** the `-N.log`. Never touch sidecars,
  the rotation namespace, the fresh file, or `main`. Medium.
- **P4 — end-to-end bounded-count + regression (R4/R8).** Integration: serial + *overlapping*
  generations on one host; assert bounded count and cross-host/`main` unregressed. Small.

**Total 4 phases; appetite starts small (P1), grows to small-medium.** Under the circuit-breaker.

### Architecture B — SINGLE-file per host (all same-host writers share one file)

Collapses `-N` entirely: one canonical `client-<host>.log`/host, append-only. R2/R3/R4/R5 hold
*by construction*; reap reduces to a one-time legacy consolidation.

- **P1 — Collapse slot walk → single canonical path (append; one warn if lockless).** Small.
- **P2 — Cross-process rotation-owner election.** *New hazard:* multiple live processes now share
  one file, each with its own `RotatingFileHandler` calling `os.rename`/compress under a
  **process-local** `FILE_LOCK` (`logging.py:355,400`) — which does **not** serialize across
  processes. Rotation must be gated by the **cross-process flock** (only the flock holder rotates;
  others append without rotating). This is genuinely hard. Medium-large.
- **P3 — Legacy `-N.log` one-time concat/migrate + reap.** Medium.
- **P4 — e2e bounded-count + rotation-under-concurrency integration test.** Medium.

**Appetite: big.** Two blockers: (i) it **violates R7 as written** ("distinct files for
genuinely-concurrent same-host writers where locking works") → a **GOAL amendment / human gate**;
(ii) it introduces a cross-process rotation race the slot scheme never had (single-writer
guaranteed a single rotator). Simpler for *counting*, costlier for *correctness*.

**Recommendation:** pursue **Architecture A**. It preserves R7, needs no GOAL change, and its P1
is a credible complete fix for the observed symptom. Keep SINGLE-file as an explicit *opt-in* only
if the maintainer accepts interleave — and note that making it opt-in means a config knob (§12).
Circuit-breaker: A is ≤4 phases; B trending toward the ~8 reconsider line once R7/rotation are
paid for.

## 2. Invariant gate (`.agents/factory/invariants.md`)

- **§5 (compression thread + `FILE_LOCK`).** The **concat-on-reap** (Q3) and reclaim-append are
  the real §5 exposure. `FILE_LOCK` is **process-local**, so it does *not* protect a concat that
  races another process's `rotate()`; and `recover_interrupted_compression` + the compression
  thread start at `logging.py:818-820,837`. Mitigation: perform reap/concat **before**
  `queue_listener.start()`/`compression_thread.start()`, or hold `FILE_LOCK` **and** the canonical
  flock during concat, and only touch `-N.log` we have flock-probed/pid-confirmed as orphaned —
  never the dot-separated rotation namespace (`client-host.1`/`.YYYYMMDD`). No new thread; no new
  shared mutable state beyond `_slot_locks`. Architecture B's cross-process rotation is a **harder
  §5 violation** requiring flock-gated rotation.
- **§11 (no shared client-argv builder).** **Verified by grep:** the log path is **not** forwarded
  to launched clients. `client_args` in `RemoteCluster` (`remote.py:281-306`), `AutoScalingCluster`
  (`remote.py:813-835`), and `SSHCluster` (`ssh.py:275-300`) carry only
  `--no-confirm/--capture/--monitor/-C/-M/-T/-W/-R/--no-tls`; `LocalCluster` embeds threads (no
  argv). Every client derives `client-<own-host>.log` client-side. **Neither architecture touches
  any argv builder** — pid-stamping and single-file are both purely client-side. ✔
- **§12 (same-commit docs/completions).** A **pid-stamp is behavior-only** (sidecar contents change
  0-byte → `"<pid>\n"`; no new CLI flag, no new config key) ⇒ **no** `docs/_include/*.rst` or
  `share/` edits required (matches the existing TECH §12 note). **A lock-mode knob**
  (`logging.file.lock = auto|flock|pid|off|shared`) — or making SINGLE-file opt-in via config —
  **is a new config key** and would force, in the same commit: `docs/_include/config_param_ref.rst`
  (the `[file]` block, lines 48-74), regeneration of `share/man/man1/{hs,hsx,hyper-shell}.1` (hsx.1
  is a copy of hs.1), and the bash+zsh completions under `share/`. **Recommendation: stay
  knob-free** (auto behavior: flock-primary → pid-fallback → canonical-append degrade) to avoid the
  §12 burden and keep appetite small. A non-gated behavioral note in `docs/logging.rst` is good
  practice but not the §12 same-commit trigger.

## 3. Cross-platform & backward-compat

- **Windows (`msvcrt`, no `/proc`).** `msvcrt.locking` gives a mandatory byte-range lock released
  on close/exit — keep it **authoritative**. `os.kill(pid, 0)` is unreliable on Windows (signal 0
  unsupported); the `_pid_alive` seam must degrade to "trust flock only" (or best-effort
  `OpenProcess`) and **never crash**. Windows lacks `fork`, so the P1 leak/`register_at_fork` fix
  is a POSIX-only concern and must be guarded.
- **`_LOCKING = False` path** (neither `fcntl` nor `msvcrt`). Today returns a PID suffix
  (`logging.py:713`) — itself a proliferation bug. With **no flock at all**, the pid-stamp is the
  **only** liveness signal, but `os.kill`/`signal` may also be absent on such a platform. Safest
  R5/R8-compliant behavior: **degrade to the canonical per-host path (append)**, bounded to one
  file/host; pid-liveness arbitration is opportunistic on top.
- **Legacy 0-byte sidecars (millions on disk).** A pid scheme **must** treat an **empty /
  non-integer / legacy** sidecar as **`unknown → reclaimable-if-flock-free`**: read defensively,
  never raise on unparseable contents, and fall back to the flock probe. Reap (R6) of pre-existing
  buggy-version `-N.log` files must likewise work off the flock probe when no pid stamp exists.
  **Forward/rolling-upgrade interop:** an old (non-stamping) process only flocks; a new process
  must therefore keep flock **primary** and treat the pid stamp as advisory, so mixed fleets never
  double-write.

## 4. Testability

- **pid-liveness (deterministic).** Introduce a single seam `_pid_alive(pid) -> bool` and
  `monkeypatch.setattr('hypershell.core.logging._pid_alive', fake)` (or patch
  `hypershell.core.logging.os.kill` to raise `ProcessLookupError` for "dead" / return for
  "alive"). Seed sidecars with a known-dead sentinel pid; assert reclaim vs skip. Avoids real
  `/proc` and is cross-platform. `@mark.unit`.
- **fd-leak regression (the crux, pins P1).** In-process claim canonical (handle in `_slot_locks`);
  `os.fork()` a child that sleeps holding the inherited fd; parent closes/clears `_slot_locks`;
  from a **fresh subprocess** (no shared globals) assert a claim returns the **canonical** path
  (leak ⇒ pushed to `-2` → red before fix; green after `register_at_fork`). Use raw `os.fork()`
  (not `multiprocessing`, whose start method differs macOS↔Linux); `skipif` Windows.
  `@mark.integration`.
- **`_slot_locks` (+ any new module globals) cleanup.** Autouse fixture in `tests/test_logging.py`
  that, after each test, closes every handle in `hypershell.core.logging._slot_locks` and clears
  the list (existing tests only dodge leakage via unique `tmp_path`; without the fixture a held
  slot-1 pushes later claims to `-2` → flakes). Reset the pid-stamp seam/state too.
- **concat-on-reap (Q3/§5).** Seed unlocked orphan `client-h-2.log`/`-3.log` with known bytes, a
  *locked* `client-h-4.log`, rotated `client-h.1`/`client-h.20260723`, and `main.log`; after a
  canonical claim assert the orphans' bytes are **appended to canonical** then removed, and
  everything else retained. `@mark.unit`.
