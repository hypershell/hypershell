# 03 — croniter imports & packaging

## Every croniter reference in `src/` (grep -rn croniter src/)

Only **one file** references croniter: `src/hypershell/core/logging.py`.

| Line | Context | Kind |
|------|---------|------|
| 442  | `from croniter import croniter` inside `TimedRotatingFileHandler.reset_interval()` | **functional import** |
| 443  | `self.next_rotation = croniter(self.interval, self.prev_rotation).get_next(datetime)` | functional use |
| 1066 | `from croniter import croniter` — the availability probe in `initialize_logging()` | probe (import for side effect) |
| 1068 | `panic('Missing optional dependency \'croniter\' needed for time-based rotation')` | error message |

That's it — no other module imports or names croniter.

## The only functional import is `reset_interval`, reached from two call sites

`reset_interval()` is the sole place croniter is actually *used*. It is called from:
- `RotatingFileHandler.__init__` → `self.reset_interval()` (logging.py:388), and
- `RotatingFileHandler.rotate()` → `self.reset_interval()` (logging.py:411).

`rotate()` fires from `emit()` on either a SIGHUP (logging.py:417-419, on-demand log rotation) or
when `should_rotate()` trips (logging.py:420-421). So croniter is needed at handler construction
**and** on every subsequent rotation for the life of the process. The import at 442 is guarded by
`if self.interval != ROTATE_NEVER:` (ROTATE_NEVER = `'never'`, the default), so a `'never'` interval
never imports croniter; `@daily`/cron intervals do.

## A single upfront probe at init is sufficient

Confirmed. croniter's presence is fixed for the process lifetime — a module either imports at first
touch or never will; `sys.modules` caches the success, and an absent package stays absent. So one
availability check during `initialize_logging()` covers **all** later `reset_interval`/`rotate` calls
in the same process. This is exactly the intent of the probe at 1064-1068 (it warms `sys.modules` so
line 442 is a cache hit thereafter).

## Root-cause note (why the probe is currently ineffective — double traceback)

The probe at 1064-1068 sits **after** the handler is constructed at line 1057. In the reported case
(`rotate='@daily'`, croniter absent):
1. `parse_bytes('@daily')` raises `ValueError` → `except ValueError:` block (1055).
2. `TimedRotatingFileHandler(interval='@daily')` → `__init__` → `reset_interval()` → `from croniter
   import croniter` → **`ModuleNotFoundError`**, raised *inside* the `except ValueError` block.
3. Python chains it → "During handling of the above exception, another exception occurred" → the
   double traceback. The panic probe at 1066 is **never reached**.

The fix must probe/panic **before** constructing the `TimedRotatingFileHandler`. (Design detail, not
this brief's topic; noted for the planner. Also: `panic()` at logging.py:587 logs via
`getLogger(__name__).critical` then `sys.exit(exit_status.bad_config)` — the data/core.py reference
uses `display_critical`; worth confirming the logger is usable mid-init, but out of scope here.)

### Pre-existing / possibly out of scope
The probe condition is `isinstance(file_handler, TimedRotatingFileHandler)` (1064), which is **also
true for `interval='never'`** (the default when `[logging.file]` is configured without `rotate`, since
`parse_bytes('never')` raises ValueError and falls into the timed branch). A `'never'` handler never
imports croniter, yet the current probe would `panic` on it if croniter were absent. So file logging
with default/no rotation would spuriously demand the `cron` extra. Any fix should gate the probe on
`interval != ROTATE_NEVER` (or `interval in DATE_ELIGIBLE`/is-a-cron-expr) to avoid this regression.

## Packaging

- **End users:** croniter is *only* in the optional extra — `pyproject.toml:61`
  `cron = ["croniter>=6.2.2"]`. Not a base dependency. Confirmed absent unless `hypershell[cron]`.
- **Dev/CI:** separately listed in the `dev` dependency-group (`pyproject.toml:72`
  `"croniter>=6.2.2"`), which `uv sync` installs by default — so croniter **is present in CI/dev**,
  which is why the crash never surfaces in the test suite.
- `uv.lock` pins croniter 6.2.2 (lock lines 328-336; extra marker line 669; dev line 687).

## Docs / share — does invariants.md §12 apply?

§12 (`.agents/factory/invariants.md:134-138`) requires same-commit updates to `docs/_include/*.rst`
help snippets **and** `share/` completions **for a CLI/feature change**. This fix is neither: it only
improves the error message emitted when croniter is *absent*; behavior with croniter **present is
unchanged**, and no CLI surface, flag, or help snippet changes. So §12's same-commit doc/share rule
**does not strictly apply**.

Existing docs already document the requirement correctly and need no edit:
- `docs/install.rst:59` — the `cron` extra enables croniter-backed schedules.
- `docs/logging.rst:361-363` — "A cron expression (requires the `croniter` package)…".
- `share/man/man1/{hs,hsx,hyper-shell}.1:~2156` — "…`croniter` package). Sending SIGHUP rotates…".

(The `share/` `--rotate` completion hits are for the unrelated `hs initdb --rotate` DB-partition flag,
not `logging.file.rotate` — no overlap.) No doc/share change is functionally required; a planner could
optionally tweak wording so the new runtime message matches docs, but nothing is stale.
