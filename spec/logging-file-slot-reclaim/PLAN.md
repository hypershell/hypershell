# PLAN — Reclaim per-host log-file slots on shared HPC filesystems

> **Status:** Draft for review · **Last updated:** 2026-07-23
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

The proliferation is a single defect: `_try_lock` collapses *every* `flock` `OSError` into
"slot contended," so on filesystems where advisory locking is unavailable (Lustre default
`noflock` → `ENOSYS`; NFS-exported ZFS with flaky NLM → `ENOLCK`) each process generation walks
past the canonical per-host log and manufactures a new file. The fix — entirely within
`src/hypershell/core/logging.py`, no new config knob — teaches `_try_lock` to **distinguish a
genuine conflict (`EAGAIN`/`EWOULDBLOCK`) from an unavailable lock**, **degrades to the reused
canonical per-host path** (append) when locking is unavailable, and **opportunistically reaps
orphaned `-N.log` slots**. On filesystems where `flock` works, reclaim-and-append *already*
works (`mode='a'` + OS lock release on exit), so R7's strict single-writer is preserved
untouched there. Small appetite: three coupled, coherent phases in one module + tests.

## 2. Design

All changes are in `src/hypershell/core/logging.py`. The log path is **not** forwarded to
launched clients (confirmed: all three argv builders in `cluster/{remote,ssh}.py` omit it; each
client independently derives `client-<own-host>.log`), so no cluster/argv code is touched.

**(a) errno discrimination — `_try_lock`.** Replace the bare `except OSError` (`logging.py:675`)
with three outcomes:
- **LOCKED** → return the held handle (unchanged success path).
- **CONFLICT** — `errno ∈ {EAGAIN, EWOULDBLOCK}` (equivalently, `except BlockingIOError`). A live
  sibling holds this slot → the caller advances to the next `-N` slot (preserves **R7**).
- **UNSUPPORTED** — any other `OSError` (`ENOSYS`, `ENOLCK`, `EOPNOTSUPP`/`ENOTSUP`, `EINVAL`,
  `EROFS`, …). Locking is unavailable on this FS → the caller must **not** walk slots.
- `EINTR` → retry the same candidate.
Signalled cleanly to `claim_file_slot` (e.g. `_try_lock` returns the handle, returns `None` for
CONFLICT, and raises/sentinels `UNSUPPORTED`). Compare **symbolic `errno.*`** constants only
(numbers differ macOS↔Linux); guard rare names with `getattr(errno, 'ENOTSUP', None)`. The
`msvcrt` branch keeps its current conflict-only behavior; the `_LOCKING = False` branch is
retained but redirected (below).

**(b) degrade-to-canonical — `claim_file_slot`.** On the *first* UNSUPPORTED result, or when
`_LOCKING is False`, stop iterating and return the **canonical** (n=1) per-host path — reused and
appended (the handler already opens `mode='a'`, `logging.py:377`) — emitting **one** `warn` that
advisory locking is unavailable. This replaces the `_LOCKING=False` PID-suffix return. The
genuine-exhaustion branch (all 100 slots returned CONFLICT — i.e. 100 *real* concurrent live
siblings on one host) legitimately **keeps** the PID-suffix fallback, since distinct files are
the correct single-writer response there; with errno discrimination an unsupported FS degrades at
n=1 and can never reach exhaustion.

**(c) reclaim + append (R2/R3).** No new mechanism: the existing "first lockable slot wins" loop
already reclaims the canonical slot on a working-flock FS after the prior owner exits (OS drops
the flock), and the handler appends. The degrade path (b) extends the same reuse+append to
lockless filesystems.

