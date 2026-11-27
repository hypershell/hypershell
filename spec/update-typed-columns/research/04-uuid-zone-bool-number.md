# 04 — uuid, zone, boolean and decimal columns

Read on `fix/update-typed-columns` (develop `39834600` + GOAL commit `bda42399`); `bounded-inputs`
read from `fix/bounded-inputs` at `bf759ec1` (the GOAL names `a0c227a6`; the branch has moved on
since, with a review cycle). Line numbers are those commits'. Probes ran on SQLite in a throwaway site
and as read-only `SELECT` casts on the local PostgreSQL 18 (no database created).

## 1. The `UUID` type and the uuid columns

- `UUID = Text().with_variant(POSTGRES_UUID(as_uuid=False), 'postgresql')` (`data/model.py:238`).
  Not a TypeDecorator: `TEXT` on SQLite, native `uuid` on PostgreSQL; it takes and returns **`str`**.
  It does **not** normalise anything. SQLite stores the text as given. PostgreSQL parses it and returns
  lower case.
- Probe (SQLite, today): `hs update ID source 0198F3A2-…-0123456789AB` stores the upper-case text,
  and `hs list`/`hs info` show it upper case. PostgreSQL `cast(:v as uuid)` accepts upper case,
  `{…}`, 32 hex digits without hyphens, and hyphens after any group of 4 (`0198f3a2-1b2c7d3e-…`).
  It returns all of them in the canonical lower 8-4-4-4-12 form. So the two backends disagree
  unless `hs update` gates the form itself.
- Columns, all `UUID`: `id` (`:428`, PK, not null), `submit_id` (`:432`, not null), `server_id`
  (`:442`), `client_id` (`:446`), `previous_id` (`:469`), `next_id` (`:470`), `source` (`:472`),
  `zone` (`:474`), each nullable unless stated. `Task.columns` types every one of them `str`
  (`:482-517`), the same as the text columns. That is why `change_value`'s text branch
  (`task.py:1042-1044`) takes them as text today. **The new check must look up a per-column kind map;
  `Task.columns`' Python type cannot tell uuid, zone and text apart.**
- Existing `UUID_PATTERN` (`task.py:129-131`):
  `^[0-9a-fA-F]{8}\b-…-[0-9a-fA-F]{12}$`, used with `.match` (`check_uuid` `:134-137`, the
  single-task form `:1118`). It accepts either case and is anchored, **but `$` accepts a trailing
  `\n`** (probe: `'<uuid>\n'` matches; `fullmatch` refuses it). The `\b`s add nothing.
- `uuid.UUID(text)` accepts `{…}`, `urn:uuid:…` and 32 hex digits without hyphens (probe), which the
  GOAL's 8-4-4-4-12 excludes.

**Recommend:** `uuid_value(text)`: `UUID_PATTERN.fullmatch(text.strip())` (strip, as `read_number`
does), else refuse. Return `text.strip().lower()`, which equals `str(uuid.UUID(x))` for that form.
Clearing with `none`/`null` (any case) comes before the gate, except for `submit_id` (R8). Don't use
`uuid.UUID` as the gate.

## 2. `source`

- The reserved ids are **uuids**: `DIRECT_SOURCE_ID = '00000000-0000-7000-8000-000000000000'`,
  `STDIN_SOURCE_ID = '…-000000000001'` (`model.py:163-167`). `<direct>`/`<stdin>` are the reserved
  rows' **`path`** values (`RESERVED_SOURCE_PATHS`, `:169-180`; `Source.reserved` `:2036-2050`).
  The uuid form covers both ids. **GOAL wording:** the Column-kinds row calls `<direct>`/`<stdin>`
  "reserved ids", but `source=<direct>` will be refused by the form. No alias is needed (adding one
  would be new grammar, a non-goal). The docs should name the ids, or say the form only.
- No `ForeignKey` or relationship anywhere in `model.py`. `Source` (`:1984-2000`) is looked up only
  for display: `resolve_source` (`task.py:1443-1460`) shows the raw id when no row exists. A
  referential check is not needed, which agrees with the GOAL.

## 3. `zone`

- `DEFAULT_ZONE_ID = '00000000-0000-7000-8000-000000000010'` (`:184`), `DEFAULT_ZONE_NAME = 'default'`
  (`:187`), `ZONE_NAMESPACE` (`:191`), `ZONE_NAME_MAX = 256` (`:195`).
- `zone_name(value)` (`:198-207`) strips the value. It raises `ValueError` if the value is empty, longer
  than 256, or not `isprintable()`. It allows any other characters and **keeps case**.
- `zone_id(name)` (`:210-214`) gives `DEFAULT_ZONE_ID` for `None`, `''` and exactly `'default'`, and
  `str(uuid5(ZONE_NAMESPACE, name))` for anything else. It is case-sensitive.
