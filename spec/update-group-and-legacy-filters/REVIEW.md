# REVIEW — `hs update` writes only an integer group, and the single-task form refuses the filters it would ignore

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only**. Every finding cites
> an **executed** command.

- **Reviewed commit:** 1d097603  ·  **Base:** develop (merge-base 03a02bd8; develop d132d45c merges cleanly)  ·  **Date:** 2026-11-24
- **Verdict:** approved
- **Cycle:** 1 of ≤3
- **Contract drift check:** `GOAL.md` changed after shaping only in the GOAL gate commit.

## Verification run

Fresh reviewer, xhigh effort, on Python 3.14 and 3.11, against SQLite and local PostgreSQL 18.

- **Groups (R1/R2), both forms, both backends:** 29 values tried with `-f` and with a "yes" at the
  prompt. Accepted values are stored as integers (`typeof`/`pg_typeof`), and every refused value
  exits 2 before counting, prompting or writing, leaving every row unchanged, including the other
  changes given in the same command. `hs submit -g` accepts and refuses exactly the same set.
- **Single-task form (R3):** 26 selection-option variants, alone, with and without `-f`, with a bad
  group and with `--cancel`: 110 refusal probes per backend, all exit 2 with no row changed. The
  refusal comes before the database is opened. `-f` and `--no-confirm` alone are still accepted.
- **Everything else (R4):** a 70-step sequence run on a develop checkout and on the branch, on both
  backends, compared exit status, the last stderr lines and full-row snapshots. Every step matched.
  The one textual difference is a source UUID inside a PostgreSQL IntegrityError message.
- **Develop contrast:** develop stores text, reals or out-of-range values for the same inputs, or
  exits 5 with a traceback, as the Problem section describes.
- **Suites:** full suite on 3.14 with PostgreSQL gave 4760 passed and 32 environment failures (30
  upgrade templates not configured locally, 2 TimescaleDB-version checks); neither file is touched by
  the diff. The cycle's tests on 3.11 gave 129 passed, 39 PostgreSQL skipped. The same tests fail
  against develop's code on every refusal case.
- **Docs:** `sphinx-build -W` on 3.12 passes; the rendered pages carry the new text.

## Requirement → evidence matrix

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1 | Accepted forms (`07`, `+5`, `-0`, the int32 bounds, `1_000`, surrounding space) stored as integers in both forms, on SQLite and PostgreSQL | ✅ |
| R2 | 19 refused values exit 2 in both forms with or without `-f`, nothing written, message names the value or the range | ✅ |
| R3 | Every selection option by short and long name refused in the single-task form, exit 2, message names each option and the guarded `-w "id == ID"` form | ✅ |
| R4 | 70-step develop/branch comparison identical on both backends | ✅ |
| R5 | `tests/test_update_group.py` and `tests/test_update_refusals.py` cover every listed value, form and option; the CI PostgreSQL job runs the group tests | ✅ |
| R6 | `hs update --help`, `task_update_desc.rst`, `task_update_help.rst`, `versionchanged:: 2.9.0` in `docs/cli/task_update.rst`; top-level commands only | ✅ |

Unmapped changes: none. `change_value` replaces the coercion each form did on its own;
`SELECTION_OPTIONS`/`option_given` implement R3; AGENTS.md and invariants §3 carry the same sentence;
ROADMAP and the two seeds move to `adopted`.

## Findings

None.

## Absurd-input and non-goal notes (not findings)
- A 5,000-digit group is refused with the "must be an integer" message rather than the range one
  (Python's integer-digit limit); exit 2 either way.
- Fullwidth digits and a trailing newline are accepted, exactly as `hs submit -g` accepts them.
- A bad group together with a selection option reports the option first; the group is reported once
  the option is dropped. This matches #119's first-refusal-wins pattern and the plan.
- On develop, PostgreSQL also stored `0x10` as 16 and rounded `1.5` to 2; those now exit 2 too. The
  release note's "before" clause describes the SQLite behaviour, which is accurate as written.

## Human-gate triggers

None. No coupled-core or security/DB-invariant file is touched; `task.py` and the docs only.
