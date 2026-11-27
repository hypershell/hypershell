# 01 — bounded-inputs API (as of fix/bounded-inputs bf759ec1)

All paths are on `fix/bounded-inputs` unless marked `develop`. Behaviours marked *(probed)* were run
against the branch's `config_schema.py` (stdlib only) in a scratch copy.

## 1. `core/config_schema.py`

- **`Kind`** (51-61): `TEXT, BOOL, INT, NUMBER, INT_OR_NONE, DURATION, SIZE, LOGGING_FILE, UNTYPED`.
- **Grammar:** `INT_FORM` `[+-]?[0-9]+(?:_[0-9]+)*`; `NUMBER_FORM` adds `.frac` and `e±N` (198-200), ASCII
  digits only, so `nan`, `inf`, `1/2`, `0x10` never match. `BOOL_WORDS` (192-195): `true/yes/on/1`,
  `false/no/off/0`; callers lower-case. `read_value(kind, text)` (235-258) is the env-layer reader,
  raising bare `ValueError(FORMS[kind])`, not `SettingError` — do not use it for columns, except
  `read_value(Kind.BOOL, text)` is the one boolean-word reader (R5 `retried`); it strips and lower-cases.
- **`match_seconds(text) -> Optional[Fraction]`** (459-467), **`match_bytes(text) -> Optional[int]`**
  (470-478, rounds down); both return None if not the form, raise `ValueError` for thousands of digits.
- **`Range`** (557-731), frozen dataclass:
  `Range(kind, low, high, why, zero=None, least=None, low_open=False, rounds_to=None, signed=False, per='', advise=None, detail=None)`.
  - `refusal(name, value, source=None, out_of_range=True) -> str` (614-623):
    `f'{name}: expected {KIND_FORMS[kind]} from {self.bounds}, given {shown(value)}'`, plus
    `; <forms>` when not of the kind and no source (DURATION/SIZE list `TIME_FORMS`/`MEMORY_FORMS`),
    plus ` (from: <source>)` for configuration. `KIND_FORMS` (499-505): INT "a whole number",
    NUMBER "a number", DURATION "a duration", SIZE "a size".
  - `read(value, name, source=None, whole=True)` (625-633): kind only, no range.
  - `parse(value, name, source=None, whole=True)` (635-651): kind + range (+ `rounds_to`); raises
    `SettingError`. `whole` matters only for DURATION text: True → `math.ceil` seconds (`'90.5'`→91),
    False → exact (`90.5`). A Python number is kept as given (`1.5`→1.5 even with `whole`) *(probed)*:
    pass the **text**, never a `smart_coerce`d value.
  - `inside(v)` 653; `advised(v) -> bool` 661-666 (False for bool/non-number); `advice(name, given)`
    668-671: `"{name}: {shown(given)} is above the advised 1,024 (<reason>); running anyway"`.
  - `_read` (673-731): bool → refused; non-finite float → refused; INT accepts `int`, a whole `float`
    (`4.0`→4), or `INT_FORM` text (stripped; >30 significant digits → out of range); **text `'4.0'`
    and `'1.5'` refused for INT**. NUMBER text: `INT_FORM`→`int`, `NUMBER_FORM`→`float`, overflow to
    inf (`1e999`) → out of range. SIZE via `match_bytes`, DURATION via `match_seconds`.
- **`SettingError(ValueError)`** (734-751): `SettingError(name, entry, value, source=None, *, out_of_range=True)`;
  attrs `.name .entry .value .source .out_of_range`; `str()` is `entry.refusal(...)`.
- **Range constants** (757-816): `ATTEMPTS` 770 (INT 1…32,767, advise above 100), `CORES` 778 (INT
  0…32,767, `zero='unconstrained'`, advise above 1,024), `GROUP` 783 (INT −2³¹…2³¹−1), `PART` 784
  (INT 0…2³¹−1), `DURATION` 785 (0…2³¹−1 s), `SIZE` 805 (0…2⁶³−1 bytes, `zero='unconstrained'`).
  `INT16_MAX`/`INT32_MAX`/`INT64_MAX` 484-491.
- **`SETTINGS`** (818-849): `'server.attempts': ATTEMPTS` (825); `'task.cores': CORES, 'task.memory': SIZE,
  'task.timeout': DURATION` (832); option-only `'option.max_retries', 'option.group': GROUP,
  'option.part': PART, 'option.limit', 'option.delay', 'option.interval', 'option.client.timeout',
  'option.client.task_timeout'` (846-848).
- **`setting_for(path)`** (858-865): returns `path` if in `SETTINGS` **and not `option.`-prefixed**.
  Used by `hs config set` (`config.py:343`) and the env layer (378).