**(d) opportunistic orphan reap — new helper (R6).** Performed **only by the process that just
won the canonical n=1 lock** (one reaper per host, naturally serialized), invoked at claim time
alongside `recover_interrupted_compression` (`logging.py:837`). Enumerate directory entries
matching the **exact `-N` slot shape** `client-<host>-<int>.log` (a compiled regex on
`basename_without_ext`-scoped prefix, mirroring `recover_interrupted_compression`'s
`prefix = basename_without_ext(log_file) + '.'` discipline, `logging.py:513-533`). For each: if
`_try_lock(slot + '.lock')` returns LOCKED, the owner is gone → `os.remove` the orphaned
`-N.log`; on CONFLICT/UNSUPPORTED, skip. **Never** touch the dot-separated rotation namespace
(`client-<host>.<N>` / `.YYYYMMDD` / `.YYYYMMDD-HHMMSS`), the `.lock` sidecars, the freshly
claimed file, or the `main` role.

**Deliberately not reaped** (inode-reuse safety + appetite): the `.lock` sidecars themselves
(unlinking a sidecar another process may `flock` is a POSIX inode-reuse race → two writers), and
an orphan slot's *own* rotated children. Sidecar count is bounded once `-N` growth stops.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | Root cause documented in [`research/00-digest.md`](research/00-digest.md) + briefs (errno conflation; Lustre `noflock`→`ENOSYS`, NFS→`ENOLCK`); TECH P1 adds a local repro test that forces the errno path and confirms local working-flock does **not** reproduce. |
| R2 | (c) existing reclaim loop + (b) degrade both return the canonical n=1 path when no live holder exists. |
| R3 | Handler opens `mode='a'` (`logging.py:377`); reclaim/degrade reuse the canonical name → append, never truncate. |
| R4 | (b) degrade bounds to 1 file/host on lockless FS; (c) reclaim bounds to peak concurrency on working FS; (d) reap removes accumulated orphans. Verified by the serial-generations count assertion. |
| R5 | (a)+(b) errno discrimination + degrade-to-canonical keep the count bounded when locking is unavailable (resolves GOAL Q2: hybrid mechanism). |
| R6 | (d) opportunistic reap of orphaned `-N.log` by the canonical-lock holder. |
| R7 | (a) CONFLICT (`EAGAIN`/`EWOULDBLOCK`) still advances the slot loop → distinct files for genuinely concurrent same-host writers where `flock` works. |
| R8 | `msvcrt` branch retained; `_LOCKING=False` degrades to canonical (still host-scoped) not PID; `main` role stays un-host-scoped and is never reaped; cross-host names differ by baked-in hostname. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`invariants.md`](../../.agents/factory/invariants.md) before research and again
after this design. `core/logging.py` is **not** in the high-blast-radius list (§16).

- **§5 (concurrency — background compression thread + `FILE_LOCK`)** — the reap runs at claim
  time in `initialize_logging` (single-threaded, before the queue listener/compression thread do
  meaningful work), touches only exact `client-<host>-<int>.log` data files, and never the
  dot-separated rotation namespace that `RotatingFileHandler.rotate` manages under `FILE_LOCK`.
  No new thread, no shared mutable state beyond the existing `_slot_locks` list.
- **§11 (cluster orchestration — no shared client-argv builder)** — honored trivially: the fix
  forwards **no** new argument; the log path is derived client-side and is not in any argv
  builder, so `local/remote/ssh/autoscale` are untouched.
- **§12 (same-commit conventions)** — internal-only change: **no** new CLI flag or config key ⇒
  no `docs/_include/*.rst` or `share/` completion edits required. New tests are tagged
  `@mark.unit` / `@mark.integration`; `errno.*` symbolic constants (not integer literals) are
  used; Python 3.11–3.14 only (`fcntl`/`errno` are stdlib on all).
