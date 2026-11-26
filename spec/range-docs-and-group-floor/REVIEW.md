# REVIEW — Numeric ranges out of the user docs; task groups start at 0

> Adversarial QA by `hs-review`. The correctness pass graded the branch diff (spec/ excluded) against
> [`GOAL.md`](GOAL.md) and the AGENTS.md invariants only, without `PLAN.md`/`TECH.md`. Every finding
> cites an executed command.

## Review cycle 1 — changes-requested (2026-11-25)

- **Reviewed commit:** 9339df34 · **Base:** develop · **Mode:** fresh blind pass, xhigh
- **Verdict:** changes-requested (2 CONFIRMED MEDIUM, 1 PLAUSIBLE LOW)

### Verification run

- 11 changed or related test files, serially, with `HS_TEST_POSTGRES` → 1,289 passed.
- 7 neighbouring group and update files on SQLite → 218 passed, 51 skipped (PostgreSQL-only).
- `sphinx-build -W --keep-going -b html` → exit 0; a fresh `-b man` matches the committed pages bar `.TH`.
- Throwaway-site drives of every R4 path beside the above-maximum value (submit `-g`, `-t`, inline and
  tag-only, `--from-json`, stdin, `hs cluster`/`hs server` file and stream, `hs update` both forms, the
  `-g` filters, `hs run -t`); R3 messages (`-c 40000`, `-c 2000`, `client -N 5000000`,
  `HYPERSHELL_SERVER_PORT=0`, `update attempt=40000`, `update cores=2000`).

### Requirement → evidence matrix

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1 | grep of `docs/**/*.rst`, `share/man/man1`; man rebuild | ✅ (but "32 bits" wording, F1) |
| R2 | the 9 CLI #125 markers, the parameter reference, config set, update and run pages | ✅ |
| R3 | refusal and warning drives above; `Range.text`/`advice_text` have no callers | ✅ |
| R4 | every path drive, same form and exit status as above-maximum | ✅ |
| R5 | `task_groups_alt`, `submit_help`, `task_update_*`, `UPDATE_HELP`, new markers | ❌ partly (F1) |
| R6 | new guard tests pass | ⚠️ weak (F2) |

Unmapped changes: the 64-bit figures in `config_set_desc.rst` and the 68-year phrase for
`server.evict` (figure removals in R1's spirit); `Range.text`/`advice_text` deleted (supports R6);
`test_group_order` layout (required by R4). None harmful.

### Findings

#### [MEDIUM/CONFIRMED] F1 — Pages still describe a group without the floor
- **Where:** `docs/_include/submit_desc.rst:32`; `docs/cli/cluster.rst:110-111`, `docs/cli/server.rst:63-64`
  (#118 markers, "must be an integer"); `docs/cli/task_search.rst:34`, `docs/cli/task_update.rst:82`
  (#125 markers, a `-g` "outside 32 bits").
- **Failure scenario:** a reader of the `hs submit`, `hs cluster` or `hs server` page learns a group is
  "an integer" and that only values outside 32 bits are refused; `-1` is refused.
- **Evidence:** grep; `hs cluster t.in`/`hs server t.in` with `group:-1` refused (exit 5, `from 0`);
  `hs list -g -1` exit 2.
- **Requirement:** R5 (and R1: "32 bits" names the storage width).

#### [MEDIUM/CONFIRMED] F2 — The R6 guard misses figures written without commas
- **Where:** `tests/test_bounded_docs.py:39-43, 63-84`.
- **Failure scenario:** develop's own "A group is an integer from -2147483648 to 2147483647" (removed by
  hand here) passes the guard; so would "from 1 to 65535" or "More than 1,024 cores runs with a warning".
- **Evidence:** the guard's regexes and `bound_figures()` applied to `git show develop:` text.
- **Requirement:** R6.

#### [LOW/PLAUSIBLE] F3 — An upgraded database's negative group is auto-selected for new tasks
- **Where:** `src/hypershell/submit.py:1777-1790` → `Task.current_group()`.
- **Failure scenario:** a 2.8 database whose most recently scheduled group is -5; `hs submit` with no
  `-g` logs "Auto-selected task group -5" and stores new rows at -5, while `-g -5` is refused.
- **Evidence:** reproduced in a throwaway site (row set to -5 with a schedule time; arguments, file and
  `--from-json` submits all stored at -5, exit 0).
- **Triage (foreman, 2026-11-25):** new rows below 0 contradict the GOAL's outcome ("task groups are
  non-negative integers everywhere a group is read") even though R4 names given groups. Fixed in this
  remediation: the auto-selected group is never below 0; reported to the maintainer.

### Human-gate triggers

- None. No CONFIRMED finding touches the high-blast-radius core or a security/DB invariant
  (`data/model.py` changed in docstrings only).

## Review cycle 2 — approved (2026-11-25)

- **Reviewed commit:** 713544e5 · **Mode:** scoped — verify the cycle-1 remediation and look for
  regressions it introduced (blind; GOAL, the cycle-1 findings and the diff only).
- **F1 fixed:** all five pages carry the floor; a fresh man build matches the committed pages bar
  `.TH`; no "32 bits" left on a group or option.
- **F2 fixed:** the guard's own patterns over develop's 107 doc and man files flag 476 range
  sentences, 105 thresholds and 499 bound figures, `task_groups_alt.rst`'s uncommaed range among them;
  0 on the current tree; a separate search for every `SETTINGS` low–high pair finds none.
- **F3 fixed:** in a throwaway site with rows at group -5, submissions naming no group are stored at
  0 ("lowest task group; -5 is below it"); `-g -5` exits 2; `hs cluster --restart` still runs the -5
  rows first, then group 0.
- **New findings:** none. Notes (not findings): the `submit_from` API docstring still says "not an
  integer" (pre-existing; code docstrings are outside the GOAL's docs scope); the cluster, server and
  list/update floor wording sits inside the #118/#125 2.9.0 markers.
- **Tests:** 13 group, bounded and update files, serially with PostgreSQL → 1,194 passed, 1 skipped.
- **R1–R6:** all ✅. **Human gate:** not triggered.
