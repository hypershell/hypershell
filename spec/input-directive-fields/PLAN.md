# PLAN — Task input sets every declarable field, with `KEY=VALUE` or `KEY:VALUE`

> **Status:** Draft for review · **Last updated:** 2026-11-25
> **Authoritative technical design.** Contract: [`GOAL.md`](GOAL.md) (locked 2026-11-25). Phases:
> [`TECH.md`](TECH.md). Research: [`research/01-input-paths.md`](research/01-input-paths.md),
> [`research/02-shared-reader.md`](research/02-shared-reader.md). Planned against `8dcbe126`
> (develop `7a1040bb` + the GOAL). Touches `data/model.py` (high blast radius): any CONFIRMED review
> finding there is a human gate.

## 1. Summary

One token reader moves out of `hs update` into `core/tag.py`: it splits a token at the first `=` or
`:`, applies the near-column rule to `=` keys, refuses `=` on a column the caller cannot set, and checks
each tag by the tag rules on its own. `hs update` calls it with every column writable and no field
taken by `:` (its behaviour unchanged). Task input calls it with the five **declarable fields**
(`group`, `zone`, `cores`, `memory`, `timeout`) taken by either separator. Fields keep riding in the
tag dict up to `Task.new`, which pops all five (zone now among them) **before** checking the remaining
tags, so precedence (directive > tag-only line > `-t` > option), the running tag-only line and
`TagAdvice` are untouched. Zones from input are registered with `Zone.ensure` before the write, on the
thread that writes. Every refusal from task input is a `TaskInputError` subclass, so R6's forms (whole
input exit 5 naming the line or record, a stream refuses one line, `-t` exit 2) come from the paths that
already give them for a group.

## 2. Design

### 2.1 The shared reader (R4, R5, R8, R9) — `src/hypershell/core/tag.py`

`core/tag.py` imports only `core.types`; `data/model.py`, `submit.py` and `task.py` already import it
(no cycle; brief 02 §2). Added beside `Tag` (which, with `parse_cmdline_list`, stays as is for the
`-t` *filters* of `hs list`/`hs update`):