- **R7/R8 (module's own single-writer + fallback contract)** — preserved where lockable;
  consciously relaxed only on lockless filesystems, which is precisely what R5 authorizes.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| —         | —         | — |

No `invariants.md` invariant is bent. (The single-writer *relaxation* on lockless filesystems is
the GOAL's R5, not an invariant deviation — recorded as an accepted trade-off in §5 Risks.)

## 4. Rabbit holes (resolved)

- **Why proliferation on *both* ZFS and Lustre, and not locally** → same errno-conflation defect
  via two different errnos: Lustre `noflock`→`ENOSYS` (always), NFS/ZFS flaky NLM→`ENOLCK`; local
  working-flock reclaims correctly (no bug) ([`research/01`](research/01-fs-lock-semantics.md),
  [`research/02`](research/02-code-trace-signatures.md)).
- **Which on-disk signature confirms it** → unsupported ⇒ PID-suffixed logs + fixed 100 `.lock` +
  per-launch "Exhausted" warning; stale ⇒ sequential `-N.log` + growing `.lock`
  ([`research/02`](research/02-code-trace-signatures.md)). A one-line `ls` on Gautschi confirms
  which (not a blocker).
- **How to detect "unsupported" vs "contended" portably** → only `{EAGAIN, EWOULDBLOCK}`
  (`BlockingIOError`) is contention; symbolic `errno.*` (numbers differ by OS)
  ([`research/01`](research/01-fs-lock-semantics.md)).
- **Is reaping `.lock` files safe?** → No — POSIX inode-reuse race; reap only the `-N.log` data,
  leave sidecars ([`research/03`](research/03-fix-surface-and-tests.md)).
- **Does the fix need to touch cluster argv / add a knob?** → No to both; contained to
  `core/logging.py` ([`research/02`](research/02-code-trace-signatures.md),
  [`research/03`](research/03-fix-surface-and-tests.md)).

## 5. Risks & open questions

- **Interleaved writes on the degrade path (accepted, = R5).** When advisory locking is
  unavailable, multiple *concurrent* same-host clients append to one file; `O_APPEND` write
  atomicity is not guaranteed on Lustre for large records. This is the GOAL's explicit R5 choice
  (bounded proliferation > strict single-writer on lockless FS). Documented; not mitigated.
- **`.lock` sidecars are not reaped** (inode-reuse safety) — their count is bounded once `-N`
  growth stops, but pre-existing orphan sidecars from the buggy version persist. Acceptable
  (0-byte files); deeper GC is a Non-goal.
- **Cannot CI-test real Lustre/ZFS.** Mitigation: unit tests inject the exact errnos
  (`ENOSYS`/`ENOLCK` vs `EAGAIN`) to exercise both branches deterministically; integration test
  asserts the bounded count via serial generations on the local FS.
- **`_slot_locks` global leak** must be cleaned between in-process tests (autouse fixture) —
  a test-hygiene requirement, called out so P1 doesn't produce flaky cross-test contamination.
- **Confirmation (optional, non-blocking):** an `ls` of the log dir on Gautschi would confirm
  which signature is in play; the fix cures both regardless.

## 6. Verification strategy

Prefer driving the real CLI in a throwaway site, backed by targeted unit tests that inject
filesystem behavior:

- **errno discrimination / degrade (P1):**
  `uv run pytest -v tests/test_logging.py -k "slot or lock or degrade or unsupported"` — patch
  `hypershell.core.logging.fcntl.flock` to raise `OSError(errno.ENOSYS)` / `OSError(errno.ENOLCK)`
  (→ claim returns canonical) vs `OSError(errno.EAGAIN)` (→ claim returns `-2`); and
  `monkeypatch.setattr('hypershell.core.logging._LOCKING', False)` (→ canonical). Autouse fixture
  clears `_slot_locks`.
- **reap (P2):** seed orphan `client-h-2.log`/`-3.log` (unlocked) + a *locked* `client-h-4.log`
  and rotated `client-h.1`/`client-h.20260723` and `main.log`; assert only the unlocked orphans
  are removed, everything else retained.
- **bound end-to-end (P3):** drive the CLI and assert no proliferation across repeated runs:
  `.agents/factory/bin/temp_site.sh sh -c "HYPERSHELL_LOGGING_FILE=enabled; for i in 1 2 3; do seq 20 | uv run hsx -t 'echo {}' -N2 >/dev/null; done; ls \"$HYPERSHELL_SITE\"/**/client-*.log 2>/dev/null | wc -l"`
  (bounded, not growing with the loop) plus `uv run pytest -v -m integration tests/test_logging.py`.
- **regression:** `uv run pytest -v tests/test_logging.py` (existing role/host-scope/reclaim tests
  must stay green).

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
