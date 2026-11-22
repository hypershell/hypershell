---
slug: update-client-retry
title: hs update --delete refuses changes it would ignore, hs client reads its own
  bundle keys, and a task that never ran is not retried
kind: fix
appetite: big
status: in_progress
branch: fix/update-client-retry
base: develop
current_phase: P2
last_updated: '2026-11-22'
phases:
- id: P1
  name: 'Tests first: the red list on 1f688c7c (tests only)'
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
  verify: git diff --quiet 1f688c7c -- src docs share AGENTS.md .agents && test -z
    "$(git diff --name-only 1f688c7c -- . ':!spec' ':!tests' ':!issues' ':!ROADMAP.md')"
    && test -n "$HS_TEST_POSTGRES" && test "$(grep -cvE '^#|^$' spec/update-client-retry/red.txt)"
    -eq 79 && RED_BASE_REV=1f688c7c RED_LIST="$PWD/spec/update-client-retry/red.txt"
    sh spec/update-client-retry/drive/bound.sh 1200 sh spec/relay-hardening/drive/red_check.sh
    && RED_LIST="$PWD/spec/update-client-retry/red.txt" sh spec/update-client-retry/drive/bound.sh
    2400 sh spec/contended-writes/drive/rest_green.sh tests/test_update_refusals.py
    tests/test_client_bundle_keys.py tests/test_retry_terminal.py tests/test_retry_declarations.py
    tests/test_retry_defaults.py tests/test_client_request.py tests/test_duration_options.py
    tests/test_dispatch_workload.py tests/test_update.py tests/test_retry_gate.py
    tests/test_groups.py tests/test_zone_model.py tests/test_zone_postgres.py tests/test_client.py
    tests/test_finish_wake.py tests/test_restart.py
- id: P2
  name: hs update refuses --delete with a change and the single-task form with an
    action option (R1, R2, their docs)
  status: pending
  satisfies:
  - R1
  - R2
  - R9
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'sh spec/update-client-retry/drive/bound.sh 900 uv run pytest -q -p no:cacheprovider
    tests/test_update_refusals.py tests/test_update.py tests/test_main.py && sh spec/update-client-retry/drive/bound.sh
    300 .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/update-client-retry/drive/ucr_drive.sh"
    update && for f in docs/_include/task_update_desc.rst docs/_include/task_update_help.rst
    src/hypershell/task.py; do grep -q ''takes no change'' $f && grep -q ''single-task
    form takes no'' $f || exit 1; done && test "$(grep -c ''versionchanged:: 2.9.0''
    docs/cli/task_update.rst)" -ge 8 && ! grep -rn ''hs task '' docs/_include/task_update_desc.rst
    docs/_include/task_update_help.rst docs/cli/task_update.rst && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-ucr
    sh spec/update-client-retry/drive/bound.sh 900 uv run --python 3.12 --group docs
    sphinx-build -W --keep-going -q -E -b html docs /tmp/claude-501/docs-html-ucr'
