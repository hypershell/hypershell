---
slug: logging-file-slot-reclaim
title: "Reclaim per-host log-file slots on shared HPC filesystems"
kind: fix
appetite: small
status: in_progress
branch: fix/logging-file-slot-reclaim
base: develop
current_phase: P1
last_updated: "2026-07-23"
phases:
  - id: P1
    name: "errno discrimination + degrade-to-canonical (core fix)"
    status: pending
    satisfies: [R1, R2, R3, R5, R7, R8]
    depends_on: []
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -v tests/test_logging.py -k \"slot or lock or degrade or unsupported\""
  - id: P2
    name: "Opportunistic orphan-slot reap"
    status: pending
    satisfies: [R6, R4]
    depends_on: [P1]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -v tests/test_logging.py -k \"reap or orphan\""
  - id: P3
    name: "End-to-end bounded-count verification (CLI-driven)"
    status: pending
    satisfies: [R4, R8]
    depends_on: [P1, P2]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -v -m integration tests/test_logging.py"
review:
  last_reviewed_commit: ""
  verdict: none
  blocked_reason: ""
  cycle: 0
---

# TECH.md — Reclaim per-host log-file slots on shared HPC filesystems

The **context engine and finite-state machine** for building this fix. The YAML frontmatter
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/logging-file-slot-reclaim/TECH.md`); the
per-phase checklists below are the work.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–03.

## Conventions (apply to every phase)

- Invariants and house style come from [`AGENTS.md`](../../AGENTS.md) /
  [`invariants.md`](../../.agents/factory/invariants.md). The whole fix is contained to
  `src/hypershell/core/logging.py` + `tests/test_logging.py`.
- One phase per `hs-build` invocation; one atomic commit with **both** code and the `TECH.md`
  state change. Subjects: `[fix] Build logging-file-slot-reclaim P<n>: …`. **No `Co-Authored-By`.**
- **No new CLI flag or config key** (internal-only) ⇒ **no** `docs/_include` / `share/` edits
  (invariant §12). If a phase discovers a knob is truly needed, STOP and escalate — it would
  change scope and force companion doc/completion edits.
- Use symbolic `errno.*` constants, never integer literals (numbers differ macOS↔Linux).
- Add an **autouse fixture** in `tests/test_logging.py` (P1) that closes and clears the module
  global `_slot_locks` after each test — otherwise held handles leak across tests and cause flakes.

---

## Phase P1 — errno discrimination + degrade-to-canonical (core fix)
**Satisfies:** R1, R2, R3, R5, R7, R8 · **Depends on:** —
**Goal:** teach the slot machinery to tell a genuine lock *conflict* from *unavailable* locking,
and on unavailable locking reuse-and-append the canonical per-host path instead of manufacturing
a new file — curing the primary proliferation bug end to end.

- [ ] In `_try_lock` (`logging.py:669-677`), replace the bare `except OSError` with errno
      classification: success → return handle; `errno ∈ {errno.EAGAIN, errno.EWOULDBLOCK}`
      (i.e. `BlockingIOError`) → **CONFLICT** (return `None`, meaning "advance a slot");
      `errno == errno.EINTR` → retry the same candidate; **any other** `OSError` → **UNSUPPORTED**
      (signal the caller to stop walking — e.g. raise a small module-internal `_LockUnavailable`
      or return a distinct sentinel). Use `getattr(errno, 'ENOTSUP', None)`-style guards only if
      you enumerate names; the "everything else = unsupported" default avoids that.
- [ ] Keep the `msvcrt` branch behavior as conflict-only (its `OSError` still means contended);
      keep the `_LOCKING = False` guard.
- [ ] In `claim_file_slot` (`logging.py:701-713`): on CONFLICT advance to the next `-N` slot
      (unchanged); on the **first** UNSUPPORTED, or when `_LOCKING is False`, stop and return the
      **canonical** (n=1) per-host path, emitting exactly one `warn(...)` that advisory locking is
      unavailable so files are shared per host. Leave the genuine-exhaustion (all-100-CONFLICT)
      branch returning the PID suffix — that is real 100-way concurrency where a distinct file is
      correct. Remove the `_LOCKING=False` PID-suffix return in favor of the canonical degrade.
- [ ] Update the false comment at `logging.py:660-666` to state the real contract (fall through
      only on genuine contention; degrade to the shared canonical path when locking is
      unavailable). Declarative statement of the invariant, no spec ids.
- [ ] Confirm reclaim+append needs no code change: the handler already opens `mode='a'`
      (`logging.py:377`); the degrade path returns the same canonical name → appends (R3).
- [ ] Tests (`tests/test_logging.py`, `@mark.unit`): add the autouse `_slot_locks` cleanup
      fixture. Patch `hypershell.core.logging.fcntl.flock` to raise `OSError(errno.ENOSYS)` and
      `OSError(errno.ENOLCK)` → assert `claim_file_slot` returns the **canonical** path (degrade,
      R5); raise `OSError(errno.EAGAIN)` → assert it returns `client-…-2.log` (conflict
      fallthrough, R7); `monkeypatch.setattr('hypershell.core.logging._LOCKING', False)` → assert
      canonical (R8). Add a repro/documentation test asserting the *old* behavior does not recur
      (many generations under injected `ENOSYS` yield **one** canonical file, not a PID pile) — this
      is the empirical half of R1 (and confirms local working-flock still reclaims via the existing
      `test_slot_reclaimed_after_owner_dies`).
- **Verify:** `uv run pytest -v tests/test_logging.py -k "slot or lock or degrade or unsupported"`
- **Touches:** `src/hypershell/core/logging.py`, `tests/test_logging.py`.

## Phase P2 — Opportunistic orphan-slot reap
**Satisfies:** R6, R4 · **Depends on:** P1
**Goal:** the process that wins the canonical slot removes stale `-N.log` files whose owning
process is gone, so the artifacts already accumulated on Gautschi get reclaimed — without racing
live siblings or the rotation/compression machinery.

- [ ] Add a helper `reap_orphan_slots(canonical_path)` that runs **only** when the caller won the
      canonical (n=1) lock (one reaper per host). Enumerate `os.listdir(dirname)`, match the
      **exact** slot shape via a compiled regex on the `basename_without_ext`-scoped prefix:
      `^{re.escape(root_basename)}-([0-9]+){re.escape(ext)}$` (i.e. `client-<host>-<int>.log`),
      mirroring `recover_interrupted_compression`'s prefix-scoping discipline (`logging.py:513-533`).
- [ ] For each matched slot: `_try_lock(slot + '.lock')` — if it returns a handle (LOCKED →
      orphan), `os.remove` the `-N.log` **data file** and release the probe handle; on CONFLICT
      (live sibling) or UNSUPPORTED, skip. **Never** remove: the `.lock` sidecars (POSIX
      inode-reuse race — see PLAN §5), the dot-separated rotation namespace
      (`client-<host>.<N>` / `.YYYYMMDD` / `.YYYYMMDD-HHMMSS`), the freshly-claimed file, or
      anything for the `main` role. Wrap `os.remove` to tolerate `FileNotFoundError`/`OSError`
      (a racing reaper or a live writer).
- [ ] Call the reaper from `initialize_logging` right where `recover_interrupted_compression`
      runs (`logging.py:837`), guarded to fire only when the canonical slot was claimed. Keep it
      before the compression thread does real work (invariant §5 — do not race `FILE_LOCK`).
- [ ] Tests (`@mark.unit`): seed unlocked orphan `client-h-2.log`/`-3.log`, a *locked*
      `client-h-4.log` (hold its sidecar flock in-test), rotated `client-h.1` /
      `client-h.20260723`, and `main.log`; after a canonical claim assert only the unlocked
      orphans are removed and everything else is retained.
- **Verify:** `uv run pytest -v tests/test_logging.py -k "reap or orphan"`
- **Touches:** `src/hypershell/core/logging.py`, `tests/test_logging.py`.

## Phase P3 — End-to-end bounded-count verification (CLI-driven)
**Satisfies:** R4, R8 · **Depends on:** P1, P2
**Goal:** prove, by driving the real CLI, that repeated client generations on one host converge
to a bounded set of log files (no proliferation), and that cross-host / `main`-role behavior is
unregressed.

- [ ] Add an `@mark.integration` test that, in an isolated `temp_site` with file logging enabled
      (`HYPERSHELL_LOGGING_FILE=enabled`), runs several serial `hsx`/`hs cluster` generations and
      asserts the count of `client-*.log` files stays bounded (does not grow with the generation
      count). Reuse the `temp_site` fixture and the `main`/`main_lines` helpers.
- [ ] Manually drive the flow once to confirm (record the command in the commit body):
      `.agents/factory/bin/temp_site.sh sh -c "for i in 1 2 3; do seq 20 | uv run hsx -t 'echo {}' -N2 >/dev/null; done; find \"$HYPERSHELL_SITE\" -name 'client-*.log' | wc -l"`
      — the count must not scale with the loop iterations.
- [ ] Confirm no regression: full `uv run pytest -v tests/test_logging.py` stays green
      (role mapping, host-scoping, `resolve_log_path` client decoration, crash-reclaim), and the
      `main` role remains un-host-scoped and un-reaped.
- **Verify:** `uv run pytest -v -m integration tests/test_logging.py`
- **Touches:** `tests/test_logging.py`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative).
2. Pre-flight: clean tree, on `fix/logging-file-slot-reclaim`, `develop` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file if reality diverges (regenerate frontmatter with `set_phase.py`); STOP and
   escalate only on a `GOAL.md` contradiction (e.g. discovering a config knob is unavoidable).
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
