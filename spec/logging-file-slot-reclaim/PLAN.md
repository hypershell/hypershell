# PLAN — Ephemeral log-lock sidecars + hardening

> **Status:** Draft for review · **Last updated:** 2026-07-23 (re-scoped)
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail in [`research/`](research/)
> — synthesis [`09-revised-design.md`](research/09-revised-design.md) supersedes the original
> diagnosis in [`00-digest.md`](research/00-digest.md). Every design element traces to a GOAL R-ID.

## 1. Summary

Investigation refuted the original "never-reclaims / ENOSYS-exhaustion" diagnosis: `flock`
acquire, conflict-detection, and reclaim+append all work on Lustre and ZFS, and the `-N.log`
files were legitimate concurrent ranks. This plan therefore targets the *real* residual issues:
make the `.lock` sidecars **ephemeral and self-describing** (PID+start-time record, best-effort
drop at clean shutdown, flock-guarded prune of crash leftovers at startup — never touching
`-N.log` data); **close the `server`/`cluster` fd-inheritance lock leak** in the forked
queue-manager child; and **discriminate lock errnos** so a genuinely lockless filesystem degrades
to canonical-append instead of a per-PID file. All while regression-testing the behaviors that
already work. Contained to `src/hypershell/core/logging.py` plus a small `core/queue.py` fork
seam. Appetite small → small-medium.

## 2. Design

### (a) Self-describing sidecars (R2) — `core/logging.py`

Replace the 0-byte sidecar with an owner record. `_try_lock` currently does `open(mode='w')`
(**truncating**) then `flock`. Change the acquire path so that, **after** winning the `flock`, the
owner writes a single fixed-shape record — `{"v":1,"pid":…,"create_time":…,"host":…,"instance":…}`
— then `flush()` (never `close()`; closing drops the lock, `logging.py:698`/`:710`). Probers open
the sidecar **read-only** (`'r'`) and never truncate, so a losing probe can't blank the winner's
record. `psutil` (already a core dep, `pyproject.toml`, used in `core/resource.py`) supplies
`pid_exists` + `Process.create_time()` (wall-clock epoch → folds in boot time, defeats PID reuse
with a ~2s tolerance; no boot-id needed). A missing/empty/legacy/unparseable record reads as
"no live holder."

### (b) errno discrimination + degrade-to-canonical (R7) — `core/logging.py`

In `_try_lock`, classify the failure instead of swallowing all `OSError`: `EAGAIN`/`EWOULDBLOCK`
(→ `BlockingIOError`) = genuine CONFLICT → caller advances a slot; `EINTR` → retry; any other
`OSError` = UNSUPPORTED. In `claim_file_slot`, on the first UNSUPPORTED (or `_LOCKING is False`)
stop iterating and return the **canonical** per-host path (append), replacing the `<root>-<pid>`
returns at `logging.py:713`. Genuine 100-way exhaustion (all `EAGAIN`) still legitimately
PID-suffixes (real concurrency). Fix the false comment at `logging.py:660-666`.

### (c) Ephemeral sidecar lifecycle (R3, R4, R5, R8) — `core/logging.py`

**The one safety invariant (R8):** *only ever unlink a sidecar while holding its `flock`.* This
closes the inode-reuse race (unlinking a lock another process holds → they keep the old inode
while a newcomer `open()`s a fresh one → two writers).

- **Shutdown drop (R3):** a finalizer (an `atexit` hook registered when a slot is claimed, and/or
  an explicit teardown in the app stop path) stops file logging (so no further writes), then for
  each handle in `_slot_locks`: `unlink` the sidecar path *while still holding the lock*, then
  `close`. Wrapped best-effort (`try/except OSError`). Covers clean exits; `SIGKILL`/OOM
  necessarily skip it (handled by prune).
