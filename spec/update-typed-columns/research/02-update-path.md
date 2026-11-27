# 02 — The hs update path, tags, separators and the prompt

Read on `fix/update-typed-columns` (develop `39834600`); `task.py` = `src/hypershell/task.py`,
`model.py` = `src/hypershell/data/model.py`. Probes ran against this tree's `src` with the main
checkout's venv in a throwaway SQLite site (`$TMPDIR`), 2026-11-24. `bounded-inputs`' `task.py`
diff only adds `ReadingMixin` to `TaskUpdateApp` and `ranged(...)` to `-l`/`-g` (no conflict with
anything below). `ReadingMixin` wraps `from_cmdline` only, not `run()`, so a `ValueError` raised in
`run()` is still exit 5.

## 1. Where the one check goes

- `run` `task.py:1116-1136`: legacy detection (3 args, first a UUID), then #119/#124 refusals
  **before** `ensuredb()` (`:1132`), then `update_legacy` or `update_tasks`.
- `update_legacy` `:1138-1150`: unknown-field refusal (`:1142`), `tag` merge by Python
  (`{**Task.from_id(uuid).tag, **Tag.parse_cmdline_list([value])}`, `:1146`, a read then a write),
  else `Task.update(uuid, **{field: change_value(...)})`.
- `update_tasks` `:1152`: `process_arguments()` first, then "Searching database", `build_query`,
  `count` (`:1165`), prompt (`:1172`), then cancel/revert add `field_updates` (`:1195`, `:1200`) and the write.
- `process_arguments` `:1230-1260`: per argument in order: `WhereClause.pattern` (`:1235`), `=`
  (`:1238`: `id` refusal `:1241`, unknown field `:1243`, `change_value`), `:` (`:1245`,
  `smart_coerce`, **no strip**), else "missing = or :" (`:1249`); then `Task.ensure_valid_tag(tag_updates)`
  (`:1251`), then the `--desc`/`--order-by` checks.
- So today `process_arguments` already precedes the count and prompt, but follows `ensuredb()`.
- `ensuredb()` (`data/__init__.py:121`) on SQLite always runs `initdb()` → `upgradedb()`: it
  **creates the file and tables** (a write); on PostgreSQL it only checks. #124 kept
  `process_arguments` after `ensuredb()` on purpose (its PLAN §2.1: "R2 asks for before it counts, not
  before the database is opened"), while its option refusals sit before it and are tested so
  (`tests/test_update_refusals.py:97` "a fresh site gets no database file").

**Recommendation.** One pure function, say `read_changes(update_args, legacy) -> (columns, tags)`, no
database access, called in `run()` before `ensuredb()`, after #119/#124's option refusals; both
forms use its result (`update_legacy` and `update_tasks` stop parsing). Rationale: R9 needs
"before counting", which today's spot meets, but the check needs no database (zone is `uuid5` of a
name; `Zone.ensure` is a write that belongs after the prompt), and before `ensuredb()` matches #119/#124's
"argument refusals before the database is opened" and lets a test assert no database file. Cost:
the `--desc`/`--order-by` checks and the WhereClause refusal move earlier too — harmless, all exit 2.
If the plan prefers minimal motion, keeping it at the head of `update_tasks` also satisfies R9; the
single-task form then needs the check at the head of `update_legacy` (already after `ensuredb()`,
before any read).

## 2. Tags today

- `smart_coerce` (`core/types.py:40-54`): `none`/`null` → `None`; `true`/`false` (any case) →
  bool; `int()`; `float()` (so `nan`, `inf`, `1e3` are floats); else the text. Empty → `''`.
- `Tag.from_cmdline` (`core/tag.py:33-41`): split on the first `:`, **strip** name and value,
  `smart_coerce` the value; no `:` → value `''` (a naked tag). `parse_cmdline_list` → dict.
- `Task.ensure_valid_tag` (`model.py:737-777`), strict: key non-empty (`:753` "Tag key was empty"),
  ≤120 chars (`:759`, message prints `len(value)`, a small bug), `^[A-Za-z0-9_.+-]+$`
  (`:761` "Tag key must only contain alphanumerics and basic symbols [+._-]: "KEY:VALUE""); a str
  value: empty → **`return`** (`:765`, ends the loop, so keys after a naked tag go unchecked: a bug),
  `memory`/`timeout` keys by `parse_bytes`/`parse_time`, else ≤120 chars and the same character set.
  Raises `ValueError`/`TypeError`/`UnitError(ValueError)`.
- **Exit status today.** Filtered `KEY:VALUE` with a bad key or value (`'a b:1'`, `'Bad?:1'`,
  `memory:4X`): **exit 5, traceback** (`ValueError` reaches the shared `Exception` mapping); it runs
  after `ensuredb()`, before the count. Single-task `ID tag KEY:VALUE`: **never validated** —
  probed: `a b:1`, `memory:4X`, `url:http://x=1` all exit 0 and are stored (`{"a b": 1, "memory": "4X", ...}`).
  `hs submit -t` wraps it as `ArgumentError` (`task.py:119-125`), exit 2.
