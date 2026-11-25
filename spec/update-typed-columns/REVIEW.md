# REVIEW — `hs update` reads every column it writes by the column's kind

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

## Review cycle 1 — changes-requested (2026-11-25)

- **Reviewed commit:** 2abf860d  ·  **Base:** develop (ce74094c, merged in)  ·  **Date:** 2026-11-25
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** a fresh blind pass over the full diff, with `spec/` excluded (`git diff develop...HEAD -- . ':(exclude)spec/'`). One reviewer, no debate.
- **Contract drift (Step 1):** `GOAL.md` changed after the shaping commit (`dddd17b5`) in three commits:
  - `2e992557`, which widened the goal to every column;
  - `bda42399`, the GOAL gate;
  - `9aefc009`, which added R17.

  The maintainer ruled all three on 2026-11-24, and they are recorded in GOAL's Clarifications as C4, C11 and C12. This review grades the contract as amended. R16 and R17 are rulings, so neither is re-raised here.

### Verification run

The blind reviewer ran these commands. The orchestrator re-ran the F1 reproduction itself.

- **Python 3.14, PostgreSQL 18 configured:** `pytest` on the following files gave **443 passed**:
  - `test_update_numbers`, `test_update_kinds`, `test_update_datetime`, `test_update_tags`;
  - `test_update_group`, `test_bounded_table`, `test_completions`.
- **Python 3.11, SQLite:** the same files, plus `test_bounded_docs` and `test_config_schema`, gave **563 passed, 163 skipped**. The skipped tests are the PostgreSQL cases.
- **Python 3.11, PostgreSQL:** the PostgreSQL cases alone (`-k postgres`) of the kinds, datetime and tags tests gave **87 passed**.
- **Python 3.14, full suite on SQLite:** **5736 passed, 400 skipped, 0 failed**.
- **Docs:** `sphinx-build -W --keep-going -E -b html docs <out>` (docs-venv-utc) succeeded with no warnings.
- **CLI matrix:** 205 cases over all 34 columns, in both forms, on SQLite and on a throwaway PostgreSQL 18 database, each in a throwaway site (`temp_site.sh`). Both backends stored the same values. Further runs by hand on Python 3.11.
- **F1 reproduction (orchestrator, SQLite, TZ=America/Indiana/Indianapolis):**
  - `hs update ID completion_time 9999-12-31` exited 5 with `ValueError: year must be in 1..9999, not 10000` and an exception traceback log.
  - `hs update start_time=0001-01-01 -w "id == ID" -f` exited 5 (`… not 0`).
  - `hs update ID completion_time 2026-01-01T09:00` exited 0.
- **Cleanup:** the tree is clean, and HEAD is still 2abf860d. The reviewer dropped both PostgreSQL databases it created (`rev_utc_k7q2`, `rev_utc_k7q3`).

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `COLUMN_KINDS`, `change_value`, `read_changes` (`task.py`) | The 205-case matrix: both forms, both backends. Every column is writable except `id` and `tag`, and both backends store the same values. | ✅ |
| R2 | `by_range` with `task.cores`, `task.memory` and `task.timeout` | `memory`: 4G, 512MB, 1.5GiB, bare bytes and 2⁶³−1 are accepted; 2⁶³ is refused. `timeout`: 90m, 1h30m and 1:30:00 each store 5400; 1-12 stores 129600. | ✅ |
| R3 | `column.exit_status`, `server.attempts`, `option.part`, and the duration entry for `waited`/`duration` | Each bound is accepted and one past it is refused, exit 2. `-1` works in the single-task form on 3.11. | ✅ |
| R4 | `by_range(number=True)` with `column.cores_max` and `column.memory_max` | 1.5, 1e3, 0 and 32767 are accepted. 32767.5, 32768, −0.1, nan, inf, x and an empty value are refused. | ✅ |
| R5 | `read_bool` | true, NO, Yes, off, ON, 1 and 0 are stored. 2, x, an empty value, none and Null are refused. | ✅ |
| R6 | `read_datetime` | A naive value and one with an offset give the same instant on both backends. now, today, epoch, 2026-02-30 and a time in the DST gap are refused. Exception: F1. | ❌ (F1) |
| R7 | `read_uuid`, `read_zone`, `record_zones` | An upper-case uuid is stored in lower case; malformed uuids are refused. `zone=gpu` records the zone. `zone=default`, `zone=none` and `zone=null` store NULL. | ✅ |
| R8 | `ColumnKind.clears` and `form` | none and null clear the nullable columns. For the R8 columns they are refused, naming the column. | ✅ |
| R9 | `read_changes` runs before `ensuredb()` | Every refused value exits 2 before counting. With several changes in one command, the row is re-read after the refusal, and nothing was written. Exception: F1, exit 5. | ❌ (F1) |
| R10 | `read_id` | `ID id x` and `id=x` both exit 2, on both backends. | ✅ |
| R11 | `read_whole_tag` | `tag=x` and `tag=none` exit 2, and the message names the alternatives. `ID tag x` still merges the tag. | ✅ |
| R12 | `advise()` in `run()` | One WARNING at cores=1025 and at attempt=101; none at 1024 or 100. The value is written and the exit status is 0. | ✅ |
| R13 | unchanged paths | `group`, `--revert`, `--cancel`, `--remove-tag`, `--delete`, `-l 1` and the text columns all work as before. `test_actions_unchanged` passes. | ✅ |
| R14 | `tests/test_update_{numbers,kinds,datetime,tags}.py`, `tests/update_checks.py` | All pass on both backends and both Pythons. No test covers the extreme dates in F1. | ✅ |
| R15 | `UPDATE_HELP`, `docs/_include/task_update_{desc,help}.rst`, `docs/cli/task_update.rst` | Four `versionchanged:: 2.9.0` markers. The new table rows are listed. Only top-level commands are named. The `-W` build is clean. | ✅ |
| R16 | `read_assignment`, `add_tag`, `show_changes` | Non-column keys become tags in both forms and on both backends. An invalid tag key exits 2. The prompt lists each change as a column or a tag. | ✅ |
| R17 | `edit_distance` (OSA), `near_columns` | Refused with a suggestion: a transposition, an insertion, a deletion, a substitution, a case-only key, and short names (`ids`, `tags`, `zoen`, `ID Part 3`). A key two edits away becomes a tag. The `:` form always sets a tag. | ✅ |

