# 04 — Test strategy: croniter-missing-guidance

All claims below were run against the current tree (croniter IS installed in dev/CI).

## The bug, precisely (needed to design assertions)

In `initialize_logging()`'s `Namespace` branch, `parse_bytes(file_policy)` raises `ValueError`
for a time-like policy (`'@daily'`). Inside that `except ValueError:` block the code constructs
`TimedRotatingFileHandler(...)`, whose `__init__` → `reset_interval()` does `from croniter import
croniter` (`logging.py:442`) — raising `ModuleNotFoundError` **while the ValueError is being
handled**, producing a chained **double traceback**. The intended guard at `logging.py:1064-1068`
is **dead code**: it runs only *after* the handler is already built (and has already crashed).
Reproduced verbatim; the two load-bearing markers in stderr are:
`Traceback (most recent call last):` and `During handling of the above exception, another
exception occurred:`, ending in `ModuleNotFoundError` for croniter.

Because `initialize_logging` runs in `main()` (`__init__.py:139`) **before** `HyperShellApp.main`,
the exception escapes cmdkit's exception mapping entirely → raw traceback, exit code 1. Desired:
`panic(...)` → single CRITICAL line + `sys.exit(exit_status.bad_config)` (== 3).

## (1) Existing logging tests (`tests/test_logging.py`)

- Pattern: **unit** tests call small helpers directly (`role_from_command`, `default_file_for`,
  `claim_file_slot`, `resolve_log_path`, `read_lock_record`, `owner_alive`, `prune_stale_sidecars`)
  and monkeypatch module state (`log.fcntl.flock`, `log.LOCKING`); **integration** tests shell out
  via `Popen([sys.executable, '-c', _RUN_CLI, 'initdb', '--yes'], env=...)` or `tests.main([...])`.
- **Nothing currently exercises `initialize_logging` in-process, nor the rotation-policy /
  croniter branch.** No test resets the `_INIT` one-shot guard — confirming the convention:
  drive full init only out-of-process; unit-test the small helpers directly.
- Autouse fixture `_clean_slot_locks` resets `log.slot_locks` / `log._FINAL` after each test.

## (2) Simulating croniter absence — VERIFIED

`monkeypatch.setitem(sys.modules, 'croniter', None)` makes `import croniter` raise
`ModuleNotFoundError: import of croniter halted; None in sys.modules` (an `ImportError` subclass,
so the existing `except ImportError` catches it). Confirmed in-process and, for the subprocess
route, by injecting the same line before `from hypershell import main` (croniter is imported only
lazily, so hypershell import still succeeds and only rotation setup crashes).

## (3) Extract a directly-unit-testable helper (recommended)

Avoid resetting `_INIT` / running full `initialize_logging`. Extract the availability check into a
tiny module-level helper so the fix and its test bypass all init state:

```python
def require_croniter() -> None:
    """Panic (single actionable message) when time-based rotation needs the absent croniter."""
    try:
        import croniter  # noqa: F401
    except ImportError:
        panic('Missing optional dependency "croniter" (the "cron" extra) needed for time-based rotation')
```

The classification (time-like vs size-like vs `'never'`) stays in `initialize_logging`'s existing
`try parse_bytes / except ValueError` control flow; the fix calls `require_croniter()` inside the
`except ValueError:` branch **guarded by `if file_policy != ROTATE_NEVER:`** (parse_bytes also
raises ValueError for `'never'`, which needs NO croniter), **before** constructing the handler.
Message wording mirrors the repo idiom (`data/core.py:276`, uuid7 example): names the module and
the extra. Alternative shape: a pure predicate `rotation_needs_croniter(policy) -> bool`
(True for `'@daily'`/`'@midnight'`/other non-size non-`never`, False for `'512MB'`/`'never'`) plus
an inline guard — also fine; the no-arg guard is the smallest testable unit.

