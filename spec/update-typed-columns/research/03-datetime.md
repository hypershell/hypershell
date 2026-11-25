# 03 — Datetimes on SQLite and PostgreSQL (R6)

Research for `update-typed-columns`, 2026-11-24, at develop 39834600. Probes ran on Python 3.14.6 (venv `/tmp/claude-501/venv314-utc`),
SQLAlchemy 2.0.50, local PostgreSQL 18.7 (database `utc_research_dt`, since dropped), with `TZ=America/Indiana/Indianapolis`
unless stated. No source file was edited. Probe scripts were written to `$TMPDIR` (`sqlprobe.py`, `probe.sh`, `pgprobe*.sh`,
`fiso*.py`, `dst*.py`, `pgnaive.py`).

## 1. The column type and what each backend stores

- `src/hypershell/data/model.py:244`: `DATETIME = DateTime(timezone=True)`, a plain SQLAlchemy type with no TypeDecorator.
  It is used for `submit_time` (:433), `schedule_time` (:444), `start_time` and `completion_time` (:450–451).
- **SQLite** drops `tzinfo` and stores the wall time as given, as text. It returns a naive value. A string raises `TypeError`.
  ```
  str  -> StatementError (builtins.TypeError) SQLite DateTime type only accepts Python datetime and date objects as input.
  raw ('naive',       '2026-01-01 09:00:00.000000', 'text')
  raw ('aware+05',    '2026-01-01 14:00:00.000000', 'text')   # 14:00+05:00 stored as 14:00: wrong instant
  raw ('aware_local', '2026-01-01 09:00:00.000000', 'text')
  orm ... datetime.datetime(2026, 1, 1, 14, 0)                 # naive on read
  ```
- **PostgreSQL** (`timestamptz`): an aware value is stored as that instant. psycopg sends a naive value as a bare
  `timestamp`, and the server reads it in the **session TimeZone**, not in the process's TZ. Values come back aware, in
  the session zone. Probe `pgnaive.py` with `PGTZ=UTC TZ=America/Indiana/Indianapolis`:
  ```
  naive 09:00        -> 2026-01-01 09:00+00:00  epoch 1767258000   # read as 09:00 UTC: wrong
  naive.astimezone() -> 2026-01-01 14:00+00:00  epoch 1767276000   # 09:00 EST: right
  ```
  The local server's TimeZone is `America/Indiana/Indianapolis` (taken at initdb). The CI `postgres:*` images default
  to `Etc/UTC` (`.github/workflows/tests.yml:67–120`).

## 2. How HyperShell writes and shows them today

