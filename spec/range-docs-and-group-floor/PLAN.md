# PLAN — Numeric ranges out of the user docs; task groups start at 0

> **Status:** Draft for review · **Last updated:** 2026-11-25
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md) (locked at the
> GOAL gate, 2026-11-25); the phased executable roadmap is [`TECH.md`](TECH.md). Lean plan
> (`appetite: small`, `kind: fix`, no unknowns; `data/model.py` gets a docstring edit only): no
> `research/`. Planned against `42e2112f`.

## 1. Summary

Two slices. **P1 (R4, R5):** the group floor. Every group a command reads goes through the one
table entry `SETTINGS['option.group']` (`GROUP`, `core/config_schema.py:783`): `-g/--group` on
`hs submit`, `hs list`, `hs update` (and every other command declaring it through `ranged(...)`),
`parse_group` (`hs update` both forms, `data/model.py`), and `ensure_group` (inline and `-t` tags,
`--from-json` records) by `GROUP_MIN`/`GROUP_MAX`, which are read from that entry. Raising its low
bound from `-2³¹` to `0` changes every reader at once, with the refusal text and exit status each
path has today. **P2 (R1–R3, R6):** strip the figures from the user docs, reword the change markers
that point at them, remove the `Range.text`/`Range.advice_text` renderers that only the docs used,
replace `tests/test_bounded_docs.py` with a guard, and rebuild the man pages.

## 2. Design

### 2.1 R4 — the floor (P1)

- `core/config_schema.py`: `GROUP = Range(Kind.INT, 0, INT32_MAX, why="A task's group is a
  non-negative 32-bit integer: groups run lowest first, from the default 0.")`.
- `data/model.py`: docstrings only — `GROUP_MIN` ("Lowest task group: 0, the default"),
  `TaskGroupError`, `ensure_group` unchanged in code. `TaskGroupError`'s out-of-range message already
  reads `an integer from {GROUP_MIN} to {GROUP_MAX}`, so it says `from 0 to 2147483647`.
- No scheduler or query change: rows a 2.8 database holds below 0 are still selected lowest first
  (GOAL non-goal), and `hs update ID group N` checks only the new value.

### 2.2 R5 — what a group takes (P1)

- `docs/_include/submit_help.rst` `-g`: "a non-negative integer (default: 0)".
- `docs/_include/task_update_desc.rst`, `task_update_help.rst`, `docs/cli/task_update.rst:63`:
  "in the 32-bit range" → "a non-negative integer, as `hs submit -g` takes it".
- `docs/_include/task_search_help.rst` / `task_update_help.rst` `-g` filters: unchanged wording
  beyond losing the Range line (P2).
- Marker: `docs/_include/submit_desc.rst`'s 2.9.0 group marker (#118) gains "a group below 0 is
  refused"; the `:pr:` link is added at release (AGENTS.md § Docs). The same clause on the `hs update`
  page's group marker.
- `AGENTS.md:276` and `invariants.md:117`: "in PostgreSQL's 32-bit range" → "from 0 to the 32-bit
  maximum".

### 2.3 R1–R3 — the docs (P2)

