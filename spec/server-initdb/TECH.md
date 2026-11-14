---
slug: server-initdb
title: hs server --initdb initializes the database
kind: fix
appetite: small
status: in_review
branch: fix/server-initdb
base: develop
current_phase: done
last_updated: '2026-11-14'
phases:
- id: P1
  name: Server start-up honours --initdb and --no-db; unit matrix, SQLite drive, docs
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'uv run pytest -q -m unit tests/test_server_initdb.py tests/test_server.py
    tests/test_key_option.py && env -u HS_TEST_POSTGRES uv run pytest -q -rs tests/test_server_initdb.py
    && git diff --quiet 6c46cbd -- src/hypershell/data src/hypershell/submit.py src/hypershell/cluster
    src/hypershell/task.py src/hypershell/relay share && ! grep -q "or launching with"
    docs/_include/database.rst && ! grep -q "does not initialize the database, even
    with" docs/_include/database.rst && grep -A3 "versionchanged:: 2.9.0" docs/cli/server.rst
    | grep -q -e "--initdb" && UV_PROJECT_ENVIRONMENT="$TMPDIR/docs-venv" uv run --python
    3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs "$TMPDIR/docs-html"'
- id: P2
  name: Real-CLI regression on PostgreSQL and TimescaleDB; CI runs it
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R7
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: l="$TMPDIR/hs-server-initdb-p2.log"; HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
    HS_TEST_TIMESCALE=community uv run --extra postgres pytest -v -rs tests/test_server_initdb.py
    tests/test_timescale.py > "$l" 2>&1; r=$?; tail -n 8 "$l"; test $r -eq 0 && !
    grep -q "SKIPPED.*test_server_initdb" "$l" && grep -q "passed" "$l" && grep -q
    "tests/test_server_initdb.py" .github/workflows/tests.yml
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — `hs server --initdb` initializes the database

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/server-initdb/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its `verify:`
command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done`
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`).
- `depends_on`: phase ids that must be `done` first.
- `parallel`: both `false` — P1 edits `server.py` (coupled core); P2 shares its test module.
- `hammerable`: both `false` — correctness of a start-up database path.
- `hill`: `uphill` → `crest` → `downhill`.
- `attempts`: durable failed-verify counter, bumped by `set_phase.py --phase P<n> --record-attempt`.
- `verify`: the exact command that proves the phase.
- `review.cycle`: completed review passes.

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; the footgun checklist is
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) (§4, §10, §12 here).
- One phase per `hs-build` invocation; one atomic commit holding the code and the `TECH.md` state
  change, subject `[fix] Build server-initdb P<n>: …`. **No `Co-Authored-By` trailer.**
- Docs change in the **same commit** as the behaviour (P1). No option is added, so `share/`
  completions and `*_usage.rst` stay as they are; man pages are `hs-release`'s.
- Keys only through `HYPERSHELL_SERVER_AUTH` (never `-k`). Every process a test starts is stopped
  on every exit path. PostgreSQL drives use fresh databases (`ts_database`), never a shared one.
- P2's verify, and P1's docs build, need the sandbox off (local PostgreSQL socket; the docs venv
  under `$TMPDIR` so `.venv` is not replaced).

---

## Phase P1 — Server start-up honours `--initdb` and `--no-db`
**Satisfies:** R1–R7 (unit level; R6 whole) · **Depends on:** —
**Goal:** `hs server` initializes the database with `--initdb`, leaves it alone with `--no-db`, and
behaves as today otherwise, proven per scheduling mode without a database server, with the docs
corrected in the same commit.

- [x] Write `tests/test_server_initdb.py` (module docstring naming the `HS_TEST_POSTGRES` /
      `HS_TEST_TIMESCALE` conventions, as `tests/test_timescale.py` does) with the unit matrix of
      PLAN §2.3: in-process `ServerApp.main(argv)`; `monkeypatch` recorders for
      `hypershell.data.checkdb`, `.upgradedb`, `hypershell.data.timescale.prepare`/`.converge`,
      `hypershell.server.serve_from`/`.serve_forever`,
      `hypershell.cluster.topology.serve_with_sites`, and `hypershell.server.resolve_auth` (a
      generated valid key); `hypershell.data.DATABASE_DIALECT`/`TIMESCALE` per provider. Parametrize
      provider × mode (FILE in `tmp_path`, `-`, `--forever`, `--restart`, `--autoscaling fixed`) ×
      flag (none, `--initdb`, `--no-db`) and assert the exact sequence in PLAN §2.3's table
      (`--autoscaling fixed --no-db` → exit 2, nothing recorded). If the import-time configuration
      has no SQLite file, give the SQLite rows one through `monkeypatch` so `ensuredb`'s
      missing-configuration refusal does not fire. All `@mark.unit`.
- [x] Add the refusal unit case: `prepare` raising `TimescaleError('…')` under `timescale` with
      `--initdb` → return `exit_status.runtime_error`, message logged, neither `upgradedb` nor
      `serve` recorded.