- **Two merges.** Filtered: SQL in `Task.__change` (`model.py:~1356-1383`): SQLite
  `json_set(tag, '$.k', v)` (a boolean binds as 1/0, a dotted key is a JSON path); PostgreSQL
  `tag || patch` (`to_json_type`). Single-task: Python dict merge, so `b:true` stores `true` there and
  `1` on SQLite filtered. Pre-existing; R16 does not ask to unify.
- **Resource names.** At submit, `Task.new` (`model.py:~640-656`) pops tags `group`, `cores`,
  `memory`, `timeout` into columns. `hs update` never does: `cores:4` sets a tag only. Under R16
  `cores=`, `memory=`, `timeout=`, `group=` are columns and never tags (confirmed: all in
  `Task.columns`); no tag key is reserved in update.

**Recommendation (R16).** `KEY=VALUE` (non-column) and single-task `ID KEY VALUE` (non-column) build
`{KEY: smart_coerce(VALUE)}` — the filtered `KEY:VALUE` reading, unstripped — join `tags`, and the
whole tag dict goes through `Task.ensure_valid_tag` inside the check, with `ValueError`/`TypeError`
wrapped as `ArgumentError` (exit 2), as `hs submit` does. Single-task writes merge as `ID tag KEY:VALUE`
does (`:1146`). Fix the `:765` `return` → `continue` so every key is checked. Flag for the plan:
wrapping turns filtered `KEY:VALUE`'s exit 5 into exit 2 (R13 says "as today"; R9's "no traceback"
spirit), and whether `ID tag KEY:VALUE` gains the check (R11 says "as today"; R16 says "same key
rules") — recommend checking all three paths alike, noted as a behaviour change.

## 3. Separator precedence

- `WhereClause.pattern` `task.py:1317` `^([a-z_]+)\s*(==|!=|>|>=|<|<=|~)\s*(.*)$` runs first. It
  matches `cores==4`, `cores!=x`, `cores>=4` (as `>` then `=4`; alternation order), `note~x`,
  `a<b:c`; not `cores=4`, `path=~/x`, `time=12:30`, `Cores==4` (upper case), `gpu2>1` (digit). Keep
  it first: it stops `a==b` from becoming the tag `a` = `=b`. `Cores==4` falls through to R17.
- `:` is not a tag-key character, and `=` and `:` are not tag-value characters (except values of
  `memory`/`timeout`). So any argument with `:` before `=` is refused whatever the rule; only the
  message differs. No argument accepted today changes meaning under any option (today every `=`
  argument is a field, and a field with `:` was never a column).

| Argument | (a) leftmost wins (≡ (c)) | (b) `=` always wins (today) |
| --- | --- | --- |
| `time=12:30` | tag `time` = `12:30` → value rule refuses `:` (exit 2) | same |
| `start_time=2026-01-01T09:00`, `timeout=1:30:00` | column | column |
| `note:a=b` | tag `note` value `a=b` → "Tag value must only contain…: "note:a=b"" | key `note:a` → "Tag key must only contain…: "note:a:b"" |
| `url:http://x=1` | tag value refused (message shows the argument) | key `url:http://x` refused, garbled echo |
| `a=b:c` | column if `a` is text (`command=b:c` ok), else tag value refused | same |

**Recommendation: (a), the first separator splits.** Same outcome as (b) everywhere, but the message
quotes what the user typed and the rule is one sentence for the help. (b) is acceptable if the
plan wants zero code motion. Note: `time=12:30` style tags (non-column key, `:` in value) are
refused by the tag value rule in both — worth one help line.

## 4. The prompt

- Today `input(f'Update affects {count} tasks, continue? yes/[no]: ')` `:1172`: `input()` writes the
  prompt to **stdout**; every log line ("Searching database", "Updated N tasks") is stderr. Probed.
- Tests assert only its absence: `'affects' not in …` and `'continue?' not in …`
  (`tests/test_update_group.py:93-99`, `tests/test_update_refusals.py:83-94`). No test asserts the
  text; R16 adds one.
- `--cancel`/`--revert` add `field_updates` after the prompt (`:1195`, `:1200`), so they are not in
  the change set when it is shown.

**Recommendation.** Lines on stdout before today's question (keep "affects" and "continue?" so the
negative assertions stay meaningful), argument order, one per change, value as given:

```
Update affects 3 tasks:
  column  exit_status=0
  tag     exit_stauts=0
  tag     color:blue
  remove  tag size
Update affects 3 tasks, continue? yes/[no]:
```

(or one header line `Changes:`; the plan picks.) Action options shown as one line (`action --cancel`)
optional; R16 needs only columns vs tags. Nothing printed under `-f` or when the count is 0
(stays "stopping"). Value as given rather than as read keeps it simple; as read would show
`memory=4294967296` — plan's call.

## 5. Refusals: first or all

