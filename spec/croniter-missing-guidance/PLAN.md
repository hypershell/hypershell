# PLAN — Graceful guidance when croniter is missing for time-based log rotation

> **Status:** Draft for review · **Last updated:** 2026-07-24
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

A single, localized fix in `core/logging.py`'s `initialize_logging()`. The missing-`croniter`
failure is an **ordering bug**: the intended friendly guard runs *after* the
`TimedRotatingFileHandler` is constructed, but the constructor eagerly imports `croniter` and
crashes first — inside an `except ValueError:` block, so the size-parse `ValueError` chains into the
`ModuleNotFoundError` (the reported **double traceback**). We move the availability check **before**
handler construction, gate it on the rotation policy actually needing croniter (`file_policy !=
ROTATE_NEVER`), emit the existing repo-standard "missing optional dependency" message via `panic()`,
and delete the now-dead guard. Appetite is `small`: ~10 lines of source plus targeted tests, no new
surface area, no behavior change when croniter is present.

## 2. Design

Everything lives in **`src/hypershell/core/logging.py`**, `initialize_logging()`'s `[logging.file]`
**Namespace** branch (currently `~:1046-1067`). See [`research/00-digest.md`](research/00-digest.md)
for the triple-verified root cause and full path enumeration.

**Extract a helper** (module-level, near `panic()`):

```python
def require_croniter() -> None:
    """Missing croniter is fatal only for time-based rotation; guide to the 'cron' extra."""
    try:
        import croniter  # noqa: F401
    except ImportError:
        panic('Missing optional dependency "croniter" (the "cron" extra) needed for time-based log rotation')
```

**Rewrite the discriminator's time-like branch** so the check precedes construction and the
exception chain is broken (handler is no longer built inside a nested failure path):

```python
try:
    total_bytes = parse_bytes(file_policy)
    set_naming_policy('count')
    file_handler = SizeRotatingFileHandler(filename=file_path, interval=file_policy)
    info = [ ... ]                                   # size-like (unchanged)
except ValueError:
    if file_policy != ROTATE_NEVER:                  # 'never' also lands here but needs no croniter
        require_croniter()                           # panic-before-construct if absent
    set_naming_policy('date' if file_policy in DATE_ELIGIBLE else 'datetime')
    file_handler = TimedRotatingFileHandler(filename=file_path, interval=file_policy)
    info = [ ... ]                                   # time-like (unchanged)

# DELETE the dead block:
#   if isinstance(file_handler, TimedRotatingFileHandler):
#       try: from croniter import croniter
#       except ImportError: panic('Missing optional dependency \'croniter\' needed for time-based rotation')
```

**Why this is correct and complete:**
- `panic()` raises `SystemExit(int)`; raising it *before* `:1057` means no `TimedRotatingFileHandler`
  is constructed, no `croniter` import fires, and there is no nested exception to become
  `__context__` → **no traceback, no double traceback**.
- The `file_policy != ROTATE_NEVER` gate mirrors `TimedRotatingFileHandler.reset_interval`'s own
  `interval != ROTATE_NEVER` guard, so we require croniter in exactly the cases the handler would
  actually import it. This **also closes a latent bug (D2)**: today a `[logging.file]` Namespace with
  no `rotate=` (defaults to `'never'`) + missing croniter falsely reaches the dead guard and panics.
- A **single upfront probe** suffices: `reset_interval` (the only croniter site, reached from
  `__init__` and the SIGHUP `rotate()` path) cannot start needing croniter later — module presence is
  fixed per process.

**Message & exit:** the repo-standard optional-dependency wording (double-quoted package + `(the
"cron" extra)` + purpose), delivered via the local `panic()` (`critical()` +
`sys.exit(exit_status.bad_config)`), which is already the idiom for the other bad-logging-config exits
in this function. Consistent in spirit with the missing-`psycopg` guidance (R5). No invented integer
literal (§12).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `require_croniter()` panics **before** handler construction with a message naming `croniter` + the `cron` extra; `panic()` → `sys.exit(exit_status.bad_config)` (non-zero, clean). |
| R2 | Check moved out of the nested `except`-block construction path → `SystemExit` exits with no traceback and no chained `__context__` double traceback. |
| R3 | `if file_policy != ROTATE_NEVER` gate skips the check for `'never'`/default; with croniter present, `@daily`/`@midnight`/cron construct the timed handler exactly as before (branch body unchanged). |
| R4 | Size policies parse successfully in the `try:` → the `except` (and the whole croniter path) is never entered; independent of croniter. |
| R5 | Reuses `panic()` + the `data/core.py`-style "Missing optional dependency …" wording — same single-critical-line + clean-exit shape as the psycopg case. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`invariants.md`](../../.agents/factory/invariants.md) before research and again
after this design. Touched sections:

- **§8 Signals** — The SIGHUP → `rotate()` → `reset_interval()` path also imports croniter. The
  upfront probe guarantees croniter is present for the entire process whenever a cron timed handler
  is built, so every later `rotate()`/`reset_interval()` succeeds. The signal handling itself is not
  modified; no `reset_signal()` added/removed. **Honored.**
