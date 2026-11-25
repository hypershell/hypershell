# 00 — Digest: one decision per question

Five briefs, 2026-11-24, read against `develop` `39834600` and `fix/bounded-inputs` `bf759ec1` (under
review remediation). Where they disagree or leave a choice, the decision below is the plan's.

| # | Question | Decision | Brief |
| --- | --- | --- | --- |
| D1 | Which parser reads `cores`, `memory`, `timeout` | `parse_setting('task.cores' / 'task.memory' / 'task.timeout', text, name=<column>)`, the parser `-c`/`-m`/`-W` use through `RangedOption`. Not `ensure_resources`, which reads tags and names the setting key. Whatever F8 rules for `4.0`, `hs update cores=4.0` agrees with `hs submit -c 4.0` | 01 §1, §2, §4 |
| D2 | What to pass the parser | The text as given, never a `smart_coerce`d value (a Python float bypasses the whole-number check) | 01 §1 |
| D3 | `none` and `null` | Handled before any parser (none knows them): any case, stripped; NULL where the column may be empty, else the column's refusal | 01 §1, 04 §4 |
| D4 | Three new rows | `column.exit_status`, `column.cores_max`, `column.memory_max` in `SETTINGS`; `setting_for` excludes the `column.` prefix as it does `option.`; `tests/test_bounded_table.py` gains `COLUMN_ONLY` | 01 §3 |
| D5 | Exit status for a table refusal | `SettingError` raised in `run()` is not mapped by `ReadingMixin`; wrap as `ArgumentError` (exit 2), as the group is | 01 §2, 02 |
| D6 | Refusal `<name>` | The bare column (`cores: expected …, given 'x'`), the table's form | 01 §5, 02 §5 |
| D7 | Several refusals in one command | First in argument order (#119, #124) | 02 §5 |
| D8 | Where the check runs | One pure reader in `run()`, after #119/#124's option refusals and before `ensuredb()`; both forms use its result | 02 §1 |
| D9 | Separator in an argument holding `=` and `:` | The first one splits. Never changes an accepted argument; quotes the user's text in the refusal | 02 §3 |
| D10 | Prompt | One stdout line per change, in argument order, before today's question, which is unchanged | 02 §4 |
| D11 | Tag validation | Each tag checked alone by `Task.ensure_valid_tag({key: value})`, so the early `return` at `model.py:765` cannot skip a key, without editing `model.py`; wrapped as exit 2. Applied to all three tag paths (question Q2) | 02 §2 |
| D12 | Near-miss (R17) | Optimal-string-alignment distance in `task.py`, stdlib only, key lower-cased against `Task.columns`; after the exact-column test, before the tag rules; ties in `Task.columns` order (none occur today: the nearest two columns are 3 apart) | 02 §8 |
| D13 | uuid columns | `UUID_PATTERN.fullmatch(text.strip())`, stored lower case; not `uuid.UUID` (takes braces, `urn:`, no hyphens) | 04 §1 |
| D14 | `source` | The uuid form alone. `<direct>` and `<stdin>` are reserved paths, not ids; the reserved ids are uuids and pass | 04 §2 |
| D15 | `zone` | `none`/`null` → NULL; else `zone_name(text)` then `zone_id(name)`, the default id → NULL; `Zone.ensure(name)` after the prompt, just before the write | 04 §3 |
| D16 | `retried` | `read_value(Kind.BOOL, text)` (the configuration's words) | 04 §4 |
| D17 | Peaks | `Kind.NUMBER` rows; stored as `float(value) + 0.0` so both backends hold a float and `-0.0` is `0.0` | 04 §5 |
| D18 | Datetime parse | ASCII regex gate (hour 00–23, fraction 1–6 digits, `Z` or `±HH:MM`), then `datetime.fromisoformat` for the calendar | 03 §3 |
| D19 | Datetime store | `parsed.astimezone()`, an aware local value, as `--cancel` writes; a nonexistent local time is refused; an ambiguous one takes fold 0 | 03 §4 |
| D20 | Datetime display | Not changed (question Q1): same instant and same local wall time on both backends; PostgreSQL keeps its offset suffix, as every stamp shows today | 03 §4 |
| D21 | Test shape | In-process grid through `change_value`; one subprocess test per (column, form, backend) looping over the column's whole value list; cross-cutting single tests | 05 §7 |
| D22 | Red list and drive | `spec/update-typed-columns/red.txt` in #124's format, base = the `origin/develop` commit merged in P0; `spec/relay-hardening/drive/red_check.sh`, `spec/contended-writes/drive/rest_green.sh`, `spec/update-client-retry/drive/bound.sh`; a drive `spec/update-typed-columns/drive/utc_drive.sh BACKEND MODE` on `ugl_drive.sh` and `spec/hs-update-contended-writes/drive/update_drive.sh` | 05 §4 |
| D23 | CI | New files appended to the PostgreSQL job's list (`tests.yml:121`); the `tests` job's timeout raised 45 → 60 minutes | 05 §2 |
| D24 | R6 tests | A non-UTC `TZ` and `PGTZ=UTC` in the subprocess environment, or UTC hides a naive write | 03 §6 |

Facts the plan carries forward:

- `Task.columns` types uuid, zone and text columns all `str`; the reader needs its own kind map.
- `Task.update`, `update_matching` and `update_selected` convert nothing; values bind through the
  column types, so the reader must hand over the stored Python type.
- `ensuredb()` on SQLite creates the database file: a refusal before it leaves no file behind.
- The single-task `ID tag KEY:VALUE` is not validated today; a filtered `KEY:VALUE` with a bad key
  exits 5 with a traceback.
- The bash and zsh completions offer `tag=`, which R11 refuses.
- `hs info` fails on a stored `inf` peak today (`OverflowError`); R4's range closes the way in.