- **Startup prune (R4):** the process that wins the **canonical** slot scans sibling
  `client-<host>-N.log.lock` sidecars (exact dash-`N` shape, prefix-scoped like
  `recover_interrupted_compression`, `logging.py:513-533`). For each: `flock(LOCK_EX|LOCK_NB)` —
  acquired ⇒ no live holder ⇒ `unlink` the **sidecar only** while holding it, then release;
  blocked ⇒ a live client holds it ⇒ skip. The record confirms staleness for diagnostics but the
  `flock`-acquire is the safety gate. **`-N.log` data and the dot-separated rotation namespace are
  never touched (R5).**

### (d) `server`/`cluster` fd-leak hardening (R6) — `core/queue.py`

`initialize_logging()` (`__init__.py:139`) runs before `SecureManager/BaseManager.start()`
forks the manager child (`core/queue.py` ~`:243-281`,`:385`) under the default `fork` method, so
the child inherits the role's slot-lock OFD and holds the lock alive. Close the inherited
`_slot_locks` handles in the child via the existing post-fork initializer seam (`_tls_bootstrap`
path) or `os.register_at_fork(after_in_child=…)`. `os.set_inheritable(False)` is **useless** here
(it acts on exec, not fork). Guard as a no-op where there is no fork / on Windows.

### Requirement → design map

| R-ID | Design element(s) |
|------|-------------------|
| R1 | Retained `research/04-11` + `09`; P4 regression tests codify reclaim/append/concurrency. |
| R2 | (a) owner record written under the held lock; `psutil` liveness. |
| R3 | (c) shutdown finalizer: stop logging → unlink-under-lock → close. |
| R4 | (c) startup flock-guarded prune of stale sibling sidecars. |
| R5 | (c) reap targets `.lock` only; `-N.log`/rotation namespace never touched. |
| R6 | (d) close inherited `_slot_locks` in the forked manager child. |
| R7 | (b) errno discrimination + degrade-to-canonical (kills the `:713` per-PID branch). |
| R8 | only-unlink-under-held-lock invariant; msvcrt/`_LOCKING=False`/`main`/cross-host paths preserved; healthy-FS reclaim unchanged. |
| R9 | P4 docs note in the file-logging section. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`invariants.md`](../../.agents/factory/invariants.md) before research and again
after this design.

- **§5 (concurrency — background compression thread + `FILE_LOCK`)** — startup prune runs in
  `initialize_logging` beside `recover_interrupted_compression`, before the compression thread
  does real work; it touches only `.lock` sidecars, never the `FILE_LOCK`-managed rotation
  namespace. The shutdown finalizer stops the `QueueListener` before unlinking.
- **§9 / high-blast-radius `core/queue.py`** — R6/(d) edits `core/queue.py`, which **is** on the
  high-blast-radius list (§16). Keep the change to closing inherited fds in the child; do not
  touch the pickle-framed RPC, TLS context install, authkey, or handshake/fingerprint. A
  CONFIRMED finding here forces a human review gate — expected; P3 is `parallel:false`, extra care.
- **§11 (no shared client-argv builder)** — honored: no argument is forwarded to launched
  clients; path derivation stays client-side. No argv-builder edits.
- **§12 (same-commit conventions)** — no new CLI flag or config key ⇒ no `docs/_include/*.rst`
  help-snippet or `share/` completion edits. R9 is prose in the logging docs (not CLI help).
  New tests tagged `@mark.unit`/`@mark.integration`; symbolic `errno.*`; Python 3.11–3.14.
- **R8 self-contract** — the only-unlink-under-held-lock invariant is the load-bearing safety
  rule; every unlink site must be audited against it.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| Unlink sidecars (vs. research 00/07 "never unlink") | Maintainer wants ephemeral sidecars; the inode-reuse hazard is fully closed by the only-unlink-while-holding-the-lock rule (adversarially confirmed in [`research/11`](research/11-stress-reap.md) Part 3) | "Never unlink + document" leaves the alarming accumulation the maintainer explicitly wants gone |

No `invariants.md` invariant is bent.

## 4. Rabbit holes (resolved)

