# 05 — Tests, the R1 coverage test, docs and the verify harness

Research brief for `hs-plan`, cycle `bounded-inputs` (worktree HEAD `925e7f0d`; `src/`, `tests/`,
`docs/`, `share/` identical at `03a02bd8`, `8548053b` and `develop`). Read-only.

## 1. Test layout today

- **Config schema:** `tests/test_config_schema.py` (unit). `parse_reference()` (`:39`) parses
  `config_param_ref.rst` into `{path: (key|table, body)}`; `default_leaves()` (`:60`) walks
  `core/config.py` `default`; `test_defaults_declared` (`:70`), `test_reference_entries_declared` (`:81`),
  `test_prose_keys_named` (`:92`, zone keys live in the `relay.zone` prose), `test_duration_keys_documented`
  (`:418`, already asserts per-entry text and a `versionchanged:: 2.9.0`). This is the sync-test pattern R1/R12 extend.
- **Env layer:** `test_env_config.py`, `test_env_duration.py` (`config_seconds`), `test_env_blame.py`.
- **`hs config set`:** `test_config_set_types.py` — `config_set()` (`:49`) runs a child with
  `timeout=60`; `test_typed_refused` (`:145`) asserts exit 2, `'<path>: expected <form>'`, file byte-identical, no backups.
- **Parsers:** `parse_time`/`parse_delay` in `test_duration_options.py`, `test_size_duration.py`;
  `parse_bytes` in `test_size_duration.py`, `test_submit.py`; `parse_period` in `test_period_floor.py`;
  `parse_rate` only indirectly (`test_config_unread.py:222`, `-R` words).
- **Cluster/evict/failed launches/group:** `test_config_unread.py` (`check_arguments`, rate),
  `test_cluster_evict.py` (`REFUSALS` `:121`), `test_autoscale_failed_launches.py:294`,
  `test_group_integer.py`, `test_retry_defaults.py`, `test_memory_column.py` (`check_large_memory` `:63`).
- **Harness:** `tests/conftest.py` (`clean_env` autouse, `temp_site`); `tests/__init__.py:main` has
  **no timeout**. Bounded helpers to reuse: `tests/input_checks.py` — `bounded()` (own session,
  `pytest.fail` at the bound), `db_site` fixture (params `sqlite`, `postgres` with `needs_server`;
  lazy `ts_database`/`ts_site`, `hs initdb --yes`, private engine), `rows()`; `test_config_unread.py`
  — `file_layer`, `child_env`, `run_bounded`, `config_repr` (child evaluates an expression on an app
  built by `from_cmdline`); `test_period_floor.py:run_recorded` (records entry points, binds nothing).
  Exit statuses are asserted as `cmdkit.app.exit_status.bad_argument|bad_config`.
- **PostgreSQL:** gated by `HS_TEST_POSTGRES` (admin URL); `needs_server` in `test_timescale.py:58`.
  `db_site` is the right fixture for R11's "largest values stored": `hs submit -c 32767 -m … -W 2147483647`,
  then `rows(engine, 'SELECT cores, memory, timeout FROM task')`; attempts via `hs cluster -r 32766`
  is too slow at 32,767 runs — store `attempt` by `server.attempts=32767` and one forced retry, or
  write the row through the engine and read it back.
- **Markers:** `--strict-markers`, only `unit`/`integration` (`pyproject.toml:142`).

**Base probes (temp site, SQLite, `03a02bd8`).** `hs submit -c 100000`, `-W 99999999999`, `-b 0`,
`hs list -l -5`, `-g 99999999999`, `--part -1`: exit 0. `-m 8388608TB`: exit 5, `OverflowError` into
SQLite with an exception log. `attempts = "3"`: exit 1, `TypeError` traceback. `hs config set
server.port 99999999999999999999999` and a 4,300-digit `server.evict`: exit 0, written.
**`parse_bytes('9223372036854775807')` returns 2⁶³** (`types.py:118`, `int(float(num) * scale)`):
the size ceiling 2⁶³−1 cannot be reached by text today, so the size parser must use exact arithmetic
(`Fraction`, as `_match_seconds` does) or R11's upper-bound test fails. `parse_bytes('9'*4300)`
raises `OverflowError`; `parse_time('9'*4300)` returns the huge int.

