---
slug: submit-input-checks
title: 'Submitted input is checked: # is part of the command, a group is an integer,
  and a group below the reached one is refused'
kind: fix
appetite: big
status: in_progress
branch: fix/submit-input-checks
base: develop
current_phase: P2
last_updated: '2026-11-21'
phases:
- id: P1
  name: 'Tests first: merge develop; the five test files and two flipped tests; the
    red list on dfdbaed5 and the guards'
  status: done
  satisfies:
  - R8
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && git merge-base --is-ancestor 02f53777 HEAD
    && RED_LIST=spec/submit-input-checks/drive/red.txt RED_BASE_REV=dfdbaed5 python3
    spec/submit-input-checks/drive/bounded.py 2700 sh spec/relay-hardening/drive/red_check.sh
    && sh spec/submit-input-checks/drive/run_list.sh spec/submit-input-checks/drive/guards.txt
    1200 && python3 spec/submit-input-checks/drive/bounded.py 120 uv run pytest -q
    -p no:cacheprovider --collect-only tests/test_command_hash.py tests/test_group_integer.py
    tests/test_group_stream.py tests/test_reached_group.py tests/test_resubmit_lower_group.py
    tests/test_data/test_model.py tests/test_group_order.py
- id: P2
  name: 'The # parser: no bare cut, comment rule on the raw line, split before the
    template, the Loader''s tags copy'
  status: pending
  satisfies:
  - R1
  - R2
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && python3 spec/submit-input-checks/drive/bounded.py
    2400 uv run pytest -q -p no:cacheprovider tests/test_command_hash.py tests/test_data/test_model.py
    tests/test_submit.py tests/test_template.py tests/test_source.py tests/test_queue_workload.py
    tests/test_stream_input.py && sh spec/submit-input-checks/drive/run_list.sh spec/submit-input-checks/drive/guards.txt
    1200 && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/submit-input-checks/drive/input_drive.sh"
    hash
- id: P3
  name: The integer rule; read-in-full input scanned before any write (hs submit incl.
    stdin, hs cluster incl. --no-db, hs server FILE, --from-json, -t, -g)
  status: pending
  satisfies:
  - R3
  - R4
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && python3 spec/submit-input-checks/drive/bounded.py
    2700 uv run pytest -q -p no:cacheprovider tests/test_group_integer.py tests/test_command_hash.py
    tests/test_submit.py tests/test_submit_json.py tests/test_group_order.py tests/test_cluster_no_db.py
    tests/test_signal_wake.py tests/test_restart.py tests/test_source.py tests/test_data/test_model.py
    && sh spec/submit-input-checks/drive/run_list.sh spec/submit-input-checks/drive/guards.txt
    1200 && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/submit-input-checks/drive/input_drive.sh"
    group
- id: P4
  name: 'Streams refuse one line: the Loader''s line count and stream policy'
  status: pending
  satisfies:
  - R5
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && python3 spec/submit-input-checks/drive/bounded.py
    2700 uv run pytest -q -p no:cacheprovider tests/test_group_stream.py tests/test_group_integer.py
    tests/test_stream_input.py tests/test_cluster_exit.py tests/test_queue_workload.py
    tests/test_group_order.py && sh spec/submit-input-checks/drive/run_list.sh spec/submit-input-checks/drive/guards.txt
    1200 && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/submit-input-checks/drive/input_drive.sh"
    stream
- id: P5
  name: 'The reached group: Task.reached_group, hs submit''s all-or-nothing refusal,
    the C9 default'
  status: pending
  satisfies:
  - R6
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && python3 spec/submit-input-checks/drive/bounded.py
    2700 uv run pytest -q -p no:cacheprovider tests/test_reached_group.py tests/test_groups.py
    tests/test_group_order.py tests/test_submit.py tests/test_submit_json.py tests/test_zone_postgres.py
    tests/test_signal_wake.py && sh spec/submit-input-checks/drive/run_list.sh spec/submit-input-checks/drive/guards.txt
    1200 && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/submit-input-checks/drive/input_drive.sh"
    reached
