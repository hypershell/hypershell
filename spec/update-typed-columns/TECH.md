---
slug: update-typed-columns
title: hs update reads every column it writes by the column's kind
kind: fix
appetite: big
status: in_progress
branch: fix/update-typed-columns
base: develop
current_phase: P0
last_updated: '2026-11-24'
phases:
- id: P0
  name: 'Merge origin/develop once bounded-inputs has landed; pin its API'
  status: pending
  satisfies: []
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: >-
    git merge-base --is-ancestor origin/develop HEAD &&
    git show origin/develop:src/hypershell/core/config_schema.py | grep -q '^def parse_setting' &&
    uv run python -c "from hypershell.core.config_schema import SETTINGS, Range, Kind, parse_setting, SettingError, advise, setting_for, read_value, FORMS, shown, INT16_MAX, INT64_MAX;
    from hypershell.core.exceptions import ReadingMixin;
    from hypershell.data.model import parse_group, TaskGroupError, zone_name, zone_id, DEFAULT_ZONE_ID, Zone;
    from hypershell.task import TaskUpdateApp, change_value;
    assert issubclass(TaskUpdateApp, ReadingMixin);
    assert {'task.cores', 'task.memory', 'task.timeout', 'server.attempts', 'option.part', 'option.group'} <= set(SETTINGS);
    assert setting_for('option.part') is None and issubclass(SettingError, ValueError);
    assert parse_setting('task.memory', '4G', name='memory') == 4294967296;
    assert read_value(Kind.BOOL, 'Yes') is True and Kind.NUMBER and Kind.INT" &&
    test -n "$HS_TEST_POSTGRES" &&
    sh spec/update-client-retry/drive/bound.sh 2400 uv run pytest -q -p no:cacheprovider
    tests/test_bounded_table.py tests/test_bounded_parsers.py tests/test_bounded_advisory.py
    tests/test_update_group.py tests/test_update_refusals.py tests/test_update.py
- id: P1
  name: 'Tests first: the red list on the merged develop (tests only)'
  status: pending
  satisfies: [R1, R2, R3, R4, R5, R6, R7, R8, R9, R10, R11, R12, R13, R14, R15, R16, R17]
  depends_on: [P0]
  parallel: false
  hammerable: false
  hill: uphill
  verify: >-
    base="$(sed -n 's/^# base: //p' spec/update-typed-columns/red.txt)" && test -n "$base" &&
    git merge-base --is-ancestor "$base" HEAD &&
    git show "$base:src/hypershell/core/config_schema.py" | grep -q '^def parse_setting' &&
    test -z "$(git diff --name-only "$base" -- src docs share AGENTS.md .agents .github)" &&
    test -n "$HS_TEST_POSTGRES" &&
    for f in numbers kinds datetime tags; do grep -q "^tests/test_update_$f.py::" spec/update-typed-columns/red.txt &&
    grep -q "^pg:tests/test_update_$f.py::" spec/update-typed-columns/red.txt || exit 1; done &&
    grep -q '^tests/test_bounded_table.py::' spec/update-typed-columns/red.txt &&
    RED_BASE_REV="$base" RED_LIST="$PWD/spec/update-typed-columns/red.txt"
    sh spec/update-client-retry/drive/bound.sh 5400 sh spec/relay-hardening/drive/red_check.sh &&
    RED_LIST="$PWD/spec/update-typed-columns/red.txt" sh spec/update-client-retry/drive/bound.sh 2400
    sh spec/contended-writes/drive/rest_green.sh tests/test_update_numbers.py tests/test_update_kinds.py
    tests/test_update_datetime.py tests/test_update_tags.py tests/test_bounded_table.py
    tests/test_update_group.py tests/test_update_refusals.py tests/test_update.py