- `hs submit --zone`: `zone_name` in `check_arguments` (`submit.py:1655-1660`, as `ArgumentError`, exit 2).
  After the source checks it calls `Zone.ensure(name)` (`:1694-1695`). `Task.new` stores
  `DEFAULT_ZONE_ID` as NULL (`model.py:665-666`). `TaskSubmitApp` does the same (`task.py:106-115`),
  and so do `hs client`/`hs cluster` (`client.py:1783`, `cluster/__init__.py:415`).
- **"Recording the zone"** (C8) means `Zone.ensure(name)` (`model.py:2147-2168`). It is an idempotent
  upsert of the `Zone` row (`id`, `name`, `created`; `:2109-2122`; for `default` it records the
  default row). Readers never require that row. Without it, `hs list` and `hs info` resolve the id
  through `Zone.names_for_ids`/`resolve_zone` (`task.py:767-773`, `:1463-1478`) and **show the raw
  id instead of the name**. `Zone.ensure` commits on its own, so it is a write. Call it only after
  the change has passed the checks and the prompt (R9: "write nothing"), immediately before
  `Task.update`.
- The default-zone filter `_filter_zone` (`model.py:932-938`) maps `DEFAULT_ZONE_ID` to
  `zone IS NULL`. It needs the default zone stored as **NULL**, never the literal id. `Task.update`
  does no such mapping (only `Task.new` does), so `hs update` must.
- `bounded-inputs`' `ZONE_KEYS` (`config_schema.py:143-146`; also on develop `:131`) are the
  `[relay.zone.<name>]` configuration keys (`launcher`, `size`, `min`…). They do not apply to `task.zone`.

**Recommend** `zone_value(text) -> (id_or_None, name_or_None)`:
1. `text.strip().lower() in {'none', 'null'}` gives `None` (clear, R8).
2. `name = zone_name(text)`. A `ValueError` is refused, exit 2, with its message
   (`zone: expected a zone name …, given '…'`).
3. `id = zone_id(name)`. If it equals `DEFAULT_ZONE_ID`, the result is `None`; otherwise `(id, name)`.
   At write time, `Zone.ensure(name)` runs for a non-default name.

| Given | Result (probe) |
| --- | --- |
| `default` | NULL |
| `none`, `None`, `NULL`, `null` | NULL (step 1) |
| `Default` | `0902584e-c8e2-565b-8be3-cc7cfead3444`: a **different zone** named `Default`, as `hs submit --zone Default` gives |
| `x`, ` x ` | `ccc5166c-eb03-5579-ba02-9f2d6f273ee1` |
| `''`, `' '` | refused: "Zone name must not be empty" |
| `a<TAB>b`, 257 chars | refused: control characters / at most 256 |
| `00000000-0000-7000-8000-000000000010` | read as a **name**: `d86ecce7-f9fb-588a-9391-1822aed4f9e3`, a named zone, not the default, the same as `hs submit --zone <that>` |

Two points for the plan: (a) `hs submit --zone none` makes a zone *named* `none`, but `hs update
zone=none` clears it, as R7/R8 rule. So a zone literally named `none`/`null` cannot be set by
`hs update`. That follows from the GOAL and should go in the docs. (b) Taking the reserved id
literally as a name follows "as `hs submit --zone` reads it". If the plan would rather refuse a value
that is exactly a uuid, that adds a rule the GOAL does not state.

## 4. `retried`

