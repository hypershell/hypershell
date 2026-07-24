# Research 06 — Liveness-signal design (flock-independent slot reclaim)

Scope: design a slot-liveness signal that does **not** depend on flock's release *visibility*,
so a canonical per-host slot is reclaimed exactly when its intended owner is dead — even when
flock disagrees (H-leak) or goes stale (H-stale). Grounded in `core/logging.py` as it stands;
the refuted ENOSYS/exhaustion theory is not relied on anywhere below.

## The enabling invariant — same-host guarantee

`default_file_for` bakes `HOSTNAME_SHORT` into every distributed slot name
(`logging.py:654-657`, `stem = f'{role}-{HOSTNAME_SHORT}'`). Therefore **every process that ever
competes for `client-a306.log` runs on a306.** A pid written into that slot's sidecar is a
*local* pid: it can be checked with `os.kill(pid, 0)` / `psutil.pid_exists(pid)` on the
claiming host, with no cross-host RPC. This is what makes a pid-record a usable liveness
signal, and it is the property the current 0-byte sidecar throws away (nothing is written into
it — `_try_lock` at `logging.py:669-677` only opens+flocks; no pid is recorded).

## Dependency finding (settles the "psutil vs stdlib" question)

**`psutil>=7.0.0` is already a hard, non-optional dependency** (`pyproject.toml:37`) and is
already imported in `core/resource.py:17` (`from psutil import Process, NoSuchProcess, …`). The
EPEL/RHEL low-floor concern does **not** apply — the floor is already committed and the project
already ships psutil on every platform. So the cross-platform liveness problem (Linux has
`/proc`, macOS has neither `/proc` nor `os.kill(pid,0)` semantics we want to hand-roll, Windows
has no `os.kill` at all) is solved uniformly by:

- `psutil.pid_exists(pid)` — cross-platform existence.
- `psutil.Process(pid).create_time()` — **wall-clock epoch** process-start timestamp; the
  pid-reuse defense. (Prefer this over `/proc/<pid>/stat` field-22 jiffies, which are
  since-boot and need a boot-id to interpret — with psutil's epoch value **no boot-id is
  needed**; it is already reboot-safe.)
- `NoSuchProcess` → dead; `AccessDenied` → a live process owned by another user (conservatively
  treat as **alive**, never steal — same-user is the HPC norm but this is the safe default).

We keep a raw-stdlib `os.kill(pid, 0)` path only as an in-extremis fallback if `psutil.Process`
raises unexpectedly; it is not the primary mechanism.

## The sidecar record

Replace the 0-byte sidecar with one atomically-written JSON line, e.g.

```json
{"pid": 12345, "host": "a306", "create_time": 1690000000.12, "instance": "<uuid>", "v": 1}
```

- `pid` — local liveness target (same-host guarantee).
- `create_time` — from `psutil.Process(pid).create_time()`; pid-reuse defense (compare with a
  small tolerance; a mismatch means a *different* process now holds that pid → original is dead).
- `host` — `HOSTNAME_SHORT`; defense-in-depth for the `resolve_log_path` explicit-path case
  (`logging.py:716-723`) where a user's non-default path could be shared across hosts. If
  `record.host != HOSTNAME_SHORT`, we are not entitled to judge it → treat as live/advance.
- `instance` — the existing per-process `INSTANCE` uuid (`logging.py:57`). Used as the
  race tie-breaker token (write-then-reread-verify): after we claim, the record carrying *our*
  `instance` proves we own the slot.
- `v` — schema version, so a future field add is not a flag day.

## Composition with `_try_lock` / `claim_file_slot` (concrete)

Two code-level hazards must be handled or the record is silently corrupted:

1. **`_try_lock` opens `mode='w'` (TRUNCATE) — `logging.py:671`.** A *losing* probe truncates
   the sidecar to 0 bytes *before* it gets `EAGAIN` and closes, blanking the winner's record
   while the winner still holds the flock. Fix: probe with a **non-truncating** open
   (`open(p, 'a+')` or `os.open(p, O_RDWR|O_CREAT)`), flock, and only the **winner**
   `seek(0)+truncate()+write(record)+flush()` — never `close()` (closing drops the flock; the
   handle stays in `_slot_locks`, `logging.py:698`).
2. **Reader path must never truncate.** Liveness checks read the sidecar `mode='r'`; parse
   failure / empty / `v` unknown ⇒ "no live holder."

