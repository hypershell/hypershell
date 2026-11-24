# Resume notes — bounded-inputs (paused 2026-11-24 for a host reboot)

These notes are from P1's builder. Delete them when P2 lands.

- **State:**
  - P1 is done (e4b9a853), after the plan gate (8d0ee70e) and a merge of develop d132d45c (9458d2d9).
  - P2–P4 are pending, with `current_phase: P2`.
  - The red list is 50/50 RED on 8548053b. The guards give 429 passed.
- **Rebuild first:**
  - a Python 3.14 venv for the worktree (`uv sync --frozen` with `UV_PROJECT_ENVIRONMENT` and a per-cycle `UV_CACHE_DIR`);
  - the Sphinx docs venv.

  Local PostgreSQL 18 must be running for `HS_TEST_POSTGRES`.
- **P2 must produce exactly the message forms P1 pins:**
  - `<name>: expected <form> from <A> to <B>, given <V>`, with thousands separators.
  - Durations carry ` s` on both bounds, and sizes carry ` bytes`.
  - Examples: `from 0 (unconstrained) to 32,767`; `from 0 s (exclusive) to 2,147,483,647 s`; the rate as `a number from 0.000001 to 1,000,000,000 tasks per second, or 0 (no limit)`; `server.evict: expected a duration from 10 s to 2,147,483,647 s, given '5s' (from: …)`.
  - Kind errors: the size form contains "powers of 1024" and "iB", the duration form "1h30m", and the delay form "--delay-start=-10m".
- **The API the tests use:**
  - `core/config_schema.py`:
    - `Range` (`kind`, `low`, `high`, `least`, `advise=(direction, limit, reason)`, `why`, `.parse(value, name, source=None)`, `.advised(value)`, `.text`, `.advice_text`);
    - `SETTINGS`;
    - `parse_setting(setting, value, name=None, source=None, whole=True)`;
    - `SettingError(ValueError)` with `.name` and `.source`;
    - `shown()`;
    - `ADVISED` (a set) and `PENDING` (a list, or None once logging is up).
  - `core/types.py`: `RangedOption` with `.setting` and `.name`, raising cmdkit's `ArgumentError`. The `parse_*` wrappers raise `UnitError` with the R4 text.
  - The option map is `OPTIONS` in `tests/test_bounded_table.py`.
- **One test fix in P2:** `test_bounded_parsers.py` expects a numeric `task.timeout` of `1.5` to read as `2`. Change it to `(1.5, 1.5)`, since numbers are kept as given (the guard `test_parse_period_accepted`). Also drop the `Fraction` case in `test_parse_duration_exact_when_not_whole`.
- **Before P4's whole-suite gate:** fetch and merge `origin/develop`. `update-group-and-legacy-filters` will likely have landed by then: reconcile `task.py`, `task_update_help.rst` and `invariants.md` §3, keeping both cycles' intent.
