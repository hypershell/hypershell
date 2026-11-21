---
slug: env-config-keys
title: Environment variables reach every configuration key with the value as written;
  the remaining duration keys take units
kind: fix
appetite: big
status: in_review
branch: fix/env-config-keys
base: develop
current_phase: done
last_updated: '2026-11-21'
phases:
- id: P1
  name: 'Tests first: the regression tests and the red list on 90ab2fc2 (tests only)'
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
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: git diff --quiet 90ab2fc2 -- src .github share docs && test -z "$(git diff
    --name-only 90ab2fc2 -- . ':!spec' ':!tests' ':!issues' ':!ROADMAP.md')" && test
    "$(grep -cvE '^#|^$' spec/env-config-keys/red.txt)" -ge 62 && RED_BASE_REV=90ab2fc2
    RED_LIST="$PWD/spec/env-config-keys/red.txt" sh spec/relay-hardening/drive/red_check.sh
    && RED_LIST="$PWD/spec/env-config-keys/red.txt" sh spec/contended-writes/drive/rest_green.sh
    tests/test_env_config.py tests/test_env_duration.py tests/test_env_export.py tests/test_env_blame.py
    tests/test_auth_variants.py tests/test_autoscale_failed_launches.py tests/test_duration_options.py
    tests/test_key_option.py tests/test_initdb.py tests/test_logging.py tests/test_size_duration.py
    tests/test_completions.py tests/test_cluster_launch.py
- id: P2
  name: The key table, the matcher and the typed environment layer (core/config_schema.py,
    core/config.py)
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q -p no:cacheprovider tests/test_env_config.py tests/test_config_schema.py
    tests/test_auth_variants.py tests/test_autoscale_failed_launches.py tests/test_duration_options.py
    tests/test_key_option.py tests/test_logging.py tests/test_initdb.py tests/test_size_duration.py
    tests/test_queue_tls.py tests/test_topology_server.py tests/test_relay_launch.py
    tests/test_cluster_launch.py tests/test_submit.py && for m in keys typed; do .agents/factory/bin/temp_site.sh
    uv run sh "$PWD/spec/env-config-keys/drive/env_drive.sh" $m || exit 1; done
- id: P3
  name: '[export] as written, and key variables recognised by the matcher'
  status: done
  satisfies:
  - R7
  - R4
  - R1
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q -p no:cacheprovider tests/test_env_export.py tests/test_env_config.py
    tests/test_cluster_launch.py tests/test_relay_launch.py tests/test_client.py tests/test_key_option.py
    && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/env-config-keys/drive/env_drive.sh"
    export
- id: P4
  name: blame, hs config which and completion name keys and variables by the loader's
    rule
  status: done
  satisfies:
  - R8
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q -p no:cacheprovider tests/test_env_blame.py tests/test_completions.py
    tests/test_initdb.py tests/test_topology_server.py tests/test_autoscale_failed_launches.py
    tests/test_env_config.py && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/env-config-keys/drive/env_drive.sh"
    which
- id: P5
  name: The ten duration keys take units, converted at the start of the command that
    reads them
  status: done
  satisfies:
  - R6
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q -p no:cacheprovider tests/test_env_duration.py tests/test_config_schema.py
    tests/test_duration_options.py tests/test_cluster_launch.py tests/test_relay_launch.py
    tests/test_relay_outage.py tests/test_relay_db.py tests/test_relay_workload.py
    tests/test_server.py && for m in durations serve; do .agents/factory/bin/temp_site.sh
    uv run sh "$PWD/spec/env-config-keys/drive/env_drive.sh" $m || exit 1; done
- id: P6
  name: 'Docs: the environment rule once, the duration forms, [export]; AGENTS.md
    and invariants in lockstep'
  status: done
  satisfies:
  - R9
  depends_on:
  - P3
  - P4
  - P5
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'uv run pytest -q -p no:cacheprovider tests/test_config_schema.py && grep
    -q ''_config_env:'' docs/_include/config_intro.rst && grep -q ''HYPERSHELL_RELAY_ZONE_''
    docs/_include/config_intro.rst && grep -q ''HYPERSHELL_PYTHONPATH'' docs/_include/config_intro.rst
    && grep -q ''versionchanged:: 2.9.0'' docs/_include/config_intro.rst && grep -q
    ''versionchanged:: 2.9.0'' docs/_include/config_task_env.rst && test "$(grep -c
    ''versionchanged:: 2.9.0'' docs/_include/config_param_ref.rst)" -ge 19 && ! grep
    -qs ''aligns to the path'' docs/_include/config_intro.rst docs/_include/config_intro_alt.rst
    docs/manual.rst && grep -q config_schema AGENTS.md && grep -q config_schema .agents/factory/invariants.md
    && grep -q EMPTY_UNSET AGENTS.md && grep -q EMPTY_UNSET .agents/factory/invariants.md
    && grep -q ''recognised by the matcher'' AGENTS.md && grep -q ''recognised by
    the matcher'' .agents/factory/invariants.md && grep -q ''recognised by the matcher''
    docs/security.rst && grep -q ''optional text key'' docs/_include/config_intro.rst
    && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs
    sphinx-build -W --keep-going -q -E -b html docs $TMPDIR/docs-html-eck'
