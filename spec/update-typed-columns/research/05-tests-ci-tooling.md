# 05 — Tests, the CI PostgreSQL job and drive tooling

Read on `fix/update-typed-columns` at `bda42399` (develop `39834600` + GOAL). Timings measured
locally 2026-11-24 (venv `/tmp/claude-501/venv314-utc`, local PG 18); CI timings from the latest
green `Tests` run on develop (2026-11-24).

## 1. How the existing `hs update` tests work

All three files drive the real CLI **by subprocess**; none calls `TaskUpdateApp.main` in process.

- `tests/test_update.py`, `tests/test_update_refusals.py`: SQLite only, `temp_site` fixture
  (`tests/conftest.py:82-90`: fresh `tmpdir` site, `HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`,
  `LOGGING_LEVEL=DEBUG`; SQLite schema created on first use). Run with `tests.main(argv)`
  (`tests/__init__.py`, plain `subprocess.run`, no bound). Rows read with raw `sqlite3`
  (`test_update_refusals.py:36-43`). Refusal asserted as `rc == cli_status.bad_argument`, words in
  stderr, no `Searching database`, no `Deleted`/`Updated`, `rows() == before`
  (`test_update_refusals.py:65-79`).
- `tests/test_update_group.py` (#124) is **the template to copy**: both backends via `db_site`
  (`tests/input_checks.py:76-91`), parametrised `['sqlite', postgres(marks=needs_server)]`; the PG
  branch makes a throwaway database per test (`ts_database`, `tests/test_timescale.py:77-95`, `CREATE
  DATABASE t_<hex>` … `DROP … WITH (FORCE)`) and points the CLI at it (`ts_site`, `:108-133`, sets
  `HYPERSHELL_DATABASE_PROVIDER/HOST/PORT/USER/PASSWORD/DATABASE`). Then `hs initdb --yes`. A module
  using `db_site` must also import `admin, ts_database, ts_site` from `tests.test_timescale`
  (`test_update_group.py:28-29`). Commands run under `bounded()` (`input_checks.py:56-73`, 30 s,
  own session, kills the group). Reads go through the private engine with `rows(engine, sql)`
  (`input_checks.py:99-102`); `set_task()` (`:126-131`) writes typed columns directly.
  Helpers worth lifting: `snapshot()` (full-row compare, `:72-76`), `task_id()` (`:79-83`),
  `update_argv(engine, form, value, *extra)` building both forms (`:86-90`), `assert_refused()`
  (exit 2, message names column and value, no `Searching database`, no `affects`/`continue?`, no
  `Updated`, `:93-100`), the `-f`/prompt variant with `stdin='yes\n'` (`:116-130`), the
  "refusal writes no other change" case (`:133-142`), and the SQLite `typeof()` storage check
  (`:158-159`) — R5/R6-style "stored as integer, not text" on SQLite.
- Markers: `pyproject.toml:141-147`, `addopts = ["--strict-markers"]`; use only `@mark.unit` /
  `@mark.integration` (a `parameterize` placeholder exists; don't use it).
- Pinned today and replaced by R16: `src/hypershell/task.py:1142` (`Invalid field name`) and `:1243`
  (`Unrecognized task field`). No test greps either string (grep of `tests/`), so R16 breaks no
  existing assertion; `test_update_refusals.py` changes only if a case uses an unknown key.
- In-process seam: `change_value(field, text)` (`task.py:1036-1046`) is pure, and
  `TaskUpdateApp.from_cmdline([...]).process_arguments()` (`:1230-1260`) parses changes without the
  database (today it runs after `ensuredb()` in `update_tasks`; the refusal must move before
  `ensuredb()`, `:1116-1136`, beside the #119/#124 checks).

## 2. CI: the PostgreSQL job runs an explicit file list

`.github/workflows/tests.yml`:
- `tests` job (`:20-47`): `uv run --python ${{ matrix.python-version }} pytest -v` — whole suite,
  **no** `HS_TEST_POSTGRES`, so every `db_site[postgres]` case skips. `timeout-minutes: 45`
  (`:23`); latest develop run took **35–42 min** (3.14: 42 min). Headroom is ~3 min.
- `postgres` job (`:68-121`), 5 entries (pg13, pg18, timescale, timescale-oss, timescale-floor),
  `timeout-minutes: 20`, took 4–8 min. Env (`:104-107`):
  `HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres`,
  `HS_TEST_POSTGRES_TEMPLATES=hs283,hs290a1`, `HS_TEST_TIMESCALE=${{ matrix.timescale }}`.
  **The one line to edit** (`:121`):
  `run: uv run --python 3.14 --extra postgres pytest -v -rs tests/test_upgrade_postgres.py … tests/test_server_initdb.py tests/test_update_group.py`
  — append each new file. Also extend the comment block above (`:63-66` names
  `tests/test_update_group.py`). TimescaleDB entries run the same list; `db_site` uses provider
  `postgres` on them too, so no TimescaleDB-specific line is needed. UGL's verify pinned this with
  `grep -q 'tests/test_update_group.py' .github/workflows/tests.yml` — do the same per new file.
- Docs job (`:212-228`): `uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs docs/_build/html`.

## 3. Running a file on each backend (local)

Run from the worktree root. `uv run` needs the env for this cycle:

```sh
export UV_CACHE_DIR=/tmp/claude-501/uv-cache-utc UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-utc
# SQLite only (PG cases skip):
env -u HS_TEST_POSTGRES uv run pytest -q -p no:cacheprovider tests/test_update_group.py
# SQLite + PostgreSQL 18 (sandbox off; HS_TEST_TIMESCALE empty; tests create/drop their own DBs):
HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres HS_TEST_TIMESCALE= \
  uv run pytest -q -p no:cacheprovider tests/test_update_group.py
```

No database needs creating; the admin URL's maintenance DB is used for `CREATE/DROP DATABASE`.
`HYPERSHELL_*` variables are scrubbed by `conftest.py:57-79`, so the shell's own settings never leak.
Best UGL verify line (P2, `spec/update-group-and-legacy-filters/TECH.md:47-55`), the shape to copy:

```sh
test -n "$HS_TEST_POSTGRES" && sh spec/update-client-retry/drive/bound.sh 1800 uv run pytest -q \
  -p no:cacheprovider tests/test_update_group.py tests/test_update.py … && \
  grep -q 'tests/test_update_group.py' .github/workflows/tests.yml
```

and the gate (P4, `:95-104`): whole suite with `env -u HS_TEST_POSTGRES … bound.sh 5400 uv run
pytest -q -p no:cacheprovider`, then the PG-aware files with it set, then the red list, drive, docs.

## 4. Drive and red-list tooling

- `.agents/factory/bin/temp_site.sh CMD…` — throwaway **SQLite** site under `$TMPDIR`
  (`HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`, `UV_PROJECT` = repo), cwd = site, removed on exit.
  `.agents/factory/bin/temp_site.sh uv run hs list`.
- `spec/update-client-retry/drive/bound.sh SECONDS CMD…` — perl wall-clock bound (macOS has no
  `timeout`): SIGINT to the group at the bound, SIGKILL 15 s later, exit 124.
- `spec/update-group-and-legacy-filters/drive/ugl_drive.sh MODE` (`group`|`legacy`) — CLI drive on
  SQLite via temp_site: submit three tagged tasks, run `hs update` cases, `fail`/`pass` lines, exit 1
  at first failure. Usage: `.agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/update-group-and-legacy-filters/drive/ugl_drive.sh" group`.
- `spec/hs-update-contended-writes/drive/update_drive.sh sqlite|postgres|timescale` — the **only
  `hs update` drive that runs on both backends**: for PG it parses `HS_TEST_POSTGRES`, exports the
  `HYPERSHELL_DATABASE_*` vars, `CREATE DATABASE hu_drive_<kind>`, `hs initdb --yes`, drops it on
  exit (`:46-64`).
- `spec/update-client-retry/drive/ucr_drive.sh MODE` — same mode/fail/pass shape as ugl, SQLite.
- **Red list** = node ids of tests written first that must fail on the base build *for what they
  check*, then pass after the fix. `spec/relay-hardening/drive/red_check.sh`
  (env `RED_LIST`, `RED_BASE_REV`, optional `RED_BASE_SRC`): `git archive $REV src` into `$TMPDIR`,
  checks `hypershell` imports from there, then runs **each node alone** (`uv run pytest -q -p
  no:cacheprovider -p red_outcome NODE`) with base `src` first on `PYTHONPATH` (`hs` subprocesses
  inherit it). RED only if pytest exits 1 and the first failure (recorded by `red_outcome.py`, a
  pytest plugin writing phase/kind/exception MRO to `$RED_OUTCOME`) is in the **call** phase and is an
  assertion/`pytest.fail` — or, with a ` raises=NAME` suffix, that exception. Import/collection/
  setup errors are NOT RED, so new APIs must be imported **inside** the test (as
  `test_bounded_parsers.py:30-33` does with `table()`). Format: one node per line, `#` comments,
  `pg:` prefix = needs `HS_TEST_POSTGRES` (skipped with a note otherwise). Exit 0 only if every run
  node is RED.
- `spec/contended-writes/drive/rest_green.sh [PYTEST ARGS]` — runs pytest with every red node
  `--deselect`ed (strips `pg:`/`raises=`); its default list is `spec/contended-writes/red.txt`, so
  always pass `RED_LIST=`.
- Precedent red list: `spec/update-group-and-legacy-filters/red.txt` (106 ids, 29 `pg:`; header
  names base, checker and counts; grouped by file and backend; ids like
  `pg:tests/test_update_group.py::test_group_change_refused[postgres-x-filtered]`).

**Recommendation.** `spec/update-typed-columns/red.txt` in the UGL format; P1 verify modelled on UGL
P1 (`TECH.md:26-34`): fence, counts by `grep -cvE '^#|^$'` and `grep -c '^pg:'`, then
`RED_BASE_REV=<develop after bounded-inputs lands> RED_LIST="$PWD/spec/update-typed-columns/red.txt"
sh spec/update-client-retry/drive/bound.sh 3600 sh spec/relay-hardening/drive/red_check.sh` and
`RED_LIST=… sh spec/update-client-retry/drive/bound.sh 1800 sh spec/contended-writes/drive/rest_green.sh tests/test_update_*.py`.
The base is *not* `39834600`: the tests will use bounded-inputs' messages, so the base must be
develop with bounded-inputs merged. Drive: `spec/update-typed-columns/drive/utc_drive.sh BACKEND MODE`,
with the mode/fail/pass body of `ugl_drive.sh` and the backend setup of
`hs-update-contended-writes/drive/update_drive.sh` (SQLite in a `$TMPDIR` site, or PG
`utc_drive_<mode>` created/dropped), so R6 (same instant) and the PG traceback cases are driven on
both. Usage: `sh spec/update-typed-columns/drive/utc_drive.sh sqlite numbers` and
`HS_TEST_POSTGRES=… sh spec/update-typed-columns/drive/utc_drive.sh postgres numbers`.

## 5. bounded-inputs (`fix/bounded-inputs`, head `bf759ec1`, review: changes-requested)

- `tests/bounded_checks.py`: `refusal(name, source=None)` regex for the one refusal line
  `<name>: expected <form> from <A> to <B>, given <V>`; `refusal_lines`; `assert_one_refusal(rc,
  stderr, status, name)` (status, no `Traceback`, exactly one line, ≤ `SHORT`=400 chars); `LONG`
  (4,300 digits) / `WIDER` (5,000) inputs; `hs_cli(site, argv, bound, **env)`.
- `tests/test_bounded_parsers.py:37-107`: `CASES` = (entry, accepted (value, reads-as), refused)
  plus `ALWAYS_REFUSED` (`x`, `nan`, `inf`, LONG, WIDER, `true`, …), expanded by `accepted()` /
  `refused()` into params; `table().parse_setting(name, value)` / `SettingError`. Entries already
  there for reuse: `task.cores`, `task.memory`, `task.timeout`, `server.attempts`, `option.part`,
  `option.group` with their bound/past-bound values. A column matrix can be built the same way —
  `(column, entry, accepted, refused)` — with the bounds read from `SETTINGS` rather than retyped.
- `tests/test_bounded_advisory.py:117-160`: `live_advice` fixture (reset `config_schema.ADVISED`,
  `PENDING`, capture WARNINGs in process) and `advice()`/`assert_advised_once()` for stderr — the R12
  "once just past the limit, not at it" check.
- `tests/test_bounded_tags.py` uses `db_site` (SQLite + PG). **The branch does not touch
  `.github/workflows/tests.yml`**: its `db_site` tests run on PostgreSQL only locally.

## 6. Docs, help and completions

- `docs/_include/task_update_desc.rst` (43 lines), `docs/_include/task_update_help.rst` (127),
  `docs/cli/task_update.rst` (78; 10 `versionchanged:: 2.9.0`/`versionadded` markers, includes at
  `:9,:14,:78`). `UPDATE_HELP` is `task.py:957`.
- **No test pins `UPDATE_HELP` against the rst.** Precedents for a doc test: `test_bounded_docs.py`
  (help snippet gives the range; page marks ranges via `test_client_host_default.MARKER`),
  `test_relay_advertise.py:230-240` (text in `_include/*_help.rst`). UGL used verify greps instead
  (`TECH.md:53-60`: one-line phrases, `! grep -n 'hs task '`, count of `versionchanged:: 2.9.0`).
- Completions list no columns statically: bash `_hs_task_update` (`share/bash_completion.d/hs:450-466`)
  and zsh `_hs_update_args` (`share/zsh/site-functions/_hs:118-126`) offer `FIELD=` for every
  `hs list --fields` name except `id`, so they still offer `tag=` (refused under R11). Optional
  one-word fix each; `tests/test_completions.py` has no `hs update` case.
- Docs build locally (UGL P3):
  `UV_PROJECT_ENVIRONMENT="${TMPDIR:-/tmp}/docs-venv-utc" sh spec/update-client-retry/drive/bound.sh 900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs "${TMPDIR:-/tmp}/docs-html-utc"`

## 7. Runtime and recommended test shape

Measured: one `test_update_group` case = `hs initdb` + `hs submit` + `hs update` subprocesses,
**~1.1 s on SQLite** (22 cases 24 s incl. collection), **~1.6 s on PG** (fresh database per test;
8 cases 13 s). A naive grid — 33 columns × ~15 values × 2 forms × 2 backends ≈ 2,000 subprocess
tests — is ~40 min, which alone would push the CI `tests` job (42 of 45 min) over its timeout, and
the red check (one pytest process per node, ~3 s each) past an hour.

Recommend three layers:
1. **In-process unit grid** (`@mark.unit`, milliseconds): every column × every value R14 lists
   (accepted forms, each bound and one past, every R9 refusal, `none`) through the one reader
   (`change_value` or its successor), refusal via `ArgumentError` and the `refusal()` pattern. This
   is where "every column, every value" lives. Not backend-specific.
2. **Subprocess integration per column** (`db_site`, both backends): parametrise by
   `(column, form)` only — 33 × 2 × 2 backends ≈ 132 tests — and inside each, one site, one
   `submit_three`, then loop over that column's representative values (one accepted with storage
   check, the bound and one past, one refused, `none`), each one `hs update` (~0.35 s), asserting
   `assert_refused` + `snapshot` unchanged. ≈ 132 × ~3 s ≈ 7 min split across both backends; the CI
   `tests` job pays only the SQLite half (~3–4 min) — still raise its `timeout-minutes` from 45 to 60
   in the same commit that adds the CI file line.
3. **Cross-cutting cases** as single tests (both backends): refusal with another change, `-f` vs
   prompt, R6 same instant naive/offset, R10, R11, R12 WARNING, R16 (unknown key, misspelled column,
   invalid tag key, prompt text).

Red list mainly from layers 2–3 (coarse nodes keep `red_check` near UGL's 106-node / 40-min scale).
Layer 1 is red on the base only if it calls an API the base has (`change_value` exists there:
`pytest.raises(ArgumentError)` failing with "DID NOT RAISE" is a `pytest.fail` → RED); a call to a
new reader fails on a missing name → NOT RED, so either import inside the test and leave layer 1 off
the list, or list one node per kind rather than per value. Keep pytest serial (no `-n`); PG runs need the sandbox off.