- **Was there a reclaim bug at all?** No — legitimate concurrency; reclaim+append works on both
  FS ([`research/04`](research/04-evidence-forensics.md), [`research/09`](research/09-revised-design.md);
  confirmed by the maintainer's timestamp forensics + ZFS restart test).
- **Is unlinking a sidecar safe?** Only under a held `flock`; that closes the inode-reuse race
  ([`research/11`](research/11-stress-reap.md) Part 3).
- **Do we need PID-override of a held lock?** No — reclaim works; the record is liveness/
  diagnostics + safe-prune only. The override branch was adversarially shown to risk R7 on a
  *healthy* FS ([`research/10`](research/10-stress-liveness.md) H1/H9) and is dropped.
- **Where is the fd-leak, and does it hit clients?** `BaseManager.start()` fork; `server`/
  `cluster` only — clients never fork a manager ([`research/05`](research/05-process-model-audit.md)).
- **PID reuse / no boot-id / psutil dep?** `create_time()` folds boot time; `psutil>=7` already a
  dep; pin ~2s tolerance ([`research/06`](research/06-liveness-design.md),
  [`research/10`](research/10-stress-liveness.md) H8).

## 5. Risks & open questions

- **Torn/partial record read** — a prober reading mid-write must treat unparseable as "no live
  holder"; a single fixed-shape `write()` + reader retry-once mitigates. Safety never depends on
  the record alone — the `flock`-acquire is the gate ([`research/10`](research/10-stress-liveness.md) H1).
- **`AccessDenied` on a reused PID owned by another user** (node-shared allocations) → read as
  "alive," slot not pruned. Safe (never steals); document. Gautschi nodes are typically
  exclusive ([`research/10`](research/10-stress-liveness.md) H6).
- **PID namespaces / containers** break local PID checks; out of practical scope for MPI/Slurm
  launchers; document ([`research/10`](research/10-stress-liveness.md) H7).
- **Shutdown finalizer coverage** — `atexit` runs on clean exit and handled `KeyboardInterrupt`,
  not on uncaught `SIGTERM`/`SIGKILL`; those leftovers are swept by the startup prune. Acceptable.
- **`core/queue.py` blast radius** — R6 touches a high-blast-radius file; forces a human gate.

## 6. Verification strategy

Prefer driving the real CLI in a throwaway site plus targeted unit tests that inject
filesystem/liveness behavior:

- **errno/degrade (P1):** patch `hypershell.core.logging.fcntl.flock` to raise
  `OSError(errno.ENOSYS)`/`ENOLCK` (→ canonical) vs `OSError(errno.EAGAIN)` (→ `-2`);
  `monkeypatch.setattr('hypershell.core.logging._LOCKING', False)` (→ canonical). Assert the
  record round-trips. Autouse fixture clears `_slot_locks`.
- **sidecar lifecycle (P2):** claim → assert record present + owner alive; simulate clean exit →
  assert own sidecar removed; seed a stale sibling `.lock` (dead PID, unlocked) + a *held* sibling
  (in-test `flock`) + real `-N.log` data + rotated `client-h.20260723` → after a canonical claim,
  assert only the unlocked stale sidecar is removed and **all `.log`/rotated files survive**.
- **fd-leak (P3):** a POSIX-only test that `os.fork()`s after a claim and asserts the parent's
  lock releases once the parent handle closes iff the child closed its inherited copy
  (`skipif` Windows).
- **e2e bound + regression (P4):** `.agents/factory/bin/temp_site.sh sh -c "HYPERSHELL_LOGGING_FILE=enabled; for i in 1 2 3; do seq 20 | uv run hsx -t 'echo {}' -N2 >/dev/null; done; find \"$HYPERSHELL_SITE\" -name '*.log.lock' | wc -l"`
  (bounded; sidecars cleaned across clean restarts) + `uv run pytest -v tests/test_logging.py`
  (existing reclaim/host-scope/role tests green).

---

*Backing research: [`research/09-revised-design.md`](research/09-revised-design.md) (supersedes
[`00-digest.md`](research/00-digest.md)); briefs [`04`](research/04-evidence-forensics.md)–[`11`](research/11-stress-reap.md).*
