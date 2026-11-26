# GOAL — Task input sets every declarable field, with `KEY=VALUE` or `KEY:VALUE`

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).

- **slug:** input-directive-fields
- **kind:** fix
- **appetite:** big
- **target:** 2.9.0

## Problem

A task's inline `# HYPERSHELL:` directives set a column when they name one: `group:`, `cores:`,
`memory:` and `timeout:` are taken out of the tags and written to the task's own columns. `zone:` is
not. It stays a tag, so the task runs outside the zone it names: `echo e # HYPERSHELL: zone:4`
submitted with `--zone gpu` is stored in zone `gpu` with the tag `{"zone": 4}`. `-t zone:…` and a
`zone` key in a `--from-json` record are tags in the same way; only `--zone` sets the column.

Task input also has one separator where `hs update` (#126) has two. `hs update` reads `KEY=VALUE` as
a column when `KEY` names one, refuses a key one edit from a column name as a likely typo, and takes
any other key as a tag; `KEY:VALUE` is a tag. In task input `zone=gpu` and `group=2` refuse the whole
file (exit 5, "Failed to parse inline tags"), and `grop:2` is stored as a tag named `grop` without a
word.

Separately, the tag check on the `hs submit` side stops at the first tag with an empty value (a
naked tag), so `-t naked other:a/b` stores a value that `-t other:a/b` refuses, and an inline list
does the same (seed `tag-check-stops-at-empty-value`, folded in here).

## Outcome / vision

Wherever task input names a field a task declares at submission (its group, zone, cores, memory or
timeout), it sets that field, never a tag. Task input reads `KEY=VALUE` and `KEY:VALUE` through the
same reader `hs update` uses, so a field is read by one parser everywhere, a likely typo is caught
when `=` says "this is a field", and `:` keeps meaning what it does today.

## Acceptance criteria (the contract)

Task input, below, is every place a task's fields and tags arrive with the task: an inline
`# HYPERSHELL:` directive (in a task file, standard input, or a command given as arguments, to
`hs submit`, `hs cluster`, `hs server` and `hs run`), the `-t`/`--tag` option of those commands, and
the keys of a `--from-json` record. The **declarable fields** are `group`, `zone`, `cores`, `memory`
and `timeout`.

- **R1** — WHEN task input names `zone` (`zone:NAME` or `zone=NAME`, `-t`, or a `--from-json` key),
  the task SHALL be stored in the zone called `NAME`, read as `--zone NAME` reads it and registered
  as `--zone` registers it, and SHALL carry no `zone` tag.
- **R2** — WHEN more than one source gives a declarable field, the most specific SHALL win, as it
  does for `group` today: a line's directive (or a record's key), then `-t`, then the field's own
  option (`--zone`, `-g`, `-c`, `-m`, `-W`).
- **R3** — An inline directive and `-t` SHALL take `KEY=VALUE` and `KEY:VALUE` alike, for the
  declarable fields and for tags. A declarable field SHALL be read by its kind, with the parser and
  refusal of its `hs submit` option, whichever separator is used.
- **R4** — IF a `KEY=VALUE` key is not a task column but is one edit from one (letter case aside),
  THEN it SHALL be refused, naming the column it is near and saying that `KEY:VALUE` writes a tag by
  that name. A `KEY:VALUE` key is never refused for being near a column; it is a tag as today.
- **R5** — IF a `KEY=VALUE` key names a task column that is not a declarable field (e.g.
  `exit_status`, `attempt`, `id`), THEN it SHALL be refused as not set from task input. The same key
  with `:` stays a tag.
- **R6** — A refusal under R1, R3–R5 or R7 SHALL take the form a refused group takes today: input
  read in full is refused whole, with nothing submitted or run, exit status 5, naming the value and
  its line or record; on a stream only that line is refused, with an error naming it; a refused
  `-t` exits with status 2 before any input is read.
- **R7** — IF a zone arrives in task input where there is no database (`hs submit --queue`,
  `hs cluster --no-db`, `hs server --no-db`), THEN it SHALL be refused (R6), as `--zone` with
  `--queue` is refused today.
- **R8** — `hs update` and task input SHALL read `KEY=VALUE` and `KEY:VALUE` with one shared reader:
  the split at the first separator, the near-column rule and the per-tag check. `hs update`'s
  behaviour SHALL be unchanged.
- **R9** — Every tag in task input SHALL be checked by the tag rules whatever its position: a tag
  with an empty value ends nothing, and `-t naked other:a/b` or an inline `naked other:a/b` is
  refused as `-t other:a/b` is.
- **R10** — The documentation of the inline directives, `-t` and `--from-json` SHALL describe both
  separators, the declarable fields (zone among them), the typo rule and the refusals, with
  `versionchanged:: 2.9.0` markers.

## Non-goals (no-gos)

- New declarable fields beyond the five (no `part`, `attempt` or `exit_status` from input).
- Changing `hs update`'s syntax or rules (R8 only moves its reader).
- Typo checks on `--from-json` keys (a record has no separator; its keys are template fields too).
- Migrating tasks already stored with a `zone` tag.
- Changing how a task's identity (fingerprint) is computed, beyond `zone` no longer being a tag.

## Clarifications

- **Q:** Should `zone:` in task input set the zone? — **A:** Yes: "In all other cases if you set a
  named field/column it pops that from the inline processing instead of setting it as a tag."
  (maintainer, 2026-11-25).
- **Q:** Separators and typos? — **A:** "allow both the "=" and ":" syntax for tags and fields just
  like we fixed up in the recent cycle for `hs update`. We should exercise the same code path there,
  and catch single-distance changes as typos unless the ":" separater is used." (maintainer,
  2026-11-25).
- **Q:** A non-declarable column with `=` (`exit_status=0`)? — **A:** Refused; with `:` a tag
  (maintainer, 2026-11-25) → R5.
- **Q:** A zone with no database? — **A:** Refused as input, as a bad group is (maintainer,
  2026-11-25) → R7.
- **Q:** A `zone` key in a `--from-json` record? — **A:** Sets the zone, as `group` does; no typo
  check on JSON keys (maintainer, 2026-11-25) → R1, non-goals.
- **Q:** Fold in `tag-check-stops-at-empty-value`? — **A:** Yes (maintainer, 2026-11-25) → R9.
- **Q:** Does an inline `zone:` override `--zone`? — **A:** Shaping default: yes, as an inline
  `group:` overrides `-g` today (R2). Not asked separately; the maintainer may overrule at the gate.

## Related materials

- Seed: [`issues/input-directive-fields.md`](../../issues/input-directive-fields.md);
  [`issues/tag-check-stops-at-empty-value.md`](../../issues/tag-check-stops-at-empty-value.md) (folded in).
- #126 `update-typed-columns`: `read_changes`, `read_assignment`, `near_columns`, `check_tag`
  (`src/hypershell/task.py`).
- `Task.new`, `Task.split_argline`, `ensure_valid_tag`, `Zone.ensure`, `zone_name`
  (`src/hypershell/data/model.py`); `Tag.from_cmdline` (`src/hypershell/core/tag.py`);
  `read_tag_options`, `SubmitApp.check_tags`, `json_record_tags`, `validate_json_records`
  (`src/hypershell/submit.py`).
- Docs: `docs/_include/task_groups_alt.rst`, the `hs submit` page and help snippets.