Remove every `Range: …` sentence and every figure-bearing advisory sentence (`Above 1,024 it runs
with a warning.`, `… is written with a warning`, `A cores tag past 1,024 runs, with one warning`)
from: `docs/_include/{client,server,cluster,relay,submit,task_search,task_update,task_wait,task_run}_help.rst`,
`submit_desc.rst`, `task_update_desc.rst`, `task_run_desc.rst`, `config_param_ref.rst` (48), and the
figure-bearing passages elsewhere: `docs/cli/submit.rst:41` (`-c` above 32,767),
`docs/cli/task_update.rst:93-94`, `docs/api/{server,cluster}.rst` (`server.evict`'s range figures),
`docs/_include/config_set_desc.rst:16` (the 64-bit parenthetical; "outside 64 bits" stays).

Rewording (R2), figures out, rule kept:
- the CLI pages' #125 marker "each numeric option has a range, given with the option below" →
  "each numeric option has a range"; the rest of the marker stands (refusal names the option, the
  range and the value, exit 2; an advisory limit warns).
- `task_update_desc.rst:19` "within the range listed below" → "within its range"; the column list
  keeps each column's form, drops its Range sentence.
- `submit_desc.rst:50` "take the ranges of the -c, -m and -W options" stays (no figure); the three
  bullet Range lines go.
- The 48 identical config-reference markers ("the range is checked when the configuration loads…")
  stay: they carry no figure.

R3: `Range.bounds`, `refusal`, `advice` and every message are untouched. `Range.text` and
`Range.advice_text` have no caller outside `tests/` once the docs no longer mirror them; delete both
and point `tests/test_update_numbers.py:184-186` at `.bounds`.

### 2.4 R6 — the guard (P2)

`tests/test_bounded_docs.py` keeps its marker/prose tests that carry no figures
(`test_cli_page_help_marks_ranges`, env page, `config set` page, API pages, AGENTS/rubric) and replaces
`test_help_snippet_gives_range`, `test_param_reference_gives_range` and
`test_param_reference_zone_keys` with: every `docs/**/*.rst` (not `_build`) and
`share/man/man1/*.1` file carries no `Range:` sentence, no `(Above|Below|above|below|past) <number>
… warning` sentence, and none of the table's bound figures written in the docs' style (each
`SETTINGS` entry's `figure(high)` / `figure(low)` with at least four digits, e.g. `2,147,483,647`,
`32,767`, `65,535`), allowing the `database.rst` and changelog-style passages that name a column's
width only if the build finds any (none expected; record any exception in the test with a reason).
The module docstring is rewritten to say the docs state the rule, the code holds the figures.

### 2.5 Man pages (P2)

Rebuild as `hs-release` Step 4 does (`sphinx-build -b man docs docs/_build/man`, copy `hs.1`,
`hyper-shell.1`, `hsx.1`), version unchanged; the diff is the removed lines and the rewording.

## 3. Invariant gate (AGENTS.md constitution check)

- §1 Task lifecycle: no predicate or query changes; group order selection untouched. Pre-existing
  negative rows keep running in order (GOAL non-goal).
- §10 Configuration / `SETTINGS`: the one-table rule holds — the floor changes in the table only.
  `AGENTS.md` § Configuration's table description stays true (docs never were part of the table's
  contract; `test_agents_and_rubric_name_table` keeps).
- §12 Conventions: help snippets, prose page and marker change in the same commit as the behaviour
  (P1); `share/` completions are untouched (no option added or renamed).

**Deviations:** none.

## 4. Rabbit holes (resolved)

- *Does any path read a group outside `GROUP`?* No: `ensure_group`/`parse_group` read
  `GROUP_MIN/MAX` from the entry; `-g` everywhere is `ranged(..., setting='option.group')`
  (`task.py:685,1410`, `submit.py:1672`). `Task.new(group=…)` stays the caller's (AGENTS.md).
- *Tests with negative groups:* `test_group_order.py:268,416` (layouts with -1) move to non-negative
  layouts with the same shape (`[2, 1, 2, 1]`, `1, 1, 2`); `test_bounded_{table,parsers,options}.py`
  and `test_update_group.py` take the new low bound and gain a `-1` refusal case each.

## 5. Risks & open questions

- A 2.8 user filtering old negative-group rows with `-g -1` is now refused; `-w 'group < 0'` remains
  (GOAL non-goal, stated in the squash note).

## 6. Verification strategy

- P1: the group tests above, plus a CLI drive in a throwaway site: `hs submit -g -1` exits 2 naming
  the range; an inline `# HYPERSHELL: group:-1` line is refused (exit 5); `hs update ID group -1` and
  `hs list -g -1` exit 2; `-g 0` still works.
- P2: `tests/test_bounded_docs.py`, `tests/test_update_numbers.py`, the docs build with warnings as
  errors, and a `grep` over `docs` and `share/man` for `Range:`.