- id: P2
  name: 'Numbers, sizes and durations: the reader, the new rows, refusal before the database (R2-R4, R8-R10, R12)'
  status: pending
  satisfies: [R1, R2, R3, R4, R8, R9, R10, R12, R13, R14, R15]
  depends_on: [P1]
  parallel: false
  hammerable: false
  hill: uphill
  verify: >-
    test -n "$HS_TEST_POSTGRES" &&
    sh spec/update-client-retry/drive/bound.sh 2400 uv run pytest -q -p no:cacheprovider
    tests/test_update_numbers.py tests/test_bounded_table.py tests/test_bounded_parsers.py
    tests/test_bounded_config_set.py tests/test_update_group.py tests/test_update_refusals.py tests/test_update.py &&
    for b in sqlite postgres; do sh spec/update-client-retry/drive/bound.sh 600
    sh spec/update-typed-columns/drive/utc_drive.sh $b numbers || exit 1; done &&
    for f in src/hypershell/task.py docs/_include/task_update_desc.rst docs/_include/task_update_help.rst;
    do grep -q 'is read by its kind' $f || exit 1; done &&
    grep -A6 'versionchanged:: 2.9.0' docs/cli/task_update.rst | grep -q 'by its kind' &&
    grep -q "'column.exit_status'" src/hypershell/core/config_schema.py &&
    uv run python -c "from hypershell.core.config_schema import setting_for; assert setting_for('column.exit_status') is None" &&
    for f in numbers kinds datetime tags; do grep -q "tests/test_update_$f.py" .github/workflows/tests.yml || exit 1; done &&
    grep -q 'timeout-minutes: 60' .github/workflows/tests.yml
- id: P3
  name: 'Booleans, uuids, the zone and the text columns (R5, R7, R8)'
  status: pending
  satisfies: [R1, R5, R7, R8, R9, R13, R14, R15]
  depends_on: [P2]
  parallel: false
  hammerable: false
  hill: uphill
  verify: >-
    test -n "$HS_TEST_POSTGRES" &&
    sh spec/update-client-retry/drive/bound.sh 2400 uv run pytest -q -p no:cacheprovider
    tests/test_update_kinds.py tests/test_update_numbers.py tests/test_update_group.py
    tests/test_update_refusals.py tests/test_update.py tests/test_zone_postgres.py &&
    for b in sqlite postgres; do sh spec/update-client-retry/drive/bound.sh 600
    sh spec/update-typed-columns/drive/utc_drive.sh $b kinds || exit 1; done &&
    for f in src/hypershell/task.py docs/_include/task_update_desc.rst docs/_include/task_update_help.rst;
    do grep -q '8-4-4-4-12' $f && grep -q 'hs submit --zone' $f && grep -q 'yes/no' $f || exit 1; done &&
    ! grep -n 'hs task ' docs/_include/task_update_desc.rst docs/_include/task_update_help.rst docs/cli/task_update.rst
- id: P4
  name: 'Datetimes: ISO 8601, local time, the same instant on both backends (R6); the map is complete'
  status: pending
  satisfies: [R1, R6, R8, R9, R14, R15]
  depends_on: [P3]
  parallel: false
  hammerable: false
  hill: uphill
  verify: >-
    test -n "$HS_TEST_POSTGRES" &&
    sh spec/update-client-retry/drive/bound.sh 2400 uv run pytest -q -p no:cacheprovider
    tests/test_update_datetime.py tests/test_update_kinds.py tests/test_update_numbers.py
    tests/test_update_refusals.py tests/test_update.py &&
    env -u HS_TEST_POSTGRES UV_PROJECT_ENVIRONMENT="${TMPDIR:-/tmp}/venv311-utc"
    sh spec/update-client-retry/drive/bound.sh 1200 uv run --python 3.11 pytest -q -p no:cacheprovider
    tests/test_update_datetime.py &&
    for b in sqlite postgres; do sh spec/update-client-retry/drive/bound.sh 600
    sh spec/update-typed-columns/drive/utc_drive.sh $b datetime || exit 1; done &&
    for f in src/hypershell/task.py docs/_include/task_update_desc.rst docs/_include/task_update_help.rst;
    do grep -q 'ISO 8601' $f || exit 1; done &&
    grep -q 'COLUMN_KINDS' AGENTS.md && grep -q 'COLUMN_KINDS' .agents/factory/invariants.md
