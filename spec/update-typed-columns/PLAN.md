# PLAN — `hs update` reads every column it writes by the column's kind

> **Status:** Draft for review · **Last updated:** 2026-11-24
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md) (R1–R17,
> amended `9aefc009`); the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/), consolidated in [`research/00-digest.md`](research/00-digest.md).

## 1. Summary

One pure reader in `src/hypershell/task.py` turns `hs update`'s arguments into typed changes before
the database is opened: each column through a per-column kind map, the ranged kinds through
`bounded-inputs`' `parse_setting`, the others through small readers for boolean, uuid, zone, text
and datetime; any other key becomes a tag (R16) unless it is one edit from a column (R17). The first
refusal exits 2 and nothing is opened, counted, asked or written. The write path in `data/model.py`
is unchanged. The table gains three rows. The build starts only after `bounded-inputs` lands on
`develop`: P0 merges it and pins the API this plan names (§2.9).

## 2. Design

### 2.1 Where the check runs (R9, R10, D8)

`TaskUpdateApp.run()` today: single-task detection, #119/#124's option refusals, the `--delete`
refusal, `ensuredb()`, then `update_legacy()` or `update_tasks()`, where `process_arguments()` reads
the changes after `ensuredb()` and before the count.

New order in `run()`:

1. Single-task detection and #119/#124's refusals, unchanged.
2. `--delete` with changes, unchanged.
3. **`self.changes = read_changes(self.update_args, single=legacy)`**, pure: no database, no
   `Zone.ensure`. It raises `ArgumentError` (exit 2) on the first refusal, in argument order (D7).
   `process_arguments()`'s other checks (`WhereClause` refusal, `--desc` without `--order-by`,
   `--order-by` without `--limit`) move with it; all are exit 2 already.
4. The advisory WARNINGs (§2.6), only once every change has been read.
5. `ensuredb()`, then the write. `update_legacy()` and `update_tasks()` take `self.changes` and stop
   parsing.

Refusing before `ensuredb()` follows #119/#124's option refusals: on SQLite `ensuredb()` creates the
database file, so a test can assert that a fresh site gets no file. `-f` never reaches step 3's
outcome.

`read_changes` returns a small record (a `NamedTuple` `Changes`):

- `columns: Dict[str, Any]`, column → stored value;
- `tags: Dict[str, Any]`, tag → value;
- `zones: List[str]`, zone names to record with `Zone.ensure` before the write (§2.4);
- `advice: List[Tuple[str, Any, str, str]]`, `(setting, value, column, text)` for §2.6;
- `shown: List[str]`, one prompt line per change (§2.7).

### 2.2 The kind map and `change_value` (R1–R8)

`COLUMN_KINDS: Final[Dict[str, ColumnKind]]` in `task.py`, one entry per `Task.columns` name (a test
pins `set(COLUMN_KINDS) == set(Task.columns)`), each with a reader and whether `none` clears.
`Task.columns`' Python types cannot serve: uuid, zone and text columns are all `str` there.

`change_value(field, text) -> Any` stays the one per-column seam (#124's name, so the in-process
grid is red on the base): it strips nothing for text, applies D3, then the column's reader, and
raises `ArgumentError` naming the column. Its refusals use the table's form,
`<column>: expected <form>[ from A to B], given <V>` with `shown(V)` from `config_schema`, so every
kind reads alike.

| Column(s) | Reader | `none` |
| --- | --- | --- |
| `cores` | `parse_setting('task.cores', text, name='cores')` | clears |
| `memory` | `parse_setting('task.memory', …)` (every size form, D1) | clears |
| `timeout` | `parse_setting('task.timeout', …)` (every duration form, whole seconds rounded up as `-W` does) | clears |
| `waited`, `duration` | `parse_setting('task.timeout', text, name=<column>)` (the `DURATION` class) | clears |
| `exit_status` | `parse_setting('column.exit_status', …)` (new) | clears |
| `attempt` | `parse_setting('server.attempts', text, name='attempt')` | refused |
| `part` | `parse_setting('option.part', text, name='part')` | refused |
| `cores_max`, `memory_max` | `parse_setting('column.cores_max' / 'column.memory_max', …)` (new), stored `float(v) + 0.0` (D17) | clears |
| `retried` | `read_value(Kind.BOOL, text)`; form `FORMS[Kind.BOOL]` | refused |
| `submit_id` | uuid (§2.3) | refused |
| `server_id`, `client_id`, `previous_id`, `next_id`, `source` | uuid (§2.3) | clears |
| `zone` | zone (§2.4) | clears (default zone) |
| `submit_time` | datetime (§2.5) | refused |
| `schedule_time`, `start_time`, `completion_time` | datetime (§2.5) | clears |
| `group` | `parse_group`, #124's message unchanged (R13) | refused (#124) |
| `args` | text as given | refused |
| `command`, `outpath`, `errpath`, `csvpath`, `submit_host`, `server_host`, `client_host`, `fingerprint` | text as given | clears |
| `tag` | never assigned whole: R11 refusal (§2.8) | — |
| `id` | never written: R10 refusal, both forms | — |

