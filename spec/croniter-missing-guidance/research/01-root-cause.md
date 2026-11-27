# Root cause: double traceback for `rotate='@daily'` without `croniter`

Slug: croniter-missing-guidance. Read-only research. All line numbers verified against
current source (2026-07-24, branch `develop`).

## Exact control flow (confirmed)

Entry: `initialize_logging()`, the `elif isinstance(file_config, Namespace):` branch,
`src/hypershell/core/logging.py`.

1. **`file_policy = '@daily'`** is read at `logging.py:1036`.
2. **`try:` at `logging.py:1046`** calls **`parse_bytes(file_policy)` at `logging.py:1047`**.
3. **`parse_bytes('@daily')` raises `ValueError`** — `src/hypershell/core/types.py:95-100`.
   `MEMORY_PATTERN` (`types.py:85`, `r'(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[KMGT]B)?'`) is
   applied via `re.match(value.upper())` at `types.py:97`. `re.match` is start-anchored and the
   `num` group is **required**; `'@DAILY'` begins with `@` (not `\d`), so the match fails
   entirely → `None` → `raise ValueError(...)` at `types.py:100`.
   Empirically confirmed: `MEMORY_PATTERN.match('@DAILY')` → no match. (Note `'never'` also
   fails to match and raises — it too takes the except branch; see gotcha below.)
4. **`except ValueError:` at `logging.py:1055`** runs. At **`logging.py:1057`** it constructs
   **`TimedRotatingFileHandler(filename=file_path, interval='@daily')`**.
5. Construction → `RotatingFileHandler.__init__` (`logging.py:381`) → calls
   **`self.reset_interval()` at `logging.py:388`**.
6. `TimedRotatingFileHandler.reset_interval` (`logging.py:438-443`): guard
   `if self.interval != ROTATE_NEVER:` at `logging.py:441` is **True** (`'@daily' != 'never'`),
   so **`from croniter import croniter` at `logging.py:442`** executes and raises
   **`ModuleNotFoundError`** (a subclass of `ImportError`) when the `cron` extra is absent.

## Claim-by-claim

- **(a) parse_bytes → ValueError (regex no-match): CONFIRMED.** `types.py:97-100`; verified empirically.
- **(b) except branch builds `TimedRotatingFileHandler` → `reset_interval` → croniter import →
  ModuleNotFoundError: CONFIRMED.** `logging.py:1057` → `:388` → `:442`.
- **(c) the standalone friendly block is UNREACHABLE (dead code) when croniter is missing:
  CONFIRMED.** The guard lives at `logging.py:1064-1068`:
  ```
  1064  if isinstance(file_handler, TimedRotatingFileHandler):
  1065      try:
  1066          from croniter import croniter
  1067      except ImportError:
  1068          panic('Missing optional dependency \'croniter\' needed for time-based rotation')
  ```
  Control can only reach line 1064 if line 1057 (construction) *succeeded*. When croniter is
  missing, line 1057 raises first, so lines 1064-1068 never run. Worse: the block is **dead in
  both cases** — if croniter is present the import at 1066 also succeeds, so `panic` at 1068 is
  never taken. The guard was clearly *intended* to catch the missing dep but was placed *after*
  the construction that already imports it. The good message text already exists at 1068 —
  only its position is wrong.
- **(d) why the two tracebacks chain: CONFIRMED.** The `ModuleNotFoundError` (step 6) is raised
  *while the interpreter is still handling the `ValueError`* — construction at `logging.py:1057`
  sits lexically inside the `except ValueError:` block (opened at 1055). Python sets the new
  exception's `__context__` to the in-flight `ValueError` (implicit chaining), so the default
  traceback printer emits the `ValueError` traceback, then
  *"During handling of the above exception, another exception occurred:"*, then the
  `ModuleNotFoundError` traceback. Neither is caught, so both propagate to the top and both print.

## Minimal structural change

Break the chain **and** reach the friendly path by performing the croniter availability check
*before* the `TimedRotatingFileHandler` is constructed — i.e. move the guard (currently
`logging.py:1064-1068`) into the `except ValueError:` block ahead of the construction at
`logging.py:1057`. Because the check then runs *before* any exception is in flight (the
`ValueError` from `parse_bytes` is already handled and no new `except` is nested), a missing
`croniter` yields a single clean `panic(...)` (log CRITICAL + `sys.exit(exit_status.bad_config)`;
`panic` at `logging.py:587-590`, `critical = getLogger(__name__).critical` at `logging.py:619`)
with **no** traceback and no chaining. The now-redundant block at 1064-1068 should be removed.

**Required gate (do not skip):** the check must fire **only when `file_policy != ROTATE_NEVER`**
(`ROTATE_NEVER='never'`, `logging.py:365`). `'never'` (and the Namespace-branch default,
`file_policy = DEFAULT_ROTATION_INTERVAL = 'never'`, `logging.py:366`/`:1036`) *also* fails
`parse_bytes` and enters the same `except` branch, but `reset_interval`'s guard at
`logging.py:441` skips the croniter import for `'never'` — so rotation-off configs work fine
without the extra today. An ungated pre-construction check would `panic` for those users =
a regression. Equivalent forms: gate on `file_policy != ROTATE_NEVER`, or on
`file_policy in DATE_ELIGIBLE or <looks-like-cron>` (broadest correct gate is simply
`!= ROTATE_NEVER`, since every non-`never` policy reaching this branch needs croniter).

## Pre-existing / possibly out of scope

- The friendly `panic` message text at `logging.py:1068` does not follow the psycopg/uuid7
  wording convention (`Missing optional dependency "croniter" (the "cron" extra) needed for
  time-based log rotation`). Improving wording is a separate (small) call — noted, not required
  by this root-cause topic.
- The reference psycopg pattern (`data/core.py:266-284`) uses `display_critical` +
  `sys.exit(exit_status.runtime_error)`; the in-module `panic` uses the logger's `critical` +
  `exit_status.bad_config`. Both are traceback-free; `panic` is the established convention
  *inside* `logging.py`. Choice of exit code / emitter is a design decision for the plan, not a
  correctness issue.
