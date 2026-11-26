# GOAL — Numeric ranges out of the user docs; task groups start at 0

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).

- **slug:** range-docs-and-group-floor
- **kind:** fix
- **appetite:** small
- **target:** 2.9.0

## Problem

`bounded-inputs` (#125, in 2.9.0a10) gave every numeric setting a declared range and an advisory
tier, and rendered both into the user documentation: every numeric option on the command help pages,
every numeric key in the parameter reference, the resource tags on the `hs submit` page and every
column on the `hs update` page now carry a line such as "Range: -2,147,483,648 to 2,147,483,647." or
"Above 1,024 it runs with a warning." The manual pages are built from the same text.

The bounds are mostly the limits of the stored type. They are correct, but they are noise for a
person reading the docs, and printing them makes the storage width a documented interface: a future
schema change would become an interface change. The advisory thresholds are the same kind of detail.
The refusal message already names the range when a value falls outside it, and the table in code is
where the figures belong.

Separately, a task group may be negative today (`option.group` runs from -2³¹). Groups order work,
lowest first, starting from the default of 0. A negative group is a logic error, not a feature.

## Outcome / vision

The website and the manual pages say what each numeric option means and how it is read, not how wide
its column is. A reader learns that an out-of-range value is refused, and that the error says what
the range is. Task groups are non-negative integers everywhere a group is read.

## Acceptance criteria (the contract)

- **R1** — The user documentation (the `docs/` pages and snippets the website renders, and the
  manual pages built from them) SHALL NOT state a numeric setting's bounds or advisory threshold:
  no "Range: …" sentence and no "Above/Below N … it runs with a warning" (or "is written with a
  warning") sentence for any option, configuration key, resource tag or `hs update` column.
- **R2** — Where the documentation describes the range rule, it SHALL state it without figures: a
  value outside the setting's range is refused (exit status 2) with a message naming the setting,
  its range and the value; a value past an advisory limit is taken with one warning. The
  `versionchanged:: 2.9.0` markers from #125 stay, reworded where they point at ranges "given below".
- **R3** — The refusal and warning messages a command prints SHALL be unchanged: they still name the
  range or the advised limit. The figures stay in the code (the `SETTINGS` table and its docstrings).
- **R4** — IF a task group below 0 is given — `hs submit -g`, a group in task input (an inline group,
  a `--from-json` record), `hs update` (`group=N` or `ID group N`), or the `-g` filter of the
  commands that take one — THEN the command SHALL refuse it the way it refuses a group above the
  maximum today (same message form, same exit status). Group 0 stays the default.
- **R5** — The documentation SHALL say a task group is a non-negative integer wherever it describes
  what a group takes, and the 2.9.0 change marker for groups SHALL name the new floor.
- **R6** — The tests that tied the documentation to the table (`tests/test_bounded_docs.py`) SHALL be
  replaced by a guard that the user documentation carries no bounds or advisory-threshold sentence,
  so the figures do not drift back in.

## Non-goals (no-gos)

- Changing any range or advisory limit other than the group floor.
- Changing refusal or warning text (R3).
- Migrating rows a 2.8 database already holds with a negative group. They are still scheduled in
  order; `hs update ID group N` moves one, and `-w 'group < 0'` finds them.
- Removing the figures from code docstrings or the `SETTINGS` table.

## Clarifications

- **Q:** Which documentation loses the figures? — **A:** "the user facing docs (website and manual
  page) should not exhaustively list all of these"; the code docstrings may (maintainer, 2026-11-25).
- **Q:** What about the advisory thresholds? — **A:** "we just shouldn't bother writing about that;
  if we ever change something in the schema, now that's a documented interface change. So let's yank
  all of those out." (maintainer, 2026-11-25). Shaping default: the general rule (a warning, not a
  refusal) may still be stated once without figures (R2).
- **Q:** Negative task groups? — **A:** "I also don't want to allow negative groups, that seems like a
  logic error." (maintainer, 2026-11-25). Floor 0, everywhere a group is read (R4).

## Related materials

- #125 `bounded-inputs` (2.9.0a10): the range table and the doc lines this removes.
- `src/hypershell/core/config_schema.py` (`Range`, `SETTINGS`, `GROUP`), `src/hypershell/data/model.py`
  (`GROUP_MIN`, `ensure_group`), `tests/test_bounded_docs.py`, `docs/_include/*_help.rst`,
  `docs/_include/submit_desc.rst`, `docs/_include/task_update_desc.rst`,
  `docs/_include/config_param_ref.rst`, `share/man/man1/`.