- id: P5
  name: 'Tags: tag= refused, any other key a tag, near-misses refused, the prompt (R11, R16, R17)'
  status: pending
  satisfies: [R1, R9, R11, R13, R14, R15, R16, R17]
  depends_on: [P4]
  parallel: false
  hammerable: false
  hill: uphill
  verify: >-
    test -n "$HS_TEST_POSTGRES" &&
    sh spec/update-client-retry/drive/bound.sh 3600 uv run pytest -q -p no:cacheprovider
    tests/test_update_tags.py tests/test_update_numbers.py tests/test_update_kinds.py
    tests/test_update_datetime.py tests/test_update_group.py tests/test_update_refusals.py
    tests/test_update.py tests/test_completions.py &&
    for b in sqlite postgres; do sh spec/update-client-retry/drive/bound.sh 600
    sh spec/update-typed-columns/drive/utc_drive.sh $b tags || exit 1; done &&
    for f in src/hypershell/task.py docs/_include/task_update_desc.rst docs/_include/task_update_help.rst;
    do grep -q 'not a column is a tag' $f && grep -q 'one edit' $f || exit 1; done &&
    ! grep -n 'hs task ' docs/_include/task_update_desc.rst docs/_include/task_update_help.rst docs/cli/task_update.rst &&
    UV_PROJECT_ENVIRONMENT="${TMPDIR:-/tmp}/docs-venv-utc" sh spec/update-client-retry/drive/bound.sh 900
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs "${TMPDIR:-/tmp}/docs-html-utc"
- id: P6
  name: 'Gate: the whole suite, PostgreSQL, the red list, every drive mode, the docs'
  status: pending
  satisfies: [R1, R2, R3, R4, R5, R6, R7, R8, R9, R10, R11, R12, R13, R14, R15, R16, R17]
  depends_on: [P5]
  parallel: false
  hammerable: false
  hill: uphill
  verify: >-
    test -n "$HS_TEST_POSTGRES" &&
    env -u HS_TEST_POSTGRES sh spec/update-client-retry/drive/bound.sh 5400 uv run pytest -q -p no:cacheprovider &&
    sh spec/update-client-retry/drive/bound.sh 3600 uv run pytest -q -p no:cacheprovider
    tests/test_update_numbers.py tests/test_update_kinds.py tests/test_update_datetime.py tests/test_update_tags.py
    tests/test_update_group.py tests/test_bounded_table.py tests/test_zone_postgres.py &&
    sh spec/update-client-retry/drive/bound.sh 1800 uv run pytest -q -p no:cacheprovider
    $(sed -e '/^#/d' -e '/^$/d' -e 's/^pg://' -e 's/ raises=.*$//' spec/update-typed-columns/red.txt) &&
    base="$(sed -n 's/^# base: //p' spec/update-typed-columns/red.txt)" && test -n "$base" &&
    RED_BASE_REV="$base" RED_LIST="$PWD/spec/update-typed-columns/red.txt"
    sh spec/update-client-retry/drive/bound.sh 5400 sh spec/relay-hardening/drive/red_check.sh &&
    for m in numbers kinds datetime tags; do for b in sqlite postgres; do
    sh spec/update-client-retry/drive/bound.sh 600 sh spec/update-typed-columns/drive/utc_drive.sh $b $m || exit 1;
    done; done &&
    UV_PROJECT_ENVIRONMENT="${TMPDIR:-/tmp}/docs-venv-utc" sh spec/update-client-retry/drive/bound.sh 900
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs "${TMPDIR:-/tmp}/docs-html-utc"
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---

# TECH.md — `hs update` reads every column it writes by the column's kind

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/update-typed-columns/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its `verify:`
command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R1–R17 are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs `01`–`05`.

## The build waits for `bounded-inputs`

This cycle builds on `bounded-inputs` (branch `fix/bounded-inputs`, in review remediation). **No
phase starts until `bounded-inputs` has landed on `develop`; the foreman tells the builder.** P0 then
merges `origin/develop` and its verify imports and checks the exact API PLAN §2.9 names, so a
remediation change that moved one of them stops the build at the merge. If P0's verify fails on a
moved name, stop and return to `/hs-plan`; do not adapt the design in P0.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `hs-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`). P0 satisfies
  none: it is the dependency merge.
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: every phase is `false`: they all edit `TaskUpdateApp` and `change_value` in `task.py`,
  the same three docs files, and P2 edits `core/config_schema.py`.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
  Every phase here is `false`.
- `hill`: `uphill` (still figuring it out) → `crest` (unknowns resolved) → `downhill` (just
  executing). A phase stuck `uphill` across builds is a raised hand → escalate to the human.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3.
- `verify`: the exact command that proves the phase, run from the worktree root with `sh -c`.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md); the checklist is
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §1, §2, §3, §10, §12.
- One phase per `hs-build` invocation; one atomic commit containing **both** the code and the
  `TECH.md` state change, subject `[fix] Build update-typed-columns P<n>: …`, signed; **no
  `Co-Authored-By` trailer**. Squashed into the single PR-title commit at `hs-publish`.