**R1 conflict to record:** `-N 0` is documented and implemented as *auto-detect cores*
(`client.py:1362`, `docs/_include/client_help.rst:7`, `cluster_help.rst:36`, help text "0=auto",
both shells' completions offer `0`). The GOAL's Threads floor of 1 refuses it. R1(a) requires the
floor to widen to 0 ("0 = all cores"); `hs-plan` records it in `TECH.md`.

## 2. The R1 coverage test (`tests/test_bounded_table.py`, `@mark.unit`)

Every app is reachable by walking `HyperShellApp.commands` recursively (`cmdkit.app.ApplicationGroup`
subclasses have `.commands`; leaves have `.interface`, an `argparse.ArgumentParser` subclass), and
`interface._actions` gives each option with its `type`. Prototype run here: 21 leaf apps (including
the `task` aliases), option actions by `type`: `int` 33, `float` 2, `parse_time` 18, `parse_bytes` 5,
`parse_delay` 2, `parse_rate` 2, `parse_period` 2, `group_option` 1, untyped (`None`) 84 across 33
distinct long names, all text (`--host`, `--template`, `--where`, `--tag`, `--failures`, `--autoscaling`, …).

```python
def leaf_apps(app=HyperShellApp, path=()):
    if issubclass(app, ApplicationGroup):
        for name, sub in app.commands.items(): yield from leaf_apps(sub, (*path, name))
    else: yield path, app
def value_options():  # every option that takes a value
    for path, app in leaf_apps():
        for a in app.interface._actions:
            if a.option_strings and a.nargs != 0 and not isinstance(a, (_HelpAction, _VersionAction)):
                yield path, a
```

- **(a) keys:** `{p for p, k in KEYS.items() if k in NUMERIC} | {f'relay.zone.*.{z}' for z, k in ZONE_KEYS.items() if k in NUMERIC}`
  equals the table's key entries (set equality, so a stray entry fails too). Today: 46 keys
  (21 int, 18 duration, 3 int_or_none, 2 size, 2 number) plus 4 zone keys = the GOAL's 50.
- **(b) defaults:** for each numeric `default_leaves(default)` leaf, the setting's parser reads
  `str(value)` (or the value) into range; `<none>` passes for `int_or_none`; `database.port` has no
  default (skip). Also each ranged option's argparse `default` when not `None`.
- **(c) options:** `assert a.type not in (int, float)` for every action (RED on base by assertion —
  35 actions); every action with a `type` carries the marker, e.g. a `RangedType` callable with
  `.setting` naming a table entry (`getattr(a.type, 'setting', None) in TABLE`); every untyped action's
  long name is in an explicit `TEXT_OPTIONS` allow-list compared by **set equality**, so a new
  untyped option forces a decision. The marker callable must keep a `__name__`/`__repr__` and raise
  `ArgumentTypeError` (as `UnitError` does) so argparse prints its message, not "invalid int value".
- Split so red_check can use it: (c)'s `type not in (int, float)` test touches no new name and is
  RED on base; (a)/(b) import the new table and fail on base by absence, so they are **not** listed
  (house rule, `spec/submit-input-checks/drive/red.txt` header).

## 3. Docs (R12)

- `config_param_ref.rst` (1,167 lines): entries are `    ``.name``` terms, body indented 8, first
  line `Description (default: `N`).`, then prose ("Takes … forms"), `See also ``-b``…`, then
  `.. only:: html` holding `.. versionchanged:: 2.9.0` blocks (`.evict` `:361-385`). A `Range: …`
  sentence goes after the forms sentence; the marker joins the `.. only:: html` group. Zone keys
  are prose in `relay.zone`. **Enforce it:** a test renders each entry's range from the table and
  asserts it is in `parse_reference()[path][1]` (pattern of `test_duration_keys_documented`).
- Option help pages are hand-written (no generator): `docs/_include/{cluster,server,client,relay,submit,task_search,task_update,task_wait,task_run}_help.rst`,
  included by `docs/cli/<cmd>.rst` (list = `task_search`, update = `task_update`, …). Never mark
  inside a snippet; the `versionchanged` goes above the `.. include::` (AGENTS.md § Docs). Enforce
  with `rst_entry()` and `MARKER` from `tests/test_client_host_default.py:290,327` (per ranged option,
  the entry holds the rendered range; each page has a 2.9.0 marker naming ranges).
- Env page: `docs/_include/config_intro.rst:156-161` (bullets "converted value… stops the command"
  and "A duration or a size … checked by the command that reads it" — the latter becomes false).
  `config_intro_alt.rst` is the manual's copy; check it too. `exit_status.rst` lists 2 and 3 already.
- `AGENTS.md:550-584` § Configuration (name the table; `:563-566` describes durations/sizes "stay
  as given" — update), `.agents/factory/review-rubric.md:45-58` (drop "before `bounded-inputs`
  lands", point at the table). API docs for R10: `docs/api/*.rst`.
- **Build:** `-W --keep-going -q -E -b html` (and `-b man`). It **cannot run in the sandbox**:
  `sphinx_sitemap` binds a socket, EPERM. `/tmp/claude-501/docs-venv-sic` exists but its editable
  `hypershell` is gone (`conf.py:15` import fails); use `PYTHONPATH=src` or make
  `docs-venv-bnd` with `uv run --python 3.12 --group docs`, sandbox off.

## 4. `share/` completions

Word lists only, no types or ranges (`_hs:480-624`, `bash_completion.d/hs:561-675`). Values offered
are in range, except `-N` offers `0` in both shells (`_hs_thread_counts`, `hs:593`): fine if the floor
widens to 0 (§1), else drop it. No other change; `share/man/man1/hs.1` is rebuilt at release.

## 5. Tests that flip (amend in P1, outside the red list)

- `test_cluster_evict.py` `REFUSALS` and `:178`, `:193`: `runtime_error`→`bad_config`, cap
  `timedelta.max.days*86400`→2³¹−1, message form.
- `test_period_floor.py::test_period_key_refused` (`:224`, key routes 2→3), message form `:276`.
- `test_config_unread.py`: `test_cluster_rate_not_finite_refused`, `_text_not_finite_refused`,
  `_under_floor_refused` (2→3, message); **`test_cluster_rate_option_over_not_finite_key`** (exit 0
  today; under C2 a file's `ratelimit = inf` stops every command even with `-R`).
- `test_config_set_types.py` `SIZED`: `evict-half`, `evict-inf`, `evict-True`, `evict-null` become R7 refusals.
- Message regexes: `test_config_schema.py:286`, `test_env_duration.py:74`,
  `test_autoscale_failed_launches.py:294`, `test_size_duration.py:149`.

## 6. Harness and verify recommendation

Reuse the landed scripts; do not copy them: `spec/submit-input-checks/drive/bounded.py`,
`run_list.sh`, `spec/relay-hardening/drive/red_check.sh` (+ `red_outcome.py`). New:

```
spec/bounded-inputs/drive/red.txt       # node ids RED on 8548053b; pg: lines; new-name tests not listed
spec/bounded-inputs/drive/guards.txt    # in-range forms green before and after every phase
spec/bounded-inputs/drive/bounds_drive.sh   # modes: options, config, set, inrange
```

**Red candidates (assertion on base):** `-c 100000`, `-m 8388608TB`, `-W 2147483648`, `-b 0`,
`hs list -l -5`/`-g 2147483648`/`--part -1`, `hs cluster --no-db -F nan`/`-F 0` → 2; file
`client.ratelimit = 1e-300` under `hs client` → 3; 4,300-digit `server.evict` → 3, printable;
`attempts = "3"` → three attempts; TOML `bundlesize = true` → 3; `hs config set` 4,300 digits and
`database.connect_args.x 2⁶⁴` → 2, file unchanged; `-t cores:100000` → 2, a file's
`cores:100000` → 5 nothing inserted; R8 by a child that patches an argument check to raise
`OverflowError`; `pg:` `-m 9223372036854775807` stored (exit 5 today via §1's float).
**Guards:** `-c 32767`/`-W 2147483647`/`-r 32766` stored (`pg:` too), `-P 0.5`→1, `-d -10m`,
`-R 1e-6`, `test_period_floor.py::test_period_key_accepted`, `test_config_unread.py::test_cluster_rate_at_floor`,
`test_config_set_types.py::test_typed_converted`, `test_duration_options.py`, `test_size_duration.py`,
`test_memory_column.py`, `test_retry_defaults.py`, `test_group_integer.py::*accepts*`.

**R11 economy:** run the full matrix (each kind × bounds, ±1, `x`, `nan`, `inf`, 4,300 digits,
`true`, text) in process against the parsers (`@mark.unit`, cheap), and through the CLI only one
setting per kind × route (option, file, variable). Each `hs` start costs ~0.35 s here.

**Verify lines** (env as the precedent: `UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-bnd`,
`UV_CACHE_DIR`, `HS_TEST_POSTGRES`, sandbox off for PostgreSQL and docs):

- P1: `test -n "$HS_TEST_POSTGRES" && RED_LIST=spec/bounded-inputs/drive/red.txt RED_BASE_REV=8548053b
  python3 spec/submit-input-checks/drive/bounded.py 2700 sh spec/relay-hardening/drive/red_check.sh
  && sh spec/submit-input-checks/drive/run_list.sh spec/bounded-inputs/drive/guards.txt 1200
  && python3 spec/submit-input-checks/drive/bounded.py 120 uv run pytest -q -p no:cacheprovider --collect-only tests/test_bounded_*.py`
- Middle phases: `bounded.py 2400 uv run pytest -q -p no:cacheprovider <new files> <touched suites>`
  `&& run_list.sh …guards.txt 1200 && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/bounded-inputs/drive/bounds_drive.sh" <mode>`.
- Last: whole suite `bounded.py 7200`, the red list re-run (all green), docs html and man with `-W`.

**Timings** (Python 3.14, serial, this tree): `-m unit` 3,047 passed, 15 skipped in **110 s**;
`test_config_schema.py test_config_set_types.py test_period_floor.py test_cluster_evict.py` 463
passed in **95 s**. Suite collects **4,671** (3,694 at the precedent, whose serial run took ~25 min):
budget ~35 min, bound 7200 s. `-n` is not used.

**Phase count (C4):** the house P1 (tests only) plus the GOAL's four make five, over C4's fallback
trigger. Folding the table into P1, or counting P1 outside the four, is a plan decision to state.
