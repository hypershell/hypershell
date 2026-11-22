# 05 — Tests, harness, bounded runs and the docs build

Research brief for `hs-plan`, cycle `submit-input-checks` (worktree HEAD `46dcb430`, tree = `develop`
`dfdbaed5`). Read-only; nothing in `src/`, `tests/` or `docs/` was edited.

## Question

Which existing tests pin behaviour the GOAL changes, what harness the R8 tests should use (SQLite and
PostgreSQL, CLI subprocesses, bounded pipes), how long the related suites take today, and where the R9
passages live and how the docs build is run.

## Findings

**How tests run `hs`.** Integration tests shell out to the installed CLI (editable install of this
worktree in `/tmp/claude-501/venv314-sic`). `tests/__init__.py:main(argv)` is `subprocess.run` with
**no timeout**; `main_lines` wraps it. Bounded patterns already in the tree:
`run(..., timeout=DEADLINE)` (`tests/test_cluster_no_db.py:111`, `test_group_order.py:448,461`);
`run_cli(site, command, producer=)` in `tests/test_cluster_exit.py:588` (bash command in its own
session, optional FIFO producer, `proc.wait(timeout)`, `kill_group` = `killpg(SIGKILL)` in `finally`);
`run_child` (same file, `:128`). In-process scheduler replays use `task_db`
(`test_group_order.py:46`, a per-test SQLite file bound to the global `Session`), reused by
`test_stream_input.py`.

**Fixtures.** `temp_site` (`tests/conftest.py`) sets `HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`,
`HYPERSHELL_LOGGING_LEVEL=DEBUG`; autouse `clean_env` scrubs `HYPERSHELL_*` and pins a free port.
PostgreSQL: `HS_TEST_POSTGRES` (admin URL) gates everything; unset means skip. Two fixture styles:
- in-process: `pg_engine` (`test_zone_postgres.py:46`) — `CREATE DATABASE t_<hex>`, `upgrade_schema`,
  binds `Session`, drops `WITH (FORCE)`;
- CLI: `ts_database` + `ts_site` (`test_timescale.py:75,106`, `needs_server` mark at `:52`) — make a
  `t_<hex>` database and point the CLI at it by environment (`HYPERSHELL_DATABASE_PROVIDER=postgres`,
  host/port/user/password/database; `HYPERSHELL_DATABASE_FILE` popped). `contended_db`
  (`test_contended_write.py:91`) adds `hs initdb --yes`; `pg_site` (`test_server_initdb.py:314`) wraps
  both. These are imported across modules (`from tests.test_timescale import admin, ts_database, ...`).

**Group coverage today.** Only `tests/test_zone_postgres.py::test_group_checks_on_native_uuid_ids`
exercises group logic on PostgreSQL; none of `test_submit.py`, `test_groups.py`, `test_group_order.py`,
`test_stream_input.py`, `test_submit_json.py`, `test_restart.py` is PostgreSQL-aware. No test submits a
JSON non-integer group or `-t group:…`; no test asserts the `Auto-selected task group` log line
(`submit.py:1451`), so C9's default change is free of test churn.

**`#` coverage today.** `split_argline` has one unit test. No CLI test puts a bare `#` inside a
command. Comment-only and tag-only lines are pinned by
`test_submit.py::test_source_stamp_named_file` (`Found 3 tasks`, R2 — must stay green). Every other `#`
in task lines across `tests/` is a `#HYPERSHELL:` marker (e.g. `test_queue_workload.py:56-64`, whose
`line.split('  #')[0]` still matches).

**Template subtlety.** `line_is_task` (`submit.py:138`) splits the **template-expanded** line, and
`Task.new` re-splits `raw_args` for the fingerprint (`model.py:558`); `Loader` re-splits at `:369`. R1
"under `--template`" therefore needs a test where the template itself carries a `#`.

## Tests that change

