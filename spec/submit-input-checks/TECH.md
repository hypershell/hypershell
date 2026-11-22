---
slug: submit-input-checks
title: 'Submitted input is checked: # is part of the command, a group is an integer,
  and a group below the reached one is refused'
kind: fix
appetite: big
status: blocked
branch: fix/submit-input-checks
base: develop
current_phase: done
last_updated: '2026-11-22'
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
  status: done
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
  status: done
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
  status: done
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
  status: done
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
  status: done
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
  status: done
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
    -- docs src AGENTS.md .agents | grep -c ''^+.*hs task '')" -eq 0 && grep -q ''refused
    instead'' spec/submit-input-checks/RELEASE-NOTE.md && grep -q ''retry runs'' docs/_include/submit_desc.rst'
- id: P8
  name: Whole suite, serially; the PostgreSQL-aware files with PostgreSQL; the red
    list re-proved on dfdbaed5; the four drives
  status: done
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
  attempts: 1
  verify: test -n "$HS_TEST_POSTGRES" && env -u HS_TEST_POSTGRES python3 spec/submit-input-checks/drive/bounded.py
    7200 uv run pytest -q -p no:cacheprovider && python3 spec/submit-input-checks/drive/bounded.py
    2700 uv run pytest -q -p no:cacheprovider tests/test_command_hash.py tests/test_group_integer.py
    tests/test_group_stream.py tests/test_reached_group.py tests/test_resubmit_lower_group.py
    tests/test_submit.py tests/test_groups.py tests/test_zone_postgres.py tests/test_contended_write.py
    tests/test_server_initdb.py tests/test_writeback_postgres.py tests/test_data/test_model.py
    tests/test_group_order.py && RED_LIST=spec/submit-input-checks/drive/red.txt RED_BASE_REV=dfdbaed5
    python3 spec/submit-input-checks/drive/bounded.py 2700 sh spec/relay-hardening/drive/red_check.sh
    && RED_LIST=spec/submit-input-checks/drive/red-cycle1.txt RED_BASE_REV=44e177b4
    python3 spec/submit-input-checks/drive/bounded.py 900 sh spec/relay-hardening/drive/red_check.sh
    && for mode in hash group stream reached; do .agents/factory/bin/temp_site.sh
    uv run sh "$PWD/spec/submit-input-checks/drive/input_drive.sh" $mode || exit 1;
    done
review:
  last_reviewed_commit: 3dd066a4
  verdict: changes-requested
  blocked_reason: 'review cycle 2: G1 retry still crashes on a non-integer group held
    in stored tags (MEDIUM); human gate (data/model.py)'
  cycle: 2
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

- [x] `Task.split_argline`: comment rule on the stripped line, marker found anywhere as today, no
      other cut; `TaskGroupError` not wrapped (it is added in P3; keep the `except` ready for it).
- [x] `Loader.load_line_task` and `line_is_task`: split the raw line, then expand the template on the
      command; `Task.new(..., raw_args=command, parse_inline=False, tag={**running, **inline})`.
- [x] `Loader.__init__`: `self.tags = dict(tags or {})`.
- **Built:** the marker is the module constant `TAG_MARKER`; the shared split is
  `submit.py::split_task_line(line, template) -> (command, expanded, inline_tags)`, used by
  `line_is_task` and the Loader (and the P3 scan). A tag-only line no longer builds a throwaway
  `Task`. **Test fix (P1 slip):** `test_submit_stdin_keeps_hash` and the stdin step of
  `test_submit_default_group_is_reached_group` ran `hs submit` with no argument, which prints usage
  and exits 1; they now pass `-f -`. Both re-proved RED on `dfdbaed5` by assertion (3 of 3).
- **Verified (2026-11-21):** suites 105 passed (PostgreSQL on); guards 19 passed; `input_drive.sh
  hash` PASS 4 of 4.