- **Environment.** `UV_CACHE_DIR` and `UV_PROJECT_ENVIRONMENT` exported to the cycle's own paths
  (the foreman names them); `HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres`
  and `HS_TEST_TIMESCALE=` (empty) exported for every verify, so `db_site` runs SQLite and local
  PostgreSQL 18; the sandbox off for anything that reaches PostgreSQL. Tests create and drop their own
  databases; the drive creates and drops `utc_drive_<mode>`. pytest runs serially (no `-n`).
- **Tests first.** P1 lands every new test, red on the merged base for what it checks. Later phases
  change a test only to fix a mistake in it, noted in the commit body and in `red.txt` (a renamed
  test is renamed there, never dropped without a PLAN note). New names (`COLUMN_KINDS`,
  `read_changes`, the row constants) are imported **inside** tests, so a missing name fails in the
  call phase and counts as RED.
- **Docs land with their phase**: `UPDATE_HELP`, `docs/_include/task_update_desc.rst`,
  `docs/_include/task_update_help.rst` and a `.. versionchanged:: 2.9.0` entry in
  `docs/cli/task_update.rst` in the commit that changes the behaviour (no `:pr:` role). Each grep
  phrase in a verify (`is read by its kind`, `8-4-4-4-12`, `hs submit --zone`, `yes/no`, `ISO 8601`,
  `not a column is a tag`, `one edit`) sits on one line in each file.
- Docs and help name top-level commands only (`hs update`, `hs submit`, `hs list`, `hs info`;
  never `hs task …`); no internal or cluster names; no key on a command line in any example.
- **One parser per kind.** `task.py` reads ranged columns only through `parse_setting` with the
  entries PLAN §2.2 names, never its own `int()`/`float()`/bound; the group only through
  `parse_group`.
- **The model is not edited.** `data/model.py` stays as merged (PLAN §3); tags are checked one at a
  time (PLAN §2.7).

---

## Phase P0 — Merge `origin/develop` once `bounded-inputs` has landed; pin its API
**Satisfies:** — (the dependency) · **Depends on:** —
**Goal:** the branch carries `bounded-inputs` as it landed, and every name PLAN §2.9 relies on is
there and behaves as the plan assumes.

- [ ] Only on the foreman's word that `bounded-inputs` is on `develop`: `git fetch origin`, then
      `git merge --no-ff origin/develop` (signed), resolving nothing in `spec/update-typed-columns/`.
- [ ] Read what landed against PLAN §2.9: `parse_setting`, `SettingError`, `advise`, `setting_for`,
      `read_value`/`FORMS`, `shown`, the six entries, `ReadingMixin` on `TaskUpdateApp`,
      `parse_group`. Note in the commit body, from `spec/bounded-inputs/REVIEW.md` and the landed
      code: how F8 settled whole decimals (`Range._read` or the tag path only), where F2 put the `-t`
      tag reading, and whether `hs config` exemptions (F4) touched `setting_for`.
- [ ] If a relied-on name moved or changed meaning, stop: set the phase `blocked` with the reason and
      return to `/hs-plan`.
- **Verify:** see frontmatter: `origin/develop` merged and carries `parse_setting`; the API import and
  behaviour checks; the bounded and update suites on both backends.
- **Touches:** the merge; `spec/update-typed-columns/TECH.md`.

## Phase P1 — Tests first: the red list on the merged `develop` (tests only)
**Satisfies:** R1–R17 · **Depends on:** P0
**Goal:** every new test is in the tree and fails on the merged base for what it checks; every guard
and every other test in the touched files passes.