## (4) Asserting R1 (single actionable message) and R2 (no traceback / no chaining)

- **R1 (unit):** `panic`/`require_croniter` emits via `getLogger('hypershell.core.logging')
  .critical` and `sys.exit(bad_config)`. VERIFIED that `caplog` captures it even without full init
  (NullHandler doesn't block propagation):
  ```python
  def test_require_croniter_panics_when_absent(monkeypatch, caplog):
      monkeypatch.setitem(sys.modules, 'croniter', None)
      with caplog.at_level('CRITICAL', logger='hypershell.core.logging'):
          with pytest.raises(SystemExit) as ei:
              log.require_croniter()
      assert ei.value.code == exit_status.bad_config           # 3, single clean exit
      assert 'croniter' in caplog.text and 'cron' in caplog.text  # names dep + extra
  ```
  And a positive test: with croniter present, `require_croniter()` returns None, raises nothing.
- **R2 (integration, user-visible):** the definitive proof is a subprocess whose **stderr contains
  the guidance but neither traceback marker**. Raising `SystemExit` (not a re-raised error) is what
  structurally guarantees no chained traceback, so assert on the real process output:
  ```python
  _RUN_CLI_NO_CRONITER = "import sys; sys.modules['croniter'] = None; from hypershell import main; sys.exit(main())"

  @mark.integration
  def test_missing_croniter_is_clean_not_double_traceback(temp_site):
      env = {**os.environ, 'HYPERSHELL_LOGGING_FILE_ROTATE': '@daily'}
      proc = Popen([sys.executable, '-c', _RUN_CLI_NO_CRONITER, 'list', '--count'],
                   env=env, stdout=PIPE, stderr=PIPE)
      _, err = proc.communicate(); err = err.decode()
      assert proc.returncode != 0
      assert 'croniter' in err and 'cron' in err                      # R1 actionable
      assert 'Traceback (most recent call last)' not in err           # R2 no traceback
      assert 'During handling of the above exception' not in err      # R2 no chaining
      assert 'ModuleNotFoundError' not in err                         # raw dep error not leaked
  ```
  (`HYPERSHELL_LOGGING_FILE_ROTATE='@daily'` alone puts `logging.file` into the `Namespace` branch —
  VERIFIED; no explicit `file=enabled` needed.)

## (5) CLI-drive / `temp_site.sh` angle

`temp_site.sh` runs with croniter **present**, so it can only verify the **happy/size/never** paths,
NOT the missing path. Optional positive CLI check (should now start cleanly):
`.agents/factory/bin/temp_site.sh sh -c "HYPERSHELL_LOGGING_FILE_ROTATE=@daily uv run hs list --count"`.
The missing-croniter path is only reachable via the `sys.modules['croniter']=None` injection —
either the in-process unit test (R1) or the subprocess integration test (R2) above.

## Recommended tests to write

1. `test_require_croniter_panics_when_absent` (unit) — R1: SystemExit(bad_config) + message names
   `croniter` and `cron` (via `caplog`, `monkeypatch.setitem(sys.modules,'croniter',None)`).
2. `test_require_croniter_noop_when_present` (unit) — no raise when croniter importable.
3. (If a predicate is chosen) `test_rotation_needs_croniter_classification` (unit, parametrized) —
   `'@daily'`/`'@midnight'` → True; `'512MB'`/`'never'` → False.
4. `test_missing_croniter_is_clean_not_double_traceback` (integration) — R2: subprocess stderr has
   guidance, no `Traceback`/`During handling`/`ModuleNotFoundError`, nonzero exit.

## Pre-existing / possibly out of scope

- The guard block at `logging.py:1064-1068` is unreachable dead code once the fix moves the check
  earlier; it should be removed as part of the fix (not a separate bug, but note it).
- `initialize_logging` runs before cmdkit exception handling, so *any* init-time error yields a raw
  traceback + exit 1 — a broader hardening opportunity, out of scope here.