First-refusal-wins today: `process_arguments` raises on the first bad argument in order; #124
REVIEW (`spec/update-group-and-legacy-filters/REVIEW.md:57-58`) records "matches #119's
first-refusal-wins pattern"; `tests/test_update_group.py:134-141` (`group=x args=changed color:red`)
asserts the refusal and nothing written, not the count of messages. **Recommend first refusal in
argument order**, one `ArgumentError`: matches the table's one-message form, #119/#124, and every
existing test; the user fixes and re-runs. R9 only demands nothing be written.

**Refusal `<name>`.** Recommend the bare column (`cores: expected …, given x`): the table's
`Range.refusal` form is `<name>: expected <form> from <A> to <B>, given <V>`
(`config_schema.py:614` on `fix/bounded-inputs`), R17's example uses `exit_stauts:` the same way,
and `#124` tests only look for the column and the value on one line.

## 6. The model write

- `Task.update` `model.py:1344` → `__write_rows([id], changes, …)`: `changes` passed as given;
  `ID id x` → `TypeError: Task.update() got multiple values for argument 'id'`, exit 5 (probed).
- `update_matching` `:1416`, `update_selected` `:1386` → `__change` `:1356`: `dict(values)` as given,
  plus one `tag` expression. **No conversion anywhere**; values bind through the column types
  (`model.py:238-246`): `UUID` = `Text` / PG `UUID(as_uuid=False)` (pass a lower-case `str`),
  `DATETIME` = `DateTime(timezone=True)` (pass a `datetime`; SQLite's processor drops tzinfo and
  stores wall time, so pass `.astimezone()` local for R6), `BOOLEAN` (pass `bool`, not 1),
  `FLOAT` (`float`), `SMALL_INTEGER` = PG `SMALLINT`. A `tag` value in `values` would be a literal JSON.
- `Task.columns` `model.py:482-517`: 34 names, all lower case `[a-z_]`: `id` str, `group` int,
  `args` str, `submit_id` str, `submit_time` datetime, `submit_host` str, `cores` int, `memory` int,
  `cores_max` float, `memory_max` float, `timeout` int, `server_id` str, `server_host` str,
  `schedule_time` datetime, `client_id` str, `client_host` str, `command` str, `start_time` datetime,
  `completion_time` datetime, `exit_status` int, `outpath`/`errpath`/`csvpath` str, `attempt` int,
  `retried` bool, `waited`/`duration` int, `previous_id`/`next_id`/`source` str, `fingerprint` str,
  `zone` str, `part` int, `tag` dict. The `str` type covers uuids, so the plan needs its own kind map.
- `revert_columns` `:550-564`: schedule_time, server_host, server_id, client_host, client_id,
  command, start_time, completion_time, exit_status, outpath, errpath, csvpath, waited, duration.

## 7. `id` and `tag=`

Both go in the one check: a change whose column is `id` (either form) → R10 refusal
(`Cannot alter task "id"`, today's `:1241` text); `tag` in the filtered form → R11. Single-task
`ID tag KEY:VALUE` stays the merge. Single-task `ID tag VALUE` where VALUE has no `:` is a naked tag today.

## 8. R17 near-miss (amended GOAL, commit `9aefc009`)

Order inside the check, per `=` change (and single-task KEY): (1) WhereClause pattern;
(2) split by the separator rule; (3) KEY exactly a column (case-sensitive) → read by kind, or
`id`/`tag` refusals; (4) else KEY within OSA distance 1 of a column, compared lower-cased → R17
refusal; (5) else a tag, `ensure_valid_tag`. Near-miss before the tag rules, since its message is
the more useful (`Exit-Status=0` gets "did you mean exit_status?", not the charset message).
`KEY:VALUE` skips (3)-(4) entirely. Columns are all lower case, so `key.lower()` against the names
catches `Cores=` (distance 0) and `Exit_Status=`.

```python
def edit_distance(a: str, b: str) -> int:
    """Optimal string alignment distance: insertions, deletions, substitutions, adjacent swaps."""
    before, previous = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            row[j] = min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], before[j - 2] + 1)
        before, previous = previous, row
    return previous[-1]

def near_columns(key: str) -> List[str]:
    """The columns within one edit of `key`, ignoring case, in `Task.columns` order."""
    return [name for name in Task.columns if edit_distance(key.lower(), name) <= 1]
```

Checked: `ab/ba`=1, `exit_stauts/exit_status`=1, `''/id`=2. **Ties:** recommend `Task.columns`
order (the `hs info` order), joined `a or b`; in practice none occur: the closest two columns are 3
apart (`args`/`cores`), and no 1-edit neighbour of any column is within 1 of a second.

**Caught keys worth telling the maintainer** (beyond `tags`, `ip`, `groups`, `zones`): `id` is a
magnet for short keys — `pid`, `uid`, `sid`, `idx`, `ids`, `in`, `is`, `it`, `io`, `i`, `d`; also
`argv`/`arg`→`args`, `port`/`parts`→`part`, `done`→`zone`, `core`/`codes`→`cores`, `bag`/`ta`→`tag`,
`sources`, `commands`, `timeouts`, `attempts`. All still settable by `KEY:VALUE`.