- [ ] `tests/test_update_numbers.py` (new; `db_site` from `tests/input_checks.py`, importing
      `admin`, `ts_database`, `ts_site` from `tests.test_timescale`; helpers from
      `tests/test_update_group.py` and `tests/bounded_checks.py`): PLAN §6's row. Layer 1: in-process
      `change_value(field, text)` for `cores`, `memory`, `timeout`, `waited`, `duration`,
      `exit_status`, `attempt`, `part`, `cores_max`, `memory_max`: every accepted form R2 names, each
      bound read from `SETTINGS` (never retyped) and one past, every R9 refusal (empty, `x`, `true`,
      `nan`, `inf`, a fraction for the whole numbers, a duration for `memory`, a size for `timeout`),
      `none`/`null`/`NONE` (clears, or refused for `attempt` and `part`). Layer 2: one test per
      (column, form, backend) looping over the same values through the CLI, with `snapshot`
      unchanged on refusal and the stored type checked on acceptance (SQLite `typeof()`). Layer 3:
      `id` refused in both forms; a refused change given with a good one and a tag (nothing written,
      with and without `-f`); first refusal wins (the message names the first bad argument only); a
      fresh site gets no database file on refusal; `cores=4.0` exits as `hs submit -c 4.0` does;
      R12: one WARNING at `cores=1025` and `attempt=101`, none at 1,024 and 100, exit 0; the new
      rows' `Range.text` and `setting_for` returning None for them; `task_update_desc.rst` gives each
      ranged column's `Range.text`.
- [ ] `tests/test_update_kinds.py` (new): `retried` (every word in any case, `none`, `x`, `2`,
      empty); the six uuid columns (lower case stored from upper case; braces, `urn:uuid:`, no
      hyphens, a trailing newline, `<direct>` refused; the reserved source ids accepted; `none`
      clears all but `submit_id`); `zone` (`default`, `none`, `NULL` → NULL; `x` → its id, the
      `Zone` row recorded and `hs list` showing `x`; `Default` a separate zone; empty and control
      characters refused; nothing recorded on a refusal); `args` (`none` refused, `''` and `1` kept as
      text); the eight text columns unchanged (R13).
- [ ] `tests/test_update_datetime.py` (new): the four columns; accepted forms (date, `T` and space,
      seconds, 1–6 fraction digits, `Z`, `±HH:MM`); refused (`now`, `today`, `epoch`, `x`, `5`, `1h`,
      `20260101T0900`, `2026-W01`, `2026-01-01T09`, `2026-01-01x09:00`, `T24:00`, `…,5`, 7 fraction
      digits, `+05`, `2026-02-30`, empty, a DST-gap time); `none` clears all but `submit_time`; R6:
      `…T09:00`, `…T14:00Z`, `…T19:00+05:00` give the same instant on both backends, with
      `TZ=America/Indiana/Indianapolis` and `PGTZ=UTC` in the subprocess environment (a naive write
      would be caught), and the same local wall time in `hs info` and `hs list`;
      `set(COLUMN_KINDS) == set(Task.columns)`.
