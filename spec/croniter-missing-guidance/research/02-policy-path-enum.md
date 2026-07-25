# 02 — Rotation-policy path enumeration

Trace of every `logging.file` rotation-policy input through
`initialize_logging()` (`src/hypershell/core/logging.py`), for croniter present
and absent. Verified against source and live `parse_bytes` runs.

## parse_bytes ground truth (measured)

`parse_bytes` (`core/types.py:95`) does `MEMORY_PATTERN.match(value.upper())`
where `MEMORY_PATTERN = r'(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[KMGT]B)?'`. Because
`re.match` anchors only at the start and the `unit` group is optional, any
**digit-leading** string matches (consumes the leading number, ignores the rest).

| value | parse_bytes | raises ValueError? |
|-------|-------------|--------------------|
| `'never'` | — | YES |
| `'2GB'` | 2147483648 | no |
| `'@daily'` | — | YES |
| `'@midnight'` | — | YES |
| `'@weekly'` | — | YES |
| `'30 2 * * *'` | **30** | **no** (misroute) |
| `'0 0 * * *'` | **0** | **no** (misroute) |

## Two config shapes reach the handler build

- **plain string / bool** (`logging.file = "/path"` or `= true`), lines 1017–1028:
  always builds `TimedRotatingFileHandler(filename=...)` with the **default
  interval `'never'`**. `parse_bytes` is **not** called; there is **no** standalone
  croniter check. `reset_interval` skips croniter when `interval == 'never'`
  (`logging.py:441`). → works with **and without** croniter.
- **`[logging.file]` Namespace** (any sub-key set, incl. `rotate=`), lines 1030–1073:
  `file_policy = rotate or 'never'`; `try parse_bytes → SizeRotatingFileHandler`
  else `except ValueError → TimedRotatingFileHandler`; then a **standalone**
  `if isinstance(...TimedRotatingFileHandler): import croniter else panic(...)`
  block at **1064–1068**.

## Full policy → behavior table (Namespace branch unless noted)

| policy input | shape | parse_bytes | handler | needs croniter? | w/ croniter | w/o croniter |
|---|---|---|---|---|---|---|
| default `'never'` | plain str/bool | (not called) | Timed(`never`) | no | OK, no rotation | **OK** (no check, croniter skipped) |
| default `'never'` (no `rotate`) | Namespace | ValueError | Timed(`never`), naming `datetime` | no | OK, no rotation | **PANIC (false-positive)** at 1068 |
| `'2GB'` | Namespace | 2147483648 | Size, naming `count` | no | OK | OK |
| `'@daily'` | Namespace | ValueError | Timed(`@daily`), naming `date` | yes | OK | **DOUBLE traceback** at ctor 1057 |
| `'@midnight'` | Namespace | ValueError | Timed(`@midnight`), naming `date` | yes | OK | **DOUBLE traceback** at ctor 1057 |
| `'@weekly'` | Namespace | ValueError | Timed(`@weekly`), naming `datetime` | yes | OK | **DOUBLE traceback** at ctor 1057 |
| `'30 2 * * *'` | Namespace | **30** | **Size**, `count`, interval=30 bytes | no | **rotates every 30 B** | same wrong behavior |
| `'0 0 * * *'` | Namespace | **0** | **Size**, `count`, interval=0 | no | **rotates every message** | same wrong behavior |

## Answers to the critical questions

**Q1 — Does default `'never'` in a Namespace hit the standalone croniter panic
(false-positive)?** YES. `parse_bytes('never')` raises ValueError → `except`
branch builds `TimedRotatingFileHandler(interval='never')`. Its `__init__ →
reset_interval` **skips** croniter (`interval == ROTATE_NEVER`), so construction
succeeds even without croniter. Control then reaches the standalone
`isinstance(file_handler, TimedRotatingFileHandler)` check (1064), which
unconditionally imports croniter and `panic()`s if absent — a **false positive**:
`'never'` never needs croniter. So a user who merely sets, e.g.,
`logging.file.level = 'info'` (a `[logging.file]` section with no `rotate`) and
lacks croniter gets a spurious "Missing optional dependency 'croniter'" exit.
This is a real bug and directly adjacent to the reported fix — the fix must not
demand croniter for `'never'`.

**Q2 — Is a digit-leading cron expr misrouted to SizeRotatingFileHandler?**
YES (pre-existing, separate bug). `parse_bytes('30 2 * * *') → 30` and
`parse_bytes('0 0 * * *') → 0`; neither raises, so both take the **size** path:
`SizeRotatingFileHandler(interval='30 2 * * *')` with `count_interval=30`
(rotate every 30 bytes) or `0` (`should_rotate` is `count_bytes >= 0` → rotates
on **every** record). croniter is never consulted, so behavior is identical with
or without croniter — it just silently does the wrong thing. **Pre-existing /
out of scope** for the croniter-guidance fix, but worth flagging: the size-vs-time
discrimination via `parse_bytes` is unsound for any cron field list starting with
a number. (`@`-prefixed nicknames and word crons are safe because they fail the
regex.)

## Key structural finding for the fix

The reported **double traceback** is thrown at **line 1057** (the
`TimedRotatingFileHandler(...)` constructor, via `reset_interval`'s
`from croniter import croniter`) **inside the `except ValueError:` block** — so
Python chains it onto the just-handled `parse_bytes` ValueError ("During handling
of the above exception, another exception occurred"). The **standalone croniter
check at 1064–1068 is never reached** for `@daily`/`@midnight`/`@weekly`/named
crons — the constructor crashes first. That block is therefore effectively
**dead code for every real time policy**, and its **only** live effect is the
`'never'` **false-positive** in Q1.

Consequence for a fix: guarding croniter only at 1064 cannot prevent the reported
double traceback — the guard must precede/replace the `TimedRotatingFileHandler`
construction (e.g. probe `import croniter` and `panic()` cleanly before building
the timed handler when `file_policy != ROTATE_NEVER`, mirroring the
`data/core.py:266-284` psycopg pattern: catch `ImportError`, `display_critical`
naming `'croniter'` + the `'cron'` extra, `sys.exit`). The `'never'` case must be
exempted so the false-positive disappears.

Reference message style (`data/core.py`): `Missing optional dependency "psycopg"
(or its system "libpq") needed for PostgreSQL`; uuid7: `Missing optional
dependency "uuid-utils" (the "uuid7" extra) needed for TimescaleDB`. Extra name
is `cron` (`pyproject.toml [project.optional-dependencies] cron =
["croniter>=6.2.2"]`).
