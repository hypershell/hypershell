---
slug: update-group-and-legacy-filters
title: hs update writes only an integer group, and the single-task form refuses the
  filters it would ignore
kind: fix
appetite: small
status: in_progress
branch: fix/update-group-and-legacy-filters
base: develop
current_phase: P3
last_updated: '2026-11-24'
phases:
- id: P1
  name: 'Tests first: the red list on 03a02bd8 (tests only)'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  depends_on: []
  parallel: false
  hammerable: false
  hill: downhill
  verify: git diff --quiet 03a02bd8 -- src docs share AGENTS.md .agents .github &&
    test -z "$(git diff --name-only eec25fc0 -- . ':!spec' ':!tests')" && test -n
    "$HS_TEST_POSTGRES" && test "$(grep -cvE '^#|^$' spec/update-group-and-legacy-filters/red.txt)"
    -eq 106 && test "$(grep -c '^pg:' spec/update-group-and-legacy-filters/red.txt)"
    -eq 29 && RED_BASE_REV=03a02bd8 RED_LIST="$PWD/spec/update-group-and-legacy-filters/red.txt"
    sh spec/update-client-retry/drive/bound.sh 2400 sh spec/relay-hardening/drive/red_check.sh
    && RED_LIST="$PWD/spec/update-group-and-legacy-filters/red.txt" sh spec/update-client-retry/drive/bound.sh
    1800 sh spec/contended-writes/drive/rest_green.sh tests/test_update_group.py tests/test_update_refusals.py
    tests/test_update.py tests/test_group_integer.py
- id: P2
  name: An integer group in both forms (R1, R2, R6 part)
  status: done
  satisfies:
  - R1
  - R2
  - R6
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: downhill
  verify: 'test -n "$HS_TEST_POSTGRES" && sh spec/update-client-retry/drive/bound.sh
    1800 uv run pytest -q -p no:cacheprovider tests/test_update_group.py tests/test_update.py
    tests/test_group_integer.py tests/test_contended_recovery.py && sh spec/update-client-retry/drive/bound.sh
    300 .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/update-group-and-legacy-filters/drive/ugl_drive.sh"
    group && for f in src/hypershell/task.py docs/_include/task_update_desc.rst docs/_include/task_update_help.rst;
    do grep -q ''takes an integer as'' $f || exit 1; done && grep -q ''parse_group''
    src/hypershell/task.py && ! grep -nE ''GROUP_M(IN|AX)'' src/hypershell/task.py
    && grep -q ''hs update group=VALUE'' AGENTS.md && grep -q ''hs update group=VALUE''
    .agents/factory/invariants.md && test "$(grep -c ''versionchanged:: 2.9.0'' docs/cli/task_update.rst)"
    -ge 9 && grep -q ''tests/test_update_group.py'' .github/workflows/tests.yml'
- id: P3
  name: The single-task form refuses selection options (R3, R4, R6 part)
  status: pending
  satisfies:
  - R3
  - R4
  - R6
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/update-client-retry/drive/bound.sh 1800 uv run pytest -q -p no:cacheprovider
    tests/test_update_refusals.py tests/test_update.py tests/test_contended_recovery.py
    tests/test_main.py && sh spec/update-client-retry/drive/bound.sh 300 .agents/factory/bin/temp_site.sh
    uv run sh "$PWD/spec/update-group-and-legacy-filters/drive/ugl_drive.sh" legacy
    && for f in src/hypershell/task.py docs/_include/task_update_desc.rst docs/_include/task_update_help.rst;
    do grep -q 'takes no option that selects tasks' $f && grep -qF -- '-w "id == ID"'
    $f || exit 1; done && ! grep -n 'hs task ' docs/_include/task_update_desc.rst
    docs/_include/task_update_help.rst docs/cli/task_update.rst && UV_PROJECT_ENVIRONMENT="${TMPDIR:-/tmp}/docs-venv-ugl"
    sh spec/update-client-retry/drive/bound.sh 900 uv run --python 3.12 --group docs
    sphinx-build -W --keep-going -q -E -b html docs "${TMPDIR:-/tmp}/docs-html-ugl"