- [ ] `tests/test_update_tags.py` (new): R10 single-task `ID id x`; R11 `tag=x`, `tag=none` (exit 2,
      the message names `KEY=VALUE`, `KEY:VALUE` and `--remove-tag`), `ID tag a:1` still merges;
      R13 guards (`--cancel`, `--revert`, `--delete`, `--remove-tag`, `KEY:VALUE`); R16 (`color=blue`
      in both forms is the tag `color`; `exti_stauts=0` two edits away is a tag; an invalid key
      `a b=1` exits 2 with the tag rules' message; the prompt lists `column`, `tag` and `remove` lines
      with `stdin='no\n'`, nothing written); R17 in both forms (`exit_stauts`, `exitstatus`,
      `exit_sstatus`, `exit_statue`, `Exit_Status`, `Cores`, `tags`, `ip`, `zones`, `groups`; the
      message's four parts; `exit_stauts:0` and `exit_status:0` set tags; `-f` writes nothing); the
      separator rule (`note:a=b`, `time=12:30`, `start_time=2026-01-01T09:00`); the bash and zsh
      completions do not offer `tag=`.
- [ ] `tests/test_bounded_table.py`: `COLUMN_ONLY` (`column.exit_status`, `column.cores_max`,
      `column.memory_max`) unioned where `OPTION_ONLY` is; samples in `RANGES`.
- [ ] Existing tests that R16 changes (an unknown field expected to exit 2): updated to R16 and listed
      in the commit body (research/05 found none).
- [ ] `spec/update-typed-columns/red.txt`: header `# base: <sha of origin/develop as merged in P0>`,
      the checker and the counts; one node id per red test, `pg:` for PostgreSQL nodes. Layer 1 listed
      per kind, not per value, to hold `red_check.sh` to its bound.
- [ ] Nothing outside `tests/` and `spec/` changes.
- **Verify:** see frontmatter: the base is the merged `develop` and carries `parse_setting`; no
  change outside `tests/` and `spec/`; each new file has SQLite and PostgreSQL red nodes; every node
  red on the base (`spec/relay-hardening/drive/red_check.sh`); the rest green
  (`spec/contended-writes/drive/rest_green.sh`).
- **Touches:** `tests/test_update_numbers.py`, `tests/test_update_kinds.py`,
  `tests/test_update_datetime.py`, `tests/test_update_tags.py`, `tests/test_bounded_table.py`,
  `spec/update-typed-columns/red.txt`.

## Phase P2 — Numbers, sizes and durations (R2–R4, R8–R10, R12)
**Satisfies:** R1–R4, R8–R10, R12–R15 (part) · **Depends on:** P1
**Goal:** the ten ranged columns are read by their entries in both forms and refused with exit 2
before the database is opened; `id` is refused in both forms; the advisory tier warns; the three rows
are in the table.

- [ ] `core/config_schema.py`: `EXIT_STATUS`, `PEAK_CORES`, `PEAK_MEMORY` and their `column.` keys in
      `SETTINGS` (PLAN §2.9); `setting_for` excludes `column.`.
- [ ] `task.py`: `COLUMN_KINDS` for the ten ranged columns plus `group`, `id`, the text columns as
      today; `change_value` dispatches by it (PLAN §2.2), unmapped columns keep today's reading until
      P4; `read_changes` (PLAN §2.1) called in `run()` before `ensuredb()`, the `process_arguments`
      checks moved into it; `update_legacy`/`update_tasks` take `self.changes`; R10 in both forms;
      `advise()` after reading (PLAN §2.6).
- [ ] `UPDATE_HELP`, `task_update_desc.rst` (the column list with each entry's `Range.text`),
      `task_update_help.rst`: every column `is read by its kind`, the size and duration forms, refusal
      exit 2 before anything is counted, asked or written; `task_update.rst` `versionchanged:: 2.9.0`.
- [ ] `.github/workflows/tests.yml`: the four new files appended to the PostgreSQL job's list (line
      121) and its comment; the `tests` job's `timeout-minutes` 45 → 60.
- [ ] `spec/update-typed-columns/drive/utc_drive.sh` (new, SPDX header, `BACKEND MODE`, modes
      `numbers`, `kinds`, `datetime`, `tags`; later modes fail "not built"): PLAN §6; `numbers` here.
- **Verify:** see frontmatter: the numbers tests and neighbours on both backends; the `numbers` drive
  on both; the phrase in all three files; the marker; the row and its `setting_for` exclusion; the CI
  list and timeout.
- **Touches:** `src/hypershell/core/config_schema.py`, `src/hypershell/task.py`,
  `docs/_include/task_update_desc.rst`, `docs/_include/task_update_help.rst`,
  `docs/cli/task_update.rst`, `.github/workflows/tests.yml`,
  `spec/update-typed-columns/drive/utc_drive.sh`.

## Phase P3 — Booleans, uuids, the zone and the text columns (R5, R7, R8)
**Satisfies:** R1, R5, R7, R8, R9, R13–R15 (part) · **Depends on:** P2
**Goal:** `retried`, the six uuid columns, `zone`, `args` and the text columns are read by kind in both
forms; a zone name is recorded only after the prompt.

- [ ] `task.py`: boolean (`read_value(Kind.BOOL)`), `uuid_value`, `zone_value` (PLAN §2.3, §2.4)
      in `COLUMN_KINDS`; `args` refuses `none`/`null`; `Changes.zones` recorded with `Zone.ensure`
      just before the write in both forms.
- [ ] Help and docs: the forms for `retried` (`yes/no` on one line with the other words), the uuid
      columns (`8-4-4-4-12`), `zone` (read as `hs submit --zone` reads it; `default` and `none` the
      default zone; a zone named `none` or `null` cannot be set); the `versionchanged` entry extended.
- [ ] `utc_drive.sh`: `kinds` mode.
- **Verify:** see frontmatter: the kinds tests and neighbours (with `tests/test_zone_postgres.py`) on
  both backends; the `kinds` drive on both; the phrases; no `hs task `.
- **Touches:** `src/hypershell/task.py`, `docs/_include/task_update_desc.rst`,
  `docs/_include/task_update_help.rst`, `docs/cli/task_update.rst`,
  `spec/update-typed-columns/drive/utc_drive.sh`.

## Phase P4 — Datetimes (R6); the map is complete
**Satisfies:** R1, R6, R8, R9, R14, R15 (part) · **Depends on:** P3
**Goal:** the four datetime columns take ISO 8601 only, a naive value as local time, and hold the same
instant on both backends, on Python 3.11 and 3.14 alike; every column has its kind.

- [ ] `task.py`: `datetime_value` (PLAN §2.5: the regex gate, `fromisoformat`, `.astimezone()`, the
      DST-gap refusal); the fallback to today's reading removed, so `COLUMN_KINDS` covers
      `Task.columns`.
- [ ] Help and docs: `ISO 8601`, a value without an offset is local time, `now`/`today`/`epoch`
      refused (use `$(date -Iseconds)`); the `versionchanged` entry extended.
- [ ] `AGENTS.md` and `.agents/factory/invariants.md` §3, in lockstep: `hs update` reads every column
      by its kind through `COLUMN_KINDS`; a new `Task` column gets its kind there in the same commit.
- [ ] `utc_drive.sh`: `datetime` mode (with `TZ` set to a non-UTC zone).
- **Verify:** see frontmatter: the datetime tests and neighbours on both backends; the datetime tests
  on Python 3.11 (SQLite and in-process); the `datetime` drive on both; the phrase; the constitution
  clause.
- **Touches:** `src/hypershell/task.py`, `docs/_include/task_update_desc.rst`,
  `docs/_include/task_update_help.rst`, `docs/cli/task_update.rst`, `AGENTS.md`,
  `.agents/factory/invariants.md`, `spec/update-typed-columns/drive/utc_drive.sh`.

## Phase P5 — Tags (R11, R16, R17)
**Satisfies:** R1, R9, R11, R13–R17 (part) · **Depends on:** P4
**Goal:** `tag=` is refused; any other non-column key is a tag in both forms; a key one edit from a
column is refused with the `:` way out; every tag is checked by the tag rules with exit 2; the prompt
lists column and tag changes.

- [ ] `task.py`: the first-separator split; `tag=` refusal (PLAN §2.8); R16 tags in both forms; R17
      (`edit_distance`, `near_columns`, the two messages, PLAN §2.7); every tag checked alone with
      `Task.ensure_valid_tag`, wrapped as exit 2 (as Q2 is ruled); the prompt's change lines.
- [ ] `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs`: `tag` excluded from the offered
      `FIELD=` as `id` is.
- [ ] Help and docs: `tag=` refused; any key that `is not a column is a tag`; a key `one edit` from a
      column is refused, `KEY:VALUE` always sets a tag (the way to a tag named like a column); the
      first separator splits; the prompt lists the changes; the `versionchanged` entry extended,
      including what R16 replaced (`Unrecognized task field`, `Invalid field name`).
- [ ] `utc_drive.sh`: `tags` mode.
- **Verify:** see frontmatter: the tag tests, every other new file and neighbours (with
  `tests/test_completions.py`) on both backends; the `tags` drive on both; the phrases; no
  `hs task `; the docs build with `-W`.
- **Touches:** `src/hypershell/task.py`, `share/bash_completion.d/hs`,
  `share/zsh/site-functions/_hs`, `docs/_include/task_update_desc.rst`,
  `docs/_include/task_update_help.rst`, `docs/cli/task_update.rst`,
  `spec/update-typed-columns/drive/utc_drive.sh`.

## Phase P6 — Gate: the whole suite, PostgreSQL, the red list, every drive mode, the docs
**Satisfies:** R1–R17 · **Depends on:** P5
**Goal:** nothing else broke; every red test is green; the red list is still red on the base; every
drive mode passes on both backends; the docs build with `-W`.

- [ ] Run the frontmatter verify (sandbox off, `HS_TEST_POSTGRES` exported): the whole suite with
      `HS_TEST_POSTGRES` unset, then the PostgreSQL-aware files with it set, the red nodes green, the
      red list red on its base, the four drive modes on both backends, the docs. Record counts and
      times in the commit body.
- [ ] Release note: nothing to write here; `hs-publish` drafts the squash body from GOAL.md's eleven
      user-visible changes, plus Q2's tag-check change if ruled so.
- [ ] Seed for publish: `Task.ensure_valid_tag` stops at the first empty value (`model.py`), which
      also affects `hs submit` (PLAN §5).
- **Touches:** `spec/update-typed-columns/TECH.md` only.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable; for P0, the foreman's word that
   `bounded-inputs` has landed.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