- id: P6
  name: hs cluster FILE and hs server FILE refuse a lower group on an advanced database
    (gate split into decision and commit)
  status: pending
  satisfies:
  - R7
  depends_on:
  - P5
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && python3 spec/submit-input-checks/drive/bounded.py
    3600 uv run pytest -q -p no:cacheprovider tests/test_resubmit_lower_group.py tests/test_reached_group.py
    tests/test_source.py tests/test_restart.py tests/test_cluster_no_db.py tests/test_server.py
    tests/test_submit_json.py tests/test_group_order.py && sh spec/submit-input-checks/drive/run_list.sh
    spec/submit-input-checks/drive/guards.txt 1200
- id: P7
  name: Docs (versionchanged 2.9.0), AGENTS.md and invariants.md, the release-note
    draft
  status: pending
  satisfies:
  - R9
  depends_on:
  - P4
  - P6
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'python3 spec/submit-input-checks/drive/bounded.py 300 uv run pytest -q
    -p no:cacheprovider tests/test_main.py tests/test_completions.py && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-sic
    python3 spec/submit-input-checks/drive/bounded.py 900 uv run --python 3.12 --group
    docs sphinx-build -W --keep-going -q -E -b html docs /tmp/claude-501/docs-html-sic
    && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-sic python3 spec/submit-input-checks/drive/bounded.py
    900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b man
    docs /tmp/claude-501/docs-man-sic && grep -q ''versionchanged:: 2.9.0'' docs/_include/submit_desc.rst
    && grep -q ''versionchanged:: 2.9.0'' docs/_include/task_groups_alt.rst && test
    "$(git diff dfdbaed5 -- docs/cli/cluster.rst | grep -c ''^+.*versionchanged::
    2.9.0'')" -ge 1 && test "$(git diff dfdbaed5 -- docs/cli/server.rst | grep -c
    ''^+.*versionchanged:: 2.9.0'')" -ge 1 && grep -qF "echo ''#'''' HYPERSHELL:''"
    docs/_include/submit_desc.rst && grep -q ''reached group'' docs/_include/task_groups_alt.rst
    && grep -q ''reached group'' /tmp/claude-501/docs-man-sic/hs.1 && grep -q ''reached
    group'' AGENTS.md && grep -q ''reached group'' .agents/factory/invariants.md &&
    grep -q ''must be an integer'' AGENTS.md && grep -q ''must be an integer'' .agents/factory/invariants.md
    && test -s spec/submit-input-checks/RELEASE-NOTE.md && test "$(git diff dfdbaed5
    -- docs src AGENTS.md .agents | grep -c ''^+.*hs task '')" -eq 0'
- id: P8
  name: Whole suite, serially; the PostgreSQL-aware files with PostgreSQL; the red
    list re-proved on dfdbaed5; the four drives
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  - R8
  - R9
  depends_on:
  - P7
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && env -u HS_TEST_POSTGRES python3 spec/submit-input-checks/drive/bounded.py
    7200 uv run pytest -q -p no:cacheprovider && python3 spec/submit-input-checks/drive/bounded.py
    2700 uv run pytest -q -p no:cacheprovider tests/test_command_hash.py tests/test_group_integer.py
    tests/test_group_stream.py tests/test_reached_group.py tests/test_resubmit_lower_group.py
    tests/test_submit.py tests/test_groups.py tests/test_zone_postgres.py tests/test_contended_write.py
    tests/test_server_initdb.py tests/test_writeback_postgres.py && RED_LIST=spec/submit-input-checks/drive/red.txt
    RED_BASE_REV=dfdbaed5 python3 spec/submit-input-checks/drive/bounded.py 2700 sh
    spec/relay-hardening/drive/red_check.sh && for mode in hash group stream reached;
    do .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/submit-input-checks/drive/input_drive.sh"
    $mode || exit 1; done
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — Submitted input is checked: `#` is part of the command, a group is an integer, and a group below the reached one is refused

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/submit-input-checks/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its `verify:`
command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md) (§ 2 is the build spec).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs `01`–`05`.
- **Drive:** [`drive/`](drive/) — `bounded.py` (wall-clock bound, exit 124), `run_list.sh` (runs a
  list of node ids, `pg:` lines only with `HS_TEST_POSTGRES`), `red.txt` (for the landed
  `spec/relay-hardening/drive/red_check.sh`, run read-only), `guards.txt` (green before and after),
  `input_drive.sh` (`hash`, `group`, `stream`, `reached`; run in a `temp_site` from the repository
  root).

## Frontmatter field reference

