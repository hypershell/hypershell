# 00 — Research digest (consolidated decisions)

Synthesis of briefs `01`–`05` plus an adversarial re-derivation of the root cause. Where briefs
disagreed, the single recommendation is stated here. All claims were verified against the current
tree (`croniter` **is** installed in dev/CI, so the failure only surfaces via simulated absence).

## Root cause — CONFIRMED (triple-verified, incl. adversarial pass)

In `initialize_logging()`'s `[logging.file]` **Namespace** branch (`core/logging.py`):

1. `parse_bytes(file_policy)` is used as a **size-vs-time discriminator**. It raises `ValueError`
   for any non-`[KMGT]B` string — `'@daily'`, `'@midnight'`, `'@weekly'`, **and** `'never'` all
   raise (`types.py:100`; `MEMORY_PATTERN` is `re.match`-anchored with a required numeric group).
2. The `except ValueError:` branch (`core/logging.py:1055`) constructs
   `TimedRotatingFileHandler(interval=file_policy)` (`:1057`) → `__init__` (`:381`) →
   `reset_interval()` (`:388`) → `if self.interval != ROTATE_NEVER:` (`:441`) →
   `from croniter import croniter` (`:442`) → **`ModuleNotFoundError`** when croniter is absent.
3. That error is raised **while the `ValueError` is still being handled** (line 1057 is lexically
   inside the `except`), so Python sets `ModuleNotFoundError.__context__ = ValueError` — the
   **double traceback** ("During handling of the above exception, another exception occurred").
   Neither is caught; `initialize_logging` runs in `main()` *before* cmdkit's exception mapping, so
   the raw chained traceback prints and the process exits 1.
4. The intended friendly guard at `core/logging.py:1063-1067`
   (`if isinstance(file_handler, TimedRotatingFileHandler): try: from croniter import croniter …
   except ImportError: panic(...)`) is **unreachable for the reported case** — construction at
   `:1057` already crashed.

## Two in-scope defects (one fix closes both)

- **D1 (reported):** time/cron policy (`@daily`) + missing croniter → chained double-traceback
  crash on every `hs`/`hsx` invocation.
- **D2 (latent, closely related — corrected by the adversarial pass):** the guard block is **not**
  globally dead. For `rotate='never'` (or a `[logging.file]` Namespace with *any* sub-key and no
  `rotate=`, which defaults to `'never'`) + missing croniter, construction **succeeds**
  (`reset_interval` skips croniter for `'never'`), control reaches `:1063`, and the unconditional
  `import croniter` there fires a **false** "croniter required" panic. So a rotation-off user is
  wrongly told to install the `cron` extra. The fix must **not** regress this.

## Chosen fix (minimal; agreed by all briefs + adversarial verifier)

Relocate the croniter check **into** the `except ValueError:` branch, **before** constructing the
handler, **gated on the policy**, and delete the dead `:1063-1067` block. Extract a tiny
directly-unit-testable helper:

```python
def require_croniter() -> None:
    """Missing croniter is fatal only for time-based rotation; guide to the 'cron' extra."""
    try:
        import croniter  # noqa: F401
    except ImportError:
        panic('Missing optional dependency "croniter" (the "cron" extra) needed for time-based log rotation')
```

Call site:

```python
except ValueError:
    if file_policy != ROTATE_NEVER:
        require_croniter()                     # panic-before-construct; skipped for 'never'
    set_naming_policy('date' if file_policy in DATE_ELIGIBLE else 'datetime')
    file_handler = TimedRotatingFileHandler(filename=file_path, interval=file_policy)
    info = [...]
# (delete the old `if isinstance(file_handler, TimedRotatingFileHandler): …` block)
```

Why it satisfies the contract:
- **R1/R2** — `@daily` + missing: `require_croniter()` panics **before** `:1057`, so `panic` →
  `critical()` + `sys.exit(exit_status.bad_config)` raises `SystemExit(int)`; the interpreter exits
  without rendering any traceback and there is no `except`-nested error to chain → single clean line,
  no double traceback.
- **R3** — `'never'`/default: the `file_policy != ROTATE_NEVER` gate skips the check; construction
  skips croniter → no panic (also **closes D2**). With croniter **present**, `@daily`/`@midnight`
  behave exactly as today.
- **R4** — size policy (`2GB`): `parse_bytes` succeeds, the `except` is never entered → untouched.

