---
slug: croniter-missing-guidance
title: Graceful guidance when croniter is missing for time-based log rotation
kind: fix
appetite: small
status: in_review
branch: fix/croniter-missing-guidance
base: develop
current_phase: done
last_updated: '2026-07-24'
phases:
- id: P1
  name: Core fix + unit proof (message, exit, no eager crash)
  status: done
  satisfies:
  - R1
  - R5
  depends_on: []
  parallel: false
  hammerable: false
  hill: crest
  verify: uv run pytest -v -m unit tests/test_logging.py -k croniter
- id: P2
  name: End-to-end regression proof (no double traceback; never/size/cron-present
    unaffected)
  status: done
  satisfies:
  - R2
  - R3
  - R4
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: crest
  verify: uv run pytest -v -m integration tests/test_logging.py -k croniter && .agents/factory/bin/temp_site.sh
    sh -c "HYPERSHELL_LOGGING_FILE_ROTATE=@daily uv run hs list --count"
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — Graceful guidance when croniter is missing for time-based log rotation

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with `uv run python .agents/factory/bin/next_phase.py
spec/croniter-missing-guidance/TECH.md`); the per-phase checklists below are the work.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs `01`–`05`.

## Conventions (apply to every phase)

- Invariants and code style come from [`AGENTS.md`](../../AGENTS.md) /
  [`invariants.md`](../../.agents/factory/invariants.md). Touched sections: **§8** (signals / SIGHUP
  rotate path — one upfront probe covers it), **§10** (config sentinels — gate on `ROTATE_NEVER`),
  **§12** (use `exit_status.bad_config`, tag tests; no docs/`share` change needed — behavior with
  croniter present is unchanged).
- One phase per `hs-build` invocation; one atomic commit containing **both** the code and the
  `TECH.md` state change. Subjects: `[fix] Build croniter-missing-guidance P<n>: …`.
- **No `Co-Authored-By` trailer** (repo convention).
- croniter is present in dev/CI (`pyproject` `dev` group), so the missing path is exercised only by
  injecting `sys.modules['croniter'] = None` — never by uninstalling.

---

## Phase P1 — Core fix + unit proof
**Satisfies:** R1, R5 · **Depends on:** —
**Goal:** The missing-`croniter` failure for a time-based rotation policy becomes a single, clean,
actionable message + non-zero exit, delivered *before* any handler is constructed. Proven directly by
unit tests on an extracted helper (no `_INIT`/full-init gymnastics).

- [x] In `src/hypershell/core/logging.py`, add a module-level helper near `panic()`:
      ```python
      def require_croniter() -> None:
          """Missing croniter is fatal only for time-based rotation; guide to the 'cron' extra."""
          try:
              import croniter  # noqa: F401
          except ImportError:
              panic('Missing optional dependency "croniter" (the "cron" extra) needed for time-based log rotation')
      ```
- [x] In `initialize_logging()`'s `[logging.file]` Namespace branch, inside the `except ValueError:`
      time-like branch, **before** `file_handler = TimedRotatingFileHandler(...)`, insert the gated
      call:
      ```python
      if file_policy != ROTATE_NEVER:
          require_croniter()
      ```
      (The gate is load-bearing: `'never'`/default also raises in `parse_bytes` and lands here but
      needs no croniter — mirrors `reset_interval`'s `interval != ROTATE_NEVER` guard.)
- [x] **Delete** the now-dead, ordering-broken block that follows the `try/except`
      (`if isinstance(file_handler, TimedRotatingFileHandler): try: from croniter import croniter …
      except ImportError: panic(...)`). This is what caused the reachable-but-wrong `'never'`
      false-positive; removing it plus the earlier gated check subsumes it.
- [x] Add unit tests in `tests/test_logging.py` (import path style matches the file's `import
      hypershell.core.logging as log` / `from cmdkit.app import exit_status`):
      - `test_require_croniter_panics_when_absent` (`@mark.unit`): `monkeypatch.setitem(sys.modules,
        'croniter', None)`; `with caplog.at_level('CRITICAL', logger='hypershell.core.logging'):`
        assert `pytest.raises(SystemExit)`, `ei.value.code == exit_status.bad_config`, and both
        `'croniter'` and `'cron'` appear in `caplog.text`.  *(R1 message + clean exit; R5 mechanism.)*
      - `test_require_croniter_noop_when_present` (`@mark.unit`): with croniter importable,
        `log.require_croniter()` returns `None` and raises nothing.
- **Verify:** `uv run pytest -v -m unit tests/test_logging.py -k croniter`
- **Touches:** `src/hypershell/core/logging.py`, `tests/test_logging.py`.

## Phase P2 — End-to-end regression proof
**Satisfies:** R2, R3, R4 · **Depends on:** P1
**Goal:** Prove the user-visible behavior end to end: the double traceback is gone, and the
`'never'`/size/cron-present paths are unaffected.

- [x] Add integration tests in `tests/test_logging.py` (subprocess pattern already used there —
      `Popen([sys.executable, '-c', <src>, <argv...>], env=..., stdout=PIPE, stderr=PIPE)`):
      - `test_missing_croniter_is_clean_not_double_traceback` (`@mark.integration`): run
        `python -c "import sys; sys.modules['croniter']=None; from hypershell import main;
        sys.exit(main())" list --count` with `env={**os.environ,
        'HYPERSHELL_LOGGING_FILE_ROTATE': '@daily'}` (this env var alone forces the Namespace
        branch — verified). Assert `returncode != 0`; stderr **contains** `'croniter'` and `'cron'`;
        stderr does **not** contain `Traceback (most recent call last)`, `During handling of the
        above exception`, or `ModuleNotFoundError`.  *(R2 + R1 end-to-end.)*
      - `test_missing_croniter_never_policy_starts_clean` (`@mark.integration`): same injection with
        `HYPERSHELL_LOGGING_FILE_ROTATE='never'` → `returncode == 0`, no `'croniter'` panic in
        stderr (guards the closed `'never'` false-positive; supports R3).
      - `test_missing_croniter_size_policy_starts_clean` (`@mark.integration`): same injection with
        `HYPERSHELL_LOGGING_FILE_ROTATE='2GB'` → `returncode == 0` (size rotation unaffected by a
        missing croniter; R4).
      - (Optional, croniter **present** — R3 happy path) a subprocess *without* the injection and
        `HYPERSHELL_LOGGING_FILE_ROTATE='@daily'` → `returncode == 0`.
      Use a `temp_site`-style env so nothing touches a real DB (follow the existing integration
      tests' fixture usage in this file).
- [x] Confirm the reproduction from `~/ISSUE.md` no longer double-tracebacks (covered by the first
      test).
- **Verify:** `uv run pytest -v -m integration tests/test_logging.py -k croniter && .agents/factory/bin/temp_site.sh sh -c "HYPERSHELL_LOGGING_FILE_ROTATE=@daily uv run hs list --count"`
- **Touches:** `tests/test_logging.py`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses authoritative).
2. Pre-flight: clean tree, on `fix/croniter-missing-guidance`, `develop` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file if reality diverges; STOP and escalate only on a `GOAL.md` contradiction (e.g. if
   the pre-existing digit-leading-cron misroute turns out to be in scope after all — see PLAN §5).
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
