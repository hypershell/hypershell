# 05 — Message text & exit mechanism

Topic: recommend the exact user-facing message and the exit mechanism for the missing-`croniter`
case, consistent with existing conventions.

## Reference conventions

**data/core.py missing-optional-dependency family** (import-time, DB engine):
- turso (`core.py:221`): `display_critical('Missing optional dependency "sqlalchemy-turso" needed for Turso', module=__name__)` → `sys.exit(exit_status.runtime_error)`
- uuid-utils (`:229`): `display_critical('Missing optional dependency "uuid-utils" (the "uuid7" extra) needed for TimescaleDB', ...)` → `runtime_error`
- psycopg (`:276`): `display_critical('Missing optional dependency "psycopg" (or its system "libpq") needed for PostgreSQL', ...)` → `runtime_error`

Pattern: **double-quoted package name**, `(the "<extra>" extra)`, `needed for <purpose>`.

**logging.py local idiom** (`panic`, `logging.py:587`):
```python
def panic(_msg: str) -> None:
    critical(_msg)                       # = getLogger('hypershell.core.logging').critical
    sys.exit(exit_status.bad_config)     # 3
```
Used everywhere else in `initialize_logging` for bad logging config: `_set_compression`
(`:599`), `_set_retention` (`:608`), and the invalid-`logging.file` branch (`:1077`). The
**existing (currently dead) croniter guard at `:1064-1068` already calls `panic(...)`**.

`display_critical` (`exceptions.py:51`) is a `functools.partial(_display_message, 'CRITICAL', ...)`
that `print(...)`s straight to `sys.stderr` — it bypasses the logging subsystem entirely.

## (1) Message wording — recommendation

```python
panic('Missing optional dependency "croniter" (the "cron" extra) needed for time-based log rotation')
```

- Mirrors the uuid7 template exactly: package `"croniter"`, extra `"cron"` (pyproject:
  `cron = ["croniter>=6.2.2"]`), purpose clause.
- Double-quote the names (matches the data/core.py family; a user greps for that shape). The
  logging.py string literals use `'…'` as the outer delimiter, so `"croniter"`/`"cron"` need no
  escaping.
- "time-based log rotation" is marginally clearer than the existing dead guard's "time-based
  rotation"; either is fine — keep it terse.

## (2) Exit mechanism — recommendation: **use the local `panic()`** (`critical()` + `exit_status.bad_config`)

Reasoning (local consistency wins over the cross-module reference):

1. **`panic` is the established idiom for the entire block.** Every other bad-logging-config
   failure in `initialize_logging` (`_set_compression`, `_set_retention`, invalid `logging.file`)
   panics. This is the same class of failure — a config value the install can't honor.
2. **Minimal diff / matches existing intent.** The maintainer already wrote the croniter guard
   with `panic` (`:1068`); the real fix is *ordering* (guard must run before the
   `TimedRotatingFileHandler(...)` construction at `:1057`, whose `__init__ → reset_interval`
   does `from croniter import croniter` at `:442` and raises during construction — that is the
   double traceback, chained off the `parse_bytes` `ValueError`). Keeping `panic` means not
   also swapping the reporting mechanism.
3. **`critical()` is reliable at this point.** `stream_handler` is attached to the `hypershell`
   logger at `:1003`, before the file block. `critical = getLogger('hypershell.core.logging')
   .critical` propagates up to `hypershell` and emits via `stream_handler` to stderr. The
   queue/file handlers aren't attached until `:1080`, but that's irrelevant — the other
   in-function panics at `:1072-1073` depend on exactly this same stream-handler path and work.
4. **`bad_config` (3) is semantically apt.** `logging.file.rotate = '@daily'` without the extra
   is a bad-config condition. psycopg/uuid-utils use `runtime_error` because they're a *different
   subsystem* (DB engine import in data/core.py) with its own convention and pre-logging context
   (hence `display_critical`'s direct-to-stderr print). Matching the local block beats matching a
   cross-module handler.

Honest counter-argument: `display_critical(...) + sys.exit(exit_status.runtime_error)` would make
the message grep-identical to the turso/uuid-utils/psycopg family and is guaranteed-stderr even
if the stream handler were somehow absent. logging.py already imports from exceptions
(`write_traceback`, `:45`) and exceptions.py does **not** import `hypershell.core.logging` (only
stdlib `logging`), so adding `display_critical` to that import is circular-import-safe. But it is
strictly worse on *local* consistency and adds an import for no reliability gain here. **Prefer
`panic`.**

## (3) exit_status constant — verified

`cmdkit.app.exit_status` exposes: `success=0, usage=1, bad_argument=2, bad_config=3,
keyboard_interrupt=4, runtime_error=5, uncaught_exception=6`. Both `bad_config` (3) and
`runtime_error` (5) exist and are **non-zero**. Recommended: `exit_status.bad_config` (3), reached
via `panic()` — no invented integer literal (invariants.md §12).

## Pre-existing note (out of scope for this topic)

The guard at `:1064-1068` is **dead code** for the reported path: the `ModuleNotFoundError` fires
during `TimedRotatingFileHandler` construction at `:1057` (and again if size-parse fails), before
control reaches `:1064`. Fixing that ordering is the substance of the fix; this topic only fixes
the *message and exit* the guard should emit. Flagged as the core defect other briefs own.