- `class TagError(ValueError)` — the neutral refusal; callers wrap it.
- `split_token(token, naked: bool) -> (key, sep, text)` — at the first of `=`/`:`; `sep == ''` for a
  token with neither, allowed only when `naked` (task input's naked tags), else `TagError` with
  today's `hs update` message ("missing \"=\" or \":\"").
- `edit_distance`, `near_names(key, names)` — moved from `task.py` (`near_columns` becomes
  `near_names(key, Task.columns)`, still exported from `hypershell.task`).
- `read_token(key, sep, text, *, columns, fields, colon_fields, way_out) -> ('field', key, text) |
  ('tag', key, value)`:
  `=` and key in `fields` → field; `=` and key in `columns` not in `fields` → `TagError("{key}: a task
  column, not set from task input; to set a tag by that name, write {key}:VALUE")` (R5); `=` and key
  one edit from a column (case aside) → today's near-miss message verbatim with `way_out` (R4); `:`
  and key in `colon_fields` → field; otherwise a tag `smart_coerce(text)` (naked → `''`).
- `check_tag(key, value, strict=True)` — the body of `Task.ensure_valid_tag`'s loop for one tag;
  `Task.ensure_valid_tag` loops over it and so checks every tag whatever its position (R9: the early
  `return` at `model.py:866` goes). `hs update`'s per-tag workaround becomes a direct call.

`hs update` (`read_changes`, `read_assignment`): `fields = columns = Task.columns`, `colon_fields =
()`, no stripping, `TagError` → `ArgumentError` (exit 2). Messages, `Changes`, `COLUMN_KINDS`,
`change_value` and every name tests import from `hypershell.task` stay (R8; the `test_update_*` suite
unchanged is its gate).

### 2.2 Task input fields (R1–R3, R6) — `data/model.py`, `submit.py`, `task.py`

- `FIELD_READERS` in `model.py`, beside `RESOURCE_SETTINGS`, reading the **text** as written (never
  the `smart_coerce` value): `group` → `parse_group`; `zone` → `zone_name` (as `--zone` reads it:
  `none` is a zone named none, `default` the default zone; never `task.py`'s `read_zone`); `cores`,
  `memory`, `timeout` → today's `_read_resource` (whole-decimal `cores:4.0` kept — see §5 Q1). A naked
  or `none` field is refused (today `cores:none` silently unsets `-c`, a naked `cores` stores `''`).
- `read_input_tokens(tokens, origin=None) -> Dict` (model.py): `split_token(naked=True)` +
  `read_token(columns=Task.columns, fields=colon_fields=DECLARABLE)` + the field reader; a field is put
  in the dict under its name with its read value, a tag after `check_tag`. Refusals raise
  `TaskTokenError(TaskInputError)` with `.at(origin)` (message = the reader's, prefixed by nothing new).
  `tag_text` retires (the reader hands each field its text).
- Callers: `Task.split_argline` (inline; its plain `RuntimeError("Failed to parse inline tags")`
  becomes the subclass, so a stream refuses that line instead of ending the run, and whole input names
  the line), `read_tag_options`/`SubmitApp.check_tags` (`-t`, exit 2), and `TaskRunApp` (`hs run -t`,
  checked before `ensuredb`, `ValueError`/`TaskInputError` → exit 2).
- `Task.new`: pop `group`, `zone`, `cores`, `memory`, `timeout` from the merged tags first, then
  `ensure_valid_tag` on the rest (a zone named `a/b` is not held to the tag charset). `zone` popped →
  `other['zone'] = zone_id(name)` (pure, no database), the default zone stored NULL as today.
  Precedence falls out of the merge order already in place (brief 01 §3).

### 2.3 Zones from input: registration, JSON, no database (R1, R7)

- Registration (`Zone.ensure`, idempotent, commits): whole input — the scan collects zone names
  (`InputScan.zones`, from `scan_task_lines` and `validate_json_records`) and they are ensured where
  `SubmitApp.run` ensures `--zone`, and in `hs cluster`/`hs server` `prepare_source` (database
  branch); streams with a database — `DatabaseCommitter.commit` ensures each new zone before
  `Task.add_all`, on the writing thread (cached per committer; the Loader hands it the id→name map);
  `hs run` and `submit_one` — before `Task.add`.
- JSON: a `zone` key sets the zone when it is a JSON string (read by `zone_name`); any other JSON type
  is refused as a non-integer `group` is (`validate_json_records`, `.at('record N')`, exit 5). No typo
  check on JSON keys (GOAL non-goal).
- No database (R7): a zone from input is refused where the command has none — `hs submit --queue`
  (`-t` in `check_tags`, exit 2; file/args in `scan_source`; stdin per line in the Loader),
  `hs cluster --no-db` and `hs server --no-db` (file scan, JSON check, stream Loader), and the
  implicit case (`not DATABASE_ENABLED`, the same test `ServerThread` and `ClusterApp` use). One flag
  (`zones=False`) on the scan and on every `LiveSubmitThread` Loader carries it.
- `hs cluster` launches clients of one zone: a task zoned elsewhere from its input would wait for a
  client that never comes. The cluster logs one WARNING per such zone at ingestion (§5 Q2).

### 2.4 Docs (R10)

`docs/_include/submit_desc.rst` (inline syntax, declarable fields, both separators, the typo and
column rules, zone, refusals), `submit_help.rst` (`-t`, `--zone`, `--from-json`), `task_groups_alt.rst`
(`group=`), `task_run_desc.rst`/`task_run_help.rst` (`hs run -t`), and the help strings at
`submit.py:1629`, `task.py:82,424`; `versionchanged:: 2.9.0` markers on the `hs submit`, `hs run`,
`hs cluster` and `hs server` pages; man pages rebuilt. No `share/` completion change (no option added).

## 3. Invariant gate

- §1 lifecycle: no predicate change. §10 configuration: untouched. §12 conventions: docs, help and
  markers land with their phase; no spec ids in source.
- `AGENTS.md` § tasks (the group paragraph at `:276`): extend to "a declarable field (group, zone,
  cores, memory, timeout) from task input"; `invariants.md:117` in lockstep.
- Zones (`AGENTS.md` zone section): the zone column is set from input as from `--zone`; no foreign key,
  so an unregistered id would still run (brief 01 §2) — registration is for display and listing.

**Deviations:** none.

## 4. Rabbit holes (resolved)

Reader location and layering (02 §2); refusal class on streams (01 risk 2, 02 risk 5); zone typing
(`smart_coerce` alters `007`, `1e3`; read the text) (01 §2, 02 §3); tag charset vs zone names (pop
first) (02 risk 4); filters keep `Tag.parse_cmdline_list` (02 §4); fingerprint drift for rows that
carried a `zone` tag: accepted, one line in the squash note (01 §4); stream registration on the writer
thread (01 §2).

## 5. Risks & open questions

- **Q1 (maintainer, at the plan gate):** R3 says a field is read "with the parser of its `hs submit`
  option", but inline `cores:4.0` is 4 today by your earlier ruling (pinned by
  `tests/test_bounded_tags.py:111`), while `-c 4.0` is refused. Plan: keep the ruling for task input on
  both separators (`cores=4.0` too), and read the R3 clause as "the same kind and range".
- **Q2:** `hs cluster` and an input zone its clients do not serve — plan warns once per zone; the
  alternative is to refuse such input up front.
- `quote_arg` wraps an argument holding whitespace in quotes, so `hs submit "echo e # HYPERSHELL:
  zone:4"` reaches the parser as `zone:4"` (01 risk 5; pre-existing, any directive). Out of scope:
  tests use files or split arguments; a separate seed is filed.
- A bad inline tag on a stream now refuses one line rather than ending the run (R6) — stated in the note.

## 6. Verification strategy

Tests first (P1), red on `8dcbe126` for what they check, listed in `red.txt` and proved by
`spec/relay-hardening/drive/red_check.sh`. Then per phase the touched files' tests, and a drive script
`spec/input-directive-fields/drive/idf_drive.sh` (modes `fields`, `zones`, `nodb`) in a throwaway site:
a file with `zone:gpu`, `zone=cpu`, `group=2`, `cores=2`, `grop=2` (refused, names `group`),
`grop:2` (tag), `exit_status=0` (refused), `exit_status:0` (tag), `naked other:a/b` (refused);
`hs list` shows zones and no `zone` tag; `--queue` and `--no-db` refusals. Gate: whole suite serially,
PostgreSQL-aware files, red list, drive, docs `-W`.