**Invariants**

- **§3:** `set(COLUMN_KINDS) == set(Task.columns)` holds (34 columns).
- **§10:** the three `column.*` rows are in `SETTINGS` and covered by `test_bounded_table`.
- **§1:** the write paths are unchanged, and every value is read once, before any write.
- **§12:** the help snippets, both completions, the prose page and the markers are all in the diff.

**Unmapped changes (scope creep):** none blocking.

- `KEY:VALUE` changes are now checked against the tag rules: a bad one exits 2 instead of 5. This also covers the single-task `ID tag KEY:VALUE`, which earlier stored any tag. It follows R16's "same key rules" and R9, and it is documented in `docs/cli/task_update.rst`.
- The CI PostgreSQL job's timeout rises from 20 to 45 minutes, and the job now runs the new test files (supports R14).
- `AGENTS.md` and `invariants.md` now name `COLUMN_KINDS`.
- The seed and ROADMAP status are now `adopted:update-typed-columns`. Their prose is accurate.

### Findings

#### [MEDIUM/CONFIRMED] F1 — a calendar-edge ISO 8601 date exits 5 with a traceback instead of 2
- **Where:** `src/hypershell/task.py:1185`, in `read_datetime` at `local = value.astimezone()`. This line sits outside the `try` that guards `fromisoformat`.
- **Failure scenario:** a date near either end of Python's datetime range passes `DATETIME_PATTERN` and `fromisoformat`, then overflows in `astimezone()`. The command exits 5, logs CRITICAL and writes a traceback, where it should refuse with exit 2. Nothing is written. Failing values include:
  - `9999-12-31` (a common "never" placeholder) and `9999-12-31T23:59`;
  - `0001-01-01` and `0001-01-01T00:00Z`.

  Which of them fail depends on the local zone. Python 3.14 raises `ValueError` and 3.11 `OverflowError`. Both forms and both backends are affected.
- **Evidence:** the commands and exit codes are under *Verification run* above.
- **Touches:** R9 ("No value given to any column SHALL end in a traceback or exit status 5") and R6.
- **Severity:** MEDIUM, a bug on an edge path. These are not absurd input under the rubric:
  - the datetime form documents no range;
  - `9999-12-31` and `0001-01-01` are ordinary placeholder dates;
  - R9's "no traceback" has no exceptions.
- **Remediation hint:** also refuse a `ValueError` or `OverflowError` from `astimezone()`, with the column's `DATETIME_FORM`. Add a test for each end of the range.

### Notes (not findings)

- `note:a=b` is still refused, now by the tag value rule. The parse is as the docs describe.
- With `PGTZ` set apart from `TZ`, PostgreSQL output shows the session's zone. This predates the diff and is already seeded on develop (`timestamp-display-offsets`).
- In the single-task form, a zone change records its Zone row before `Task.update`. A change to an unknown ID therefore still leaves that row behind. It is harmless.

### Human-gate triggers

- **Not triggered.** F1 is in `task.py`, which is not a high-blast-radius core file, and it touches no security or DB-lifecycle invariant.