## Options

### (A) pid-record PRIMARY (record decides; flock optional/absent)

On claim: read the record; empty/legacy/stale/dead ⇒ reclaim canonical (rewrite record, append
to `client-a306.log`); live ⇒ advance to `-2`.

- **H-leak:** ✅ **fixes it.** The record stamps the *original* owner's `pid`+`create_time`. If a
  fork inherited the flock'd OFD but the original owner exited, `pid_exists(orig)` is False (or
  `create_time` mismatches) ⇒ record says **dead** ⇒ reclaim. This *deliberately disagrees with
  flock*, which still shows the slot held by the ghost OFD — exactly the intended behavior.
- **H-stale:** ✅ liveness never consults flock-release visibility, so a network-FS stale lock is
  irrelevant; the local pid check is authoritative.
- **Legit concurrency:** correct outcome (record alive ⇒ advance) **but** the *acquire* is not
  atomic — see race below.
- **pid-reuse:** ✅ `create_time` comparison.
- **Two-starters race:** ✗ weakest point. Two cold starts both read a stale/empty record, both
  decide "free," both write and open ⇒ two writers. A rename-based CAS
  (write-temp→`os.rename`→reread; back off unless the record still carries *my* `instance`)
  narrows it but cannot fully close it without an atomic acquire primitive: the reread is not
  ordered after all competitors' writes.
- **Crash safety:** ✅ stale record ⇒ next starter reclaims; nothing to leak.
- **Cross-platform:** ✅ psutil only; no flock needed.
- **Backward-compat:** ✅ legacy 0-byte sidecar ⇒ parse fail ⇒ "no live holder" ⇒ reclaim
  (aligns with R6 — those are the orphans we want to reclaim).

### (B) flock PRIMARY, pid-record as fallback (**recommended**)

1. Attempt `flock(LOCK_EX|LOCK_NB)` (non-truncating open).
2. **Acquired** ⇒ strict single-writer (R7). Write our record into the locked sidecar. Done.
3. **CONFLICT** (`EAGAIN`/`EWOULDBLOCK` ⇒ `BlockingIOError`) ⇒ flock says held. **Now consult the
   record:**
   - record **alive** (pid up, `create_time` matches) ⇒ genuine live sibling ⇒ advance to `-2`
     (R7 preserved by the kernel).
   - record **dead/stale/empty/legacy** ⇒ **ghost flock** (H-leak inherited OFD, or H-stale
     network lock) ⇒ **override**: use the canonical path and append; rewrite the record with our
     `instance`. We cannot seize the held OFD's flock, but the record proves no real owner
     exists, so appending is safe.
4. **UNSUPPORTED** (any other `OSError`) or `_LOCKING is False` ⇒ fall back to Option-A pure
   record mode on the canonical path (this is the R5 "bounded, accept possible interleave"
   regime).

- **H-leak:** ✅ fixed via step 3 (record override) — flock alone would loop to `-2` forever;
  the record is what breaks the ghost. **This is how B fixes the actually-observed bug** (clean
  canonical + sequential `-2`, identical on ZFS and Lustre ⇒ FS-independent ⇒ ghost-lock, not FS
  semantics).
- **H-stale:** ✅ same step-3 override.
- **Legit concurrency:** ✅ **best of the options** — in the healthy case flock is an *atomic*
  acquire, so exactly one of two simultaneous starters wins slot 1 and the other gets `EAGAIN`,
  reads the (alive) record, and advances. Strict single-writer, no interleave (R7, R8).
- **pid-reuse:** ✅ `create_time`.
- **Two-starters race:** ✅ **closed by the kernel** in the healthy case. Only in the degenerate
  flock-broken regime does it fall to A's weaker CAS — and *that regime is exactly where GOAL R5
  already accepts bounded interleave*, so the unavoidable race is confined where it is already
  permitted.
- **Crash safety:** ✅ doubled — kernel releases flock on crash *and* the record goes stale.
- **Cross-platform:** ✅ keeps the `fcntl`/`msvcrt`/`_LOCKING=False` structure (R8); psutil for
  the record check on all three.
- **Backward-compat:** ✅ healthy flock still works on a legacy 0-byte sidecar; on winning we
  upgrade it in place to carry a record. On CONFLICT with a legacy sidecar (holder on old code,
  no record) we cannot judge liveness ⇒ defer to flock's verdict (advance). Mixed-version fleets
  are transient.