- **`none`, `null` (D3):** `text.strip().lower() in {'none', 'null'}`. A column that clears gets
  `None`. A column that may not be empty is handed to its reader, whose refusal names the column and
  the form, so `attempt=none` reads `attempt: expected a whole number from 1 to 32,767, given 'none'`;
  `args` and the other non-ranged ones say `… (<column> may not be empty), given 'none'`.
- **Ranged kinds** pass the text as given (D2). A `SettingError` becomes `ArgumentError(str(error))`
  (D5). An empty value, `x`, `true`, `nan`, `inf`, a fraction for a whole number, a duration for
  `memory`, a size for `timeout`, and one past either bound are refused by the parsers already.
- **`4.0` for a whole-number column** follows `-c 4.0` (D1). If `bounded-inputs`' F8 remediation
  accepts whole decimals in `Range._read`, `cores=4.0` writes 4, as `-c 4.0` would; if only in the tag
  path, both refuse. Tests compare the two instead of pinning either (§6).
- **Text columns** keep today's rule: the text as given, `none`/`null` clears (R13); `args` refuses
  them (R8).

### 2.3 uuid columns (R7, D13, D14)

`uuid_value(column, text)`: `UUID_PATTERN.fullmatch(text.strip())` (`fullmatch`, so a trailing
newline is refused), returning the stripped text lower-cased. Form named
`a uuid (8-4-4-4-12 hexadecimal digits)`. `source` takes the same form: its reserved ids are uuids
(`DIRECT_SOURCE_ID`, `STDIN_SOURCE_ID`); `<direct>` and `<stdin>` are those rows' paths and are
refused. No referential check (non-goal).

### 2.4 `zone` (R7, C8, D15)

`zone_value(text) -> Optional[str]`: `none`/`null` → `None`; else `name = zone_name(text)` (the
reader `hs submit --zone` uses; its `ValueError` becomes `zone: expected a zone name (…), given …`),
`ident = zone_id(name)`; `DEFAULT_ZONE_ID` → `None` (the default-zone filter matches `zone IS NULL`
only); else `ident`, and `name` joins `Changes.zones`. The app calls `Zone.ensure(name)` for each just
before the write, after the prompt: it is the write `hs submit --zone` makes so `hs list` and
`hs info` show the name. Names keep case (`Default` is another zone, as for `hs submit`); a zone
literally named `none` or `null` cannot be set by `hs update` (documented).

### 2.5 Datetimes (R6, C6, D18–D20)

`datetime_value(column, text)`:

1. Gate with an ASCII regex, the GOAL's grammar and nothing more:
   `^\d{4}-\d{2}-\d{2}(?:[T ](?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?)?$`
   (`re.ASCII`). This refuses the forms `fromisoformat` also takes on 3.11–3.14 (basic format, week
   dates, hour-only, any separator, `+05`, comma fractions) and `24:00`, which only 3.14 accepts.
   `now`, `today`, `epoch` and relative forms are refused here (R6).
2. `datetime.fromisoformat(text)` checks the calendar (`2026-02-30`).
3. A value with no offset is local time: `value.astimezone()` gives it the local offset (fold 0
   for an ambiguous hour, the same on every version). A nonexistent local time (a DST gap) is
   refused: `value.astimezone().replace(tzinfo=None) != value` (3.11 and 3.12+ resolve a gap
   differently, so storing it would depend on the Python version).
4. A value with an offset becomes the same instant in local time: `value.astimezone()`.

So the value written is always an aware local `datetime`, what `--cancel` writes. SQLite keeps the
local wall time (its driver drops the offset), PostgreSQL the instant, whatever its session zone:
both hold the same instant, and `hs info` and `hs list` show the same local wall time. Form named
`an ISO 8601 date, or date and time (e.g., 2026-01-01T09:00:00-05:00)`.