- id: P7
  name: 'Regression sweep: full suite, red list green, Python 3.11, every drive mode,
    red check, docs'
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
  - P6
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q -p no:cacheprovider && uv run pytest -q -p no:cacheprovider
    $(sed -e '/^#/d' -e '/^$/d' -e 's/ raises=.*$//' spec/env-config-keys/red.txt)
    && UV_PROJECT_ENVIRONMENT=$TMPDIR/venv311 uv run --python 3.11 pytest -q -p no:cacheprovider
    tests/test_config_schema.py tests/test_env_config.py tests/test_env_duration.py
    tests/test_env_export.py tests/test_env_blame.py && for m in keys typed durations
    serve export which; do .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/env-config-keys/drive/env_drive.sh"
    $m || exit 1; done && RED_BASE_REV=90ab2fc2 RED_LIST="$PWD/spec/env-config-keys/red.txt"
    sh spec/relay-hardening/drive/red_check.sh && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    $TMPDIR/docs-html-eck
review:
  last_reviewed_commit: cb5e122b7c6c7e7471d30a98a2ff4771dff14d0b
  verdict: approved
  blocked_reason: ''
  cycle: 3
---
# TECH.md — Environment variables reach every configuration key with the value as written; the remaining duration keys take units

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/env-config-keys/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its `verify:`
command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md) (section numbers below are PLAN's).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs `01`–`05`.
- **Red list:** [`red.txt`](red.txt) (62 nodes, all red on `90ab2fc2`); **CLI drive:**
  [`drive/env_drive.sh`](drive/env_drive.sh).

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done`
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`).
- `depends_on`: phase ids that must be `done` first.
- `parallel`: every phase here is `false` (each touches `core/config.py` or the coupled core).
- `hammerable`: every phase is `false` (configuration correctness and the key's handling).
- `hill`: `uphill` → `crest` → `downhill`; a phase stuck `uphill` across builds is a raised hand.
- `attempts`: durable failed-verify counter (`set_phase.py --phase P<n> --record-attempt`).
- `verify`: the exact command that proves the phase.
- `review.cycle`: completed review passes.

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; the gate is
  [`invariants.md`](../../.agents/factory/invariants.md) §9–§12 (PLAN §3).
- One phase per `hs-build` invocation; one atomic commit with the code and this file's state. Subjects
  `[fix] Build env-config-keys P<n>: …`. **No `Co-Authored-By` trailer.**
- A user-visible change updates its prose page in the same commit with `.. versionchanged:: 2.9.0`
  (inside `.. only:: html` in `config_param_ref.rst`, which the man page also includes); P6 completes
  the guide, but P2–P5 each update what they change.
- Tests are tagged `@mark.unit` or `@mark.integration`. Tests that need the configuration built from
  variables run a child interpreter or the `hs` CLI with the variables set (the configuration is built
  at import), inherit `PYTHONPATH` (so `red_check.sh` exercises the base `src`), and set
  `HYPERSHELL_CONFIG_FILE=` and a `temp_site`.
- Comments state the rule, never a spec id (`R#`, `S#`).
- Use `UV_CACHE_DIR=$TMPDIR/uv-cache-eck` (the shared `$TMPDIR/uv-cache` had a broken
  `trove-classifiers` entry during planning). The docs build runs with the sandbox off.
- Docs and messages name only the top-level commands (`hs list`, `hs info`, `hs wait`, `hs run`,
  `hs update`); a key is given in `HYPERSHELL_SERVER_AUTH`, never on a command line.

---

## Phase P1 — Tests first (tests only)
**Satisfies:** R1–R8 · **Depends on:** —
**Goal:** every test of [`red.txt`](red.txt) in the tree, red on `90ab2fc2` by an assertion about
the defect, and every other test in the touched files still green; no `src/`, `docs/` or `share/`
change.

- [x] `tests/test_env_config.py` with helpers `config_repr(env, expr)` (a child interpreter printing
      `repr(<expr>)` over `hypershell.core.config.config`; returns `(rc, stdout, stderr)`; every test
      asserts `rc` first so a crash is an assertion, never an exception) and `hs_lines(env, argv)`.
      Cases as `red.txt` lists them (PLAN §6), with explicit `ids=` so node ids have no spaces:
      underscore keys (each of the seven keys set to a distinctive value), any letter case,
      `relay.launcher` kept beside `launcher_args`, `hs config get`, `server.port_x` held whole with
      `server.port` an `int`; open-table entries, `database.ssl_mode`, the zone key, the two zone
      refusals (exit 3, both variable names in stderr); `server.auth_env`/`relay.upstream.auth_eval`
      through `hs config get`, `database.password_env` through `config.database.password`, two forms
      refused; text kept (`false`, `true`, `''`, `null`, `0012345678901234`, `1234567890123456e5`);
      `test_all_digit_key_server_and_client` and `test_all_digit_key_submit_queue` (the
      `test_auth_variants.py` server/client pattern, key `0012345678901234` in
      `HYPERSHELL_SERVER_AUTH`); boolean words (`no`, `OFF` → `False`, `Yes`, `1` → `True`, asserted
      with `is`); refusals (`server.port=abc`, `server.eager=maybe`, `relay.max=1.0`,
      `autoscale.factor=inf`, `server.tls.enabled=`) exit 3 with the variable and a form word in
      stderr; `logging.file=Enabled` reads as enabled (`True`), `logging.file=false` exits 3 naming the
      variable; the untyped rule (`DATABASE_CONNECT_ARGS_TIMEOUT=5` → `5`,
      `DATABASE_PRAGMAS_X=007` → `'007'`, `DATABASE_PRAGMAS_Y=true` → `True`).
- [x] The same file's **must-keep** tests (green before and after; not in `red.txt`): PLAN §6's list,
      including `RELAY_ZONE=gpu` with `RELAY_ZONE_GPU_SIZE=2`, `TASK_MEMORY=16G`, `SERVER_EVICT=10`
      (an `int`), the #106 keys with `7`/`0.5`/units, `LOGGING_FILE=enabled` + `LOGGING_FILE_LEVEL`.
      *As built:* `test_kept[...]`; `RELAY_ZONE=gpu` and `RELAY_ZONE_GPU_SIZE=2` are two cases, since
      the two together are the refusal R2 asks for; `DATABASE_PORT` is left out (SQLite refuses a
      port at import, so it cannot be a child-interpreter case).
- [x] `tests/test_env_duration.py`: `test_server_evict_units[file|environment]` (`evict = "10m"` /
      `HYPERSHELL_SERVER_EVICT=10m`; `hs server --no-db` with a one-task file and a key reaches
      "Server listening" and finishes with a client); `test_duration_refused_at_start[…]` for
      `server.evict` (`hs server`), `client.heartrate` (`hs client`), `relay.upstream.deadline` and
      `relay.poll` (env) and `relay.upstream.evict_hint` (file) under `hs relay` with a free
      `HYPERSHELL_RELAY_PORT`: exit 3 within a bound, stderr naming the key, the variable or file, and
      `Duration`. A process still running at the bound is stopped (SIGTERM, then kill) and the test
      fails with `pytest.fail`, so the base's never-ending relay is red, not an error.
- [x] `tests/test_env_export.py`: `test_export_as_written[leading-zeros|mixed-case|empty|exponent]`
      (child: `dict(load_task_env())`), `test_export_reaches_task` (`hs cluster --no-db -N1`, a task
      printing the variables), `test_key_variable_forms_not_inherited` (child: a key variable set;
      not in `client.inherited_task_env()`), `test_key_variable_forms_not_exported` (not in
      `cluster.launch.ssh_env_exports(os.environ)`); both renamed in the review-cycle-1 remediation;
      must-keep: `HYPERSHELL_EXPORT_FOO` over a file's `foo`, a file's list joined and upper-cased.
- [x] `tests/test_env_blame.py`: `test_blame_names_variable` (`blame(config, 'server', 'tls',
      'min_version')` under `HYPERSHELL_server_tls_min_version`), `test_which_names_variable[…]`,
      `test_completion_offers_key_paths`, `test_completion_one_per_line`,
      `test_process_variables_not_keys` (`site`, `config`, `pythonpath`, `zone` not in `config`, nor
      in the completion list).
- [x] Reconcile [`red.txt`](red.txt) with the node ids as written (renaming is fine; dropping a line
      needs a note in PLAN §5).
- [x] Record the red: the `red_check.sh` output (every line RED) goes in the commit body.
- **Verify:** frontmatter (no `src`/`.github`/`share`/`docs` change; ≥62 listed; all RED on
  `90ab2fc2`; the rest of the touched files green with the red list deselected).
- **Touches:** `tests/test_env_*.py`, `spec/env-config-keys/red.txt`.

## Phase P2 — The key table, the matcher and the typed environment layer
**Satisfies:** R1, R2, R3, R4, R5 · **Depends on:** P1
**Goal:** every `HYPERSHELL_*` name reaches its key by PLAN §2.2 with the value read by its kind
(§2.3); `hs config get` shows it; the reference and the table cannot drift.

- [x] `src/hypershell/core/config_schema.py` (PLAN §2.1): `Kind`, `KEYS`, `TABLES`, `ZONE_KEYS`,
      `PROCESS_VARIABLES`, `PROSE_KEYS`, the kind readers (§2.3) and
      `read_environment(environ) -> (Namespace, variables)` (§2.2, steps 1–4).
- [x] `core/config.py`: `reload_env`/`load_env` return the `Namespace` and keep `ENV_VARIABLES`
      (§2.4); `is_key_variable(name)` (§2.5, used in P3); `__all__` updated.
- [x] Empty text as unset where `None` was (§2.9): `core/tls.py` `resolve_auto_paths`, `core/logging.py`
      (`logging.file.path`/`compress`), `data/core.py` (user/password), `task.cwd`.
- [x] `cluster/remote.py` `configured_failed_launches` reads `('autoscale', 'failed_launches')` (§2.10);
      reshape `tests/test_autoscale_failed_launches.py::test_refused_from_environment` (loader refusals
      for `true`/`abc`/`1.5`, reader refusal for `-1`), keeping its pinned message text where it holds.
- [x] `tests/test_config_schema.py` (§2.1 sync test; parse rules for every kind, including the
      untyped list in §2.3).
- [x] `docs/_include/config_param_ref.rst`: `logging.file`'s words; nothing else yet (P6).
- *As built:* `SCALAR_TABLES` (`logging.file`, `ssh.nodelist`, `database`: each a key and a table)
  is split from `PROSE_KEYS` (`logging.file.format` and the timescale keys, named in their table's
  text), so the sync test checks each against what the reference holds; the undocumented
  `database` scalar needs no prose. The refusal from a typed variable is
  `<key>: expected <form>, given <text> (from: <VARIABLE>)` (forms: `a whole number`, `a number`,
  `a boolean (true/false, yes/no, on/off, 1/0)`, `a whole number or <none>`, `true, enabled, or a
  path`). `test_variant_database_password_env` reads the configuration without the package's own
  import (SQLite takes no password); it is still red on `90ab2fc2`.
  `test_cluster_launch.py::test_client_zone_never_comes_from_configuration` pinned `config.zone`
  from `HYPERSHELL_ZONE`; it now asserts the zone variable is not a key. The drive's `typed` mode
  sets `HYPERSHELL_PYTHONPATH` to the site directory, since `core/sys.py` refuses a path that does
  not exist (unchanged here), which `/nowhere` was. An empty `database.user`/`password` is unset in
  `DatabaseURL` whatever the layer, so a file's `password = ""` beside a user is refused too.
- **Verify:** frontmatter (the new and touched tests; drive modes `keys` and `typed`).
- **Touches:** `src/hypershell/core/config_schema.py`, `src/hypershell/core/config.py`,
  `src/hypershell/core/tls.py`, `src/hypershell/core/logging.py`, `src/hypershell/data/core.py`,
  `src/hypershell/cluster/remote.py`, `tests/`.

## Phase P3 — `[export]` as written, and key variables recognised by the matcher
**Satisfies:** R7, R4, R1 · **Depends on:** P2
**Goal:** a task sees `HYPERSHELL_EXPORT_<NAME>` by its name and value as written; no key variable,
as the matcher recognises it, reaches a task or a remote command line.

- [x] `core/config.py` `load_task_env` per layer (PLAN §2.7).
- [x] `client.py` `inherited_task_env` and the no-key hint, `cluster/launch.py` `ssh_env_exports` and
      `assigns_key`: `is_key_variable` in place of `== AUTH_ENV` (§2.5); the Open MPI list entry the
      same way.
- [x] `docs/_include/config_task_env.rst` and `_alt`: names and values as written from the
      environment; a file's table upper-cased; the same-name rule (`versionchanged:: 2.9.0` in the
      html page).