- **Writes.** Every stamp is aware local time from `datetime.now().astimezone()`:
  - submit: `model.py:668`, `:686`
  - schedule: `model.py:1023`
  - client start and completion: `client.py:852–860`, `:879`, `:908`
  - `--cancel`: `task.py:1194–1197`

  `client.py:880` already handles a mix of naive and aware values (`submit_time.astimezone()`, commented "Possible TZ
  aware submit_time").
- **`hs update` today.** `change_value` (`task.py:1036–1046`) passes a datetime column through `smart_coerce`
  (`core/types.py:40`), so a string, int or float reaches the database unparsed.
- **Display.** Neither `hs info` nor `hs list` converts the zone:
  - `to_json_type` (`core/types.py:65–67`) is `value.isoformat(sep=' ')`.
  - `format_json` (`core/pretty_print.py:44`) builds on it, and `hs info` uses it (`task.py:244`, `:1512`).
  - `hs list` uses it for its table, plain, json and csv output (`task.py:811`, `:845`, `:853`, `:867`).

  The result: SQLite prints the naive wall time, and PostgreSQL prints the aware value in the session zone, with its
  offset.
- **Careful:** `to_json_type` and `Task.to_json()` are also the wire format (`server.py:468`, `client.py:566`,
  `model.py:302`, `:389`, `relay/store.py:415`). A display change must not go there.

## 3. The parse

`datetime.fromisoformat` on 3.11.18, 3.12.13, 3.13.16 and 3.14.6 (`fiso.py`, `fiso2.py`):

| input | result | in GOAL grammar? |
|---|---|---|
| `2026-01-01`, `2026-01-01T09:00`, `2026-01-01 09:00:00.5`, `…T09:00Z`, `…T09:00+05:30` | parsed | yes |
| `20260101`, `20260101T0900`, `2026-01-01T0900` (basic format) | parsed | **no** |
| `2026-W01`, `2026-W01-1` (week dates → 2025-12-29) | parsed | **no** |
| `2026-01-01T09` (hour only), `…+05`, `…+0530`, `…+05:30:15` | parsed | **no** |
| `2026-01-01x09:00` (any separator), `2026-01-01t09:00`, `2026-01-01 09:00 Z` | parsed | **no** |
| `…09:00:00,5` (comma fraction), `…00.123456789` (silently truncated to µs) | parsed | grammar unclear |
| `2026-01-01T24:00` | **ValueError on 3.11–3.13; 2026-01-02 00:00 on 3.14** | no |
| `2026-001` (ordinal), `2026-01`, `2026-1-1`, `…T09:00z`, `now`, leading space | ValueError | no |

`fromisoformat` accepts much more than the GOAL's grammar, and it differs across the supported versions (`24:00`).
**Gate it with a regex first**, then call `fromisoformat` to check the calendar (`2026-02-30` and `09:00:60` are
already refused). A suggested regex, compiled with `re.ASCII` so `\d` cannot match Unicode digits:

```
^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?)?$
```

The regex refuses a date with an offset and no time, which fromisoformat also refuses. The plan has to rule on these
choices:

- `24:00`: the regex passes it and 3.14 accepts it. Either gate the hour to `([01]\d|2[0-3])`, or refuse
  `hour == 24` after the parse.
- A fraction of 7 or more digits: refuse it, or truncate it.
- Comma fractions: GNU `date -Ins` emits `…,123456789-05:00`, which the regex above refuses. `date -Iseconds`, the
  form in release note 6, prints `2026-11-24T10:00:00-05:00` and passes.

## 4. The storage rule: `datetime.fromisoformat(text).astimezone()`

`.astimezone()` gives an aware local value either way. A naive value is taken as local and gets its offset, and an
aware value is converted to local. That is what `--cancel` writes. SQLite then keeps the local wall time, and
PostgreSQL keeps the instant, whatever the session TimeZone.

**End-to-end probe on SQLite** (`TZ=America/Indiana/Indianapolis .agents/factory/bin/temp_site.sh sh probe.sh`;
`write.py` calls `Task.update(id, start_time=datetime.fromisoformat(t).astimezone())`). First, today's `hs update`:

```
today: hs update start_time=2026-01-01T09:00 -> exit 5 Error: (builtins.TypeError) SQLite DateTime type only accepts …
today: … now / epoch / today / x            -> exit 5 (same TypeError)
```

Then the three values written through `write.py`, and how `hs list` and `hs info` show them:

```
wrote 2026-01-01T09:00       -> 2026-01-01T09:00:00-05:00
wrote 2026-01-01T14:00Z      -> 2026-01-01T09:00:00-05:00
wrote 2026-01-01T19:00+05:00 -> 2026-01-01T09:00:00-05:00
hs list id start_time      -> 2026-01-01 09:00:00   (all three)
hs info <id>               ->      started: 2026-01-01 09:00:00 (waited: null)   (all three)
hs list --format json      -> "start_time": "2026-01-01 09:00:00"
```

**The same probe on PostgreSQL** (temp site with `HYPERSHELL_DATABASE_PROVIDER=postgres`, `_HOST`, `_PORT`, `_USER`,
`_PASSWORD` and `_DATABASE=utc_research_dt`; `HYPERSHELL_DATABASE_FILE` must be **unset**, or the run fails with
`ConfigurationError: Cannot provide 'file' if not SQLite`; then `hs initdb --yes`):

```
TZ=Indianapolis : hs list -> 2026-01-01 09:00:00-05:00 (all three); hs info -> started: 2026-01-01 09:00:00-05:00
TZ=Asia/Kolkata : hs list -> 2026-01-01 09:00:00-05:00   # session zone, not the process zone
PGTZ=UTC        : hs list -> 2026-01-01 14:00:00+00:00
```

The **instant agrees** on both backends for a naive value and for one with an offset. The **display does not**:

- SQLite prints `2026-01-01 09:00:00`.
- PostgreSQL prints `2026-01-01 09:00:00-05:00`, in the session TimeZone.
- In CI, where both zones are UTC, the two still differ by the `+00:00` suffix.

Today `submitted:` shows the same split (`2026-11-24 18:52:12.656743-05:00` on PostgreSQL).

To meet "shown the same" literally, the plan needs a display-only step in `format_json` and in the json and csv paths
of `hs list` (`task.py:853`, `:867`), never in `to_json_type`, since that is also the wire format. The step converts
each datetime to local time before formatting. There are two forms:

- **(A)** `v.astimezone().replace(tzinfo=None)`: both backends print the naive local time. This is SQLite's look
  today, and it changes PostgreSQL output.
- **(B)** `v.astimezone()`: both print local time with an offset. This changes SQLite output.

Either one changes visible output on one backend, which needs a release note. Otherwise the plan reads R6's "shown the
same" as "the same instant and wall time", and asks the maintainer.

**DST** (`dst.py`, America/Indiana/Indianapolis):

- **Ambiguous** (`2026-11-01T01:30`): fold 0 gives `-04:00`, the first occurrence, on every version. This is
  deterministic. SQLite keeps only the wall time `01:30`, so the instant is lost on read. That is inherent to SQLite
  and already true for the stamps HyperShell writes.
- **Nonexistent** (`2026-03-08T02:30`):
  - 3.12–3.14 give `03:30-04:00`; **3.11.18 gives `01:30-05:00`**, so the stored value depends on the Python version.
  - Detect it with `v.astimezone().replace(tzinfo=None) != v`, which prints `True` for 02:30 and stores `03:30` on SQLite.
  - **Recommend:** refuse a nonexistent local time (exit 2, "does not exist in local time"), and take fold 0 for an
    ambiguous one.
  - A value with an offset never hits either case.

## 5. Where the refusal must happen

- **PostgreSQL** (`pgprobe.sh`, today's `hs update`):
  ```
  start_time=2026-01-01T09:00       -> exit 0 | 2026-01-01 09:00:00-05:00
  start_time=now                    -> exit 0 | 2026-11-24 18:52:17.387936-05:00
  start_time=epoch                  -> exit 0 | 1969-12-31 19:00:00-05:00
  start_time=today                  -> exit 0 | 2026-11-24 00:00:00-05:00
  start_time=x                      -> exit 5 | psycopg.errors.InvalidDatetimeFormat … "x"
  start_time=2026-01-01T09:00+05:00 -> exit 0 | 2025-12-31 23:00:00-05:00
  ```
  The server parses the text, so the refusal has to happen in Python, in `change_value`, before anything is written.
- **SQLite:** every value except `none` exits 5. The error comes from SQLAlchemy's SQLite `DateTime` bind processor
  (`StatementError` wrapping `TypeError: SQLite DateTime type only accepts Python datetime and date objects`), because
  `smart_coerce` hands it a string (or an int or float, for `5`).

## 6. Session TimeZone, the process TZ, and tests

- On read, psycopg returns aware values in the **session TimeZone**. That zone is the server's default, or `PGTZ`
  when set, and the process's `TZ` has no effect. HyperShell converts nothing, so the PostgreSQL display follows the
  server's zone.
- With the storage rule above, writes no longer depend on the session zone. Only the display does (see the
  options in §4).
- **No test or CI step pins TZ.** `tests/conftest.py` sets only `HYPERSHELL_SITE` and `HYPERSHELL_DATABASE_FILE`
  (:88), and `tests.yml` has no `TZ`. GitHub runners are UTC, and the postgres images default to `Etc/UTC`.
- An R6 test under UTC proves nothing, since naive, local and UTC coincide. The R14 test should:
  - set a non-UTC zone (`monkeypatch.setenv('TZ', 'America/Indiana/Indianapolis')`, then `time.tzset()`) and restore
    it afterwards;
  - write `…T09:00`, `…T14:00Z` and `…T19:00+05:00`;
  - assert the same epoch (`value.timestamp()` after `.astimezone()` on read) on both backends;
  - give the PostgreSQL connection a session zone that differs from TZ (`PGTZ=UTC`, or
    `connect_args={'options': '-c timezone=UTC'}`), so the test catches a naive write.

## Recommendation

- **Parse:** apply the ASCII regex gate, then `datetime.fromisoformat`. Refuse anything else with exit 2, including
  hour 24, a week date, the basic format, `now`, `today` and `epoch`.
- **Store:** write `parsed.astimezone()`. Refuse a nonexistent local time, and take fold 0 for an ambiguous one.
- **Display:** the plan decides whether "shown the same" needs a display-only local conversion, (A) or (B). It must
  not change `to_json_type`.
- **Risks:**
  - SQLite has no offset, so a reader in another TZ sees the writer's wall time. This is true of every stamp today.
  - 3.11 resolves the DST gap differently from 3.12 and later.
  - A display change alters `hs list` and `hs info` output on one backend.
