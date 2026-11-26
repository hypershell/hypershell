---
status: adopted:input-directive-fields
kind: fix
appetite: big
target: 2.9.0
lane: public
---

# A `zone:` directive is stored as a tag, and task input has no `KEY=VALUE` form

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

A task's inline `# HYPERSHELL:` directives set a column when they name one: `group:`, `cores:`,
`memory:` and `timeout:` are taken out of the tags and written to the task's own columns
(`Task.new`, `src/hypershell/data/model.py:737-753` on `develop` 42e2112f). `zone:` is not. It is left
in the tags, so the task runs in whatever zone `--zone` gave (the default zone without it) and carries
a tag named `zone`. Reproduced on a throwaway SQLite site (`.agents/factory/bin/temp_site.sh`):

- `echo e # HYPERSHELL: zone:4`, submitted with `hs submit --zone gpu`, is stored with zone `gpu`
  and the tag `{"zone": 4}`.
- `-t zone:…` and a `zone` key in a `--from-json` record are tags in the same way; only `--zone` sets
  the column.

Task input also has one separator where `hs update` now has two. `hs update` reads `KEY=VALUE` as a
column when `KEY` is one, refuses a key one edit from a column name as a likely typo, and takes any
other key as a tag; `KEY:VALUE` is always a tag (`read_changes`, `read_assignment`, `near_columns`
in `src/hypershell/task.py`, from `update-typed-columns`, #126). In task input:

- `echo b # HYPERSHELL: zone=gpu` refuses the whole file (exit 5, `Failed to parse inline tags (Tag
  key must only contain alphanumerics …: "zone=gpu:")`).
- `group=2` is refused the same way, and `grop:2` is stored as a tag named `grop` with no warning.

Found by the maintainer while testing 2.9.0a10 (2026-11-25). The `zone:` half is pre-existing since
zones shipped; the `=` half is new syntax.

## Outcome sought (maintainer, 2026-11-25)

- A field a task can declare at submission (today `group`, `cores`, `memory`, `timeout`; `zone`
  joins them) is set from input as the column, never as a tag, wherever task input names it: an
  inline directive, `-t`, and (to shape) a `--from-json` key. `zone:NAME` puts the task in the zone
  called `NAME`, as `--zone NAME` would.
- Task input takes both separators, `KEY:VALUE` and `KEY=VALUE`, for fields and for tags, through
  the same reader `hs update` uses, so the two cannot drift.
- With `=`, a key one edit from a field name is refused as a likely typo, naming the field it is
  near and how to write a tag by that name; with `:`, a key near a field is a tag, as it is today.

## Draft R-IDs (input to shaping)

- **R1** — WHEN task input names `zone` (inline, `-t`), THEN the task SHALL be stored in that zone,
  with no `zone` tag; the zone is registered as `--zone` registers it.
- **R2** — Task input SHALL take `KEY=VALUE` and `KEY:VALUE` alike for the declarable fields and for
  tags, read by the field's kind (the same parser and refusal as `hs submit`'s option for it).
- **R3** — IF a `KEY=VALUE` key is one edit from a field name (case aside), THEN the input SHALL be
  refused (exit 5 for input read in full, naming the line; exit 2 for `-t`), saying which field it is
  near and that `KEY:VALUE` writes a tag.
- **R4** — `hs update` and task input SHALL share one reader for `KEY=VALUE`/`KEY:VALUE`.

## Open questions for shaping

1. **Other columns with `=`.** `hs update` writes every column. In task input, should
   `exit_status=0`, `attempt=3` or `id=…` be refused as "not set from input", or taken as tags? (With
   `:` they are tags today.)
2. **Precedence.** Today an inline directive overrides `-t`, and an inline `group:` overrides `-g`.
   Should an inline `zone:` override `--zone` the same way?
3. **No database.** `--zone` is refused with `--queue` ("zones require the database"). An inline
   `zone:` in a queue-mode submission, or in `hs cluster --no-db`/`hs server --no-db` input: refuse it
   (as input, naming the line), or drop it with a warning?
4. **JSON records.** A `zone` key in a `--from-json` record: set the column, as `group` does? Its
   keys are template fields too, and a record has no separator, so the typo rule would not apply.
5. **The naked-tag check.** `tag-check-stops-at-empty-value` (2.9.x) is the same reader's tag check
   on the `hs submit` side. Fold it in, since this cycle moves task input onto the per-tag check
   `hs update` already does?

## Notes

- Refusing `KEY=VALUE` near-misses is an interface change, inside 2.9.0 by the maintainer's ruling.
- Related: `Tag.from_cmdline` (`core/tag.py`), `read_tag_options` and `SubmitApp.check_tags`
  (`submit.py`), `json_record_tags` and `validate_json_records` (`submit.py`), `Zone.ensure`,
  `zone_name` (`data/model.py`), the inline syntax on `docs/_include/task_groups_alt.rst` and the
  `hs submit` page.