- *As built:* `load_task_env` returns a plain `dict`; `ompi_env_list` is unchanged, since the launch
  environment it names holds `AUTH_ENV` exactly as `client_launch` sets it. The task in
  `test_export_reaches_task` and the drive's `export` mode calls `printenv` once per name: BSD
  `printenv` takes one name, so `printenv RUN_ID MyVar` printed only the first on macOS (the test
  is still red on `90ab2fc2`).
- **Verify:** frontmatter (export tests, launch and relay-launch contract tests; drive `export`).
- **Touches:** `src/hypershell/core/config.py`, `src/hypershell/client.py`,
  `src/hypershell/cluster/launch.py`, `docs/_include/config_task_env*.rst`, `tests/`.

## Phase P4 — `blame`, `hs config which` and completion
**Satisfies:** R8 · **Depends on:** P2
**Goal:** every key a variable sets is named by that variable; completion offers real dotted paths,
one per line, never a process variable.

- [x] `core/config.py` `blame` and `ACTIVE_CONFIG_VARS` (PLAN §2.8); `src/hypershell/config.py`
      `ConfigWhichApp` uses the map; `--list-available` prints one per line.
- [x] `tests/test_completions.py`: bash and zsh consume the list one key per candidate (change
      `share/` only if this shows it must; keep the CI `share/` list in step if a path changes).