- id: P4
  name: 'Gate: the whole suite serially, PostgreSQL, the red list, the drive, the
    docs'
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && env -u HS_TEST_POSTGRES sh spec/update-client-retry/drive/bound.sh
    5400 uv run pytest -q -p no:cacheprovider && sh spec/update-client-retry/drive/bound.sh
    2400 uv run pytest -q -p no:cacheprovider tests/test_update_group.py tests/test_group_integer.py
    tests/test_contended_write.py && sh spec/update-client-retry/drive/bound.sh 900
    uv run pytest -q -p no:cacheprovider $(sed -e '/^#/d' -e '/^$/d' -e 's/^pg://'
    -e 's/ raises=.*$//' spec/update-group-and-legacy-filters/red.txt) && RED_BASE_REV=03a02bd8
    RED_LIST="$PWD/spec/update-group-and-legacy-filters/red.txt" sh spec/update-client-retry/drive/bound.sh
    2400 sh spec/relay-hardening/drive/red_check.sh && for m in group legacy; do sh
    spec/update-client-retry/drive/bound.sh 300 .agents/factory/bin/temp_site.sh uv
    run sh "$PWD/spec/update-group-and-legacy-filters/drive/ugl_drive.sh" $m || exit
    1; done && UV_PROJECT_ENVIRONMENT="${TMPDIR:-/tmp}/docs-venv-ugl" sh spec/update-client-retry/drive/bound.sh
    900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html
    docs "${TMPDIR:-/tmp}/docs-html-ugl"
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — `hs update` writes only an integer group, and the single-task form refuses the filters it would ignore

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/update-group-and-legacy-filters/TECH.md`); the
per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one
atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md). No `research/` (lean plan).

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `hs-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`).
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work. Every phase here is `false`:
  P2 and P3 both edit `TaskUpdateApp` in `task.py` and the same two `_include` files.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
- `hill`: `uphill` → `crest` → `downhill`. A phase stuck `uphill` across builds is a raised hand.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3.
- `verify`: the exact command that proves the phase, run from the worktree root.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md); the checklist is
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §1, §3, §12.
- One phase per `hs-build` invocation; one atomic commit containing **both** the code and the
  `TECH.md` state change, subject `[fix] Build update-group-and-legacy-filters P<n>: …`, signed;
  **no `Co-Authored-By` trailer**. Squashed into the single PR-title commit at `hs-publish`.
- **Tests first.** P1 lands every new test, red on `03a02bd8`; P2 and P3 change no test except to
  fix a mistake in one, noted in the commit body and in `red.txt` (a renamed test is renamed there,
  never dropped without a PLAN note).
- **Docs land with their phase**: help (`UPDATE_HELP`), `docs/_include/task_update_desc.rst`,
  `docs/_include/task_update_help.rst` and the `docs/cli/task_update.rst` marker in the commit that
  changes the behaviour. No `share/` change (no option added or renamed; man pages are rebuilt at
  release). The marker carries no `:pr:` role (added at release).
- Grep phrases in the verifies (`takes an integer as`, `takes no option that selects tasks`,
  `-w "id == ID"`, `hs update group=VALUE`) must each sit on one line in the file.
- Docs and help name top-level commands only (`hs update`, `hs submit`, `hs list`; never `hs task …`);
  no internal or cluster names (public repository); no key on a command line in any example.
- pytest runs serially (no `-n`). The PostgreSQL half needs `HS_TEST_POSTGRES` exported (local
  PostgreSQL 18, `HS_TEST_TIMESCALE` empty) and the sandbox off.
- **One group parser.** `task.py` calls `parse_group` and names neither `GROUP_MIN`/`GROUP_MAX` nor
  its own `int()` for a group (PLAN §2.4, so `bounded-inputs` can re-point the parser).

---

## Phase P1 — Tests first: the red list on `03a02bd8` (tests only)
**Satisfies:** R1–R5 · **Depends on:** —
**Goal:** every new test is in the tree and fails on `03a02bd8` for what it checks; every guard and
every other test in the touched files passes.

- [x] `tests/test_update_group.py` (new): PLAN §6's first table, on `db_site` (imports `admin`,
      `ts_database`, `ts_site` from `tests.test_timescale` as `tests/test_group_integer.py` does).
      Assert exit 2, `group` and the (non-empty) value in stderr, no `Searching database`, no
      `Updated`, every row unchanged; not #118's sentence word for word (PLAN §2.4).
- [x] `tests/test_update_refusals.py`: PLAN §6's second table: the 23 spellings as one table, the
      three red tests and the three guards. The filtered-form guard sets its five tasks with
      `hs update FIELD=VALUE -t n:K -f` (accepted values only).
- [x] `spec/update-group-and-legacy-filters/red.txt`: one node id per red test, 106 lines, the 29
      PostgreSQL ids prefixed `pg:`; a comment header naming the base and the count.
- [x] Nothing outside `tests/` and `spec/` changes.
- **Verify:** see frontmatter: the fence; `HS_TEST_POSTGRES` set; `red.txt` holds 106 ids, 29 `pg:`;
  `red_check` all red on `03a02bd8`; `rest_green` over the new and neighbouring files.
- **Touches:** `tests/test_update_group.py`, `tests/test_update_refusals.py`,
  `spec/update-group-and-legacy-filters/red.txt`.

## Phase P2 — An integer group in both forms (R1, R2, R6 part)
**Satisfies:** R1, R2, R6 · **Depends on:** P1
**Goal:** `group=VALUE` and `ID group VALUE` write only a group `parse_group` accepts, and refuse
anything else with exit 2 before counting, asking or writing; the help and docs say so.

- [x] `task.py`: `change_value(field, text)` (PLAN §2.1), importing `parse_group` and
      `TaskGroupError` from `hypershell.data.model`; `process_arguments` and `update_legacy` call it in
      place of their two copies of the typing code (`tag` path in `update_legacy` unchanged).
- [x] `UPDATE_HELP`, `docs/_include/task_update_desc.rst`, `docs/_include/task_update_help.rst`
      (`ARGS`): PLAN §2.3's group sentence.
- [x] `docs/cli/task_update.rst`: one `.. versionchanged:: 2.9.0` after #119's entry: the group
      refusal and what happened before (PLAN §2.3). P3 adds its sentence to the same entry.
- [x] `AGENTS.md` and `.agents/factory/invariants.md` §3: PLAN §2.3's clause, in lockstep.
- [x] `.github/workflows/tests.yml`: `tests/test_update_group.py` added to the PostgreSQL job's list
      (PLAN §5; drop this step and its verify term if the plan gate rules otherwise).
- [x] `spec/update-group-and-legacy-filters/drive/ugl_drive.sh` (new, SPDX header, modes `group` and
      `legacy`, `legacy` failing "not built" until P3): PLAN §6's drive; `group` mode here.
- **Verify:** see frontmatter: the group tests (both dialects) and neighbours; the `group` drive;
  the phrase in all three files; `parse_group` used and no group bound in `task.py`; the constitution
  clause; ≥ 9 markers; the CI list.
- **Touches:** `src/hypershell/task.py`, `docs/_include/task_update_desc.rst`,
  `docs/_include/task_update_help.rst`, `docs/cli/task_update.rst`, `AGENTS.md`,
  `.agents/factory/invariants.md`, `.github/workflows/tests.yml`,
  `spec/update-group-and-legacy-filters/drive/ugl_drive.sh`.

## Phase P3 — The single-task form refuses selection options (R3, R4, R6 part)
**Satisfies:** R3, R4, R6 · **Depends on:** P2
**Goal:** `hs update ID FIELD VALUE` with any of the thirteen selection options exits 2 naming each,
with or without `-f`, and writes nothing; everything else behaves as before; the help and docs say so.

- [ ] `task.py`: the `(attribute, name)` constant and the identity tests (PLAN §2.2) in `run()`'s
      single-task refusal, action options first; the `-w "id == ID"` hint when a selection option is
      named; #119's message unchanged for action options alone.
- [ ] `UPDATE_HELP`, `docs/_include/task_update_desc.rst`, `docs/_include/task_update_help.rst`
      ("Legacy Mode"): PLAN §2.3's single-task sentences.
- [ ] `docs/cli/task_update.rst`: P2's entry gains the options sentence (options ignored and the
      task written, before).
- [ ] `ugl_drive.sh`: `legacy` mode.
- **Verify:** see frontmatter: the refusal tests and neighbours; the `legacy` drive; the phrases in
  all three files; no `hs task `; the docs build with `-W`.
- **Touches:** `src/hypershell/task.py`, `docs/_include/task_update_desc.rst`,
  `docs/_include/task_update_help.rst`, `docs/cli/task_update.rst`,
  `spec/update-group-and-legacy-filters/drive/ugl_drive.sh`.

## Phase P4 — Gate: the whole suite serially, PostgreSQL, the red list, the drive, the docs
**Satisfies:** R1–R6 · **Depends on:** P3
**Goal:** nothing else broke; every red test is green; the red list is still red on `03a02bd8`; both
drive modes pass; the docs build with `-W`.

- [ ] Run the frontmatter verify (sandbox off, `HS_TEST_POSTGRES` exported): the whole suite with
      `HS_TEST_POSTGRES` unset, then the PostgreSQL-aware update and group files with it set. Record
      counts and time in the commit body.
- [ ] Release note: nothing to write here; `hs-publish` drafts the squash body from GOAL.md's two
      user-visible changes.
- **Touches:** `spec/update-group-and-legacy-filters/TECH.md` only.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