- [x] Add the SQLite real-CLI case (`@mark.integration`, `temp_site`): a two-line task file,
      `hs server --no-db -p PORT FILE` and `hs client -p PORT -T 10` as subprocesses with
      `HYPERSHELL_SERVER_AUTH` set; both exit 0, the client's output holds both tasks' output, and
      the configured database file does not exist. Write the launch helper (free port via
      `find_available_ports` or a bound socket; wait until the port accepts, bounded; client;
      terminate/kill the server in `finally`) so P2 reuses it.
- [x] **Red check:** run the new module before touching `server.py`; record in the commit body
      which rows fail (expected: every `--initdb` row under postgres/timescale, every `--no-db` row,
      the refusal case, the SQLite drive).
- [x] `src/hypershell/server.py`: replace the bare `ensuredb()` in `ServerApp.run` with
      `self.check_database()`; add `ServerApp.check_database()` per PLAN §2.1 (skip under
      `in_memory`, else `ensuredb(auto_init=self.auto_initdb)`). Nothing else in the file changes.
- [x] Docs per PLAN §2.4: `docs/_include/server_help.rst` (`--initdb`, `--no-db`);
      `docs/_include/database.rst` (SQLite note, Initialization, TimescaleDB section's three
      passages); `docs/_include/database_alt.rst`; `docs/cli/server.rst` third
      `.. versionchanged:: 2.9.0` with one clause. Read each against the behaviour.
- **Verify:** see frontmatter (unit tests, the SQLite drive with `HS_TEST_POSTGRES` unset, the
  non-goal diff guard against `6c46cbd`, the stale-sentence greps and marker grep, docs build `-W`).
- **Touches:** `src/hypershell/server.py`, `tests/test_server_initdb.py`, `docs/_include/server_help.rst`,
  `docs/_include/database.rst`, `docs/_include/database_alt.rst`, `docs/cli/server.rst`.

## Phase P2 — Real-CLI regression on PostgreSQL and TimescaleDB; CI runs it
**Satisfies:** R1, R2, R3, R4, R5, R7 · **Depends on:** P1
**Goal:** the regression R7 names: the real `hs server` against fresh PostgreSQL and TimescaleDB
databases, skipped without `HS_TEST_POSTGRES`, and run by CI.

- [x] In `tests/test_server_initdb.py`, import by name from `tests.test_timescale` the fixtures
      `admin`, `ts_database`, `ts_site` and the helpers it needs (`needs_server`, `with_timescale`,
      `assert_converged`, `shape`, `pass_lines`, `task_rows`, `is_hypertable`, `database_engine`);
      never `import *` (it would collect that module's tests twice). Set `HYPERSHELL_SERVER_AUTH`
      in the environment the fixtures prepare.
- [x] `postgres` provider (`@mark.integration @needs_server`): fresh `--initdb FILE` (tables
      `client source task zone`, tasks completed); fresh `--initdb --forever` (port accepts,
      `hs submit` a task, client completes it, server stopped); re-launch `--initdb FILE` on the
      first database (earlier rows unchanged, new tasks completed); fresh, no flag (exit 5,
      `DatabaseUninitialized` on stderr, no table); fresh `--no-db FILE` (exit 0, no table).
- [x] `timescale` provider (`+ @with_timescale`): fresh `--initdb FILE` (`assert_converged`, tasks
      completed); re-launch (`shape()` unchanged, no `hypershell.data.timescale` INFO line);
      `--no-db FILE` on a `template0` database (no table, no extension); refusal: a non-superuser
      LOGIN role (created and dropped by the test) on a `template0` database it does not own →
      server exit 5, the `TimescaleError` message text equal to `hs initdb --yes`'s on the same
      database (compare text after the logger name), no table, no `Server listening` line.
- [x] **Red check:** `git checkout 6c46cbd -- src/hypershell/server.py`, run the database cases,
      record the failures (every `--initdb` and `--no-db` case) in the commit body, then
      `git checkout HEAD -- src/hypershell/server.py` and confirm the tree is clean.
- [x] `.github/workflows/tests.yml`: add `tests/test_server_initdb.py` to the PostgreSQL job's
      pytest line and to its comment (the `postgres`-provider cases also run on the plain entries).
- **Verify:** see frontmatter (sandbox off; the new module's cases all ran, none skipped;
  `tests/test_timescale.py` still green; CI line present).
- **Host note (build, 2026-11-14):** `test_timescale.py::test_legacy_database_upgraded_not_converted`
  needs the `hs283` template that `tests/fixtures/make-legacy-postgres.sh` builds through Docker,
  which the build host lacks; it fails in its `ts_database` fixture ("Template hs283 not listed in
  HS_TEST_POSTGRES_TEMPLATES") before any HyperShell code runs, and the file is unchanged since
  `6c46cbd`. The gate was run with that one test deselected; CI's PostgreSQL job builds the
  templates and runs it.
- **Touches:** `tests/test_server_initdb.py`, `.github/workflows/tests.yml`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file if reality diverges (state via `set_phase.py`; note it in the commit body). STOP
   and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