- id: P3
  name: hs client reads client.bundlesize and client.bundlewait, not submit.* (R3,
    R4, their docs)
  status: pending
  satisfies:
  - R3
  - R4
  - R9
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'sh spec/update-client-retry/drive/bound.sh 1200 uv run pytest -q -p no:cacheprovider
    tests/test_client_bundle_keys.py tests/test_client_request.py tests/test_duration_options.py
    tests/test_dispatch_workload.py tests/test_client.py tests/test_config_unread.py
    tests/test_config_schema.py tests/test_env_config.py tests/test_main.py && sh
    spec/update-client-retry/drive/bound.sh 300 .agents/factory/bin/temp_site.sh uv
    run sh "$PWD/spec/update-client-retry/drive/ucr_drive.sh" client && for f in docs/_include/client_help.rst
    src/hypershell/client.py; do grep -q ''HYPERSHELL_CLIENT_BUNDLESIZE'' $f && grep
    -q ''client.bundlesize'' $f && ! grep -q ''HYPERSHELL_SUBMIT_BUNDLESIZE'' $f ||
    exit 1; done && ! grep -q ''HYPERSHELL_SUBMIT_BUNDLESIZE'' docs/_include/config_param_ref.rst
    && test "$(grep -c ''Read by ``hs client``'' docs/_include/config_param_ref.rst)"
    -ge 2 && grep -q ''Read by ``hs submit`` only'' docs/_include/config_param_ref.rst
    && grep -q ''Read by ``hs submit``, ``hs server`` and ``hs cluster``'' docs/_include/config_param_ref.rst
    && test "$(grep -c ''versionchanged:: 2.9.0'' docs/_include/config_param_ref.rst)"
    -ge 29 && test "$(grep -c ''versionchanged:: 2.9.0'' docs/cli/client.rst)" -ge
    6 && ! grep -q ''config.submit.\*` for CLI defaults'' AGENTS.md && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-ucr
    sh spec/update-client-retry/drive/bound.sh 900 uv run --python 3.12 --group docs
    sphinx-build -W --keep-going -q -E -b html docs /tmp/claude-501/docs-html-ucr'
- id: P4
  name: Never-ran statuses are not retried and fail their group; a retry keeps its
    declarations; one retry default (R5-R8, their docs)
  status: pending
  satisfies:
  - R5
  - R6
  - R7
  - R8
  - R9
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'test -n "$HS_TEST_POSTGRES" && sh spec/update-client-retry/drive/bound.sh
    2400 uv run pytest -q -p no:cacheprovider tests/test_retry_terminal.py tests/test_retry_declarations.py
    tests/test_retry_defaults.py tests/test_retry_gate.py tests/test_groups.py tests/test_group_order.py
    tests/test_stream_input.py tests/test_zone_model.py tests/test_zone_postgres.py
    tests/test_zone_autoscale.py tests/test_autoscale.py tests/test_restart.py tests/test_finish_wake.py
    tests/test_writeback.py tests/test_client.py tests/test_cluster_launch.py tests/test_cluster_no_db.py
    tests/test_config_unread.py tests/test_main.py && for m in retry defaults; do
    sh spec/update-client-retry/drive/bound.sh 300 .agents/factory/bin/temp_site.sh
    uv run sh "$PWD/spec/update-client-retry/drive/ucr_drive.sh" $m || exit 1; done
    && for f in docs/_include/config_param_ref.rst docs/_include/server_desc.rst docs/_include/server_help.rst
    docs/_include/cluster_help.rst src/hypershell/server.py src/hypershell/cluster/__init__.py;
    do grep -q ''never retried'' $f && grep -q ''cores, memory and walltime'' $f ||
    exit 1; done && grep -q -- ''-1001'' docs/_include/server_desc.rst && grep -q
    -- ''-1002'' docs/_include/server_desc.rst && for f in docs/api/server.rst docs/api/cluster.rst;
    do grep -q ''server.attempts'' $f || exit 1; done && test "$(grep -c ''versionchanged::
    2.9.0'' docs/api/server.rst)" -ge 2 && test "$(grep -c ''versionchanged:: 2.9.0''
    docs/api/cluster.rst)" -ge 3 && test "$(grep -c ''versionchanged:: 2.9.0'' docs/cli/server.rst)"
    -ge 10 && test "$(grep -c ''versionchanged:: 2.9.0'' docs/cli/cluster.rst)" -ge
    16 && for f in AGENTS.md .agents/factory/invariants.md; do grep -q NO_RETRY_STATUSES
    $f && grep -q new_retry $f && grep -q configured_retries $f || exit 1; done &&
    ! grep -rn ''hs task '' docs/_include/server_desc.rst docs/_include/config_param_ref.rst
    docs/api/server.rst docs/api/cluster.rst && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-ucr
    sh spec/update-client-retry/drive/bound.sh 900 uv run --python 3.12 --group docs
    sphinx-build -W --keep-going -q -E -b html docs /tmp/claude-501/docs-html-ucr'
- id: P5
  name: 'Gate: the whole suite serially, the red list, PostgreSQL, the drive, the
    docs'
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
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: test -n "$HS_TEST_POSTGRES" && sh spec/update-client-retry/drive/bound.sh
    5400 uv run --python 3.14 pytest -p no:cacheprovider -q && sh spec/update-client-retry/drive/bound.sh
    900 uv run pytest -q -p no:cacheprovider $(sed -e '/^#/d' -e '/^$/d' -e 's/^pg://'
    -e 's/ raises=.*$//' spec/update-client-retry/red.txt) && RED_BASE_REV=1f688c7c
    RED_LIST="$PWD/spec/update-client-retry/red.txt" sh spec/update-client-retry/drive/bound.sh
    1200 sh spec/relay-hardening/drive/red_check.sh && for m in update client retry
    defaults; do sh spec/update-client-retry/drive/bound.sh 300 .agents/factory/bin/temp_site.sh
    uv run sh "$PWD/spec/update-client-retry/drive/ucr_drive.sh" $m || exit 1; done
    && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-ucr sh spec/update-client-retry/drive/bound.sh
    900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html
    docs /tmp/claude-501/docs-html-ucr
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — `hs update --delete` refuses changes it would ignore, `hs client` reads its own bundle keys, and a task that never ran is not retried

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/update-client-retry/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its `verify:`
command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract; the GOAL gate
  ruled one cycle in three fix phases (R1–R2, R3–R4, R5–R8) with R9 alongside each.
- **Authoritative design:** [`PLAN.md`](PLAN.md); research digest
  [`research/00-digest.md`](research/00-digest.md) and briefs `01`–`05`.
- **Test prototypes:** [`research/prototypes/`](research/prototypes/) — `update_refusals_proto.py`,
  `client_bundle_keys_proto.py`, `retry_terminal_proto.py`, `retry_declarations_proto.py`,
  `retry_defaults_proto.py`, named so the suite never collects them. P1 copies each to
  `tests/test_<name>.py` (without `_proto`), dropping only the docstring's "Prototype (plan time): …"
  sentence and the blank line before it.
- **Red list:** [`red.txt`](red.txt), 79 node ids (4 `pg:`, 1 ` raises=KeyError`), proved against
  **`1f688c7c`** (develop with #117), not the merge commit's first parent `b52d6734`: the tests are
  written against #117's tree (`-R` as a number, `evict_after`, `hs cluster` reading
  `server.attempts`), and the merge `eb2a4487` has `1f688c7c`'s `src`, `tests` and `docs` byte for
  byte. Checked by the shared `spec/relay-hardening/drive/red_check.sh` (each must fail on
  `1f688c7c` in its call by an assertion, or by the named error) and
  `spec/contended-writes/drive/rest_green.sh` (everything else in the named files passes).
- **Drive:** [`drive/ucr_drive.sh`](drive/ucr_drive.sh), modes `update`, `client`, `retry`,
  `defaults`, run in a throwaway site. The builder may amend it; a changed check is noted in the
  commit. `tests/test_key_option.py` sweeps `spec/*/drive/*` for a key option, so the drive never
  writes one, even in a comment.
- **Bound:** [`drive/bound.sh`](drive/bound.sh) `SECONDS COMMAND...` bounds every verify step (macOS
  has no `timeout`): SIGINT to the step's own process group at the bound, SIGKILL 15 s later,
  status 124.

## Running the verify commands here

- Run every `verify:` from the worktree root with these exported (the drive's `uv run`, the tests'
  `hs` subprocesses and child interpreters inherit them):
  `UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-ucr`, `UV_CACHE_DIR=/tmp/claude-501/uv-cache-ucr`,
  `PATH=/tmp/claude-501/venv314-ucr/bin:$PATH`. The worktree's own `.venv` is not used.
- **PostgreSQL** (P1, P4, P5): also export
  `HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres` and an empty
  `HS_TEST_TIMESCALE`, and run with the sandbox off (local socket). The verifies refuse to run without
  `HS_TEST_POSTGRES`, so the four `pg:` lines and `tests/test_zone_postgres.py` are always proved.
  The tests create and drop their own databases.
- **Docs build** (P2–P5) runs with the sandbox off (its sitemap extension binds a socket), in this
  cycle's own docs venv `/tmp/claude-501/docs-venv-ucr` (the verify sets it on the command).
- `pytest -n auto` is broken on this suite: everything runs serially.
- Every server binds `127.0.0.1`; `hs cluster` makes its own key; `hs server` takes its key from
  `HYPERSHELL_SERVER_AUTH`, never a key option. The tests stop every process they start; the drive's
  commands all end on their own (the server runs a file; the client stops with it).
- A builder works in the `submit-input-checks` worktree (#118): never stop its processes, never use
  its venvs, never edit its files.
- **Dry runs at plan time** (zsh, a machine shared with other sessions; see §"Plan-time evidence"
  at the end for the numbers).

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md); the checklist is
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §1, §2, §3, §4, §10, §11, §12.
- One phase per `hs-build` invocation; one atomic commit containing **both** the code and the
  `TECH.md` state change, subject `[fix] Build update-client-retry P<n>: …`, signed; **no
  `Co-Authored-By` trailer**. Squashed into the single PR-title commit at `hs-publish`.
- **Docs land with their phase** (PLAN §3 deviation row 1): each fix phase updates its help snippets,
  in-program `HELP`, prose page and `.. versionchanged:: 2.9.0` markers in the same commit. No
  `share/` change (option names and value lists are unchanged; man pages are rebuilt at release).
- In `config_param_ref.rst`, never a body line holding only a ``` ``.name`` ``` term
  (`tests/test_config_schema.py`'s parser reads those as entries).
- The grep phrases in the verifies (`takes no change`, `single-task form takes no`,
  `Read by ``hs client```, `never retried`, `cores, memory and walltime`, …) must each sit on one
  line in the file (no wrap inside the phrase).
- Docs name top-level commands only (`hs update`, `hs client`, …; never `hs task …`); no internal
  or cluster names (public repository).
- **#118 (`fix/submit-input-checks`) may land first.** If it does, rebase at the start of P4 and take
  PLAN §5's resolution: its retry-construction loop becomes the `new_retry` comprehension; keep both
  sides of the `sqlalchemy` import and the `AGENTS.md`/`invariants.md` §3 sentences; re-run its
  retry tests (`tests/test_group_integer.py -k retry`) in P4's verify run. `red.txt` stays valid: a
  rebase changes `RED_BASE_REV` only if a red-listed file conflicts (none does).

---

## Phase P1 — Tests first: the red list on 1f688c7c (tests only)
**Satisfies:** R1–R8 · **Depends on:** —
**Goal:** every new test is in the tree and fails on `1f688c7c` for what it checks; everything else in
the touched and neighbouring test files passes.

- [x] `tests/test_update_refusals.py`, `tests/test_client_bundle_keys.py`,
      `tests/test_retry_terminal.py`, `tests/test_retry_declarations.py`, `tests/test_retry_defaults.py`
      from the prototypes (see above), otherwise unchanged.
- [x] `tests/test_client_request.py::test_advertise_only_an_explicit_size`: its `submit` layers become
      `client` layers (`env`/`local` `client.bundlesize` 7, 1, 3 advertised; `user` `submit.bundlesize`
      9 → `None`; `-b 5` over `client` 7 → 5). Red on `1f688c7c`.
- [x] `tests/test_duration_options.py` `SECONDS_KEYS`: drop `('ClientApp', [], 'bundlewait')` from the
      `submit.bundlewait` row (the client's key is covered by `tests/test_client_bundle_keys.py`; a
      `client.bundlewait` row would run `test_seconds_key_refused` through `hs cluster`, which does
      not read it). Green on both.
- [x] `tests/test_dispatch_workload.py::test_configured_bundlesize_is_advertised`: the client's
      `env` becomes `{'HYPERSHELL_CLIENT_BUNDLESIZE': '3'}`. Red on `1f688c7c`.
- [x] Nothing outside `tests/` and `spec/` changes. A renamed or split test is renamed in `red.txt`
      too (never dropped without a PLAN note).
- **Verify:** see frontmatter: the fence; `HS_TEST_POSTGRES` set; `red.txt` holds 79 ids;
  `red_check` 79 of 79 red on `1f688c7c`; `rest_green` over the five new files, the three edited ones
  and their neighbours.
- **Touches:** the five new test files, `tests/test_client_request.py`,
  `tests/test_duration_options.py`, `tests/test_dispatch_workload.py`.

## Phase P2 — `hs update` refusals (R1, R2, R9 part)
**Satisfies:** R1, R2, R9 · **Depends on:** P1
**Goal:** `--delete` with a change, and the single-task form with an action option or `--remove-tag`,
exit 2 before the database is opened; the `hs update` page says so.

- [ ] `task.py` `TaskUpdateApp.run`: PLAN §2.1 — `legacy` computed once; the R2 refusal
      (`Cannot use {options} with the single-task form ID FIELD VALUE`, options in the order
      `--cancel`, `--revert`, `--delete`, `--remove-tag`, joined by `, `); else the R1 refusal
      (`Cannot use --delete with changes (given: {words})`, positionals as typed, then `--remove-tag`
      and its names); then `ensuredb()` and the existing routing on `legacy`. `ArgumentError` from
      `cmdkit.cli` (already imported).
- [ ] `UPDATE_HELP` (`task.py`), `docs/_include/task_update_help.rst` (the `--delete` entry and
      "Legacy Mode"), `docs/_include/task_update_desc.rst` (the `--delete` paragraph and the
      single-task paragraph): the sentences "``--delete`` takes no change: given a ``FIELD=VALUE``, a
      ``KEY:VALUE`` or ``--remove-tag``, ``hs update`` exits with status 2 before it counts or asks,
      and deletes nothing." and "The single-task form takes no ``--cancel``, ``--revert``,
      ``--delete`` or ``--remove-tag``; given one, it exits with status 2 and writes nothing."
      (plain text in `UPDATE_HELP`; wrap so each verify phrase stays on one line).
- [ ] `docs/cli/task_update.rst`: one `.. versionchanged:: 2.9.0` after the existing 2.9.0 block:
      both refusals and what happened before (deleted with the changes ignored; wrote the field and
      ignored the option).
- **Verify:** see frontmatter: the two test files and `test_main.py`; drive `update`; the phrases in
  all three files; ≥ 8 markers on the page; no `hs task `; the docs build.
- **Touches:** `src/hypershell/task.py`, `docs/_include/task_update_desc.rst`,
  `docs/_include/task_update_help.rst`, `docs/cli/task_update.rst`.

## Phase P3 — `hs client`'s bundle keys (R3, R4, R9 part)
**Satisfies:** R3, R4, R9 · **Depends on:** P2
**Goal:** `hs client` advertises `client.bundlesize` when a layer above the built-in default sets it
(any form) and waits `client.bundlewait`; `[submit]` no longer reaches it; the reference, help and
page say so.

- [ ] `client.py` `advertised_size`: PLAN §2.2 — the first form of `client.bundlesize` set above the
      built-in default (each `which` in `try`/`except KeyError`), the value as is when an `int`, else
      `read_value(Kind.INT, str(value))`; a `ValueError` or an unset `_env` variable raises
      `ConfigurationError(f'client.bundlesize: expected a whole number ({blame(base, "client", form)})')`.
      Imports: `blame` from `core.config`, `Kind`/`read_value` from `core.config_schema`,
      `ConfigurationError` from `cmdkit.config`. Docstring: the `client` key, its forms, the error.
- [ ] `ClientApp`: `bundlewait: int = config.client.bundlewait`; `HELP`'s `-b`/`-w` lines name
      `client.bundlesize`/`HYPERSHELL_CLIENT_BUNDLESIZE` and `client.bundlewait`.
- [ ] `docs/_include/client_help.rst`: the `-b` paragraph's `HYPERSHELL_SUBMIT_BUNDLESIZE` /
      `submit.bundlesize` → `HYPERSHELL_CLIENT_BUNDLESIZE` / `client.bundlesize`; `-w` names
      `client.bundlewait`.
- [ ] `docs/_include/config_param_ref.rst`: `[client].bundlesize` gains "Read by ``hs client``" and the
      advertise rule moved from `[submit].bundlesize` (set in a file, by `HYPERSHELL_CLIENT_BUNDLESIZE`
      or its `_env`/`_eval` forms → advertised and returned in bundles of that size; left at the
      built-in default → no size, the server's own), plus a new html-only `.. versionchanged:: 2.9.0`
      (before, `hs client` read `submit.bundlesize`). `[client].bundlewait` gains "Read by ``hs
      client``" and the duration-forms sentence; its existing 2.9.0 marker gets a second line (before,
      `hs client` read `submit.bundlewait`). `[submit].bundlesize`: the client paragraph removed;
      "Read by ``hs submit`` only."; its `:pr:`78`` marker rewritten (unreleased: no client wording).
      `[submit].bundlewait`: "Read by ``hs submit``, ``hs server`` and ``hs cluster`` (which hands it
      to its clients as ``-w``)." (PLAN §5 S3).
- [ ] `docs/cli/client.rst`: one new `.. versionchanged:: 2.9.0` with the others: `hs client` reads
      `client.bundlesize` and `client.bundlewait`; `submit.*` no longer reach it (move them to
      `[client]`).
- [ ] `AGENTS.md` § Configuration: the "`bundlesize`/`bundlewait` are read from `config.submit.*` for
      CLI defaults but `config.client.*` for module defaults" sentence becomes: `hs client` reads
      `client.bundlesize` (advertised only when set above the built-in default) and
      `client.bundlewait`; `hs submit` reads `submit.*`; `hs server`/`hs cluster` read
      `submit.bundlewait` and `server.bundlesize`.
- **Verify:** see frontmatter: the client tests and neighbours; drive `client`; the help, reference
  and page greps; the docs build.
- **Touches:** `src/hypershell/client.py`, `docs/_include/client_help.rst`,
  `docs/_include/config_param_ref.rst`, `docs/cli/client.rst`, `AGENTS.md`.

## Phase P4 — Never-ran statuses, a retry's declarations, one retry default (R5–R8, R9 part)
**Satisfies:** R5, R6, R7, R8, R9 · **Depends on:** P3
**Goal:** `-1001`/`-1002` are never retried and fail their group at once (selection, gate and
autoscaler demand together, both dialects); a retry carries cores, memory and walltime; every Python
entry point defaults `max_retries` from `server.attempts`; the retry docs and the constitution say so.
One commit: §3's "change them together" members move as one.

- [ ] `data/model.py`: `TASK_TEMPLATE_ERROR`, `TASK_RESOURCE_ERROR`, `NO_RETRY_STATUSES` beside
      `CANCEL_STATUS` (docstrings; `__all__`). `__awaiting_retry`'s tuple gains
      `table.c.exit_status.not_in(NO_RETRY_STATUSES)` (docstring: never-ran rows never await retry).
      `increment_group`: `case` import; the failed-count statement per PLAN §2.3 (one statement,
      `or_(attempt >= attempts, never_ran)`, a second count under `case`) and the three reason forms;
      update its comment and docstring.
- [ ] `data/model.py`: `Task.retry_columns` beside `result_columns` (docstring: copied as stored,
      never re-parsed); `Task.new_retry(task)` (PLAN §2.4); `__schedule_next_failed_tasks` builds
      `retries = {task.id: cls.new_retry(task) for task in failed_tasks}`.
- [ ] `client.py`: drop the two constant definitions, import them from `hypershell.data.model`
      (keep the comment above them); both `create_task` error lines end `(task did not run)`.
- [ ] `server.py`: `configured_retries()` after `DEFAULT_ATTEMPTS` (in `__all__`); `ServerThread`,
      `serve_from`, `serve_file`, `serve_forever`: `max_retries: Optional[int] = None`, `ServerThread`
      resolving first thing in `__init__` (before the in-memory warning); `ServerApp.max_retries =
      configured_retries()`. Docstrings: "Default: ``server.attempts`` - 1, read when called".
- [ ] `cluster/local.py`, `cluster/remote.py` (`RemoteCluster`, `AutoScalingCluster`), `cluster/ssh.py`,
      `cluster/topology.py` (`AutoScalingServer`, PLAN §5 S1): `max_retries: Optional[int] = None`;
      `AutoScalingCluster`/`AutoScalingServer` resolve `configured_retries()` before their
      `max_retries + 1`; imports; docstrings. `cluster/__init__.py`: `ClusterApp.max_retries =
      configured_retries()`.
- [ ] `tests/test_retry_declarations.py`, appended, not red-listed (it names the new attribute):
      `test_retry_columns_are_fixed` (`@mark.unit`, modelled on
      `tests/test_writeback.py::test_result_columns_are_fixed`): `Task.retry_columns`,
      `Task.result_columns` and the fresh set (`id`, `submit_id`, `submit_host`, `submit_time`,
      `attempt`, `retried`, `previous_id`, `next_id`, `server_id`, `server_host`, `schedule_time`,
      `part`) are disjoint and cover `Task.columns`.
- [ ] Docs, each phrase on one line: `[server].attempts` in `config_param_ref.rst` ("A task whose
      template does not expand (``-1001``) or that needs more cores or memory than its client has
      (``-1002``) is never retried: it did not run." / "A retry keeps its task's cores, memory and
      walltime." / read by the Python API's entry points when they are not given ``max_retries``) with
      a new marker; `server_desc.rst` (same two facts, naming `-1001` and `-1002`); `-r` in
      `server_help.rst`, `cluster_help.rst` and both in-program `HELP`s; `docs/cli/server.rst` and
      `docs/cli/cluster.rst` one new marker each (never-ran not retried and fail the group at once; a
      retry keeps its declarations); `docs/api/server.rst` and `docs/api/cluster.rst`: the
      "Configuration files have no impact…" sentence excepts `server.attempts` (the `max_retries`
      default), with a marker each. If #118 has landed: `submit_desc.rst`'s retry sentence gains
      "and its cores, memory and walltime".
- [ ] `AGENTS.md` (status table rows for `-1001`/`-1002`: never retried; § Retry model: the never-ran
      exclusion in the selection, the gate counting them on any attempt, `new_retry`/`retry_columns`,
      `configured_retries`) and `.agents/factory/invariants.md` §2 (the table and the MUST-filter
      rule: retry selection also excludes `NO_RETRY_STATUSES`), §3 (the "change them together" list
      gains the set; a retry is `new_retry`), §10 (`server.attempts` read when an entry point is
      built), §11 (the constructor sentence narrowed to `advertise=`/`evict_after=`;
      `max_retries=None` resolves through `configured_retries()`) — PLAN §3 deviation row 2.
- **Verify:** see frontmatter: `HS_TEST_POSTGRES` set; the three retry files, the gate, group,
  zone, autoscale, restart, writeback and cluster neighbours (both dialects); drive `retry` and
  `defaults`; the doc and constitution greps; the docs build.
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/client.py`, `src/hypershell/server.py`,
  `src/hypershell/cluster/{__init__,local,remote,ssh,topology}.py`, `tests/test_retry_declarations.py`,
  `docs/_include/{config_param_ref,server_desc,server_help,cluster_help}.rst`,
  `docs/cli/{server,cluster}.rst`, `docs/api/{server,cluster}.rst`, `AGENTS.md`,
  `.agents/factory/invariants.md`.

## Phase P5 — Gate: the whole suite serially, the red list, PostgreSQL, the drive, the docs
**Satisfies:** R1–R9 · **Depends on:** P4
**Goal:** nothing else broke; every red test is green; the red list is still red on `1f688c7c`; every
drive mode passes; the docs build with `-W`.

- [ ] Run the frontmatter verify (sandbox off, `HS_TEST_POSTGRES` exported). Record the whole
      suite's counts and time in the commit body.
- [ ] Release note: nothing to write here; `hs-publish` drafts the squash body from GOAL.md's six
      user-visible changes, adding to change 5 that a `cores`/`memory`/`timeout` tag written by
      `hs update` stays a tag on a retry (PLAN §5 S2).
- **Touches:** `spec/update-client-retry/TECH.md` only.

---

## Plan-time evidence

Dry-run at plan time (zsh; `eb2a4487`; Python 3.14.6; PostgreSQL 18 local; another session building
in the `submit-input-checks` worktree):

- **P1**, verbatim in the worktree (sandbox off, `HS_TEST_POSTGRES` set) with the prototypes copied
  in and the three edits applied, then removed: 222 s; `red_check` 79 of 79 red on `1f688c7c` (the
  `raises=KeyError` line by `KeyError`, every other by an assertion; the four `pg:` lines ran);
  `rest_green` 502 passed, 79 deselected.
- **P2–P4 and P5's steps after the suite**, on a scratch copy of the tree with the design applied to
  a scratch `src` (`PYTHONPATH` ahead of the editable install; the drive through `sh` rather than
  `uv run`, which would try to build the scratch project; META F7):
  - every prototype green: 90 passed on SQLite, the 4 `pg:` tests passed;
  - P2's tests 87 passed (50 s); P3's 809 passed (120 s); P4's 610 passed, 4 skipped (none of them
    PostgreSQL; 327 s, sandbox off) with P4's fence test added;
  - every drive mode `ok` (`update` 6 s, `client` 5 s, `retry` 16 s, `defaults` 6 s), and every mode
    `FAIL`s at its first check on `1f688c7c`;
  - each phase's doc and constitution greps pass with the planned phrases in place and fail on
    `1f688c7c`; the docs built with `-W` (8 s, scratch docs venv), and the verbatim build command
    passes in the worktree on `1f688c7c`'s docs (7 s, `/tmp/claude-501/docs-venv-ucr`).
- **The whole suite**, serially, with the design and the P1 tests (sandbox on, no PostgreSQL):
  3948 passed, 145 skipped in 27 min (bounded at 90 min).
- `run_local` from Python on a fresh SQLite site fails (`no such table: task`: the library never
  creates the schema; the commands do) and then hangs in `stop()`. Pre-existing and outside this
  cycle; the drive runs `hs initdb` first. Candidate seed.