- **§10 Configuration** — Gate uses the `ROTATE_NEVER` sentinel; no mutation of the config singleton;
  reads the same `file_policy`/`DATE_ELIGIBLE` as today. **Honored.**
- **§12 Conventions** — Uses the `cmdkit.app.exit_status.bad_config` constant (no literal). No
  CLI/help/completion surface change and behavior-with-croniter is unchanged, so no same-commit
  `docs/_include`/`share/` edit is required (existing docs already state the requirement). New tests
  tagged `@mark.unit`/`@mark.integration`. **Honored.**

`core/logging.py` is a footgun file but **not** in the high-blast-radius set (§16 list:
`data/model.py`, `server.py`, `client.py`, `core/queue.py|tls.py|fsm.py|thread.py|signal.py`,
`cluster/remote.py|ssh.py`) — no mandatory human gate is triggered by touching it.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| — | — | — |

## 4. Rabbit holes (resolved)

- **Is it really a bug, or just a missing message?** → Confirmed a genuine **ordering + exception-
  chaining** bug: the friendly guard is unreachable for the reported case; the double traceback is
  the size-parse `ValueError` chained to the croniter `ModuleNotFoundError`
  ([`research/01-root-cause.md`](research/01-root-cause.md), adversarially re-derived).
- **Will fixing it regress rotation-off users?** → Yes it would, if ungated: `'never'`/default also
  enters the `except ValueError:` branch. The `file_policy != ROTATE_NEVER` gate is load-bearing and
  additionally **closes** the latent `'never'` false-positive (D2)
  ([`research/02-policy-path-enum.md`](research/02-policy-path-enum.md)).
- **Do other code paths need croniter (so one probe isn't enough)?** → No; `reset_interval` is the
  sole site; one upfront probe covers `__init__` and the SIGHUP `rotate()` path
  ([`research/03-imports-and-packaging.md`](research/03-imports-and-packaging.md)).
- **How do we test a missing package that CI always installs?** → `monkeypatch.setitem(sys.modules,
  'croniter', None)` (unit) and the same injection in a subprocess (integration), both verified
  ([`research/04-test-strategy.md`](research/04-test-strategy.md)).
- **`panic()` vs `display_critical`, and which exit code?** → `panic()` + `exit_status.bad_config`,
  for local consistency ([`research/05-message-and-exit.md`](research/05-message-and-exit.md)).

## 5. Risks & open questions

- **Pre-existing, out of scope — digit-leading cron expressions are misrouted.**
  `parse_bytes('30 2 * * *') → 30`, `parse_bytes('0 0 * * *') → 0`, so a numeric-leading cron
  expression is silently sent to `SizeRotatingFileHandler` (rotate every N bytes) and never reaches
  the time path or croniter. This predates the reported bug and is excluded by the GOAL non-goals
  (no discriminator redesign). It is **not a regression** (already broken today), so R3 — which
  covers `@daily`/`@midnight` and "unchanged from today" — still holds. **Open question for the
  human:** worth a separate follow-up fix? (Not addressed here.)
- **Broader hardening (noted, not in scope):** `initialize_logging` runs before cmdkit's exception
  mapping, so *any* init-time error still yields a raw traceback. This fix only covers the croniter
  path; a general init-error wrapper is a larger, separate effort.
- **Lint:** `import croniter  # noqa: F401` is a probe-only import; the existing (to-be-deleted) dead
  block imported croniter unused without a noqa, so the repo tolerates it, but the `noqa` keeps
  intent explicit.

## 6. Verification strategy

Seeds the per-phase `verify:` commands in [`TECH.md`](TECH.md). croniter is present in dev/CI, so the
missing path is exercised only via `sys.modules['croniter'] = None` injection.

- **Unit (R1):** `test_require_croniter_panics_when_absent` — `monkeypatch.setitem(sys.modules,
  'croniter', None)`; assert `SystemExit`, `code == exit_status.bad_config`, and `'croniter'` &
  `'cron'` in `caplog.text`. Plus `test_require_croniter_noop_when_present` — no raise when
  importable.
- **Integration (R1+R2, user-visible):** `test_missing_croniter_is_clean_not_double_traceback` — a
  subprocess `python -c "import sys; sys.modules['croniter']=None; from hypershell import main;
  sys.exit(main())" list --count` with `HYPERSHELL_LOGGING_FILE_ROTATE='@daily'`; assert stderr has
  the guidance and **not** `Traceback (most recent call last)` / `During handling of the above
  exception` / `ModuleNotFoundError`, and returncode ≠ 0.
- **Regression (R3+R4):** subprocess with croniter absent + `HYPERSHELL_LOGGING_FILE_ROTATE='never'`
  → starts cleanly (D2 closed); + `='2GB'` → starts cleanly (size path unaffected).
- **Positive CLI drive (R3, croniter present):**
  `.agents/factory/bin/temp_site.sh sh -c "HYPERSHELL_LOGGING_FILE_ROTATE=@daily uv run hs list --count"`
  starts cleanly and configures time-based rotation.
- **Full gate:** `uv run pytest -v tests/test_logging.py` green.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