- **`parse_setting(setting, value, name=None, source=None, whole=True)`** (868-874) = `SETTINGS[setting].parse(value, name or setting, ...)`.
- **Advisory:** `ADVISED: Set[str]` 879, `PENDING: Optional[List[str]]` 882,
  `advise(setting, value, name=None, given=None) -> bool` 886-901: returns True whenever past the
  limit, logs **once per setting key per process** (a second call is silent, *probed*), queues to
  `PENDING` until `flush_advice()` (904-909), which `main()` calls right after `initialize_logging`
  (`__init__.py:154`). So inside `hs update`'s `run()` a call logs at once.

**Probed reads (`parse`):** CORES `'4'`,`' 4 '`,`'+4'`,`'1_000'` ok; `'1.5'`,`'4.0'`,`'true'`,`''`,`'nan'`,
`'none'` refused not-of-kind; `'-1'`,`'32768'` out of range. SIZE `'4G'`→4294967296, `'1.5GiB'`,
`'1024'` ok; **`'90m'` → 94,371,840 (M = MiB)**; `'1:30:00'`/`''`/`'-1'` refused with the size forms.
DURATION `'1:30:00'`,`'90m'`,`'1h30m'`→5400, `'1-12'`→129600, `'4G'`/`''`/`'none'` refused with the
duration forms. NUMBER: `'1.5'`,`'.5'`,`'1e3'`(1000.0),`' 2 '`(int 2) ok; `'nan'`,`'inf'`,`'1/2'`,`'true'`,`''`
refused not-of-kind; `'1e999'`,`'-0.5'` out of range. None of the parsers knows `none`/`null`: R8 must
be handled before the call.

## 2. #124's group and `-l`; resource tags

- `data/model.py:123-128` `parse_group(text) -> int` now `SETTINGS['option.group'].parse(text, 'group')`,
  re-raised as `TaskGroupError(text, out_of_range=error.out_of_range)`; develop used `int(text)` +
  `ensure_group`. `GROUP_MIN/MAX` read from the entry (66-70). `TaskGroupError` (85-105) now subclasses
  new `TaskInputError(RuntimeError)` (73-82). Message unchanged:
  `task group must be an integer, not "x"` / `… an integer from -2147483648 to 2147483647, not "…"`.
- `task.py` diff: `-l/--limit` (1073), `-g/--group` filter (1088), `hs list --part` (679) become
  `ranged(..., setting='option.limit'|'option.group'|'option.part')`; apps gain `ReadingMixin`.
  `change_value` (1036-1046) is **unchanged** from develop.
- `core/types.py`: `RangedOption(setting, name)` (179-197) → `parse_setting(...)`, `SettingError` →
  `ArgumentError(str(error))` (exit 2), then `advise(setting, value, name, given=text)`.
  `ranged(interface, *flags, setting, hidden=(), **options)` (205-211) names the refusal by the flags
  joined with `/`. `parse_bytes`/`parse_time` (120-130) now wrap `SIZE`/`DURATION` as `UnitError`.
- `exceptions.py:67-101`: `reading('arguments')` and `ReadingMixin` wrap **only `from_cmdline`**;
  a `SettingError` raised in `run()` is not converted, so `hs update` must wrap it itself (as
  `change_value` does for the group).
- `data/model.py:148` `RESOURCE_SETTINGS = {'cores': 'task.cores', 'memory': 'task.memory', 'timeout': 'task.timeout'}`;
  `ensure_resources(tags, texts=None) -> Dict` (152-167): skips absent/None/`''`, parses the text as
  written if given, else the value, **named by the setting** (`task.cores: expected …`), raises
  `TaskResourceError(str(error))` (131-145, a `TaskInputError`, exit 5 unless mapped). No advisory there.
- Tag advisory: `submit.py:203-230` `TagAdvice` (`note(tags, origin)`, `log()`), its own class-level
  `logged` set, one summary per setting: `task.cores: 3 tasks above the advised 1,024 (…), the first at
  <origin>; running anyway`. For `hs update`, R12 wants the per-value `advise()` form, not `TagAdvice`.

## 3. Adding Exit status, Peak cores, Peak memory