- **Verify:** `test_command_hash.py`, the model test and the suites around the parser (both
  dialects); guards; `input_drive.sh hash`.
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/submit.py`.

## Phase P3 — The integer rule on read-in-full input
**Satisfies:** R3, R4 · **Depends on:** P2
**Goal:** a non-integer or out-of-range group is refused wherever it arrives; a file, JSON source,
arguments or `hs submit` stdin is refused whole with nothing written (PLAN § 2.2, § 2.3).

- [x] `data/model.py`: `GROUP_MIN`/`GROUP_MAX`, `TaskGroupError`, `ensure_group`, `parse_group`; the
      check in `split_argline` (keep the text token for the message) and in `Task.new` for a tag group.
- [x] `submit.py`: `scan_task_lines`, `validate_json_groups`, `read_in_full`; `source_fingerprint_and_count`
      takes its count from the scan; `SubmitApp`: `-g` `type=parse_group`, `check_tags` first and in both
      modes (`TaskGroupError` → `ArgumentError`), stream read in full with a database, the scan before
      any write; move `Zone.ensure` and the reserved-source writes after the checks.
- [x] `cluster/__init__.py`: scan a seekable file before the `--no-db` return; `validate_json_groups`
      in `check_arguments`.
- [x] `server.py`: `ServerApp.prepare_source` scanning `hs server FILE` (both modes), called in `run`
      before `serve_from`.
- **Built (for P5/P6):** `TaskGroupError(value, origin=None, *, out_of_range=False)`, `.at(origin)`;
  message `task group must be an integer, not "x"` (text quoted as written, a JSON value as JSON:
  `true`, `null`, `1.5`, `"7"`), or `... an integer from -2147483648 to 2147483647, not "…"` when out
  of range. `ensure_group(value, text=None)`, `parse_group(text)`, `tag_text(tokens, name)` in
  `model.py`. **Deviation:** argparse only turns `ValueError`/`TypeError`/`ArgumentTypeError` from a
  `type` into a usage error, so `-g` uses `submit.py::group_option`, which wraps `parse_group` into
  `ArgumentTypeError` (exit 2). `submit.py`: `InputScan(count, groups)` (`groups` is each task's
  group in order, `None` in queue mode for untagged lines), `scan_task_lines(lines, *, template,
  group, tags, name)`, `source_fingerprint(path)` (the md5 half; `source_fingerprint_and_count`
  stays as a wrapper over both), `read_in_full(stream)` (a `StreamReader` thread under
  `join_waking`), `validate_json_groups(records)`, `split_task_line`. `SubmitApp.run`:
  `check_arguments` → `check_tags` (both modes; queue mode validates but still does not apply tags)
  → `check_source` (JSON groups checked here, both modes) → queue: `scan_source` / database:
  `check_database` → `read_source` (stdin, `-`, FIFO, process substitution become a list;
  `self.source_name`) → default group → `scan_source` (`self.scan`) → `Zone.ensure` →
  `prepare_source` (a list goes under the reserved `<stdin>` source; a file's count is
  `self.scan.count`). Arguments are checked by `split_argline` with origin `arguments`.
  `ClusterApp.prepare_source` keeps the scan in a local `scan` before the `--no-db` return; P6's
  R7 check goes after the gate decision there. **Test fix:** four `temp_site` tests in
  `test_group_integer.py` that count rows after a refusal made before the database is opened now
  run `hs initdb --yes` first; re-proved RED on `dfdbaed5` (7 of 7).
- **Verified (2026-11-21):** suites 278 passed (PostgreSQL on); guards 19 passed; `input_drive.sh
  group` PASS 6 of 6.
- **Verify:** `test_group_integer.py` and the suites around submit, JSON, restart and source gating;
  guards; `input_drive.sh group`.
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/submit.py`,
  `src/hypershell/cluster/__init__.py`, `src/hypershell/server.py`.

## Phase P4 — Streams refuse one line
**Satisfies:** R5 · **Depends on:** P3
**Goal:** a stream into `hs cluster`, `hs server` or `hs submit --queue` drops only the bad line with
an ERROR naming the value and line; nothing hangs (PLAN § 2.4).

- [x] Move `is_file_input` to `submit.py` (re-export from `server.py`); a module `source_name()`
      shared by `SubmitThread`/`LiveSubmitThread` and the Loader.
