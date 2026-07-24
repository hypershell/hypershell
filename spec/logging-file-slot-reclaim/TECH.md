---
slug: logging-file-slot-reclaim
title: Ephemeral log-lock sidecars + fd-leak/errno hardening
kind: fix
appetite: small
status: in_progress
branch: fix/logging-file-slot-reclaim
base: develop
current_phase: P2
last_updated: '2026-07-23'
phases:
- id: P1
  name: Self-describing sidecar record + errno discrimination + degrade-to-canonical
  status: done
  satisfies:
  - R2
  - R7
  - R8
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_logging.py -k "slot or lock or errno or degrade
    or record"
- id: P2
  name: Ephemeral sidecar lifecycle (shutdown drop + flock-guarded startup prune)
  status: pending
  satisfies:
  - R3
  - R4
  - R5
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_logging.py -k "sidecar or prune or ephemeral
    or shutdown"
- id: P3
  name: server/cluster fork fd-leak hardening (core/queue.py)
  status: pending
  satisfies:
  - R6
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v tests/test_logging.py -k "fork or leak or inherit or manager"
- id: P4
  name: End-to-end bound + regression + docs note
  status: pending
  satisfies:
  - R1
  - R8
  - R9
  depends_on:
  - P1
  - P2
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -v -m integration tests/test_logging.py
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — Ephemeral log-lock sidecars + fd-leak/errno hardening