Suggested constants beside the others (757-816):
```python
EXIT_STATUS: Final[Range] = Range(Kind.INT, -INT16_MAX - 1, INT16_MAX,
    why="A task's exit status is a 16-bit integer on PostgreSQL.")
PEAK_CORES: Final[Range] = Range(Kind.NUMBER, 0, INT16_MAX,
    why="A task's peak cores, a measured decimal; the ceiling is its cores' 16-bit range.")
PEAK_MEMORY: Final[Range] = Range(Kind.NUMBER, 0, INT64_MAX, per=' bytes',
    why="A task's peak memory in bytes, a measured decimal; the ceiling is its memory's 64-bit range.")
```
(`why=` house style: one or two sentences naming the floor's meaning and the storage ceiling.) Probed
bounds text: `-32,768 to 32,767`, `0 to 32,767`, `0 to 9,223,372,036,854,775,807` (with `per`, the high
bound gets ` bytes`; the low does not, since `KIND_UNITS` has no NUMBER). Columns: `exit_status`
`SMALL_INTEGER`, `cores_max`/`memory_max` **`FLOAT`** (develop `model.py:438-452`); NUMBER returns an
`int` for whole text, so cast `float()` if R1's "same value on both" needs the type pinned.

**Key names — pitfall.** `setting_for` treats any non-`option.` key in `SETTINGS` as a config key, so
`task.exit_status` would make `hs config set task.exit_status 5` range-check an undocumented key.
Use a prefix `setting_for` excludes: `option.` is the only one today (misnamed for columns), or add
`column.` (`column.exit_status`, `column.cores_max`, `column.memory_max`) and extend the exclusion at
860. bounded `PLAN.md` §2.7 expected column ranges as standalone `Range`s **outside** `SETTINGS`
("so R1's test stays 'every entry is a setting'"); this GOAL says they are added to the table — the plan
must pick and say so. No advisory, so `advise()` (which needs a `SETTINGS` key) is not required for them.

**Tests that a new row breaks:** `tests/test_bounded_table.py:180-191`
(`test_settings_cover_numeric_keys`: `set(SETTINGS) == numeric_keys() | set(OPTION_ONLY)`;
`test_settings_kinds_match_keys`). Add the rows to `OPTION_ONLY` (36-40) or a new `COLUMN_ONLY`
unioned in both tests; add samples to `RANGES` (115-134). `test_advisory_limits_inside_ranges` and the
default/option tests are unaffected. `tests/test_bounded_docs.py` is driven by `OPTIONS` and `KEYS`
only, so new rows need no edit there and get no doc check from it.

**Docs:** there is **no single page listing the table**. Ranges appear as `Range.text`/`advice_text`
per option on each `docs/_include/*_help.rst`, per key in `config_param_ref.rst`, and for the resource
tags as a list in `docs/_include/submit_desc.rst:56-58`, pinned by
`tests/test_bounded_tags.py:159-166` (`SETTINGS['task.cores'].text in flat(text)`). Option-only rows are
documented only under their option's help entry. Pattern for R15: a list of every typed column's
range in `docs/_include/task_update_desc.rst`, rendered from `Range.text`, with a test like
`test_tag_docs_give_ranges`. `AGENTS.md:567-573` says a new entry needs its row in
`test_bounded_table.py`.

## 4. What the in-flight remediation may move (re-check at the merge)

- **F8** (`4.0` accepted as 4 in tags): if done in `Range._read` (INT text branch, 689-693) it changes
  every INT option, `parse_group`, and this cycle's `cores`/`exit_status`/`attempt`/`part` together;
  if done in `ensure_resources`/`tag_text`, `hs update cores=4.0` (read as `-c`) stays refused. Also
  whether `timeout:90.5` keeps `whole=True` rounding. Re-check `_read`, `parse`, `ensure_resources`.
- **F2** (`-t` on `hs run`/single submit skip the table): likely moves `ensure_resources` into or
  before `Task.new` (`model.py:~696`, where `parse_bytes(memory, 'task.memory')` sits) or into
  `TaskRunApp.run` (`task.py:447`)/`check_tags` (`task.py:123`); may change its signature or the
  `TaskResourceError`→exit mapping. Re-check if the plan reuses `RESOURCE_SETTINGS`/`ensure_resources`.
- **F5** (advisory for single submit): may touch `TagAdvice` and/or `advise`.
- **F4** (`hs config` exemptions): `check_settings`/`_resolved` in `core/config.py:415-462`; touches
  `setting_for` only if the exemption logic changes. **F1/F3/F7**: docs, a test, `server.py`; no API.

## 5. Refusal `<name>`, and the group wording

- Options name by flags: `-c/--cores`, `-g/--group`, `--part`, `-l/--limit` (`ranged`, `types.py:210`);
  config by dotted key (`task.cores`); resource tags by the setting key (`task.cores`, from
  `ensure_resources`). `parse_setting(..., name='cores')` gives
  `cores: expected a whole number from 0 (unconstrained) to 32,767, given '1.5'` *(probed)*.
- Group in `change_value` (develop and branch, `task.py:1036-1046`): `parse_group` →
  `TaskGroupError` → `ArgumentError(str(error))`, i.e. `task group must be an integer, not "x"` — the
  **only** typed column whose refusal is not in the table's form, on either branch. R13 keeps it.
- Advisory naming: `advise('task.cores', v, 'cores', given=text)` →
  `cores: '1025' is above the advised 1,024 (no node a task runs on has more); running anyway`;
  `advise('server.attempts', …, 'attempt', …)` likewise. Because `ADVISED` is per setting key, a config
  file value past the limit that warned at load silences `hs update`'s warning in the same process.