- [x] `Loader`: `self.line`, `self.refuse_lines`; `get_task` catches `TaskGroupError` per policy.
- **Built:** `submit.py::source_name(source)` replaces both threads' `source_name` properties; the
  Loader keeps `self.name`. On whole input the Loader re-raises with the origin added
  (`{name}, line {n}`). `server.py` no longer imports `io`. `submit_from`'s docstring states the API
  policy (a list raises after earlier bundles committed; a stream refuses the line).
- **Verified (2026-11-21):** suites 192 passed (PostgreSQL on); guards 19 passed; `input_drive.sh
  stream` PASS 6 of 6; also `test_server.py` and `test_main.py` 93 passed. The red list on this tree:
  46 of 56 pass; the 10 left are the R6/R7 tests for P5 and P6.
- **Verify:** `test_group_stream.py` and the stream, cluster-exit and queue suites; guards;
  `input_drive.sh stream`.
- **Touches:** `src/hypershell/submit.py`, `src/hypershell/server.py` (the import only).

## Phase P5 — The reached group and `hs submit`
**Satisfies:** R6 · **Depends on:** P3
**Goal:** `hs submit` with a database refuses a group below the reached group, all or nothing; a plain
submission takes the reached group (PLAN § 2.5).

- [x] `Task.reached_group(in_flight=True)`: one statement, U computed once; the `typeof` filter on
      SQLite; the `CANCEL_STATUS` filter.
- [x] `SubmitApp`: the C9 default; the R6 check after the scan and the gate decision, before any
      write; `ArgumentError` naming the lowest offending group, the reached group and the workarounds.