Display is not changed (Q1): PostgreSQL shows the time with its offset, in its session zone, as every
stamp shows today; SQLite shows it without one. Unifying the display is a change to every stamp in
`hs info`/`hs list`, outside this GOAL, and must never touch `to_json_type` (the wire format).

### 2.6 Advisory tier (R12)

`read_changes` records `(setting, value, column, text)` for `cores` (`task.cores`) and `attempt`
(`server.attempts`). After every change is read, `run()` calls
`advise(setting, value, column, given=text)` for each, which logs the table's one WARNING past the
limit (`cores: '1025' is above the advised 1,024 (…); running anyway`) and nothing at it. A refused
command logs no advice. No new limit (the three new rows have none). `main()` has flushed `PENDING`
by then, so the WARNING is immediate.

### 2.7 Separators, tags and the prompt (R11, R13, R16, R17, D9–D12)

Per filtered argument, in order:

1. `WhereClause.pattern` refusal, unchanged (keeps `cores==4` from becoming a change).
2. **The first separator splits**: the earlier of `=` and `:`. No argument accepted today changes
   meaning (an argument with `:` before `=` was refused today as an unknown field, and is refused now
   by the tag rules), and the refusal quotes what was typed. An argument with neither stays refused.
3. `KEY:VALUE` → a tag, never checked against the columns (R17).
4. `KEY=VALUE`:
   - `id` → R10 (`Cannot alter task "id"`, today's message); `tag` → R11 (§2.8);
   - KEY a column (exact, case-sensitive) → `change_value(KEY, VALUE)`;
   - KEY within one edit of a column, compared lower-cased (R17) → exit 2:
     `<KEY>: not a task column; did you mean <col>[ or <col>…]? To set a tag by that name, write
     <KEY>:VALUE`;
   - else a tag change, read as `KEY:VALUE` is (R16).

The single-task `hs update ID KEY VALUE` takes the same steps for KEY: `id` → R10; `tag` → today's
merge of `Tag.parse_cmdline_list([VALUE])`; a column → `change_value`; a near-miss → R17, whose way
out reads `write ID tag <KEY>:VALUE` (the single-task spelling of a tag, with the task's id); else
the tag `KEY:VALUE` merged as `ID tag KEY:VALUE` is today (R16).

**Distance (D12):** `edit_distance(a, b)`, optimal string alignment (insertion, deletion,
substitution, adjacent swap), about fifteen lines of stdlib Python in `task.py`, no dependency.
`near_columns(key)` returns the columns within 1 of `key.lower()` in `Task.columns` order (all names
are lower case), so `Cores=` (distance 0) and `Exit_Status=` are caught. Short names count:
`tags=`, `ip=`, `groups=`, `zones=`, also `pid=`, `port=`, `core=` (research/02 §8 lists them).

**Tag rules (D11):** every tag a command writes is checked alone, `Task.ensure_valid_tag({key:
value})`, so the early `return` after an empty value (`model.py:765`) cannot skip a later key, and
`model.py` is not edited. `ValueError`/`TypeError` become `ArgumentError` with the tag rules'
message, exit 2, before anything is opened. This covers R16's invalid key and turns today's exit 5
traceback for a bad filtered `KEY:VALUE` into exit 2, and adds the check to `ID tag KEY:VALUE`, which
has none today (Q2).

**Prompt (R16, D10):** when the filtered form prompts, it first prints, on stdout (where `input()`
writes), one line per change in argument order, the value as given, then today's question unchanged:

```
Changes:
  column  exit_status=0
  tag     exti_stauts:0
  tag     color:blue
  remove  tag size
Update affects 3 tasks, continue? yes/[no]:
```

A tag from `KEY=VALUE` is shown as `KEY:VALUE`, so a misspelling farther than one edit
(`exti_stauts=0`, two edits) shows as a tag. Nothing is printed under `-f`, with a count of 0, or for `--cancel`, `--revert` and `--delete`
alone (their columns are set after the prompt, as today).

### 2.8 `tag=VALUE` (R11)

Refused in the filtered form, exit 2:
`tag: tags are not assigned whole; change one with KEY=VALUE or KEY:VALUE, remove one with
--remove-tag (given: tag=<VALUE>)`. `KEY:VALUE`, `--remove-tag` and `ID tag KEY:VALUE` stay.

### 2.9 The table rows and the `bounded-inputs` API this plan depends on

New in `src/hypershell/core/config_schema.py`, beside the other constants, and in `SETTINGS`:

```python
EXIT_STATUS = Range(Kind.INT, -INT16_MAX - 1, INT16_MAX, why="A task's exit status is a 16-bit integer.")
PEAK_CORES  = Range(Kind.NUMBER, 0, INT16_MAX, why="A task's peak cores, measured; no host a task runs on has more.")
PEAK_MEMORY = Range(Kind.NUMBER, 0, INT64_MAX, per=' bytes', why="A task's peak memory in bytes, measured.")
SETTINGS: 'column.exit_status', 'column.cores_max', 'column.memory_max'
```

`setting_for(path)` excludes `column.` as it excludes `option.`, so `hs config set` and the
environment layer never treat a column row as a configuration key; `tests/test_bounded_table.py`
gains `COLUMN_ONLY` beside `OPTION_ONLY` and samples in `RANGES`. The `why=` wording is the
builder's, in the table's style.

**The API relied on** (as on `fix/bounded-inputs` `bf759ec1`; P0's verify imports and checks each,
so a remediation change fails at the merge, not mid-build):

- `hypershell.core.config_schema`: `SETTINGS` with `task.cores`, `task.memory`, `task.timeout`,
  `server.attempts`, `option.part`, `option.group`; `Range` (`kind`, `low`, `high`, `why`, `per`,
  `.parse(value, name, source=None, whole=True)`, `.text`); `Kind.INT`, `Kind.NUMBER`, `Kind.BOOL`;
  `parse_setting(setting, value, name=None, source=None, whole=True)`; `SettingError` (a
  `ValueError`; `str()` is `<name>: expected <form> from A to B, given V`); `advise(setting, value,
  name=None, given=None) -> bool` (once per setting per process); `setting_for(path)` (skips
  `option.`); `read_value(Kind.BOOL, text)` and `FORMS[Kind.BOOL]`; `shown(value)`; `INT16_MAX`,
  `INT64_MAX`.
- `hypershell.core.exceptions`: `ReadingMixin` on `TaskUpdateApp` (wraps argument parsing only).
- `hypershell.data.model`: `parse_group(text)` and `TaskGroupError` (#124, rebased on
  `option.group`); `zone_name`, `zone_id`, `DEFAULT_ZONE_ID`, `Zone.ensure` (develop).
- Tests: `tests/bounded_checks.py` (`refusal`, `assert_one_refusal`), `tests/test_bounded_table.py`
  (`OPTION_ONLY`, `RANGES`), `tests/test_bounded_advisory.py`'s in-process advice capture.
- Not relied on: `ensure_resources`, `RESOURCE_SETTINGS`, `TagAdvice` (tag paths, under F2/F5
  remediation). If the remediation moves `-t` tag reading or whole-decimal acceptance into
  `Range._read`, this plan inherits it through `parse_setting`; P0 records what landed.

### 2.10 Docs, help, completions, constitution (R15, R16, R17)

- `UPDATE_HELP`, `docs/_include/task_update_desc.rst` and `docs/_include/task_update_help.rst`:
  every column is read by its kind; the forms for `memory`, `timeout`, `waited`, `duration`,
  `retried`, the datetime columns, the uuid columns and `zone`; a **column list** in
  `task_update_desc.rst` giving each ranged column's `Range.text` (the three new rows among them; this
  is where the table's documentation lists them, there being no single page for the table); that any
  other value exits 2 before anything is counted, asked or written; any key that is not a column is a
  tag, one edit from a column is refused, `KEY:VALUE` always sets a tag; the first separator splits.
- `docs/cli/task_update.rst`: `.. versionchanged:: 2.9.0` entries for each behaviour change, top-level
  commands only.
- `share/bash_completion.d/hs` and `share/zsh/site-functions/_hs`: stop offering `tag=` (R11), the
  same commit as the refusal.
- `AGENTS.md` and `.agents/factory/invariants.md` §3 (lockstep): `hs update` reads every column by
  its kind through `COLUMN_KINDS`; a new `Task` column gets its kind there in the same commit (pinned
  by the completeness test).
- Release note: the squash body, drafted by `hs-publish` from GOAL's eleven user-visible changes.

### Requirement → design map

| R-ID | Design element(s) | Phase |
| --- | --- | --- |
| R1 | `COLUMN_KINDS` covers every column; one reader for both forms; stored Python types bind alike on both backends | P2–P4 |
| R2 | `cores`/`memory`/`timeout` through `task.*` entries (§2.2) | P2 |
| R3 | `exit_status` (new row), `attempt`, `part`, `waited`, `duration` (§2.2) | P2 |
| R4 | Peak rows, `Kind.NUMBER`, stored as float (§2.2, §2.9) | P2 |
| R5 | `read_value(Kind.BOOL)` for `retried` | P3 |
| R6 | Regex gate, `fromisoformat`, `.astimezone()`, DST gap refused (§2.5) | P4 |
| R7 | `uuid_value`, `zone_value`, `Zone.ensure` after the prompt (§2.3, §2.4) | P3 |
| R8 | D3: `none`/`null` clears or is refused per `COLUMN_KINDS` | P2–P4 |
| R9 | `read_changes` before `ensuredb()`, first refusal, `ArgumentError` (§2.1) | P2 |
| R10 | `id` refused in both forms in `read_changes` | P2 |
| R11 | `tag=` refusal (§2.8); completions drop `tag=` | P5 |
| R12 | `advise()` after reading (§2.6) | P2 |
| R13 | group, text columns, action options, #119/#124 refusals unchanged; unknown-field refusals replaced | P2, P5 |
| R14 | Test layers (§6), both forms, both backends, CI list | P1, P6 |
| R15 | §2.10, per phase | P2–P5 |
| R16 | Non-column `KEY=VALUE` and `ID KEY VALUE` as tags; prompt lines (§2.7) | P5 |
| R17 | `edit_distance`, `near_columns`, the refusal and the `:` way out (§2.7) | P5 |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before research
and again after this design.

- **§1 Task lifecycle.** `hs update` can still set `schedule_time`, `completion_time` and
  `exit_status`, as today (R1); the state predicates read the same columns. The writes stay
  `Task.update`, `Task.update_selected` and `Task.update_matching` with their contention handling;
  this plan changes only the values handed to them. `data/model.py` is not edited (D11 checks tags
  one at a time instead of fixing `ensure_valid_tag`'s early `return`).
- **§2 `exit_status`.** The new row spans −32,768 … 32,767, covering 0–255, the signal statuses,
  `CANCEL_STATUS` and the `-1001`/`-1002` sentinels. No query changes.
- **§3 Retry model.** `retried` and `attempt` stay writable (C1); resetting `retried` is the known
  unguarded path, unchanged. Group writes keep `parse_group` (#124). No new column, so
  `retry_columns`/`result_columns` are untouched. The invariants entry gains the `COLUMN_KINDS` rule
  (§2.10).
- **§6 Payload decode.** No change to `to_json_type`/`from_json` (the datetime display is left as
  is, D20).
- **§10 Configuration.** The three rows join `SETTINGS` without becoming configuration keys
  (`setting_for` excludes `column.`); `hs config set` and the `HYPERSHELL_*` layer are unchanged.
  `advise` keeps its once-per-setting rule.
- **§12 Conventions.** Help, `_include` snippets and completions change in the commit that changes
  the behaviour; `versionchanged:: 2.9.0` markers; exit statuses via `ArgumentError`/`cmdkit`
  constants; `@mark.unit`/`@mark.integration` only; Python 3.11–3.14 (the regex gate removes
  `fromisoformat`'s version differences; the DST-gap refusal removes 3.11's).
- High-blast-radius files touched: **none** (`core/config_schema.py` is `core/*`, so `parallel:
  false`, but not on the list).

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
| --- | --- | --- |
| `SETTINGS` holds three `column.` rows that are neither configuration keys nor options; `setting_for` excludes a second prefix | GOAL (C3, C9) puts the rows in the table, and `setting_for` treats any non-`option.` row as a configuration key | Standalone `Range`s outside `SETTINGS` (bounded-inputs' PLAN §2.7) contradict the GOAL; naming them `option.` would misname them |
| The CI `tests` job's timeout rises from 45 to 60 minutes | R14's matrix adds about 7 minutes of SQLite subprocess tests to a job that already takes 35–42 | A thinner matrix would not meet R14 (each bound and one past, every column, both forms) |

## 4. Rabbit holes (resolved)

- **The table's API is still moving** → the plan calls only `parse_setting`, `SettingError`,
  `advise`, `read_value`, `shown` and three constants, not the tag path under remediation; P0 imports
  each and fails the merge if one moved ([`research/01-bounded-api.md`](research/01-bounded-api.md)).
- **A row that is a column, not a key** → `column.` prefix and `setting_for` exclusion (research/01 §3).
- **Where to refuse without writing** → before `ensuredb()`; `Zone.ensure` deferred past the prompt
  ([`research/02-update-path.md`](research/02-update-path.md) §1, [`research/04`](research/04-uuid-zone-bool-number.md) §3).
- **Separators and the tag path** → first separator splits; every tag checked alone (research/02 §2–§3).
- **Datetimes on two backends** → regex gate, `.astimezone()`; same instant probed end to end on
  both ([`research/03-datetime.md`](research/03-datetime.md) §4); display left as is (Q1).
- **uuid forms PostgreSQL accepts and SQLite stores raw** → `fullmatch`, lower case (research/04 §1).
- **Zone semantics** → `zone_name`/`zone_id`, default → NULL, `Zone.ensure` (research/04 §3).
- **A matrix too slow for CI** → three layers, per-(column, form, backend) nodes; ~15 minutes on
  both backends ([`research/05-tests-ci-tooling.md`](research/05-tests-ci-tooling.md) §7).
- **R17's distance and ties** → OSA, 15 lines, stdlib; no ties among today's columns (research/02 §8).

## 5. Risks & open questions

Questions for the maintainer, each with a recommendation; the plan is written to the recommendation.

- **Q1 — "shown the same" (R6).** The instant and the local wall time agree on both backends, but
  `hs info`/`hs list` print a PostgreSQL time with its offset, in the server's session zone, and a
  SQLite time without one, as they do for every stamp today. **Recommend:** read R6 as the same
  instant, shown at the same wall time where the session zone is local; leave the display alone and
  seed a separate cycle to show every stamp in the local zone. The alternative, a display-only local
  conversion in `format_json` and `hs list`'s json/csv paths, changes the output of every stamp on
  one backend and needs its own release note.
- **Q2 — Tag rules on every tag path.** R16 needs the tag key rules for a non-column key. Applying
  them to every tag (D11) also turns a bad filtered `KEY:VALUE` from exit 5 with a traceback into
  exit 2, and gives `ID tag KEY:VALUE` the check it lacks today (it stores `a b:1` and `memory:4X`).
  R11 and R13 say those paths behave "as today". **Recommend:** check every tag path alike, listed as
  a behaviour change in the release note. The fallback is to check only the R16 paths.
- **Q3 — R17's way out in the single-task form.** `hs update ID exit_stauts:VALUE` is not the
  single-task form. **Recommend:** the single-task message says `write ID tag exit_stauts:VALUE`
  with the task's id; the filtered message is the GOAL's example word for word.
- **Q4 — Row names.** **Recommend** `column.exit_status`, `column.cores_max`, `column.memory_max`
  and the `setting_for` exclusion (§2.9).
- **Q5 — `-0.0` and whole-number text for the peaks.** **Recommend** storing `float(v) + 0.0`
  (`-0.0` → `0.0`, `3` → `3.0`); otherwise `hs info` shows `-0.0` as `null` and SQLite stores an
  integer.
- **Q6 — DST.** **Recommend** refusing a nonexistent local time (exit 2, "does not exist in local
  time") and taking the first of an ambiguous hour; storing a gap time would depend on the Python
  version.

Residual risks:

- **The dependency.** The build waits for `bounded-inputs` on `develop`. P0 checks the API (§2.9);
  if F8 makes `Range._read` accept `4.0`, `cores=4.0` writes 4, which the tests accept by comparing
  with `hs submit -c 4.0`. A remediation that renames `parse_setting`, `advise` or the six entries
  stops P0 and returns to this plan.
- **Advice once per process.** A configuration value past `task.cores`' limit warns at load and
  silences `hs update`'s WARNING in the same process (`ADVISED` is per setting). Accepted as the
  table's rule; the R12 tests use a site without such a value.
- **R17 catches short keys** (`pid`, `ip`, `in`, `port`, `core`, `done`, …, research/02 §8). The GOAL
  accepts it; the help lists the `:` way out.
- **SQLite keeps wall time only.** A reader in another zone sees the writer's local time; true of
  every stamp today.
- **Red-list cost.** `red_check.sh` runs each node alone (~3 s); the list is held to layers 2–3 and a
  few layer-1 nodes per kind, so P1's check stays within its 5,400 s bound.
- **Seed, not fixed here:** `Task.ensure_valid_tag` stops at the first empty tag value
  (`model.py:765`), which also affects `hs submit`; recorded for a follow-up seed at publish.

## 6. Verification strategy

**Tests first (P1).** Four new files, each with an in-process layer and a subprocess layer, both
backends through `db_site` (`tests/input_checks.py`, with `admin`, `ts_database`, `ts_site` imported
from `tests.test_timescale`, as `tests/test_update_group.py` does), helpers lifted from
`tests/test_update_group.py` (`snapshot`, `update_argv`, `assert_refused`) and
`tests/bounded_checks.py` (`refusal`, `assert_one_refusal`):

| File | Covers |
| --- | --- |
| `tests/test_update_numbers.py` | R2–R4, R8–R10, R12 and R13 for the ten ranged columns and `group`; the new rows; refusal before `ensuredb()` (no database file); a refused change with another (nothing written); `-f`; first refusal wins; `cores=4.0` agrees with `hs submit -c 4.0`; the column list in `task_update_desc.rst` gives each entry's `Range.text` |
| `tests/test_update_kinds.py` | R5, R7, R8 for `retried`, the uuid columns, `zone` (names, `default`, `none`, `Default`, `Zone` row recorded, `hs list` shows the name), `args` and the text columns |
| `tests/test_update_datetime.py` | R6, R8 for the four datetime columns: the grammar's forms, every refused form (`now`, `today`, `epoch`, week dates, basic format, `24:00`, comma fraction, 7-digit fraction), a DST gap; the same instant for `…T09:00`, `…T14:00Z`, `…T19:00+05:00` on both backends under `TZ=America/Indiana/Indianapolis` and `PGTZ=UTC`; `COLUMN_KINDS` covers `Task.columns` |
| `tests/test_update_tags.py` | R10 (single-task `ID id x`), R11, R13 (action options, `--remove-tag`, `KEY:VALUE`), R16 (a key colliding with nothing, a misspelling two edits away, an invalid tag key, the prompt's lines), R17 (transposition, insertion, deletion, substitution, case only, a short name, two edits away, the `:` way out for a near-miss and for an exact column, `-f`), the separator rule |

Layer 1 calls `change_value(field, text)` in process for every column and every value R14 lists
(each accepted form, each bound and one past, each refused value, `none`). Layer 2 is one subprocess
test per (column, form, backend), one site and task per node, looping over that column's whole value
list (refusals leave the row unchanged by `snapshot`; accepted values are read back by type, with
SQLite's `typeof()`). Layer 3 is the cross-cutting cases above. `tests/test_bounded_table.py` gains
the new rows.

**Red list.** `spec/update-typed-columns/red.txt` in #124's format (`spec/update-group-and-legacy-filters/red.txt`):
header `# base: <sha>` naming the `origin/develop` commit P0 merges, node ids, `pg:` for PostgreSQL
nodes. Checked with `spec/relay-hardening/drive/red_check.sh` (`RED_BASE_REV`, `RED_LIST`) under
`spec/update-client-retry/drive/bound.sh`; the rest with `spec/contended-writes/drive/rest_green.sh`
(`RED_LIST=` always given). New APIs are imported inside tests so a missing name is a failure in the
call phase, not a collection error.

**Drive.** `spec/update-typed-columns/drive/utc_drive.sh BACKEND MODE` (`BACKEND` `sqlite` or
`postgres`; `MODE` `numbers`, `kinds`, `datetime`, `tags`), with `ugl_drive.sh`'s `fail`/`pass` body and
`spec/hs-update-contended-writes/drive/update_drive.sh`'s backend setup: a `$TMPDIR` SQLite site, or
a PostgreSQL database `utc_drive_<mode>` created from `HS_TEST_POSTGRES` and dropped on exit. It
submits three tasks and drives `hs update` in both forms through the real CLI, checking exit status,
message and stored value with `hs list`/`hs info`. No server is started.

**Both backends.** Every verify that runs tests requires `HS_TEST_POSTGRES` (local PostgreSQL 18,
`HS_TEST_TIMESCALE` empty, sandbox off), so `db_site` runs SQLite and PostgreSQL. CI: the four files
join the PostgreSQL job's list (`.github/workflows/tests.yml:121`) and its comment, in P2.

**Docs.** `sphinx-build -W` in P5 and P6.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