| Test | Today | Why it changes |
| --- | --- | --- |
| `tests/test_data/test_model.py::TestTask::test_split_argline` lines 29–33 | `echo "hello world" # Comment`, `#Comment`, `# Hypershell x`, `# hypershell x`, `# HS x` → `echo "hello world"` | R1: kept whole. Lines 23–28 and 34–45 (comment line, marker forms) stay (R2). |
| `tests/test_group_order.py::test_cluster_runs_group_stored_as_text` | `hs cluster` file with `group:x` exits 0, 2 tasks done | R3/R4: exit 5, nothing inserted. Becomes a refusal test. |
| `tests/test_group_order.py::test_late_lower_group_warns_after_text_group_started_first` | `commit_tasks('x')` → `Task.new(group='x')` | Breaks only if the check lands in `Task.new`. The scheduler guard stays (non-goal), so keep the test and insert the text row by raw SQL if so. |
| `tests/test_signal_wake.py::test_submit_takes_a_signal_taken_by_another_thread` | `hs submit -f -` with stdin open; the injector fires only once the main thread is parked in the bounded-step join | R6 reads stdin in full. If that read moves to the main thread, the injector never fires and the test fails at its 30 s `DEADLINE`. Keep the read in the worker thread. The final count (3) holds. |
| `tests/test_submit.py::test_source_stamp_non_seekable_input_streams_all` | docstring "streams every task" | Assertions (3 tasks, `<stdin>` source) hold under a full read; wording only. |

Hold unchanged (checked): the `-g0` gate tests in `test_submit.py`, `test_restart.py`,
`test_submit_json.py` (no reached group: nothing scheduled, or all finished);
`test_groups.py::test_group_starts_based_on_previous_scheduled_tasks` (second batch onto a finished
database, a ready-made R6 "no reached group" guard); stdin pipes into `hs submit` that close before exit
(`test_relay_topology.py:977,1012`, `test_relay_retire.py:499,547`, `test_auth_variants.py:339`,
`test_env_config.py:567`); `test_submit_json.py::test_from_json_failfast_missing_key` (the model for
"refused with nothing inserted").

## Harness recommendation

1. **One parametrized CLI fixture** in a new `tests/test_submit_input.py` (or split by seed):
   `params=['sqlite', pytest.param('postgres', marks=needs_server)]`. The SQLite branch is
   `temp_site`. The PostgreSQL branch calls `request.getfixturevalue('ts_database')` /
   `('ts_site')` **lazily**, because `admin` does `create_engine(None)` when `HS_TEST_POSTGRES` is
   unset, then `ts_site(name, provider='postgres')` and `hs initdb --yes`. Every R8 CLI case then runs
   on both with no other code. Pure parse cases (R3 accepted/refused values, `split_argline`) are
   `@mark.unit` and database-free.
2. **Bound every subprocess**: add a `timeout=` to each `run` (or a small `main_bounded`) and
   `pytest.fail` on `TimeoutExpired`. `group:nan` "no hang" needs this, since `main()` has none.
3. **Streams into `hs cluster`/`hs server`** (R1, R5): reuse `run_cli` from `test_cluster_exit.py`
   (import it): `printf ... | hs cluster -N1 --no-tls -H 127.0.0.1 -p <free_port>` in its own session.
   Use a timeout of about 60–120 s and `killpg` in `finally`. Assert the ERROR line in the log and the
   rows by `hs list`.
4. **`--queue`** (R1): the `start_server(..., '--no-db')` + `hs submit --queue -w 1` + bounded client
   pattern of `test_env_config.py:560-575`.
5. **R7 stranded `--restart`**: build the strand in-process (`task_db`-style rows with
   `schedule_time` set, as `test_group_order.py::test_restart_runs_stranded_lower_group`), then run
   `hs cluster FILE --restart` bounded. On PostgreSQL, write the rows via the private engine
   `ts_site` returns.
6. Tag every test `unit` or `integration` (`--strict-markers`).

## Baseline timings

Python 3.14.6, serial, `bounded.py`, this tree, 2026-11-21.