### (C) pure pid-record, drop flock — **reject**

FS-independent and simple, but throws away the atomic acquire, so the two-starter race is
present *even on a healthy filesystem* — regressing R7's "where locking works," the one property
that works today (`tests/test_logging.py::test_slot_reclaimed_after_owner_dies`). Also forfeits
free kernel crash-release. Not worth it given GOAL keeps the scheme.

### (D) mtime / heartbeat staleness — **reject as primary**

Needs a background thread touching the sidecar on an interval; the staleness threshold is a
guess (too long ⇒ slow reclaim; too short ⇒ false-reap a GC-paused live writer ⇒ two writers);
and mtime coherence/clock-skew is worst on the very network FS we distrust. A pid-record is an
*instantaneous, authoritative* signal with no timer — strictly better. Not used.

## Recommendation — (B) flock-primary + pid-record fallback

B is the only option that (i) keeps strict single-writer where flock works — honoring R7/R8 and
not regressing `test_slot_reclaimed_after_owner_dies`; (ii) **fixes the observed FS-independent
bug** by letting the local pid-record override a ghost flock; and (iii) degrades to A's
record-only mode precisely in the lockless regime GOAL R5 already scopes. It reuses machinery
already in the tree (`psutil`, `INSTANCE`, `HOSTNAME_SHORT`) and needs no new dependency and no
new config knob.

**How B fixes H-leak (if real):** the inherited-OFD ghost keeps the flock after the true owner
dies, so flock-only never reclaims. B reads the record, sees the *stamped* owner's pid gone (or
`create_time` mismatched), and reclaims the canonical path anyway — the pid check *correctly
disagrees* with flock. Two complementary, plan-level structural mitigations (defense-in-depth,
not substitutes for the record): open the sidecar fd `O_CLOEXEC` (Python fds are already
non-inheritable across **exec**, so `Popen`/spawn children never inherit it) — but note this
does **not** help `os.fork()` without exec (multiprocessing 'fork' start method on Linux shares
the OFD regardless of CLOEXEC), which is exactly why the record, not an fd flag, is the real
fix. B's record also repairs the **reaper** (P2): the current plan treats a `_try_lock`
handle-grant as "orphan," but under a ghost flock the probe gets `EAGAIN` and would **skip a
genuine orphan** — reaping on `record.pid is dead` instead makes R6 correct too.

---

## ~250-word summary

The same-host guarantee — `default_file_for` bakes `HOSTNAME_SHORT` into every distributed slot
(`logging.py:654-657`) — means every competitor for a slot runs locally, so a pid written into
the sidecar can be checked with a local `psutil.pid_exists` + `Process.create_time()`. Crucially,
**`psutil>=7.0.0` is already a core dependency** (`pyproject.toml:37`, used in
`core/resource.py:17`), so the EPEL/stdlib worry is moot and cross-platform liveness (Linux/macOS
no-`/proc`, Windows no-`os.kill`) is uniform; `create_time()` is a wall-clock epoch that defeats
pid reuse with **no boot-id needed**. Recommend **Option B: flock-primary with a pid-record
fallback.** Where flock works it stays the atomic acquire — strict single-writer (R7/R8), and the
two-starter race is closed by the kernel. On CONFLICT we consult the record: a live sibling ⇒
advance to `-2`; a dead/stale/empty/legacy record ⇒ **override the flock** and reclaim the
canonical path (append via the handler's existing `mode='a'`, `logging.py:377`). On UNSUPPORTED /
`_LOCKING=False` it degrades to record-only mode — exactly GOAL R5's bounded-interleave regime.
This **fixes H-leak**: an inherited-OFD ghost keeps the flock after the true owner dies, so
flock-only never reclaims; the record stamps the *original* pid, which reads as dead, so B
reclaims anyway — the pid check deliberately disagrees with flock. Composition caveats: switch
`_try_lock`'s truncating `mode='w'` (`logging.py:671`) to a non-truncating probe so a losing
probe can't blank the winner's record; reader path opens `'r'`; legacy 0-byte sidecars parse as
"no live holder." B also repairs the P2 reaper (reap on `record.pid` dead, not on a `_try_lock`
grant that a ghost flock would deny). Reject C (regresses R7 on healthy FS) and D (timer-based,
false-reap risk).