- **Built:** `Task.reached_group` is one `SELECT max("group")` joined to a one-row CTE `pass_start`
  (U = `min(submit_time)` over incomplete rows), so PostgreSQL scans for U once. With
  `in_flight=False` the scheduled branch counts only completed rows (`completion_time IS NOT NULL
  AND (schedule_time >= U OR completion_time >= U)`), per the P1 amendment; the retry-row branch is
  unchanged. The `typeof` filter is chosen from `Session.get_bind().dialect.name`.
  `submit.py::refuse_below_reached_group(groups, reached)` raises the `ArgumentError` (`Task group
  {lowest} is below the reached group {R} of the database (nothing submitted); submit at group {R}
  or higher, wait for the run to finish, or use a fresh database`); a `None` group (queue mode) is
  not checked. `SubmitApp.reached_group` (a cached property, read once), `default_group()` (C9;
  logs `reached group; most recently scheduled group G is below it` or `... is not an integer`),
  `task_groups()` (the scan's groups; for arguments the inline group, else `-t`, else the default;
  for `--from-json` each record's `group`, else `-t`, else the default). The check runs after the
  scan and before `Zone.ensure`/`prepare_source`. The gate is not split yet (P6), so in P5 an
  `--update` skip set is not yet excluded; P6 moves the check after the gate decision.
- **Verified (2026-11-21):** suites 154 passed, 1 skipped (the SQLite-only text-group test on
  PostgreSQL; PostgreSQL on); guards 19 passed; `input_drive.sh reached` PASS 5 of 5.
- **Verify:** `test_reached_group.py` and the group, submit, zone-on-PostgreSQL and signal suites;
  guards; `input_drive.sh reached`.
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/submit.py`.

## Phase P6 — `hs cluster FILE` and `hs server FILE` on an advanced database
**Satisfies:** R7 · **Depends on:** P5
**Goal:** a file that would insert a task below the reached group is refused before anything is
inserted or dispatched; skipped tasks never trigger it; streams are unchanged (PLAN § 2.3, § 2.5).

- [x] `submit.py`: split `apply_source_gate` into `decide_source_gate` and `GateDecision.commit`
      (keep `apply_source_gate` as the wrapper); `hs submit` uses the split so its refusals write no
      Source row.
- [x] `ClusterApp.prepare_source`/`prepare_json_source`: decide → R7
      (`Task.reached_group(in_flight=False)`, the skip set excluded; fingerprints computed only when
      needed) → commit; read `zone_id` after.
- [x] `ServerApp.prepare_source`: R7 over every task line with a database.
- **Built:** `GateDecision(path, fingerprint, count, source_id=None, skip=None)`; `commit()` records
  a new Source row unless a prior one is reused (same-file `--restart`) and returns `(source_id,
  skip)`. `decide_source_gate` keeps every log line and refusal of the old gate. The Loader's line
  replay is now `task_lines(lines, *, template, tags, name)` (yields command, expanded command and
  tags per task); `scan_task_lines` and the new `task_fingerprints` use it, and
  `json_fingerprints`/`json_record_tags` (shared with `Loader.load_json_task`) do the same for
  records. `refuse_below_reached_group(groups, reached, *, skip, fingerprints)` calls
  `fingerprints()` only when a task below R could be skipped. `SubmitApp`: `decide_source()` (the
  gate decision for a named file or `--from-json` file) → `check_reached_group()` (the P5 check,
  now with the skip set) → `Zone.ensure` → `prepare_source()` (commit). `ClusterApp` checks only a
  named file (a stream and `--from-json -` are not refused); the default group of an untagged line
  there is `DEFAULT_TASK_GROUP`, as the server's Loader. `ServerApp` checks only with a database.
  **Extra drive (temp site):** `hs submit --update` of an advanced file adding group 6 (the group 0
  line skipped) exit 0; adding group 1 exit 2, Source rows unchanged; `hs cluster --restart
  --update` adding groups 6 and 7 ran `c d f`.
- **Verified (2026-11-21):** suites 183 passed, 1 skipped (PostgreSQL on); guards 19 passed.
- **Amendment (P8, for P6):** the first P8 run failed 6 cases of
  `tests/test_server_initdb.py::test_database_step[*-file-none|initdb]` (exit 5, `no such table:
  task`): that unit test replaces `checkdb`/`upgradedb` with recorders, so the schema never exists,
  and `ServerApp.prepare_source` now reads the reached group for `hs server FILE` with a database.
  The `recorder` fixture also stubs `Task.reached_group` to return None (no reached group); the
  steps it records are unchanged. In a real launch `check_database` creates or checks the schema
  before `prepare_source`. P6's verify list did not include that file; P8's does.
- **Verify:** `test_resubmit_lower_group.py` and the source, restart, no-db, server and JSON suites;
  guards.
- **Touches:** `src/hypershell/submit.py`, `src/hypershell/cluster/__init__.py`,
  `src/hypershell/server.py`.

## Phase P7 — Docs and the invariant record
**Satisfies:** R9 · **Depends on:** P4, P6
**Goal:** every user-visible change is documented where users look, marked `.. versionchanged:: 2.9.0`,
and the constitution says the new rules.

- [x] `docs/_include/submit_desc.rst`: a line beginning with `#` is a comment, a `#` elsewhere is part
      of the command, the marker is found anywhere (break it with `echo '#'' HYPERSHELL:'`); a
      non-integer group refuses the input; with a database stdin is read in full before anything is
      inserted; the reached-group refusal.
- [x] `docs/cli/cluster.rst`, `docs/cli/server.rst`: beside the description includes, the same `#`
      rule, file versus stream for a bad group, and the reached-group refusal of a file.
- [x] `docs/_include/task_groups_alt.rst`: a group is an integer (the forms), file versus stream, what
      the reached group is and when it counts (the current pass; under-estimates fall back to the
      late-group warning), and the `--restart`/`--update` notes (C4 drift, an old failed cut row).
- [x] `AGENTS.md` (group gating, `submit.py` notes) and `.agents/factory/invariants.md` §3 in lockstep:
      a group must be an integer (R3), the reached group and its refusal (R6/R7), the Loader's stream
      policy.
- [x] `spec/submit-input-checks/RELEASE-NOTE.md`: the squash-body draft for `hs-publish`, naming the
      four GOAL changes as behaviour changes plus the `--template` line split and the tag-only fix.
- **Built:** `submit_desc.rst` gains the `#` rule (with `--template`'s split) and the integer and
  reached-group rules (stdin read in full and held in memory, the C9 default, `--update`'s skipped
  tasks), each with its own `versionchanged`. `task_groups_alt.rst` gains the integer rule (forms,
  range, file versus stream, exit statuses), the reached group (definition, `hs submit` refusal and
  default, `hs cluster`/`hs server` refusal with in-flight and skipped tasks left out, streams
  unchanged), its under- and over-estimates (PLAN § 5 risk 4) and the C4/D2 `--restart`/`--update`
  note with the old failed cut row, plus two `versionchanged` markers. `cluster.rst` and `server.rst`
  each get one `versionchanged` beside the description include. `AGENTS.md` (group-gating paragraph;
  the `submit.py` footgun now says `-t` is checked in queue mode and lines split before the
  template) and `invariants.md` § 3 (two bullets: the integer rule, the reached group and the gate
  split) say the same rules. `RELEASE-NOTE.md` holds the squash-body draft.
- **Verified (2026-11-21):** help and completion tests 66 passed; html and man builds with `-W`
  clean; every grep holds; no legacy `task` group command added.
- **Verify:** help and completion tests; html and man builds with `-W`; the greps; no legacy `task` group command added.
- **Touches:** `docs/`, `AGENTS.md`, `.agents/factory/invariants.md`, `spec/submit-input-checks/`.

## Phase P8 — Whole suite and the red list re-proved
**Satisfies:** R1–R9 · **Depends on:** P7
**Goal:** the whole suite passes serially; the PostgreSQL-aware files pass with PostgreSQL; the red
list still fails on `dfdbaed5`; every drive passes.

- [x] Run the whole suite with `HS_TEST_POSTGRES` unset (bounded 7200 s; this host's TimescaleDB and
      missing upgrade templates fail 30 environment-gated tests otherwise — see the dry run), then the
      PostgreSQL-aware files with it set, the red list, and the four drive modes. A failure here is
      fixed in the phase it belongs to, recorded as an amendment, and re-verified.
- **Attempt 1 (red):** the whole suite failed 6 cases of `test_server_initdb.py::test_database_step`
  (`*-file-none`, `*-file-initdb`): a P6 test gap, fixed in the unit recorder (see P6's amendment).
- **Verified (2026-11-21, attempt 2):** whole suite with `HS_TEST_POSTGRES` unset **3723 passed, 186
  skipped in 26 min 8 s**; the PostgreSQL-aware files **364 passed, 6 skipped** (4 min 27 s); red
  check **56 of 56 RED** on `dfdbaed5` (11 `pg:`); drives `hash` 4/4, `group` 6/6, `stream` 6/6,
  `reached` 5/5. The red list on this tree, PostgreSQL on: **56 passed**.
- **Verify:** as in the frontmatter.
- **Touches:** `tests/test_server_initdb.py` (the recorder stub, P6's amendment).

## Review cycle 1 — remediation (2026-11-22)

[`REVIEW.md`](REVIEW.md) cycle 1 asked for changes (F1 HIGH, F2 MEDIUM, both in `data/model.py`);
the maintainer signed the fixes off ([`PLAN.md`](PLAN.md) § *Review-cycle-1 rulings*). `hs-build`
reopened P5 (F1), P3 (F2), P7 (docs and the release note) and P8 (the whole suite), tightened the
P7 and P8 gates with `set_phase.py --verify`, and closed each again after its gate held. In
`data/model.py` only `Task.reached_group` and the retry construction changed.

- [x] **F1 (P5).** `Task.reached_group` takes the pass start as an uncorrelated scalar subquery
      (`select(min(submit_time)) … .scalar_subquery()`) instead of a CTE joined `ON true`; every
      predicate is kept (the `CANCEL_STATUS` filter, in flight against completed, the retry-row
      branch, the SQLite `typeof` filter), and the unused `true` import is dropped. SQLite's plan
      goes from `CO-ROUTINE pass_start` searched inside `SCAN task` to three `SCALAR SUBQUERY` steps,
      each run once. New test `test_reached_group.py::test_reached_group_at_scale` (SQLite, 20 000 rows:
      a finished group 0 and a queued group 1) reads the reached group in a subprocess bounded at 10 s
      and then runs `hs submit -g 0` under the same bound; it asserts `0 0` and exit 0.
- **Verified, P5 (2026-11-22):** suites 155 passed, 1 skipped (PostgreSQL on); guards 19 passed;
  `input_drive.sh reached` PASS 5 of 5. `test_reached_group_at_scale` fails by its 10 s bound on
  `44e177b4` (the reached-group read) and passes here in 14 s, nearly all of it the 20 000-line submit.
- [x] **F2 (P3).** The retry in `__schedule_next_failed_tasks` passes `parse_inline=False` and
      `strict_tag=False`: the retry's command is the failed task's stored command, its group the task's
      group and its tags the task's tags, none read again. New tests in `test_group_integer.py`:
      `test_cluster_json_retry_keeps_command_and_group[text|integer]` (a `--from-json` record
      `false # HYPERSHELL: group:x` or `…group:3` with `--max-retries 1`: exit 0, and the retry row
      keeps the command and group 0) and `test_cluster_json_retry_keeps_tags` (a record tag
      `input: data/a b.txt`, valid only under the relaxed JSON check, is copied to the retry).
      **Base behaviour checked:** for a valid marker in `args`, 44e177b4 and `dfdbaed5` both cut the
      retry's command at the marker and moved it to the marker's group (`false`, group 3); the retry
      now runs the first attempt's command in its group, as the first attempt did, since a JSON
      record's `args` are never read for tags. The strict tag re-check on a retry is an older bug of
      the same construction (the run exits 5 when it retries such a task, on `dfdbaed5` too); the
      ruling's "copies the failed task's tags" covers it.
- **Verified, P3 (2026-11-22):** suites 281 passed (PostgreSQL on); guards 19 passed;
  `input_drive.sh group` PASS 6 of 6. The three retry tests fail on `44e177b4` by assertion (exit 5
  on the text group and on the relaxed tag; the command cut to `false` on the integer group).
- [x] **Retry window (P7).** `task_groups_alt.rst` already says it twice: "a failure counts as
      finished unless its retry has been created" and, among the under-estimates, "while a failure
      waits for its retry to be created". No change.
- [x] **Docs and release note (P7).** `submit_desc.rst`'s `--from-json` paragraph says `args` is taken
      as written and a retry runs that command in the task's group with its tags, with its own
      `versionchanged:: 2.9.0`. `RELEASE-NOTE.md` names D2 (an old file refused on `--restart` or
      `--update` when a re-identified line is below the reached group; an old failed cut task) and
      the retry fix.
- **Verified, P7 (2026-11-22):** help and completion tests 66 passed; html and man builds with `-W`
  clean; every grep holds, the two new ones included.
- [x] **Red list for this cycle.** [`drive/red-cycle1.txt`](drive/red-cycle1.txt) (4 ids) is checked
      against `44e177b4` by the landed `red_check.sh`; P8's gate runs it after the `dfdbaed5` list.
- **Verified, P8 (2026-11-22, the gate as tightened):** whole suite with `HS_TEST_POSTGRES` unset
  **3727 passed, 186 skipped in 25 min 52 s**; the PostgreSQL-aware files (now with `test_model` and
  `test_group_order`) **391 passed, 6 skipped** (5 min 33 s); red check **56 of 56 RED** on `dfdbaed5`
  and **4 of 4 RED** on `44e177b4`; drives `hash` 4/4, `group` 6/6, `stream` 6/6, `reached` 5/5.
- **Query plans and timings (in-process, SQLite, unscheduled rows).** Before (`44e177b4`):
  `CO-ROUTINE pass_start`, `SCAN task`, `SEARCH task USING INDEX index_tasks_unscheduled`, `SEARCH
  pass_start`; 1.15 s at 5 000 rows, 7.8 s at 10 000. After: `SEARCH task USING INDEX
  index_tasks_unscheduled` and three `SCALAR SUBQUERY` steps, each a `SEARCH task` run once; 3 ms at
  5 000 rows, 7 ms at 20 000 (12 ms with 20 000 finished rows). PostgreSQL now plans three InitPlans
  (U computed once by each) where it had a materialized one-row join; at 100 000 rows 13 ms after
  against 16 ms before, and at 1 000 000 rows 95–120 ms against 123–137 ms (both runs alongside
  test load).

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