- SQLite subset — `tests/test_submit.py test_groups.py test_group_order.py test_stream_input.py
  test_source.py test_submit_json.py test_restart.py test_cluster_no_db.py test_template.py`:
  **169 passed in 216 s** (3 min 36 s).
- PostgreSQL on (sandbox off, local PG 18) — `test_zone_postgres.py test_server_initdb.py
  test_contended_write.py test_submit.py test_groups.py`: **147 passed, 5 skipped in 141 s**. The skips
  are 4 for `HS_TEST_TIMESCALE` and 1 for the `hs283` template.
- `tests/test_data/test_model.py tests/test_signal_wake.py tests/test_queue_workload.py`: **11 passed
  in 23 s**.
- Whole suite: **3694 collected**, not run. The last recorded serial run was `relay-shutdown-tail`
  P7, sandbox off: 3545 passed, 138 skipped in **24 min 26 s**, with
  `bounded.py 7200 uv run pytest -q -p no:cacheprovider`. Budget about 25–30 min plus the new tests,
  and bound it at 7200 s as that cycle did. `pytest -n auto` is broken here.

## Docs build command

The docs dependencies are the `docs` dependency group in `pyproject.toml`; there is no
`requirements*.txt`. **`/tmp/claude-501/docs-venv-sic` now exists.** I created it (about 1 s, from the
cache) and built the docs with no warnings (HTML 5 s, man 3 s). The commands, with the sandbox off:

```
UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-sic UV_CACHE_DIR=/tmp/claude-501/uv-cache-sic \
  python3 spec/submit-input-checks/drive/bounded.py 900 uv run --python 3.12 --group docs \
  sphinx-build -W --keep-going -q -E -b html docs /tmp/claude-501/docs-html-sic
# and -b man docs /tmp/claude-501/docs-man-sic
```

**R9 locations.**
- `docs/_include/submit_desc.rst:17-18` holds the only `# HYPERSHELL:` prose. It is included by
  `docs/cli/submit.rst:14` and `docs/manual.rst:119`.
- `docs/_include/cluster_desc.rst` and `server_desc.rst` say nothing about `#`. The new text goes
  beside their includes as `.. versionchanged:: 2.9.0` in `docs/cli/cluster.rst` (eight markers
  already, after `:14`) and `docs/cli/server.rst` (markers at `:18-39`). A marker is never placed
  inside a generated include (AGENTS § Docs).
- `docs/_include/task_groups_alt.rst` (32 lines, included at `manual.rst:247`) gets the integer rule,
  file versus stream, and the reached group.
- `AGENTS.md:257-272` and `.agents/factory/invariants.md:100-106` hold the group-gating text. Also
  `AGENTS.md:652` notes that queue mode ignores `-t`, which matters for C8.
- Marker style is one short clause, with no PR number during the cycle.

## Completions and help

`share/` completions carry no `#` or group-type text, so they need no change. The `hs submit` help
(`submit.py:1294-1330`) says only `-g, --group NUM`. If `-g` gains wording, the help changes, and so
do `docs/_include/submit_help.rst` and the rebuilt `share/man/man1/hs.1` (which repeats
`submit_desc` at `:1273-1274`; the release rebuilds it). `test_submit_help` compares against
`SubmitApp.interface.help_text`, so it follows automatically. The `-b`/`-w` help ("Number of lines to
buffer", "Wait before flushing") reads oddly once `hs submit` with a database reads stdin in full
(R6). The plan should decide whether that text changes.

## Risks

- `test_signal_wake` above: the full stdin read must not block the main thread.
- The CLI PostgreSQL fixtures name databases `t_<hex>`, not a cycle prefix. They drop them in
  `finally`, but a killed run leaves them behind.
- The SQLite subset already takes about 3.6 min. Running R8 over two databases roughly doubles the
  new tests' cost, so keep pure parsing in unit tests and put only the ingest paths through the CLI.
- The legacy `task` group's submit also reaches `split_argline`. It is out of R1's named commands, but it changes with
  any shared fix, and docs must not name it.
