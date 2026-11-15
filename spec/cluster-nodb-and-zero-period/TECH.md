---
slug: cluster-nodb-and-zero-period
title: hs cluster --no-db leaves the database alone, and an autoscaling period of
  zero or less is refused
kind: fix
appetite: small
status: in_progress
branch: fix/cluster-nodb-and-zero-period
base: develop
current_phase: P1
last_updated: '2026-11-15'
phases:
- id: P1
  name: Regression tests first, red on 90ab2fc2
  status: pending
  satisfies:
  - R8
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: l="$TMPDIR/cnz-p1-red.log"; uv run pytest -q -p no:cacheprovider -rf tests/test_cluster_no_db.py
    tests/test_period_floor.py -k "not kept and not accepted" > "$l" 2>&1; r=$?; tail
    -n 70 "$l"; s="$(tail -n 1 "$l")"; test $r -eq 1 && echo "$s" | grep -qE "^[0-9]+
    failed" && ! echo "$s" | grep -qE "passed|error" && uv run pytest -q -p no:cacheprovider
    tests/test_cluster_no_db.py tests/test_period_floor.py -k "kept or accepted" &&
    uv run pytest -q -p no:cacheprovider tests/test_duration_options.py
- id: P2
  name: hs cluster --no-db makes no database call
  status: pending
  satisfies:
  - R1
  - R2
  - R7
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'uv run pytest -q -p no:cacheprovider tests/test_cluster_no_db.py tests/test_server_initdb.py
    tests/test_cluster.py && .agents/factory/bin/temp_site.sh sh -c ''echo "echo a"
    > in.txt && echo "echo b" >> in.txt && uv run hs cluster in.txt --no-db -N1 --no-tls
    > out.txt && grep -qx a out.txt && grep -qx b out.txt && test ! -e "$HYPERSHELL_DATABASE_FILE"
    && HYPERSHELL_DATABASE_FILE="$HYPERSHELL_SITE/missing/task.db" uv run hs cluster
    in.txt --no-db -N1 --no-tls > out.txt && grep -qx b out.txt && test ! -e "$HYPERSHELL_SITE/missing"
    && uv run hs cluster in.txt -N1 --no-tls && test -s "$HYPERSHELL_DATABASE_FILE"''
    && git diff --quiet 90ab2fc2 -- src/hypershell/data src/hypershell/cluster/remote.py
    && grep -q "not opened at all" docs/_include/cluster_help.rst && grep -A3 "versionchanged::
    2.9.0" docs/cli/cluster.rst | grep -q -e "--no-db" && UV_PROJECT_ENVIRONMENT="$TMPDIR/docs-venv"
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    "$TMPDIR/docs-html-cnz"'
- id: P3
  name: A period of zero or less is refused on hs cluster, hs server and hs relay
  status: pending
  satisfies:
  - R3
  - R4
  - R5
  - R6
  - R7
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'uv run pytest -q -p no:cacheprovider tests/test_period_floor.py tests/test_duration_options.py
    tests/test_relay_topology.py tests/test_topology_server.py tests/test_autoscale_failed_launches.py
    && .agents/factory/bin/temp_site.sh sh -c ''echo "echo a" > in.txt && { uv run
    hs cluster in.txt --no-db --no-tls -P 0 > out.txt 2> err.txt; test $? -eq 2; }
    && ! grep -q a out.txt && grep -q -e "-P/--period" err.txt && grep -q "greater
    than zero" err.txt && { HYPERSHELL_AUTOSCALE_PERIOD=-5 uv run hs cluster in.txt
    --no-db --no-tls 2> err.txt; test $? -eq 2; } && grep -q "autoscale.period" err.txt
    && grep -q "greater than zero" err.txt && uv run hs cluster in.txt --no-db --no-tls
    -P 0.5 > out.txt && grep -qx a out.txt && HYPERSHELL_AUTOSCALE_PERIOD=0.5 uv run
    hs cluster in.txt --no-db --no-tls > out.txt && grep -qx a out.txt'' && uv run
    hs cluster --help | grep -q "Scaling period, greater than zero" && uv run hs server
    --help | grep -q "Scaling period, greater than zero" && test "$(grep -c "Scaling
    period, greater than zero" share/zsh/site-functions/_hs)" -eq 2 && grep -q "greater
    than zero" docs/_include/cluster_help.rst && grep -q "greater than zero" docs/_include/server_help.rst
    && grep -q "greater than zero" docs/_include/config_param_ref.rst && grep -A3
    "versionchanged:: 2.9.0" docs/cli/server.rst | grep -q "greater than zero" &&
    grep -A3 "versionchanged:: 2.9.0" docs/cli/cluster.rst | grep -q "greater than
    zero" && git diff --quiet 90ab2fc2 -- src/hypershell/cluster/remote.py src/hypershell/cluster/topology.py
    src/hypershell/relay/site.py src/hypershell/data && UV_PROJECT_ENVIRONMENT="$TMPDIR/docs-venv"
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    "$TMPDIR/docs-html-cnz"'
- id: P4
  name: Whole suite and docs
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
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: l="$TMPDIR/cnz-p4.log"; uv run pytest -q -p no:cacheprovider > "$l" 2>&1;
    r=$?; tail -n 15 "$l"; test $r -eq 0 && UV_PROJECT_ENVIRONMENT="$TMPDIR/docs-venv"
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    "$TMPDIR/docs-html-cnz"
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — `hs cluster --no-db` leaves the database alone, and an autoscaling period of zero or less is refused

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/cluster-nodb-and-zero-period/TECH.md`); the
per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes
one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + [01](research/01-start-up-seams.md).

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done`
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`).
- `depends_on`: phase ids that must be `done` first.
- `parallel`: `true` only for genuinely independent, non-coupled work.
- `hammerable`: `false` marks a correctness phase that scope-hammering must **never** cut.
- `hill`: `uphill` → `crest` → `downhill`.
- `attempts`: durable failed-verify counter, bumped by `set_phase.py --phase P<n> --record-attempt`.
- `verify`: the exact command that proves the phase. Run it from the worktree root with
  `UV_CACHE_DIR` under `$TMPDIR` and the sandbox off (the docs build and the CLI drives need it).

## Conventions (apply to every phase)

- `AGENTS.md` is the constitution; [`invariants.md`](../../.agents/factory/invariants.md) §4, §10,
  §11 and §12 are the sections this change touches (PLAN §3).
- One phase per `hs-build` invocation; one atomic commit with the code and the `TECH.md` state
  change, subject `[fix] Build cluster-nodb-and-zero-period P<n>: …`. **No `Co-Authored-By`
  trailer.** Signed commits; no push.
- A CLI change updates `docs/_include/*.rst` and `share/` completions in the same commit; prose
  markers are `.. versionchanged:: 2.9.0`, beside the `.. include::` for generated help snippets.
- Docs and tests name only top-level commands; a server key travels as `HYPERSHELL_SERVER_AUTH`,
  never `-k`.
- `cluster/remote.py`, `cluster/topology.py`, `relay/site.py` and `data/` stay unchanged since
  `90ab2fc2` (PLAN §2, "Not changed").

---

## Phase P1 — Regression tests first, red on `90ab2fc2`
**Satisfies:** R8 · **Depends on:** —
**Goal:** every R1–R6 behaviour has a test; the defect tests fail on the base tree and none hangs;
the guards pass. No source change.

- [ ] `tests/test_cluster_no_db.py` (PLAN §6): the unit `test_no_db_makes_no_database_call[mode]`
      (SQLite dialect; local, `--launcher env`, `--ssh localhost`) and the guard matrix
      `test_database_step_kept[dialect-mode-flag]` (every other row), both through
      `ClusterApp.from_cmdline` + `__enter__`/`__exit__` with `hypershell.cluster.initdb`/`checkdb`
      recorded and `hypershell.cluster.DATABASE_DIALECT` monkeypatched; the integration drives
      `test_no_db_creates_no_file`, `test_no_db_leaves_file_unchanged` (pre-existing non-database
      bytes), `test_no_db_runs_with_unopenable_database` (missing directory),
      `test_no_db_runs_with_launcher` (`--launcher env -H 127.0.0.1 --remote-exe` the venv's `hs`),
      and the guard `test_without_no_db_initializes_kept` (four tables).
- [ ] `tests/test_period_floor.py` (PLAN §6): `test_period_option_zero_refused` (unit, both apps,
      `-P v`/`--period=v`, six zero forms); `test_period_key_refused` (integration, fresh
      interpreter with `serve_from`/`serve_forever`/`serve_with_sites`/`run_local`/`run_cluster`/
      `run_ssh` recorded; `hs server FILE --no-db`, `hs server --autoscaling fixed`, `hs cluster
      FILE --no-db`; environment `0`, `-5`, `0s`; file `period = 0`, `-5`, `"0s"`, `0.0`;
      `HYPERSHELL_SERVER_AUTH` in the environment); `test_period_cli_zero_refused` (real
      `hs cluster FILE --no-db --no-tls -P 0`); `test_relay_period_zero_refused` (unit, `0`, `0.0`,
      `-5`, `'0'`, `'0s'`, `'0:00'`: `check_args()` raises, and `RelayApp.main([])` with the
      start-up helpers and `RelayThread` stubbed returns `bad_argument` with nothing built); guards
      `test_period_option_accepted`, `test_period_key_accepted` (key `0.5` → `0.5` at the recorded
      `period=`), `test_relay_period_accepted`.
- [ ] Every new test tagged `@mark.unit` or `@mark.integration`; no test may start a server or
      relay that a zero period would leave running (PLAN §5). Assert on "greater than zero", the
      option or key, and the value; not on the full sentence.
- [ ] `tests/test_duration_options.py::test_seconds_option_parsed`: keep `('0', 0)` for every option
      but `-P` on `ServerApp`/`ClusterApp` (now refused; covered above).
- [ ] Record the red list (the failing node ids from the verify log) in the commit body.
- **Expected red on `90ab2fc2`** (`-k "not kept and not accepted"`; all fail, none passes, no
  error): `test_no_db_makes_no_database_call` ×3 (records `initdb`);
  `test_no_db_creates_no_file` (file created); `test_no_db_leaves_file_unchanged` (exit 5, not a
  database); `test_no_db_runs_with_unopenable_database` (exit 5, `unable to open database file`);
  `test_no_db_runs_with_launcher` (file created); `test_period_option_zero_refused` ×24 (parsed as
  0); `test_period_key_refused` ×21 (exit 0, a serve recorded); `test_period_cli_zero_refused`
  (exit 0, tasks run); `test_relay_period_zero_refused` ×6 (no error). About 59 in all; the exact
  ids go in the commit body.
- **Expected green on `90ab2fc2`:** every `kept`/`accepted` test and all of
  `tests/test_duration_options.py`.
- **Verify:** see frontmatter (red selection exits 1 with only failures, then the guards and
  `test_duration_options.py` pass).
- **Touches:** `tests/test_cluster_no_db.py`, `tests/test_period_floor.py`,
  `tests/test_duration_options.py`.

## Phase P2 — `hs cluster --no-db` makes no database call
**Satisfies:** R1, R2, R7 · **Depends on:** P1
**Goal:** under `--no-db` the cluster neither creates, initializes, upgrades nor checks the database
on any provider or launch mode; without it, today's behaviour; the docs say so.

- [ ] `src/hypershell/cluster/__init__.py`, `ClusterApp.__enter__`: `if not self.in_memory:` around
      the existing `initdb()`/`checkdb()` branches (PLAN §2).
- [ ] `docs/_include/cluster_help.rst` `--no-db`: add "The configured database is not opened at
      all, so it need not exist or be initialized."
- [ ] `docs/cli/cluster.rst`: `.. versionchanged:: 2.9.0` before the include — "``--no-db`` leaves
      the configured database alone: it is not opened, created or initialized."
- **Verify:** see frontmatter (`test_cluster_no_db.py` all green with `test_server_initdb.py` and
  `test_cluster.py`; a `temp_site` drive: `--no-db` leaves no file and runs under a missing
  directory, a run without it creates the database; `data/` and `cluster/remote.py` unchanged; the
  docs passage and marker; the docs build with `-W`).
- **Touches:** `src/hypershell/cluster/__init__.py`, `docs/_include/cluster_help.rst`,
  `docs/cli/cluster.rst`.

## Phase P3 — A period of zero or less is refused on `hs cluster`, `hs server` and `hs relay`
**Satisfies:** R3, R4, R5, R6, R7 · **Depends on:** P2
**Goal:** every route to the period (option, key string, key number, environment) is refused at
start-up when it is not greater than zero, naming the option or key, the value and the floor; every
positive period reads as before.

- [ ] `src/hypershell/core/types.py`: `parse_period` (PLAN §2) and its `__all__` entry.
- [ ] `src/hypershell/cluster/__init__.py`: `-P` `type=parse_period`; first in `check_arguments()`,
      `parse_period(self.autoscaling_period)` → `ArgumentError(f'autoscale.period: {error}')`;
      `APP_HELP` `-P` line "Scaling period, greater than zero; …".
- [ ] `src/hypershell/server.py`: the same `type=`, the same check first in `check_args()`, the same
      help line. Nothing else in `server.py`.
- [ ] `src/hypershell/relay/__init__.py`: `RelayApp.site_period` returns `parse_period(…)` under the
      existing `UnitError` → `ArgumentError('autoscale.period: …')` wrap; docstring says greater
      than zero.
- [ ] `share/zsh/site-functions/_hs`: both `-P` descriptions "Scaling period, greater than zero; …".
- [ ] `docs/_include/cluster_help.rst`, `docs/_include/server_help.rst`: `-P` summary "Scaling
      period, greater than zero (default: 60)." and "A period of zero or less, here or in
      ``autoscale.period``, is refused."
- [ ] `docs/_include/config_param_ref.rst` `[autoscale] .period`: greater than zero, refused at
      start-up by `hs cluster`, `hs server` and `hs relay`, a number below one second accepted as
      given; a second `.. versionchanged:: 2.9.0` in the `.. only:: html` block.
- [ ] `docs/cli/cluster.rst`, `docs/cli/server.rst`: `.. versionchanged:: 2.9.0` — "``-P``/``--period``
      and ``autoscale.period`` must be greater than zero; zero or a negative value is refused."
- **Verify:** see frontmatter (period, duration, relay and autoscaler tests; a `temp_site` drive:
  `-P 0` exits 2 naming `-P/--period` and running nothing, `HYPERSHELL_AUTOSCALE_PERIOD=-5` exits 2
  naming `autoscale.period`, `-P 0.5` and `HYPERSHELL_AUTOSCALE_PERIOD=0.5` run; both help texts and
  both zsh entries; the three docs files; the autoscalers unchanged; the docs build). No server or
  relay is driven live: their refusals are proven in-process by P1's tests.
- **Touches:** `src/hypershell/core/types.py`, `src/hypershell/cluster/__init__.py`,
  `src/hypershell/server.py`, `src/hypershell/relay/__init__.py`, `share/zsh/site-functions/_hs`,
  `docs/_include/cluster_help.rst`, `docs/_include/server_help.rst`,
  `docs/_include/config_param_ref.rst`, `docs/cli/cluster.rst`, `docs/cli/server.rst`.

## Phase P4 — Whole suite and docs
**Satisfies:** R1, R2, R3, R4, R5, R6, R7, R8 · **Depends on:** P3
**Goal:** nothing else moved: the full suite passes serially and the docs build is clean.

- [ ] Run the whole suite serially (`-n auto` fails collection on this suite; research/00 §5). With
      `HS_TEST_POSTGRES` set, the PostgreSQL rows run too; without it they skip. Dry-run on the base
      tree (2026-11-15, no `HS_TEST_POSTGRES`): 3028 passed, 116 skipped, 1 failed in 23 min. The
      failure, `tests/test_upgrade_cli.py::test_waits_for_slow_peer_upgrade` (`database is locked`),
      came while other pytest runs shared the machine and passes alone; run P4 with nothing else
      running, and if that test alone fails, re-run it by itself and note both runs in the commit body.
- [ ] Re-read R7's passages against the behaviour; fix wording only.
- **Verify:** see frontmatter.
- **Touches:** none expected (wording fixes only).

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
