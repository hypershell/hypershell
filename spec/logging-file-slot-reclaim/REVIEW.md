# REVIEW — Ephemeral log-lock sidecars + fd-leak/errno hardening

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** e9079df  ·  **Base:** develop  ·  **Date:** 2026-07-23
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

**Contract note.** `GOAL.md` was re-scoped mid-lifecycle (commit `26a2e1a`, 2026-07-23) — the
original "reclaim bug" premise was refuted by investigation and the contract re-targeted to sidecar
hygiene + fd-leak/errno hardening (R1–R9). The re-scope landed *after* planning but *before* any
build phase, so the entire P1–P4 build targeted the current contract. Human confirmed grading
against the re-scoped `GOAL.md` before this pass ran.

## Verification run

Commands actually executed and their outcomes (the spine of the review):

**Blind reviewer (fresh subagent, spec-excluded diff):**
- `uv run pytest -v tests/test_logging.py` → **36 passed** (2.52s)
- `uv run pytest -v -m integration tests/test_logging.py` → **6 passed**, 30 deselected
- `uv run pytest tests/test_server.py tests/test_client.py tests/test_cluster.py` → **86 passed**
  (exercises the rewired `SecureManager.start`/fork seam)
- `uv run pytest -v tests/test_queue_tls.py` → **39 passed**, incl. subprocess roundtrip +
  framing/fingerprint (§9 intact)
- `uv run sphinx-build docs docs/_build` → clean (only pre-existing `pkg_resources` deprecation; no
  new `logging.rst` warnings)
- CLI drive (throwaway `temp_site.sh`, `HYPERSHELL_LOGGING_FILE=enabled`): 3 serial `hsx`
  generations → canonical `.log` reclaimed each generation, **0** `.lock` sidecars left behind
  (R1/R3/R8); planted stale `-2.log.lock` + `-2.log`, rerun → stale sidecar swept, `-2.log` data
  preserved (R4/R5)
- `git status --porcelain` → empty (reviewer left tree clean)

**Orchestrator independent checks:**
- `git status --porcelain` → empty (confirmed clean before grading)
- `uv run pytest -q tests/test_logging.py` → **36 passed** (2.42s) — re-ran independently
- Read `git diff develop...HEAD -- src/hypershell/core/queue.py` by eye: change is confined to the
  post-fork child (`close_inherited_slot_locks()` + `install_process_context` guarded on
  `cfg is not None`) and unifying `start()` so the fd-close runs on **both** TLS and no-TLS forks.
  RPC framing, authkey, `connect()`/handshake, and serializer registration are untouched — §9
  honored.

## Requirement → evidence matrix

