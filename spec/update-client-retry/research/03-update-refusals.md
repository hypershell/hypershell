# Research 03 — `hs update` refusals (R1, R2)

Tree: `fix/update-client-retry` at `eb2a4487` (develop + #117). Line numbers below are this tree's
and match GOAL.md's (taken at `6a0c28ea`); #117 did not move `task.py`.

## 1. Map of `TaskUpdateApp` (`src/hypershell/task.py`)

| What | Lines |
|---|---|
| `UPDATE_USAGE` / `UPDATE_HELP` (in-program help; "Using --delete drops the row…", "legacy interface … remains valid") | 944-993 (965, 967-968) |
| `update_args` positional, `nargs='*'` | 1001-1002 |
| filters `-w`, `-t`, `-s`/`--desc`, `-l`, alias group `-F/-C/-S/-R/-X`, `-g`, `--retries`, `--signal` | 1008-1041 |
| action group (mutually exclusive) `--revert`, `--cancel`, `--delete` | 1043-1049 |
| `--remove-tag`, `nargs='+'` (not in any group) | 1051-1052 |
| `-f/--no-confirm` | 1054-1055 |
| `exceptions` (NotFound, Contended → 5; shared mapping) | 1057-1061 |
| `run`: `ensuredb()` **first**, then routes 3 args led by a UUID to legacy | 1063-1069 |
| `update_legacy`: no count, no prompt, never reads action options or `--remove-tag` | 1071-1087 |
| `update_tasks`: `process_arguments()` | 1092 |
| "Searching database" log, `build_query()`, `query.count()` | 1099-1102 |
| zero-count stop | 1104-1106 |
| prompt (skipped by `-f`) | 1108-1115 |
| delete without `--limit` (`Task.delete_matching`) | 1117-1120 |
| delete with `--limit` (`Task.delete_selected`) | 1143-1148 |
| `report_written` ("Deleted N tasks") | 1160-1165 |
| `process_arguments`: pure parsing, raises `ArgumentError`; no DB access (`Task.columns`, `Task.ensure_valid_tag` are static) | 1167-1202 |

**Earliest point.** `run` (1063) is the first code after parsing, and its first statement,
`ensuredb()`, opens the database (SQLite: `initdb()` creates the file and schema,
`data/__init__.py:121-132`; PostgreSQL: `checkdb()`). Both checks can sit at the top of `run`,
**before `ensuredb()`**, after computing the legacy routing predicate
(`len(self.update_args) == 3 and UUID_PATTERN.match(self.update_args[0])`) so R1 applies to the
filtered form and R2 to the legacy form. Nothing is then counted, logged ("Searching database"),
prompted, opened or written. The R1 check needs only raw state (`self.delete_mode`,
`self.update_args`, `self.remove_tag`): in the filtered form every positional is a change or an
error, so "any positional or `--remove-tag` with `--delete`" is exactly R1. Doing it on raw args
means a malformed positional with `--delete` (e.g. `foo --delete`) gets the R1 message rather than
`process_arguments`' — both exit 2; either order is defensible.

`-f/--no-confirm` only gates the prompt (1108); it does not change the path to the delete, and
the legacy form never prompts or counts. So a check before `ensuredb()` is independent of `-f`.

The in-process test fixture `hs_update` (`tests/test_contended_recovery.py:1267-1285`)
monkeypatches `hypershell.task.ensuredb` and calls `TaskUpdateApp.main`; a check in `run` is
compatible with it.

## 2. How refusals exit 2 today

`cmdkit.app.Application.main` catches `cmdkit.cli.ArgumentError` → `log_critical` →
`exit_status.bad_argument` (2). argparse conflicts surface the same way. Reproduced
(`temp_site.sh`, SQLite, Python 3.14), verbatim stderr, all `[exit 2]`:

```
$ hs update --cancel --revert
CRITICAL [hypershell] argument --revert: not allowed with argument --cancel
$ hs update --cancel --delete -f
CRITICAL [hypershell] argument --delete: not allowed with argument --cancel
$ hs update group=9 --desc -f
CRITICAL [hypershell] Should not provide --desc if not using -s/--order-by
$ hs update group=9 -s args -f
CRITICAL [hypershell] Using -s/--order-by without -l/--limit is meaningless
$ hs update args==4 -f
CRITICAL [hypershell] Positional argument matches conditional (args==4), maybe you intended to use -w/--where?
$ hs update foo -f
CRITICAL [hypershell] Argument not recognized (foo): missing "=" or ":"
$ hs update id=1 -f
CRITICAL [hypershell] Cannot alter task "id" (given: id=1)
$ hs update nofield=1 -f
CRITICAL [hypershell] Unrecognized task field "nofield"
```

Note the parser-level ones come before `ensuredb()`; the `process_arguments` ones today come
after it (the DB is opened/created, nothing counted).

## 3. Defects reproduced on this tree

Six tasks `args` 1-6, group 0, no tags (`hs submit -f t.in`). Each `[exit 0]`:

```
$ hs update group=9 --delete --limit 1 -f            → "Deleted 1 tasks"   (args 1 gone; nothing has group 9)
$ hs update foo:bar --delete --limit 1 -f            → "Deleted 1 tasks"   (args 2 gone)
$ hs update --delete --limit 1 --remove-tag foo -f   → "Deleted 1 tasks"   (args 3 gone)
$ hs update group=9 --delete -w 'args == 4' -f       → "Deleted 1 tasks"   (args 4 gone; no --limit path)
$ echo no | hs update group=9 --delete -w 'args == 5'
  Update affects 1 tasks, continue? yes/[no]: Stopping   (prompt shown, counts rows, silent on group=9)
```

Each logs `Searching database: sqlite (…)` first. Filter syntax `-w 'args == 4'` is correct
(`WhereClause`, spaces optional).

Legacy form, task `args 6` (`ID`), each `[exit 0]`, no output, no prompt even without `-f`:

```
$ hs update ID group 7 --delete -f        → row kept, group 7
$ hs update ID group 8 --cancel -f        → group 8, exit_status None, schedule_time None (not cancelled)
$ hs update ID group 9 --revert -f        → group 9 (revert ignored)
$ hs update ID group 10 --remove-tag foo -f → group 10 (option ignored)
$ hs update ID group 11 --delete          → group 11, row kept (no prompt)
```

## 4. Proposed messages

House styles: argparse "argument X: not allowed with argument Y"; custom "Cannot alter task "id"
(given: id=1)". Recommended (both via `ArgumentError`, so `CRITICAL [hypershell] …`, exit 2):

- **R1:** `Cannot use --delete with changes (given: group=9 foo:bar --remove-tag foo)` — the
  "given" lists the positionals as typed, then `--remove-tag` and its names if any.
  Alternative closer to argparse: `argument --delete: not allowed with changes (group=9 foo:bar)`.
- **R2:** `Cannot use --cancel with the legacy form ID FIELD VALUE` — name every offending option
  present, in a fixed order (`--cancel`, `--revert`, `--delete`, `--remove-tag`), joined by ", "
  (only one action option can be present, plus possibly `--remove-tag`). Optional hint:
  `(filter by id instead: -w 'id == ID')`; `-w "id == <uuid>"` works today
  (`tests/test_dispatch_staged.py:242`). If hinted, keep it generic — for `--delete` the user must
  split the field write from the delete, which a one-line hint cannot say correctly.

Parser quirk worth knowing for test design: `--remove-tag` is `nargs='+'`, so
`hs update --delete --remove-tag foo group=9` parses `group=9` as a tag name (positionals empty).
Still refused by R1 (`--remove-tag` present); the message would show
`--remove-tag foo group=9`. Put positionals before `--remove-tag` in tests.

## 5. Tests and docs

**Existing tests.** `tests/test_update.py` (170 lines, all `@mark.integration`) runs `hs` as a
**subprocess** via `tests.main(argv) -> (rc, stdout, stderr)` (`tests/__init__.py:34-41`, stdin
inherited, no input) in the `temp_site` fixture (`tests/conftest.py:83-90`, SQLite file
`HYPERSHELL_DATABASE_FILE`); helpers `submit_tagged`, `stored()` (reads the DB file read-only by
the `n` tag) and `updated(stderr)`. No existing test combines `--delete` with a change or the
legacy form with an action option (grep over `tests/`), so no existing test flips. Delete paths:
`test_contended_write.py:770-1011` and `test_contended_recovery.py:1463-1497` (filters only —
must stay green); legacy: `test_contended_recovery.py:1334-1372` (in-process `hs_update`),
`test_contended_write.py:937`.

**Red-test candidates** (add to `tests/test_update.py`; red on develop, green after):

1. R1 parametrized: `['group=9','--delete','--limit','1','-f']`, `['foo:bar','--delete','--limit','1','-f']`,
   `['--delete','--limit','1','--remove-tag','foo','-f']`, `['group=9','--delete','-w','n==2','-f']`
   (no `--limit`; or `-t n:2`), `['group=9','--delete','-f']` (no filter). Assert `rc == 2`,
   `stored()` unchanged (row count and every row), stderr names `--delete` and the argument, no
   `Searching database`/`Deleted` in stderr. Develop: rc 0, a row gone.
2. R1 no prompt: run `subprocess.run([... 'hs','update','group=9','--delete','-t','n:0'],
   input=b'yes\n')` (`tests.main` cannot feed stdin). Assert rc 2, `'continue?'` not in stdout,
   rows unchanged. Develop: prompt printed, "yes" deletes.
3. R1 before the database: in a fresh `temp_site` with no DB file, `hs update group=9 --delete -f`
   → rc 2 and `HYPERSHELL_DATABASE_FILE` does not exist. Develop: file created, rc 0
   ("Update affects 0 tasks - stopping"). Pins the "before it opens" clause on SQLite.
4. R2 parametrized over `--cancel`, `--revert`, `--delete`, `--remove-tag foo`: legacy
   `hs update ID group 7 <opt> -f` (ID from `hs list id -t n:0`) → rc 2, stderr names the option,
   row present with group 0, `exit_status` None. Develop: rc 0, group 7.
5. Green guards: `--delete -t n:0 -f` (filters only) still deletes exactly that row, with and
   without `--limit`; legacy `ID group 7 -f` still writes. Plain-delete has no test in
   `test_update.py` today.

**Docs** (R9, `hs update` page):

- `docs/_include/task_update_desc.rst`: after "Using ``--delete`` drops the row…" add that
  `--delete` takes no change (`FIELD=VALUE`, `KEY:VALUE`, `--remove-tag`): given one, `hs update`
  exits with status 2 before it counts or asks and deletes nothing. Extend the last paragraph
  ("legacy interface … remains valid") with "…but takes no ``--cancel``, ``--revert``,
  ``--delete`` or ``--remove-tag`` (exit status 2)."
- `docs/_include/task_update_help.rst`: the `--delete` entry (one sentence) and the "Legacy
  Mode" section (one sentence).
- `docs/cli/task_update.rst`: one `.. versionchanged:: 2.9.0` for both, appended after the
  existing 2.9.0 block (after "The single-task form … reports the task not found" is natural),
  e.g. "``--delete`` with a field or tag change or ``--remove-tag`` exits with status 2 before
  counting or asking; before, the tasks were deleted and the changes ignored. The single-task form
  ``hs update ID FIELD VALUE`` with ``--cancel``, ``--revert``, ``--delete`` or ``--remove-tag``
  exits with status 2; before, it wrote the field and ignored the option."
- In-program help `UPDATE_HELP` (`task.py:965-968`) can take the same two sentences (optional;
  keeps `-h` in step with the page).
- `docs/_include/task_update_usage.rst`: unchanged (synopsis unchanged).
- `share/`: nothing. zsh `_hs_update` (`share/zsh/site-functions/_hs:439-460`) excludes only the
  three actions from one another; positional conflicts are not expressible there and bash
  completion lists flags only. Man pages (`share/man/man1/*.1`) are rebuilt only at release
  (`.agents/skills/hs-release/SKILL.md:182-190`).

## 6. Overlap with #118 (`fix/submit-input-checks`)

`git diff origin/develop...fix/submit-input-checks -- src/hypershell/task.py docs/` touches
`docs/_include/submit_desc.rst`, `docs/_include/task_groups_alt.rst`, `docs/cli/cluster.rst`,
`docs/cli/server.rst` only — **not `task.py` and not any `hs update` page.** No textual overlap
for R1/R2. (Outside this topic: #118 changes `data/model.py` (+171), `server.py`,
`cluster/__init__.py`, `AGENTS.md` and `invariants.md`; its P3 commit "a retry runs in its task's
group column" touches retry-row construction, which R5/R7 also touch.)