As in [`.agents/factory/templates/TECH.md`](../../.agents/factory/templates/TECH.md).

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; the touched invariants are walked in
  [`PLAN.md`](PLAN.md) § 3. `data/model.py` and `server.py` are high blast radius: every phase is
  serial, and the review's human gate applies.
- One phase per `hs-build` invocation; one atomic commit with the code and the `TECH.md` state change,
  subject `[fix] Build submit-input-checks P<n>: …`. **No `Co-Authored-By` trailer.** Signed commits.
- Comments are declarative statements; no R-ids in source. Tag every new test `unit` or `integration`.
- No new option or key: no completion change. `-g` keeps its help text. Docs change in P7 with
  `.. versionchanged:: 2.9.0` and name top-level commands only (never the legacy `task` group). Public repository:
  no internal or host names in tests, docs or commit messages.
- **Environment.** Before every verify, export `UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-sic`,
  `UV_CACHE_DIR=/tmp/claude-501/uv-cache-sic` (Python 3.14) and
  `HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres` (local
  PostgreSQL 18; **the sandbox off** for any verify that reaches it — every one but P7's). Verifies
  fail fast when `HS_TEST_POSTGRES` is unset, so the PostgreSQL cases never skip silently. P7's docs
  builds override the environment to `/tmp/claude-501/docs-venv-sic` (Python 3.12, created at plan
  time) and need the sandbox off. `pytest -n auto` is broken here: every run is serial. Drives and
  tests bind 127.0.0.1; keys only through `HYPERSHELL_SERVER_AUTH`, never `-k`.
- **Bounds.** The host has no `timeout(1)`. Every pytest run and drive goes through
  `drive/bounded.py` (`run_list.sh` and `input_drive.sh` use it). Every new test bounds each subprocess
  it starts (`subprocess.run(..., timeout=)`, `run_cli(..., timeout=)`; `pytest.fail` on expiry, so a
  hang on the base fails by assertion) and stops what it started in `finally`. End each phase with an
  out-of-sandbox `ps -axo pid,command | grep submit-input-checks` and stop only your own leftovers;
  never touch another worktree's processes.
- **Tests-first.** P1 writes every new test (red list and guards are its contract); P2–P6 make them
  pass. New test files import no name the base lacks at module level (`ensure_group`, `TaskGroupError`,
  `scan_task_lines`, `Task.reached_group`, `read_in_full`): import inside the test, so the red-list
  tests collect and fail on `dfdbaed5` by their assertions.
- **PostgreSQL fixture.** One fixture, `params=['sqlite', pytest.param('postgres', marks=needs_server)]`,
  ids `sqlite`/`postgres`; the PostgreSQL branch calls `request.getfixturevalue('ts_database')` and
  `('ts_site')` lazily (import `admin, ts_database, ts_site` from `tests/test_timescale.py` into the
  module), then `hs initdb --yes`, and yields the site and a private engine for direct row edits.
  Prototyped at plan time (below).

### Dry-run (plan time, 2026-11-21)

Every verify was dry-run against this worktree (`src/` identical to `dfdbaed5`) with the files that
exist today, and the test shapes were prototyped in a scratch module placed in `tests/` for the run
only (removed after).

- **Red list mechanics** — `red_check.sh` with `RED_BASE_REV=dfdbaed5` on 8 prototype tests (5 SQLite,
  2 `pg:`, 1 guard), PostgreSQL on, sandbox off: **7 of 7 RED**, the guard **NOT RED** (passes).
  R1 file and arguments by assertion (`echo 'issue` / `echo "direct` stored); `group:x` by assertion
  (exit 0, not 5); `group:nan` by `pytest.fail` at the 30 s subprocess bound (the base hangs); R6 by
  assertion (exit 0, not 2). **New evidence:** on PostgreSQL, `group:x` in a file also hangs
  `hs submit -b 1` (the insert is refused and the committer dies), as `group:nan` does on SQLite.
- **Stream shape** — `printf … | hs cluster -N1` through `tests/test_cluster_exit.py::run_cli`
  (45 s bound): `group:x` RED by assertion (no ERROR line, the line inserted), `group:nan` RED by the
  bound (committer `IntegrityError`, run hangs).
- **`run_list.sh`** — the existing guards (`test_submit.py::test_source_stamp_named_file`,
  `test_groups.py::test_group_starts_based_on_previous_scheduled_tasks`): 2 passed in 9 s; a `pg:` line
  skips with a note when `HS_TEST_POSTGRES` is unset.
- **`input_drive.sh` on the base** (`temp_site.sh`, SQLite): `hash` FAIL (2 of 4 checks; 1 s), `group`
  FAIL (5 of 6; `nan` killed at its 30 s bound; 32 s), `stream` FAIL (5 of 6; `nan` killed at 60 s;
  64 s), `reached` FAIL (3 of 5; 2 s); a bad mode exits 64. Each mode must pass after its phase.
- **Existing suites on the base**, PostgreSQL on (sandbox off), through `bounded.py`, all green:
  `test_model, test_submit, test_template, test_source, test_queue_workload, test_stream_input` 72
  passed (40 s); `test_submit_json, test_group_order, test_cluster_no_db, test_signal_wake,
  test_restart` 97 passed (2 min 14 s); `test_cluster_exit` 41 passed (3 min 9 s); `test_groups,
  test_zone_postgres` 16 passed (58 s); `test_server` 32 passed (11 s); `test_main, test_completions`
  66 passed (22 s). The SQLite-only subset of research 05 (9 files) is 169 passed in 3 min 36 s. The
  P1 verify's `git merge-base --is-ancestor 02f53777 HEAD` fails until P1 merges develop, as it must.
- **Docs** — the html and man builds pass with `-W` on the base from `/tmp/claude-501/docs-venv-sic`
  (sandbox off); the man page `hs.1` carries the task-group page (its "best effort" sentence), so the
  `reached group` grep looks in the right file. P7's greps fail on the base, as they must.
- **Whole suite** — run on the base with PostgreSQL on (sandbox off, `bounded.py 7200`): **3638 passed,
  30 failed, 26 skipped in 27 min 53 s**. All 30 failures are this host's PostgreSQL setup, not the code:
  the local server preloads TimescaleDB 2.32 while `HS_TEST_TIMESCALE` is empty
  (`test_timescale.py::test_server_matches_entry`, `::test_absent_fails_before_schema`), and the upgrade
  templates `hs283`/`hs290a1` are not provisioned (28 in `test_upgrade_postgres.py`; they need Docker,
  which this host lacks). P8 therefore runs the whole suite with `HS_TEST_POSTGRES` unset (every
  PostgreSQL test skips, as in the relay-shutdown-tail P7 record: 24 min 26 s) and then the
  PostgreSQL-aware files with it set: the five new files plus `test_submit, test_groups,
  test_zone_postgres, test_contended_write, test_server_initdb, test_writeback_postgres` — on the base
  the existing six are **173 passed, 5 skipped in 2 min 24 s**.
- **`next_phase.py`** reports P1.

### Expected red list on `dfdbaed5` (P1)

[`drive/red.txt`](drive/red.txt) is the contract: **56 node ids** (45 SQLite, 11 `pg:`), every one
failing on the base by assertion or by its subprocess bound. Summary:

| R | Tests | Fails on the base by |
|---|-------|----------------------|
| R1 | `test_model.py::TestTask::test_split_argline` (flipped lines 29–33); `test_command_hash.py`: file, arguments, stdin, `--template`, `hs cluster` file and stream, `--queue` (+ 2 `pg:`) | assertion: the command stored or run is cut at `#` |
| R2 | `test_command_hash.py::test_template_comment_line_is_not_a_task`, `test_cluster_tag_only_line_sets_group[file\|stream]` | assertion: a comment becomes an `echo` task under `--template`; a tag-only line exits 5 (`TypeError`) under `hs cluster` |
| R3–R4 | `test_group_integer.py`: `hs submit` file per refused value (11 + 4 `pg:`), stdin whole, `--from-json` per type (4), `-t` (database, queue), `-g` out of range, `hs cluster` file (database, `--no-db`), `hs server` file; `test_group_order.py::test_cluster_refuses_group_stored_as_text` | assertion (exit 0 and rows stored; exit 5 from `ConnectionRefusedError` for queue `-t`), or the bound (`nan`, `huge`, and PostgreSQL text values hang) |
| R5 | `test_group_stream.py`: `hs cluster` stream `x`/`nan` (+1 `pg:`), `--no-db` stream, refused tag-only line, `--queue` stream | assertion (bad line inserted or run, no ERROR), or the bound (`nan`) |
| R6, C9 | `test_reached_group.py`: below the reached group (arguments, file, stdin) (+2 `pg:`), default group (+1 `pg:`) | assertion: exit 0 and the task inserted; the default takes the late group |
| R7 | `test_resubmit_lower_group.py`: `hs cluster FILE --update` (+1 `pg:`), `hs server FILE` | assertion: exit 0, the lower group inserted |

The guards ([`drive/guards.txt`](drive/guards.txt), 19 ids) pass on the base and after every phase.

---

## Phase P1 — Tests first
**Satisfies:** R8 · **Depends on:** —
**Goal:** the branch carries develop's #115, and every new test exists, collects on the base, fails
there for what it checks (the red list) or passes there (the guards).

- [x] Merge develop into the branch (`git merge develop`; clean at plan time, `git merge-tree`), so
      the build runs on `02f53777`. A signed merge commit. **Built:** `origin/develop` was
      `6a0c28ea` at build time (it holds #116 `dfdbaed5`, #115 `02f53777` and a seed commit); merged
      clean as the signed merge `50f665e4`. The red list stays proved against `dfdbaed5`.
- [x] Shared helpers in `tests/input_checks.py` (not collected): `bounded` (own session, killed with
      its group at the bound, `pytest.fail`), the `db_site` fixture (SQLite or PostgreSQL, `hs initdb
      --yes`, a private engine), and row readers/writers (`set_task` writes typed columns through
      `Task.__table__`, so SQLite times compare as stored).
- [x] `tests/test_command_hash.py` (R1, R2): `split_argline` unit cases for every Problem-1 line, a
      full-line comment, `#` beside a marker, `## HYPERSHELL:`/`# c # HYPERSHELL:` (now comments), the
      quote escape; CLI through `hs submit` file/arguments/stdin with a database (both dialects) and
      `--queue` (to `hs server --no-db`, bounded client), `--template`, `hs cluster` file and stream,
      a tag-only line through `hs cluster` file and stream; guards for comment and marker counts and
      the quoted-marker refusal (exit 5, the non-goal).
- [x] `tests/test_group_integer.py` (R3, R4): unit `ensure_group`/`parse_group` accepted and refused
      values; `hs submit FILE` refused whole per value (ids `x, empty, float, exp, hex, true, null, nan,
      inf, huge, tagline`; a refused tag-only line in a file), naming the value and line, nothing
      inserted (no task, no Source row); stdin refused whole; `--from-json` per type (`string, float,
      bool, null`) naming the record; `-t group:x` exits 2 in both modes; `-g 3000000000` exits 2;
      `hs cluster FILE` with a database and `--no-db`, `hs server FILE`: exit 5, nothing run or
      inserted; accepted forms as guards.
- [x] `tests/test_group_stream.py` (R5): `Loader` unit tests over `iter([...])` (stream policy) and a
      list (whole policy) with a plain `Queue` and `caplog`; `hs cluster` stream with `x`/`nan` (both
      dialects for `x`), `--no-db` stream, a refused tag-only line keeping the group, `hs submit
      --queue` stream; each with an accepted line (an all-refused stream on a fresh database meets
      the pre-existing empty-input hang, out of scope — PLAN § 5.1).
- [x] `tests/test_reached_group.py` (R6, C9): unit `Task.reached_group` over research 03's scenarios
      S0–S11 on both dialects (rows written through the model, then their columns set); CLI refusals
      below the reached group (arguments, file, stdin, all or nothing, naming both groups), at and above
      it, no reached group (empty, all finished), the C9 default.
      **Amendment (P1, for P5):** the scenarios use realistic times (a task is scheduled after it is
      submitted) and pin research 03's table for both columns. Under those times PLAN § 2.5's SQL
      sketch makes `in_flight=False` a no-op, because an in-flight row's `schedule_time` is at or
      after its own `submit_time`, hence `>= U`: S1, S4, S10 and S11 would give the `in_flight`
      value. The table's intent ("in-flight rows are reverted when the server starts, so they do not
      hold lower groups back") is what the tests pin: with `in_flight=False` the scheduled branch
      counts only completed rows (`completion_time IS NOT NULL AND (schedule_time >= U OR
      completion_time >= U)`); the retry-row branch is unchanged. Also: the in-process tests bind the
      session to PostgreSQL while `DATABASE_DIALECT` is `sqlite`, so the `typeof` filter must be chosen
      from the session's bind (`Session.get_bind().dialect.name`, as `Source.reserved` does), not
      the module constant.
- [x] `tests/test_resubmit_lower_group.py` (R7): `hs cluster FILE --update` of a changed file adding
      a lower group onto an advanced database (exit 2, nothing inserted); `hs server FILE` likewise; the
      same-file `--restart` onto a stranded database (built as in
      `test_group_order.py::test_restart_runs_stranded_lower_group`) runs (guard); a stream's late lower
      group still runs with the warning (guard).
- [x] Flip `tests/test_data/test_model.py::TestTask::test_split_argline` lines 29–33 (kept whole), and
      rename/rewrite `tests/test_group_order.py::test_cluster_runs_group_stored_as_text` as
      `test_cluster_refuses_group_stored_as_text` (exit 5, nothing inserted). Leave
      `test_late_lower_group_warns_after_text_group_started_first` as is (`Task.new(group=…)` stays
      unchecked).
- [x] Make every id in `drive/red.txt` and `drive/guards.txt` exist; amend the lists only to fix an id,
      never to drop a requirement. (Every id existed as written; neither list changed.)
- **Verify:** red list RED on `dfdbaed5` (both dialects), guards green, all five files collect.
- **Verified (2026-11-21):** red check 56 of 56 RED on `dfdbaed5` (11 `pg:` included), guards 19
  passed (PostgreSQL on), 215 tests collected.
- **Touches:** `tests/`.

## Phase P2 — The `#` parser
**Satisfies:** R1, R2 · **Depends on:** P1
**Goal:** a `#` after a line's first non-blank character is part of the command everywhere; comments
and the marker keep their meaning, under `--template` too (PLAN § 2.1).

- [ ] `Task.split_argline`: comment rule on the stripped line, marker found anywhere as today, no
      other cut; `TaskGroupError` not wrapped (it is added in P3; keep the `except` ready for it).
- [ ] `Loader.load_line_task` and `line_is_task`: split the raw line, then expand the template on the
      command; `Task.new(..., raw_args=command, parse_inline=False, tag={**running, **inline})`.
- [ ] `Loader.__init__`: `self.tags = dict(tags or {})`.
- **Verify:** `test_command_hash.py`, the model test and the suites around the parser (both
  dialects); guards; `input_drive.sh hash`.
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/submit.py`.

## Phase P3 — The integer rule on read-in-full input
**Satisfies:** R3, R4 · **Depends on:** P2
**Goal:** a non-integer or out-of-range group is refused wherever it arrives; a file, JSON source,
arguments or `hs submit` stdin is refused whole with nothing written (PLAN § 2.2, § 2.3).

- [ ] `data/model.py`: `GROUP_MIN`/`GROUP_MAX`, `TaskGroupError`, `ensure_group`, `parse_group`; the
      check in `split_argline` (keep the text token for the message) and in `Task.new` for a tag group.
- [ ] `submit.py`: `scan_task_lines`, `validate_json_groups`, `read_in_full`; `source_fingerprint_and_count`
      takes its count from the scan; `SubmitApp`: `-g` `type=parse_group`, `check_tags` first and in both
      modes (`TaskGroupError` → `ArgumentError`), stream read in full with a database, the scan before
      any write; move `Zone.ensure` and the reserved-source writes after the checks.
- [ ] `cluster/__init__.py`: scan a seekable file before the `--no-db` return; `validate_json_groups`
      in `check_arguments`.
- [ ] `server.py`: `ServerApp.prepare_source` scanning `hs server FILE` (both modes), called in `run`
      before `serve_from`.
- **Verify:** `test_group_integer.py` and the suites around submit, JSON, restart and source gating;
  guards; `input_drive.sh group`.
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/submit.py`,
  `src/hypershell/cluster/__init__.py`, `src/hypershell/server.py`.

## Phase P4 — Streams refuse one line
**Satisfies:** R5 · **Depends on:** P3
**Goal:** a stream into `hs cluster`, `hs server` or `hs submit --queue` drops only the bad line with
an ERROR naming the value and line; nothing hangs (PLAN § 2.4).

- [ ] Move `is_file_input` to `submit.py` (re-export from `server.py`); a module `source_name()`
      shared by `SubmitThread`/`LiveSubmitThread` and the Loader.
- [ ] `Loader`: `self.line`, `self.refuse_lines`; `get_task` catches `TaskGroupError` per policy.
- **Verify:** `test_group_stream.py` and the stream, cluster-exit and queue suites; guards;
  `input_drive.sh stream`.
- **Touches:** `src/hypershell/submit.py`, `src/hypershell/server.py` (the import only).

## Phase P5 — The reached group and `hs submit`
**Satisfies:** R6 · **Depends on:** P3
**Goal:** `hs submit` with a database refuses a group below the reached group, all or nothing; a plain
submission takes the reached group (PLAN § 2.5).

- [ ] `Task.reached_group(in_flight=True)`: one statement, U computed once; the `typeof` filter on
      SQLite; the `CANCEL_STATUS` filter.
- [ ] `SubmitApp`: the C9 default; the R6 check after the scan and the gate decision, before any
      write; `ArgumentError` naming the lowest offending group, the reached group and the workarounds.
- **Verify:** `test_reached_group.py` and the group, submit, zone-on-PostgreSQL and signal suites;
  guards; `input_drive.sh reached`.
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/submit.py`.

## Phase P6 — `hs cluster FILE` and `hs server FILE` on an advanced database
**Satisfies:** R7 · **Depends on:** P5
**Goal:** a file that would insert a task below the reached group is refused before anything is
inserted or dispatched; skipped tasks never trigger it; streams are unchanged (PLAN § 2.3, § 2.5).

- [ ] `submit.py`: split `apply_source_gate` into `decide_source_gate` and `GateDecision.commit`
      (keep `apply_source_gate` as the wrapper); `hs submit` uses the split so its refusals write no
      Source row.
- [ ] `ClusterApp.prepare_source`/`prepare_json_source`: decide → R7
      (`Task.reached_group(in_flight=False)`, the skip set excluded; fingerprints computed only when
      needed) → commit; read `zone_id` after.
- [ ] `ServerApp.prepare_source`: R7 over every task line with a database.
- **Verify:** `test_resubmit_lower_group.py` and the source, restart, no-db, server and JSON suites;
  guards.
- **Touches:** `src/hypershell/submit.py`, `src/hypershell/cluster/__init__.py`,
  `src/hypershell/server.py`.

## Phase P7 — Docs and the invariant record
**Satisfies:** R9 · **Depends on:** P4, P6
**Goal:** every user-visible change is documented where users look, marked `.. versionchanged:: 2.9.0`,
and the constitution says the new rules.

- [ ] `docs/_include/submit_desc.rst`: a line beginning with `#` is a comment, a `#` elsewhere is part
      of the command, the marker is found anywhere (break it with `echo '#'' HYPERSHELL:'`); a
      non-integer group refuses the input; with a database stdin is read in full before anything is
      inserted; the reached-group refusal.
- [ ] `docs/cli/cluster.rst`, `docs/cli/server.rst`: beside the description includes, the same `#`
      rule, file versus stream for a bad group, and the reached-group refusal of a file.
- [ ] `docs/_include/task_groups_alt.rst`: a group is an integer (the forms), file versus stream, what
      the reached group is and when it counts (the current pass; under-estimates fall back to the
      late-group warning), and the `--restart`/`--update` notes (C4 drift, an old failed cut row).
- [ ] `AGENTS.md` (group gating, `submit.py` notes) and `.agents/factory/invariants.md` §3 in lockstep:
      a group must be an integer (R3), the reached group and its refusal (R6/R7), the Loader's stream
      policy.
- [ ] `spec/submit-input-checks/RELEASE-NOTE.md`: the squash-body draft for `hs-publish`, naming the
      four GOAL changes as behaviour changes plus the `--template` line split and the tag-only fix.
- **Verify:** help and completion tests; html and man builds with `-W`; the greps; no legacy `task` group command added.
- **Touches:** `docs/`, `AGENTS.md`, `.agents/factory/invariants.md`, `spec/submit-input-checks/`.

## Phase P8 — Whole suite and the red list re-proved
**Satisfies:** R1–R9 · **Depends on:** P7
**Goal:** the whole suite passes serially; the PostgreSQL-aware files pass with PostgreSQL; the red
list still fails on `dfdbaed5`; every drive passes.

- [ ] Run the whole suite with `HS_TEST_POSTGRES` unset (bounded 7200 s; this host's TimescaleDB and
      missing upgrade templates fail 30 environment-gated tests otherwise — see the dry run), then the
      PostgreSQL-aware files with it set, the red list, and the four drive modes. A failure here is
      fixed in the phase it belongs to, recorded as an amendment, and re-verified.
- **Verify:** as in the frontmatter.
- **Touches:** nothing new.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