The `file_policy != ROTATE_NEVER` gate is the **load-bearing** detail (mirrors `reset_interval`'s
own `interval != ROTATE_NEVER` guard). An ungated pre-check would reproduce D2.

## Message & exit — DECIDED

- **Message:** `Missing optional dependency "croniter" (the "cron" extra) needed for time-based log
  rotation` — mirrors the `data/core.py` uuid7/psycopg convention (double-quoted package name,
  `(the "X" extra)`, purpose clause). Names the dependency *and* the remedy (R1, R5).
- **Mechanism:** the local `panic()` helper (`= critical()` + `sys.exit(exit_status.bad_config)`),
  **not** `display_critical`+`runtime_error`. Rationale: `panic()` is already the idiom for every
  other bad-logging-config failure inside `initialize_logging` (`_set_compression`,
  `_set_retention`, the invalid-`logging.file` branch) and is what the dead guard already used — the
  real fix is *ordering*, not swapping mechanism. `critical()` is visible here because
  `stream_handler` is attached at `:1003`, before the file block. `exit_status.bad_config` (3) is
  non-zero and semantically apt (a config error), satisfying §12 (no invented literal). This is
  "consistent in spirit" with the psycopg guidance (R5).

## Scope facts

- **Only croniter import site** in `src/` is `reset_interval` (reached from `__init__` and
  `rotate()`; `rotate()` fires on SIGHUP or `should_rotate`). A **single upfront probe at init**
  covers all later `reset_interval`/`rotate` calls — croniter presence is fixed per process
  (`sys.modules` caches). No change to the signal/rotation path itself (invariant §8 honored).
- **Packaging:** croniter is only in the `cron` optional extra (`pyproject.toml:61`) for end users
  and separately in the `dev` group (`:72`) — hence present in CI/dev; the crash never surfaces in
  the suite unless absence is simulated.
- **Docs/`share` (§12):** no CLI/help/completion surface changes and behavior-with-croniter is
  unchanged, so no same-commit docs/completion edit is required. Existing docs already document the
  requirement (`docs/…/install.rst`, `logging.rst`).

## Test strategy — DECIDED (see `04-test-strategy.md`)

- Simulate absence with `monkeypatch.setitem(sys.modules, 'croniter', None)` (makes `import
  croniter` raise `ImportError`; verified). Full `initialize_logging` is not re-run in-process
  (`_INIT` one-shot guard) — unit-test the extracted helper directly; prove the end-to-end
  no-double-traceback via a subprocess.
- **Unit (R1):** `require_croniter()` with croniter absent → `SystemExit(exit_status.bad_config)`
  and `'croniter'`+`'cron'` in `caplog.text`; with croniter present → returns `None`, no raise.
- **Integration (R2, user-visible):** subprocess `python -c "import sys;
  sys.modules['croniter']=None; from hypershell import main; sys.exit(main())" <cmd>` with
  `HYPERSHELL_LOGGING_FILE_ROTATE='@daily'` (alone triggers the Namespace branch — verified);
  assert stderr contains the guidance but **not** `Traceback (most recent call last)`, `During
  handling of the above exception`, or `ModuleNotFoundError`, and returncode ≠ 0.
- **Regression (R3/R4):** subprocess with croniter absent + `HYPERSHELL_LOGGING_FILE_ROTATE='never'`
  → clean start (D2 closed); + `='2GB'` → clean start (size unaffected). Plus a positive CLI drive
  with croniter present via `temp_site.sh`.

## Pre-existing bug — OUT OF SCOPE (flagged as a risk, not fixed here)

`parse_bytes` misroutes **digit-leading cron expressions**: `parse_bytes('30 2 * * *') → 30`,
`parse_bytes('0 0 * * *') → 0` (the regex consumes the leading number; the unit group is optional
and unanchored at the end). Such crons are silently sent to `SizeRotatingFileHandler` (rotate every
N bytes) and never reach the time path or croniter. This is independent of the croniter issue,
predates it, and is excluded by the GOAL non-goals (do not redesign the size-vs-time discriminator).
R3 speaks to `@daily`/`@midnight` (`DATE_ELIGIBLE`) and cron working "unchanged from today"; since
digit-leading crons are already broken today, leaving them is **not a regression**. Surfaced to the
human as an open question for a possible follow-up.