- `retried` is `BOOLEAN NOT NULL` (`model.py:459`), `bool` in `Task.columns`.
- `BOOL_WORDS` (#113): develop `core/config_schema.py:176`, bounded-inputs `:192` (identical).
  `true/yes/on/1`, `false/no/off/0`, any case. `read_value(Kind.BOOL, text)` (develop `:219`,
  bounded-inputs `:235`) strips, lower-cases and looks the word up. Otherwise it raises
  `ValueError(FORMS[Kind.BOOL])`, which is `'a boolean (true/false, yes/no, on/off, 1/0)'`. Callers
  wrap it as `<key>: expected <form>, given <repr> (from: VAR)` (bounded-inputs `:387`).
- Probe: `TRUE`, `Yes`, `oN`, ` true ` give True. `OFF`, `0` give False. `none`, `x`, `2`, `''`, `t`
  are refused. **`none` is refused by `read_value` itself**, so R8 holds without a special case,
  though the plan may prefer an explicit "may not be empty" message. Today (probe): `retried yes`
  ends in exit 5 with `StatementError … Not a boolean value: 'yes'`.

## 5. `cores_max`, `memory_max`

- Both are `FLOAT` (`model.py:438-439`): emitted DDL `FLOAT` on both backends, which PostgreSQL makes
  `double precision` (probe `pg_typeof`); SQLite REAL affinity. Nullable; `float` in `Task.columns`.
- The client measures them: `check_resources(pid) -> (datetime, float cores, int bytes)`
  (`core/resource.py:91`), and the client keeps the running maximum
  (`client.py:932-933`, `max(self.task.cores_max or 0, cores)`), so memory_max is **bytes**.
- Display: `hs info` (normal/card): `cores_max` `f'{:.2f}'`, `memory_max` `format_bytes(int(…))`, and
  `'null'` when falsy (`task.py:1517-1519`). `hs list` table/json show the raw float (probe:
  `1073741824.5`, `1.234`). **Probe:** after `memory_max inf` (SQLite stores it), `hs info ID` fails:
  `OverflowError: cannot convert float infinity to integer`. R4's range closes this.
- Parser: `Range(Kind.NUMBER, …).parse` (bounded-inputs `Range._read` NUMBER branch `:699-710`;
  `read_number` `:213-221`, `NUMBER_FORM` `:199-200`, `INT_FORM` `:198`). Probe on a `Range(NUMBER,
  0, INT64_MAX)`:
  - refused: `nan`, `NaN`, `inf`, `-inf`, `infinity`, `0x10`, `4G` (no size units), `''`, `1__0`, `-1`,
    and `1e400` (→ inf → out of range); `9223372036854775808` and `9.3e18` (past 2⁶³−1);
    `32768` and `32767.5` against the 32,767 row.
  - accepted: `-0` → `0`; **`-0.0` → `-0.0`** (inside `0 …`); **`1_000` → `1000`** and `1_000.5`
    (underscores are the table's grammar); `+3`, `.5`, `1.`, `1e3`, ` 1.5 `.
  - A whole-number spelling returns `int` (`9223372036854775807` stays int). On PostgreSQL a float8 of
    that is 2⁶³.
- Message form: `memory_max: expected a number from 0 to 9,223,372,036,854,775,807, given '4G'`.
  NUMBER has no `KIND_DETAILS`, so no form list.

**Recommend:** two new `Range(Kind.NUMBER, 0, INT16_MAX | INT64_MAX, why=…)` rows (C9). Store
`float(value) + 0.0`, which gives a float on both backends and turns `-0.0` into `0.0`. Without
that, `hs info` treats `-0.0` as falsy and shows `null`. That rounding is a plan choice, not a GOAL
change.

## 6. Column types (model.py, emitted DDL)

| Column | line | PostgreSQL | SQLite | Null | `Task.columns` | GOAL kind |
| --- | --- | --- | --- | --- | --- | --- |
| `id` | 428 | UUID (PK) | TEXT | not null | str | never written |
| `group` | 429 | INTEGER | INTEGER | not null, default | int | #124 |
| `args` | 430 | TEXT | TEXT | not null | str | text, `none` refused |
| `submit_id` | 432 | UUID | TEXT | not null | str | uuid, refused |
| `submit_time` | 433 | TIMESTAMPTZ | DATETIME | not null | datetime | datetime |
| `cores` | 436 | SMALLINT | INTEGER | null | int | whole |
| `memory` | 437 | BIGINT | INTEGER | null | int | size |
| `cores_max`, `memory_max` | 438-439 | FLOAT (double) | FLOAT (REAL) | null | float | number |
| `timeout` | 440 | INTEGER | INTEGER | null | int | duration |
| `server_id`, `client_id` | 442, 446 | UUID | TEXT | null | str | uuid |
| `schedule_time`, `start_time`, `completion_time` | 444, 450, 451 | TIMESTAMPTZ | DATETIME | null | datetime | datetime |
| `exit_status` | 452 | SMALLINT | INTEGER | null | int | whole |
| `attempt` | 458 | SMALLINT | INTEGER | not null | int | whole, refused |
| `retried` | 459 | BOOLEAN | BOOLEAN | not null | bool | boolean, refused |
| `waited`, `duration` | 461-462 | INTEGER | INTEGER | null | int | duration |
| `previous_id`, `next_id`, `source` | 469, 470, 472 | UUID | TEXT | null | str | uuid |
| `zone` | 474 | UUID | TEXT | null | str | zone name |
| `part` | 478 | INTEGER, `DEFAULT 0` (server_default) | same | not null | int | whole, refused |
| `tag` | 480 | JSONB | JSON | not null, default `{}` | dict | refused whole |
| 8 text columns | 434, 443, 447, 449, 454-456, 473 | TEXT | TEXT | null | str | text |

`MISSING_COLUMN_DEFAULTS = {'part': '0'}` (`task.py:942`) only fills a column that a rotated SQLite
partition predates, in the union view (`select_task_columns`, `:926-938`). It has no bearing on writes.

**GOAL check.** All 34 `Task.columns` are covered. Two discrepancies, both wording, not kind:
(1) `cores_max`/`memory_max` are called `DOUBLE` in the GOAL; the DDL says `FLOAT`, which PostgreSQL
treats as double, so the meaning is right. (2) The `source` row's "reserved id (`<direct>`, `<stdin>`)"
uses the reserved *paths*; the ids are the uuids in §2. The SMALLINT/BIGINT limits are PostgreSQL's
only; SQLite INTEGER holds any 64-bit value, which is why the table's ranges are needed for parity.