| R-ID | Implemented by (file) | Verified how | Status |
|------|-----------------------|--------------|--------|
| R1 | regression tests `test_serial_generations_reclaim_canonical_and_leave_no_sidecars`, `test_concurrent_holders_get_distinct_slots_and_data_survives` (`tests/test_logging.py`) | both pass; CLI drive confirms reclaim+append + legitimate concurrency | ✅ |
| R2 | `write_lock_record` (`core/logging.py:686`) writes `{v,pid,create_time,host,instance}` under held lock | `test_lock_record_round_trips` passes (asserts v==1, pid, host, instance) | ✅ |
| R3 | `finalize_logging` (`core/logging.py:876`) stops `queue_listener` then unlinks each sidecar under held lock; `atexit`-registered on first claim | `test_finalize_logging_drops_own_sidecar`, `test_clean_exit_removes_own_sidecar_via_atexit`; CLI drive (0 sidecars after 3 gens) | ✅ |
| R4 | `prune_stale_sidecars`/`prune_one_sidecar` (`core/logging.py:925,948`), gated on winning canonical slot + `DISTRIBUTED_ROLES`; flock-acquire is the safety gate | `test_prune_removes_stale_unlocked_sidecar`, `test_prune_keeps_live_held_sibling_sidecar`; CLI drive | ✅ |
| R5 | prune regex matches only `<root>-N<ext>.lock` (`core/logging.py:938`) | `test_prune_sidecar_never_touches_data_or_rotated_files`; CLI drive (`-2.log` survived intact) | ✅ |
| R6 | `close_inherited_slot_locks` (`core/logging.py:906`) invoked in `_child_bootstrap` (`core/queue.py:251`) before serving | `test_forked_child_closing_inherited_lock_releases_parent_slot`; orchestrator read of queue.py diff + OFD/flock mechanism | ✅ |
| R7 | `acquire_lock` (`core/logging.py:802`) returns False only on `EAGAIN`/`EWOULDBLOCK`, retries `EINTR`, raises `LockUnsupported` else; `claim_file_slot` degrades to canonical (not per-PID) | `test_unsupported_lock_errno_degrades_to_canonical` (ENOSYS/ENOLCK/EPERM), `test_no_locking_support_uses_canonical`, `test_contention_advances_to_next_slot` | ✅ |
| R8 | reclaim+append, cross-host isolation, `main` never pruned (`main` ∉ `DISTRIBUTED_ROLES`), msvcrt/no-lock fallbacks, unlink-only-under-held-lock in finalize + prune | `test_main_role_never_prunes_sidecars`; CLI drive; suite green | ✅ |
| R9 | `docs/logging.rst` adds the three notes (same-host `-N.log`, ephemeral owner-stamped sidecars, legitimate dangling rotated leaves) | `sphinx-build` clean | ✅ |

Unmapped changes (possible scope creep): **none material.** Commit `e9079df` renames slot-lock
internals (dropped leading underscores: `_slot_locks`→`slot_locks`, `_LOCKING`→`LOCKING`,
`_try_lock`→`try_lock`/`acquire_lock`) and makes the platform branch explicit. These are in-scope
cleanups of the R2/R6/R7/R8 machinery and match the project's documented style conventions (no
leading-underscore "private" names; explicit platform branch over import-probing); tests updated in
lockstep. Not gold-plating.

## Findings

**None.** No CONFIRMED or PLAUSIBLE findings survived refutation.

Candidates the reviewer investigated and dropped under the refutation protocol:
- *Inherited `atexit` finalizer double-runs / hangs the forked manager child* → dissolved:
  `multiprocessing/popen_fork.py` calls `atexit._clear()` in the child before `_bootstrap` and exits
  via `os._exit()`, so the inherited `finalize_logging` never runs in the child; the explicit
  `close_inherited_slot_locks()` fd-close is the necessary-and-sufficient fix. Confirmed by CPython
  source read + `test_forked_child_*` passing.
- *§9 queue.py weakening* → dissolved: diff only closes inherited fds + guards
  `install_process_context` on `cfg is not None`; framing/authkey/handshake/fingerprint untouched;
  `test_queue_tls.py` (39) + cluster suite (86) pass. (Independently re-verified by orchestrator.)
- *`write_lock_record` truncate not overwriting under append mode* → dissolved: `seek(0);truncate();
  write` writes at offset 0; `test_lock_record_round_trips` + `test_losing_probe_does_not_blank_the_record` pass.
- *`prune` crash on a lockless FS* → dissolved: `prune_one_sidecar` catches `LockUnsupported` and
  bails harmlessly per sidecar.

## Human-gate triggers

The diff touches the high-blast-radius core (`core/queue.py`, `core/logging.py`), but the
mandatory human sign-off gate fires only when a **CONFIRMED finding** touches that core. **No
CONFIRMED findings → gate not triggered.** The `core/queue.py` change was nonetheless independently
inspected by the orchestrator (see Verification run) and confirmed confined to the R6 post-fork
fd-close with §9 intact.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

Not run (plain `/hs-review` invocation). All four planned phases (P1–P4) are `done` in `TECH.md` and
every R-ID (R1–R9) maps to shipped, verified code; scope stayed within the small→small-medium
appetite (4 files: `core/logging.py`, `core/queue.py`, `tests/test_logging.py`, `docs/logging.rst`).