Resume ground-truth is the YAML frontmatter (read with
`uv run python .agents/factory/bin/next_phase.py spec/logging-file-slot-reclaim/TECH.md`).

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract (re-scoped
  2026-07-23 after the reclaim-bug premise was refuted).
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/09-revised-design.md`](research/09-revised-design.md)
  (supersedes [`00-digest.md`](research/00-digest.md)); briefs 04–11.

## Conventions (apply to every phase)

- Invariants/house style from [`AGENTS.md`](../../AGENTS.md) /
  [`invariants.md`](../../.agents/factory/invariants.md). Core changes live in
  `src/hypershell/core/logging.py`; P3 also edits **`core/queue.py` (high-blast-radius §16)** —
  touch only the post-fork child fd-close; leave RPC/TLS/authkey/handshake untouched.
- One phase per `hs-build` invocation; one atomic commit (code + `TECH.md` state). Subjects:
  `[fix] Build logging-file-slot-reclaim P<n>: …`. **No `Co-Authored-By`.**
- **No new CLI flag / config key** ⇒ no `docs/_include` / `share/` edits. R9 is prose in the
  file-logging docs (not CLI help). Symbolic `errno.*`, never integer literals.
- **Load-bearing safety invariant (R8): only ever `unlink` a sidecar while holding its `flock`.**
  Audit every unlink site against it.
- Add an **autouse fixture** in `tests/test_logging.py` that closes + clears the module global
  `_slot_locks` after each test (prevents held-handle leakage / cross-test flakes).

---

## Phase P1 — Self-describing sidecar record + errno discrimination + degrade
**Satisfies:** R2, R7, R8 · **Depends on:** —
**Goal:** the sidecar carries its owner's identity, and lock failures are classified so a lockless
FS degrades to canonical-append instead of a per-PID file — foundational for P2/P3.

- [x] Rework `_try_lock` (`logging.py:669-694`): open the sidecar **non-truncating** (not
      `mode='w'`); classify failure — `EAGAIN`/`EWOULDBLOCK` (`BlockingIOError`) → CONFLICT
      (return `None` = advance); `EINTR` → retry; any other `OSError` → UNSUPPORTED (signal the
      caller). Keep the `msvcrt` branch (conflict-only) and the `_LOCKING=False` guard.
- [x] On a won lock, write the owner record `{"v":1,"pid","create_time","host","instance"}` as a
      single fixed-shape line + `flush()` (never `close()`). Add a reader `read_lock_record(path)`
      opening `'r'`, tolerant of empty/legacy/torn (→ `None` = "no live holder"), with a
      retry-once on parse failure.
- [x] Add a liveness helper `_owner_alive(record)` using `psutil.pid_exists` +
      `psutil.Process(pid).create_time()` within a ~2s tolerance; treat `AccessDenied` as alive
      (never steal); host-guard on `HOSTNAME_SHORT`. `psutil` is already a dep (`core/resource.py`).
- [x] `claim_file_slot` (`logging.py:701-713`): advance only on CONFLICT; on first UNSUPPORTED or
      `_LOCKING is False`, return the **canonical** path (append) — remove the `<root>-<pid>`
      returns at `:713` (keep PID-suffix only for genuine all-`EAGAIN` exhaustion). Fix the false
      comment `:660-666`.
- [x] Tests (`@mark.unit`): autouse `_slot_locks` cleanup; patch `fcntl.flock` to inject
      `ENOSYS`/`ENOLCK` (→ canonical) vs `EAGAIN` (→ `-2`); `_LOCKING=False` → canonical; record
      round-trips and reads back; legacy 0-byte sidecar parses as no-holder.
- **Verify:** `uv run pytest -v tests/test_logging.py -k "slot or lock or errno or degrade or record"`
- **Touches:** `src/hypershell/core/logging.py`, `tests/test_logging.py`.

## Phase P2 — Ephemeral sidecar lifecycle
**Satisfies:** R3, R4, R5 · **Depends on:** P1
**Goal:** sidecars are created while a client runs and removed when it stops — best-effort at
clean shutdown, swept for crashes at startup — **never** touching `-N.log` data.

- [ ] **Shutdown drop (R3):** register a finalizer (an `atexit` hook when a slot is claimed and/or
      an explicit `finalize_logging()` in the app stop path) that stops file logging (the
      `QueueListener`) so no further writes, then for each `_slot_locks` handle: `unlink` the
      sidecar **while holding the lock**, then `close`. Wrap best-effort (`try/except OSError`).
- [ ] **Startup prune (R4):** only the process that wins the **canonical** slot scans sibling
      sidecars matching the exact dash shape `^{re.escape(root)}-([0-9]+){re.escape(ext)}\.lock$`
      (prefix-scoped like `recover_interrupted_compression`, `:513-533`). For each:
      `flock(LOCK_EX|LOCK_NB)` — acquired ⇒ stale ⇒ `unlink` the **sidecar only** while holding
      it, then release; blocked ⇒ live ⇒ skip. Use `read_lock_record`/`_owner_alive` for
      diagnostics/logging, but the `flock`-acquire is the safety gate. Invoke beside
      `recover_interrupted_compression` (`:837`), before the compression thread does real work (§5).
- [ ] **R5 guard:** never `unlink`/rewrite `-N.log`, rotated (`client-h.<N>`/`.YYYYMMDD…`),
      `.partial`, or `main`-role files. Add an assertion/test that data files survive a prune.
- [ ] Tests (`@mark.unit`/`integration`): clean-exit path removes own sidecar; stale sibling
      (dead PID, unlocked) removed; *held* sibling retained; `-N.log` + rotated + `main.log` all
      survive; inode-reuse guard (never unlink an unlockable sidecar).
- **Verify:** `uv run pytest -v tests/test_logging.py -k "sidecar or prune or ephemeral or shutdown"`
- **Touches:** `src/hypershell/core/logging.py`, `tests/test_logging.py`.

## Phase P3 — server/cluster fork fd-leak hardening
**Satisfies:** R6 · **Depends on:** P1
**Goal:** a forked queue-manager child no longer holds the parent's log-slot lock alive, so a
killed `server`/`cluster` parent never ghost-locks its slot.

- [ ] In `core/queue.py`, close the inherited `_slot_locks` handles in the forked manager child
      via the existing post-fork initializer seam (`_tls_bootstrap` path, ~`:243-281`) or
      `os.register_at_fork(after_in_child=…)`. Import the logging handles list without creating an
      import cycle (a small `hypershell.core.logging.close_inherited_slot_locks()` helper is
      cleanest). No-op where there is no fork / on Windows. **Do not** touch RPC framing, TLS
      context, authkey, or the handshake/fingerprint (high-blast-radius §16).
- [ ] Note: `os.set_inheritable(False)` is useless here (exec-only; this is fork-without-exec).
- [ ] Tests (`@mark.unit`, POSIX-only `skipif` Windows): a raw `os.fork()` after a claim — assert
      the parent's `flock` is released once the parent closes iff the child closed its inherited
      copy (the child-close is what P3 adds). Keep it `os.fork`, not `multiprocessing`, to isolate
      the mechanism.
- **Verify:** `uv run pytest -v tests/test_logging.py -k "fork or leak or inherit or manager"`
- **Touches:** `src/hypershell/core/queue.py`, `src/hypershell/core/logging.py`, `tests/test_logging.py`.

## Phase P4 — End-to-end bound + regression + docs note
**Satisfies:** R1, R8, R9 · **Depends on:** P1, P2, P3
**Goal:** prove the whole thing holds under real CLI drive, codify the behaviors that already
work, and document the legitimate concurrency/rotation cases.

- [ ] `@mark.integration` test in a `temp_site` (`HYPERSHELL_LOGGING_FILE=enabled`): repeated
      serial `hsx` generations reclaim the canonical file and leave a bounded set of sidecars;
      simulate concurrency and assert distinct `-N.log` files are created and their data is never
      removed.
- [ ] Regression (R1/R8): existing role/host-scope/`resolve_log_path`/reclaim tests stay green;
      `main` un-host-scoped and un-pruned; cross-host isolation intact; no-lock/msvcrt fallbacks.
- [ ] Manual CLI drive (record in commit body):
      `.agents/factory/bin/temp_site.sh sh -c "HYPERSHELL_LOGGING_FILE=enabled; for i in 1 2 3; do seq 20 | uv run hsx -t 'echo {}' -N2 >/dev/null; done; find \"$HYPERSHELL_SITE\" -name '*.log.lock' | wc -l"`.
- [ ] **Docs (R9):** in the file-based-logging docs, note (a) per-host `-N.log` under legitimate
      same-host concurrency, (b) ephemeral, owner-stamped sidecars, (c) legitimate dangling
      rotated leaves when revisiting a host at lower concurrency.
- **Verify:** `uv run pytest -v -m integration tests/test_logging.py`
- **Touches:** `tests/test_logging.py`, `docs/…` (file-logging section).

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses authoritative).
2. Pre-flight: clean tree, on `fix/logging-file-slot-reclaim`, `develop` reachable.
3. Execute every `[ ]` (consult `PLAN.md`/`research/09` for detail).
4. Run the phase `verify:` — never advance on a checkbox alone.
5. Amend this file if reality diverges (`set_phase.py`); STOP only on a `GOAL.md` contradiction.
6. Mark phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
   P3 touches high-blast-radius `core/queue.py` → expect a human review gate.