- [x] Keep the pinned `which`/blame texts (`test_initdb.py:25`, `test_topology_server.py:361`,
      `test_autoscale_failed_launches.py:287`).
- *As built:* `env_variable(*varpath)` in `core/config.py` names the variable from `ENV_VARIABLES`
  (for a table, the first under it; the guide's rule only for a path no variable set); `blame` and
  `hs config which` both use it. `share/` is unchanged: the new `test_completions.py` check shows
  zsh already splits the list on newlines and bash gives it to `compgen -W`. The drive's `which`
  mode matches completion words with `grep -qxF`: as a pattern, `server.tls.min.version` matched
  `server.tls.min_version`.
- **Verify:** frontmatter (blame, completion, pinned texts; drive `which`).
- **Touches:** `src/hypershell/core/config.py`, `src/hypershell/config.py`, `share/` (if needed),
  `tests/`.

## Phase P5 — The ten duration keys
**Satisfies:** R6 · **Depends on:** P2
**Goal:** each of R6's keys takes every duration form from a file or the environment, and a bad
value stops the reading command at start (exit 3, naming key and source) before TLS material, a lock,
a bind or a launch.

- [x] `core/config.py` `config_seconds(*varpath, base=config, whole=False)` (PLAN §2.6).
- [x] `ServerApp.check_args` (`server.evict`); `ClientApp.check_args` (`client.heartrate`);
      `RelayApp.check_args` beside `site_period` (`server.evict` and the eight `relay.*` keys,
      `relay.timeout` rounded up); `RelayApp.run` passes the converted values.
- [x] Relay import-time defaults read `default.` (`relay/__init__.py:98-116`, `relay/upstream.py`,
      `relay/site.py:92`); `-w`/`-T` for site clients through `plain_seconds`.
- [x] `docs/_include/config_param_ref.rst`: the ten entries give the forms (adapt
      `server.tls.handshake`'s sentence; "rounded up" for `relay.timeout`, fractions kept for the
      rest), each with `versionchanged:: 2.9.0`.
- [x] `tests/test_config_schema.py`: each of the ten keys' reference entries gives the duration forms.
- *As built:* `config_seconds` takes `base=None` (the module's `config`, which is built after the
  function). An `_env` form naming an unset variable is refused as `resolve_auth` refuses it
  (`<key>_env names <VAR>, which is not set (from: …)`), and a form that fails to resolve is named by
  its error type only. `ServerApp.evict_after` and `ClientApp.heartrate` hold the seconds (class
  defaults `DEFAULT_EVICT`/`DEFAULT_HEARTRATE`); the relay's are `RelayApp.durations`, a cached
  property over `RELAY_DURATIONS` read in `check_args` as `site_period` is. Only the duration
  constants of `relay/__init__.py:98-116` read `default.` (`bundlesize`, `size` stay typed keys).
  `client_options` writes `-w` and `-T` with `plain_seconds` for every launch, which changes no
  word `str()` wrote without an exponent. `test_duration_refused_at_start[client.heartrate]` and the
  drive's `durations` mode ran `hs client` with no argument, which prints the usage and exits 1
  before any check (`ALLOW_NOARGS` is not set); both now give `-N 1`, and the case is still red on
  `90ab2fc2`. `tests/test_env_duration.py` gains unit tests of `config_seconds` (numbers as given,
  each form, rounding, refusals naming the variable, the `_env` form), importing `config_seconds` inside
  each test so the file still collects on `90ab2fc2` (its seven red-list nodes: 7 of 7 RED there).
- **Verify:** frontmatter (duration tests, #106's tests, relay and launch suites; drives `durations`
  and `serve`).
- **Touches:** `src/hypershell/core/config.py`, `src/hypershell/server.py`, `src/hypershell/client.py`,
  `src/hypershell/relay/`, `src/hypershell/cluster/launch.py`, `docs/_include/config_param_ref.rst`,
  `tests/`.

## Phase P6 — Docs, AGENTS.md and the invariants
**Satisfies:** R9 · **Depends on:** P3, P4, P5
**Goal:** the guide states the mapping once (PLAN §2.11) and nothing claims a key is reachable when it
is not.

- [x] `docs/_include/config_intro.rst`: the `config_env` section (rule, underscore keys, open tables,
      zones, `_ENV`/`_EVAL`, typing, process variables, unknown names, a scalar beside its table, two
      variables for one key, a name equal to a table, what stays unreachable), `versionchanged:: 2.9.0`.
- [x] `config_intro_alt.rst` and `manual.rst` § Environment Variables: the same in short, no stronger
      claim; `config_site_vars.rst` if it needs the process-variable list.
- [x] `config_param_ref.rst`: the `[server.tls]` and other "also exposed as an environment variable"
      lines hold; timescale and zone keys as prose or entries (the sync test follows).
- [x] `AGENTS.md` § Configuration and `invariants.md` §10: the environment layer is read through
      `core/config_schema.py`; a new documented key gets its kind there and its reference entry in the
      same commit.
- *As built:* the `config_env` section closes `config_intro.rst` as a `---` section of the
  Configuration page, after a transition; the page's `hs config edit` paragraph moved from
  `config.rst` into the include ahead of it, so it stays in the page's introduction rather than
  falling under the new section. Both introductions say `_ENV`/`_EVAL` variables expand as the file
  forms do. `manual.rst` gives the rule in short and names `HYPERSHELL_ZONE` beside the process
  variables it includes; `config_site_vars.rst` is unchanged. `[server.tls]`'s environment line
  links the section. AGENTS.md also names `is_key_variable()` where it describes the key variable's
  stripping (§ Queue transport and security) and invariants §11 likewise.
- **Verify:** frontmatter (sync test, docs greps, `sphinx-build -W`).
- **Touches:** `docs/`, `AGENTS.md`, `.agents/factory/invariants.md`.

## Phase P7 — Regression sweep
**Satisfies:** R1–R9 · **Depends on:** P6
**Goal:** the whole suite green, every red-list node green, the red check still red on the base,
Python 3.11, every drive mode, the docs build.

- [x] Run the verify; record timings and any flake (re-run once, name it in the commit body).
- [x] Draft notes for `hs-publish`: the GOAL's nine user-visible changes, with PLAN S4 under change 4
      and S7 under change 5.
- *As built:* the verify's first command, `pytest -n auto`, fails at collection on this suite, so the
  full suite ran serially (`uv run pytest -q -p no:cacheprovider`) in its place; every other part ran
  as written, `$TMPDIR` being `/tmp/claude-501` with the sandbox off (the docs build needs it). On
  `4f89306b` + this file: full suite 3245 passed, 116 skipped (24m05s); red list 62 passed (30s);
  Python 3.11.18 on the five new test files 215 passed (33s); drive modes `keys`, `typed`,
  `durations`, `serve`, `export`, `which` all ok (36 checks); `red_check.sh` on `90ab2fc2` 62 of 62
  RED (75s); `sphinx-build -W` clean. No flake; `test_upgrade_cli.py::test_waits_for_slow_peer_upgrade`
  passed in the full run.

### Release notes (draft for `hs-publish`)

Changes from 2.8.x (and `develop` `90ab2fc2`):

1. A variable for a key whose name has an underscore now sets that key
   (`HYPERSHELL_SERVER_TLS_MIN_VERSION`, `HYPERSHELL_RELAY_LAUNCHER_ARGS`,
   `HYPERSHELL_DATABASE_TIMESCALE_CHUNK_INTERVAL`, `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES`, …), in any
   letter case after the prefix. Previously it set a nested key no reader used, so the setting was
   silently ignored (for `database.timescale.*`, refused as unknown; for `relay.launcher_args`, it
   also replaced `relay.launcher`).
2. `HYPERSHELL_<KEY>_ENV` and `_EVAL` work as `_env`/`_eval` do in a file, including the refusal of two
   forms of one key. Previously they replaced the key with a table.
3. Values are no longer coerced by their look. A key that takes text keeps `false`, `true`, `null`, an
   empty value and number-like text (`007`, `1e3`, an all-digit `server.auth`) as written. An empty
   value for an optional text key (no default, or one that means none: `logging.file`,
   `database.host`, the `[server.tls]` files, …) or for `server.bind`/`relay.bind` is not given, so
   the key's default applies (an empty bind is the default bind).
4. A key that takes a whole number, a number or a boolean converts its value or stops the command at
   start with a configuration error (exit 3) naming the variable; booleans take `true`/`false`,
   `yes`/`no`, `on`/`off`, `1`/`0` in any case (`no` and `off` were read as true). An empty value is
   refused too: `HYPERSHELL_SERVER_TLS_ENABLED=` no longer turns TLS off. Such a variable stops every
   command, `hs config` included, even one that does not read the key.
5. `HYPERSHELL_EXPORT_<NAME>` reaches tasks under `<NAME>` as written, value unchanged (previously
   upper-cased, and `007` → `7`, empty → `null`, `1e3` → `1000.0`). The environment wins over a file's
   `[export]` entry for the same task variable, compared by exact name: `HYPERSHELL_EXPORT_foo` beside
   a file's `foo` gives tasks both `foo` and `FOO`.
6. `server.evict`, `client.heartrate`, `relay.bundlewait`, `relay.poll`, `relay.retention`,
   `relay.timeout` (rounded up), `relay.upstream.heartrate`, `.evict_hint`, `.deadline` and `.backoff`
   take units and Slurm forms in a file or the environment; a bad value stops `hs server`,
   `hs client` or `hs relay` at start (exit 3) naming the key and where it came from. Previously a
   unit value crashed the reader with a traceback (`relay.timeout` was refused by each site client).
7. A `HYPERSHELL_RELAY_ZONE_*` variable that names no zone key, or one set together with
   `HYPERSHELL_RELAY_ZONE`, is a configuration error.
8. `hs config` completion and `hs config which` name keys by their real path and the variable that
   set them, one key per completion line, and no longer offer `site` or `config.file`.
9. An unknown `HYPERSHELL_*` name past a known table, and a `[database]` provider argument from the
   environment, are held as one key with underscores kept (`HYPERSHELL_DATABASE_SSL_MODE` →
   `database.ssl_mode`). Two variables that set one key (e.g. in two letter cases) are refused.
- **Verify:** frontmatter.
- **Touches:** `spec/env-config-keys/TECH.md` only (state), unless the sweep finds a defect.

## Review-cycle-1 remediation (2026-11-16)

`REVIEW.md` cycle 1 (changes-requested on `f495fa20`) and the maintainer's rulings at the end of
`PLAN.md` ("Review-cycle-1 rulings"). P2, P3, P6 and P7 reopened; P3 renamed; P6's and P7's gates
retuned (P6 greps the matcher wording and the empty-text rule; P7 runs the suite serially, since
`pytest -n auto` fails at collection here). `origin/develop` (`91c5aa9a`, #112) merged first.

- [x] **#112 merge** — `tests/test_period_floor.py::test_period_key_refused`: the eight `env-nan` and
      `env-neginf` cases (`server-file`, `server-autoscaling`, `cluster-file`, `relay`) expect the
      refusal as not a duration (`-P/--period: Duration 'nan' is not valid`, `autoscale.period: …`
      for the relay), still `bad_argument` with nothing served or launched; the `file-nan` and
      `file-neginf` cases keep the "greater than zero" refusal of a TOML float.
- [x] **F1** — `cluster/launch.py` `ClientLaunch.start`: every key variable
      (`is_key_variable`) is left out of the inherited and task environments before the launch's own
      `env` (the run key) is applied; with `inherit_env` (`--ssh`), ssh gets the caller's environment
      without them. One place for launcher, `--mpi`, autoscaling and relay site launches.
      Tests: `test_cluster_launch.py::test_key_in_any_form_reaches_launched_clients[mode-form]`
      (`launcher`, `mpi`, `autoscaling` × `_ENV`, `_EVAL`, another spelling: each client holds the
      run key in `HYPERSHELL_SERVER_AUTH` alone and every task runs), and by construction
      `test_client_launch_gives_only_its_own_key[launcher|ssh]` and
      `test_ssh_launch_exports_no_key_variable[…]`.
- [x] **Key handling by the matcher** — `core/config.py`: `is_key_variable(name, sections=AUTH_SECTIONS)`
      recognises every form (`AUTH_FORMS`) of `server.auth` and `relay.upstream.auth` as the matcher
      reads the name; `is_relay_variable(name)` every name it maps under `relay`. `client.py`
      (`inherited_task_env`, the no-key hint narrowed to `server.auth`), `cluster/launch.py`
      (`ssh_env_exports`, `assigns_key`, `ClientLaunch.start`) and `relay/site.py` (`is_fenced`, used
      by `scrub_environment`) use them. Tests: `test_env_export.py::test_key_variable_forms_not_inherited`
      and `test_key_variable_forms_not_exported` (renamed; six forms each),
      `test_relay_launch.py::test_relay_fence_by_matcher`.
- [x] **F2** — `core/config_schema.py` `EMPTY_UNSET`: an empty variable for an optional text key (no
      default, or `<none>`/`<auto>`), a scalar table (`logging.file`, `ssh.nodelist`, `database`) or a
      bind (`server.bind`, `relay.bind`) sets nothing, so the default applies; any other text key keeps
      `''` (R4: `logging.datefmt`). The readers' own `''` checks added in P2 (`core/tls.py`,
      `core/logging.py`) are back as on `develop`, so a file reads as before. Guide (`config_intro.rst`,
      its 2.9.0 marker) and `manual.rst` say so. Tests: `test_env_config.py::test_empty_optional_text_not_given[…]`,
      `test_empty_bind_is_default_bind`, `test_empty_optional_text_runs[…]` (`hs list` with an empty
      `HYPERSHELL_LOGGING_FILE`, `_DATABASE_HOST`, `_DATABASE_DATABASE` on SQLite: exit 0, no
      traceback, no log file); `test_config_schema.py::test_empty_unset_keys` (the list against the
      defaults) and `test_read_environment_empty_unset`.
- [x] **F3** — `data/core.py` `DatabaseURL` as on `develop` (no `or None`): a file's empty
      `database.user`/`password` reads as before; the environment's empty value is not given (F2).
      Tests: `test_empty_password_in_file_as_before` (URL `postgresql+psycopg://localhost/d`,
      `hs config get` exit 0), `test_empty_password_from_environment_not_given` (exit 3, as before).
- [x] **F4** — `issues/config-keys-unread.md` cites `cluster/remote.py:949`,
      `issues/config-set-coerces-text.md` cites `core/config.py:458`; both say the line numbers are
      as of this branch (the other citations hold).
- [x] **Wording** — docstrings, `AGENTS.md`, `invariants.md` §10–§11 and `docs/security.rst` (with a
      2.9.0 marker) say key variables and relay variables are recognised by the matcher wherever
      HyperShell handles them by name; `red.txt` names the renamed tests (six forms each, 72 nodes).
- [x] Red then green: the 23 new nodes for F1–F3 are red on `f495fa20` with this branch's tests
      (`RED_BASE_REV=f495fa20 red_check.sh`, 23 of 23 RED), green here.
- Gates (on `092c80c0` + this work): P2 1389 passed, 1 skipped, drive modes `keys`, `typed` ok; P3 381
  passed, 1 skipped, drive `export` ok; P6 123 passed, greps and `sphinx-build -W` clean; P7 (sandbox
  off, 28m10s) full suite serially 3479 passed, 138 skipped, red list 72 passed, Python 3.11 on the
  five new test files 240 passed, every drive mode ok, `red_check.sh` on `90ab2fc2` 72 of 72 RED,
  docs clean. `test_upgrade_cli.py::test_waits_for_slow_peer_upgrade` passed in the full run; no
  flake. CLI drive in `temp_site.sh` (`hs cluster … --no-db --no-tls -N1 -H 127.0.0.1`, two tasks):
  `HYPERSHELL_SERVER_AUTH_ENV`, `_EVAL` and another spelling under `--launcher=env` and `--mpi` (an
  `mpirun` stand-in that runs its arguments) each print both tasks' output, exit 0.

## Review-cycle-2 remediation (2026-11-21)

`REVIEW.md` cycle 2 (changes-requested on `085538c8`, N1–N3) and the maintainer's rulings at the end
of `PLAN.md` ("Review-cycle-2 rulings"). P2, P6 and P7 reopened; P6's gate also greps `EMPTY_UNSET`
in `AGENTS.md` and `invariants.md`.

- [x] **N2** — `client.py`: `task_env` gives `TASK_CWD` as `config.task.cwd` and `TaskExecutor`
      starts a task with `cwd=config.task.cwd`, as on `develop`, so a file's `cwd = ""` reads as
      before. `core/config_schema.py`: `task.cwd` is in `EMPTY_UNSET`, so an empty
      `HYPERSHELL_TASK_CWD` is not given and tasks run in the client's own directory. Tests:
      `test_env_config.py::test_empty_task_cwd_in_file_as_before` (`TASK_CWD` is `''`; `hs cluster`
      stops on `FileNotFoundError` for `''`, no task output), `test_empty_task_cwd_from_environment_not_given`
      (`TASK_CWD` as with no variable; `hs cluster` runs the task in the site), and
      `test_empty_optional_text_not_given[task.cwd]`.
- [x] **N1** — `core/config_schema.py`: `database.timescale.chunk_interval`, `segmentby`, `orderby`
      and `compress_after` leave `EMPTY_UNSET`; the environment holds their empty text as a file does.
      `HYPERSHELL_DATABASE_TIMESCALE_SEGMENTBY=` gives `segmentby = ""` (no segmenting), alone and over
      a file's `segmentby = "id"`; an empty `chunk_interval`, `orderby` or `compress_after` is refused
      by `Settings.from_config` with the file's message. `tests/test_config_schema.py`:
      `documented_default` reads a literal default from the parameter reference (the key's
      `(default: …)`, or a timescale key's `(default …)`/`(…)` in its table's text);
      `test_empty_unset_keys` takes as optional a text key with no default, or one that means none, in
      both the defaults table and the reference, plus the scalar tables and `EMPTY_IS_DEFAULT`
      (`server.bind`, `relay.bind`, `database.host`, `task.cwd`: a default of their own, which an empty
      variable gives, as the F2 and N2 rulings name). `test_documented_default` pins the reader.
      Tests in `test_env_config.py`: `test_empty_segmentby_from_environment_no_segmenting[alone|over-file]`,
      `test_empty_timescale_setting_as_in_file[chunk_interval|orderby|compress_after]`;
      `test_read_environment_empty_unset` covers `segmentby` and `task.cwd`.
- [x] **Every other `EMPTY_UNSET` member checked** against the rule: none moved. `database.host` has a
      documented default (`localhost`) and stays, by the F2 ruling, beside the binds and `task.cwd`
      (an empty host is the default host). The rest have no literal default in either place
      (`<none>`, `<auto>`, or one given in words: `none`, `by .bind`, a path pattern, "the local
      account").
- [x] **N3** — `AGENTS.md` § Configuration: an empty variable for a key of `EMPTY_UNSET` is not
      given; every other text key holds an empty variable as written, as it holds a file's `""`.
- [x] **Guide** — `config_intro.rst` names `database.host` and `task.cwd` with the binds (an empty
      bind, host or task directory is the default one) and gives
      `HYPERSHELL_DATABASE_TIMESCALE_SEGMENTBY=` as a text key with a default of its own holding
      `""`; `manual.rst` the same in short.
- [x] **Wording** — `PLAN.md` (§2.5, S6, the deviation table, the review-cycle-1 wording note),
      `research/00-digest.md` D9 and `research/05` §2: key variables and relay variables are
      recognised by the matcher wherever HyperShell handles them by name. The deviation table's
      empty-text row and an as-built note in PLAN §2.9 give the remediation as built;
      `research/02` states what the base did with `HYPERSHELL_ZONE` as a finding.
- [x] Red then green: on `085538c8`'s source (`RED_BASE_REV=085538c8 red_check.sh` with this tree's
      tests) 9 of the 10 new or changed nodes are RED by assertion; the tenth,
      `test_empty_task_cwd_from_environment_not_given`, is a must-keep guard (its outcome held on
      `085538c8` through the old reader) and fails with `task.cwd` taken out of `EMPTY_UNSET` in a
      scratch copy. All 10 green here.
- Gates (on `fe7447cc` + this work, sandbox off): P2 1398 passed, 1 skipped (5m41s), drive modes
  `keys`, `typed` ok; P6 124 passed, greps and `sphinx-build -W` clean; P7 (30m53s) full suite
  serially 3488 passed, 138 skipped, red list 72 passed, Python 3.11 on the five new test files 249
  passed, every drive mode ok, `red_check.sh` on `90ab2fc2` 72 of 72 RED, docs clean.
  `test_upgrade_cli.py::test_waits_for_slow_peer_upgrade` passed in the full run; no flake. CLI
  drive in `temp_site.sh` (`hs cluster tasks.in --no-db --no-tls -N1 -H 127.0.0.1`): with
  `HYPERSHELL_TASK_CWD=` both tasks run in the site (`cat marker.txt` prints `here`), exit 0; with a
  file's `cwd = ""` the client stops on `FileNotFoundError` for `''`, as on `develop`.
  `HYPERSHELL_DATABASE_TIMESCALE_SEGMENTBY=` gives `Settings.segmentby == ''`; an empty `_ORDERBY`
  is refused naming `database.timescale.orderby`.
- Review cycle 3, G1 (maintainer ruling 2026-11-21): `test_env_duration.py::test_relay_passes_duration_seconds[file|environment]` pins the seconds `RelayThread` receives for the nine relay duration keys given in units; with `evict` and `bundlewait` passed as read in a scratch copy both nodes fail (`'10m' != 600`, `'1.5s' != 1.5`), green on this tree.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative).
2. Pre-flight: clean tree, on `fix/env-config-keys`, `develop` reachable.
3. Execute every `[ ]` in the phase (PLAN and `research/` for detail).
4. Run the phase's `verify:` — never advance on a checkbox alone.
5. Amend this file if reality diverges (note it in the commit body); STOP on a `GOAL.md` contradiction.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.

## Dry run (hs-plan, 2026-11-15, on `90ab2fc2` + the plan commit)

- `red_check.sh` with this `red.txt`: runs, 0 of 62 red (the tests do not exist yet; pytest exit 4).
  Its mechanics were checked with a passing test (`NOT RED … pytest exit 0`).
- `rest_green.sh tests/test_auth_variants.py -k resolve` with a one-line list: 1 deselected, 20 passed.
- P1's path checks: `src`, `.github`, `share`, `docs` unchanged; only `spec`, `tests`, `issues`,
  `ROADMAP.md` differ from the base.
- `env_drive.sh`, every mode, in a throwaway site: each fails at its first new-behaviour check, as
  expected (`keys`: `server.tls.min_version` stays `TLSv1.2`; `typed`: `SERVER_PORT=abc` exits 0;
  `durations`: `hs server` exits 5 with `TypeError` after generating TLS material; `serve`: the
  all-digit key is refused as short; `export`: the task sees `7` and no `MyVar`; `which`: names the
  default).
- Full suite collection: 3145 tests. Python 3.11 environment builds (`Python 3.11.18`). Docs build
  with `-W`: clean.
- `next_phase.py`: reports P1.
