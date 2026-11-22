---
slug: config-set-and-unread
title: hs config set writes each key by its type, and hs server and hs cluster read
  the keys they document
kind: fix
appetite: small
status: in_progress
branch: fix/config-set-and-unread
base: develop
current_phase: P5
last_updated: '2026-11-22'
phases:
- id: P1
  name: 'Tests first: the red list on d9901cf0 (tests only)'
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
  verify: git diff --quiet d9901cf0 -- src docs share AGENTS.md .agents && test -z
    "$(git diff --name-only d9901cf0 -- . ':!spec' ':!tests' ':!issues' ':!ROADMAP.md')"
    && test "$(grep -cvE '^#|^$' spec/config-set-and-unread/red.txt)" -eq 99 && RED_BASE_REV=d9901cf0
    RED_LIST="$PWD/spec/config-set-and-unread/red.txt" sh spec/config-set-and-unread/drive/bound.sh
    900 sh spec/relay-hardening/drive/red_check.sh && RED_LIST="$PWD/spec/config-set-and-unread/red.txt"
    sh spec/config-set-and-unread/drive/bound.sh 1800 sh spec/contended-writes/drive/rest_green.sh
    tests/test_config_set_types.py tests/test_config_unread.py tests/test_cluster_evict.py
    tests/test_config_schema.py tests/test_env_config.py tests/test_env_duration.py
    tests/test_duration_options.py tests/test_cluster_launch.py tests/test_period_floor.py
    tests/test_key_option.py tests/test_main.py
- id: P2
  name: hs config set writes each value by its key's type
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/config-set-and-unread/drive/bound.sh 900 uv run pytest -q -p no:cacheprovider
    tests/test_config_set_types.py tests/test_config_schema.py tests/test_env_config.py
    tests/test_main.py && for m in set; do sh spec/config-set-and-unread/drive/bound.sh
    300 .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/config-set-and-unread/drive/csu_drive.sh"
    $m || exit 1; done
- id: P3
  name: server.eager, server.attempts and server.evict reach hs server and hs cluster
  status: done
  satisfies:
  - R5
  - R6
  - R7
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/config-set-and-unread/drive/bound.sh 1800 uv run pytest -q -p no:cacheprovider
    -k 'eager or attempts' tests/test_config_unread.py && sh spec/config-set-and-unread/drive/bound.sh
    1800 uv run pytest -q -p no:cacheprovider tests/test_cluster_evict.py tests/test_env_duration.py
    tests/test_period_floor.py tests/test_cluster_launch.py tests/test_cluster_advertise.py
    tests/test_cluster_exit.py tests/test_cluster_no_db.py tests/test_retry_gate.py
    && for m in eager evict; do sh spec/config-set-and-unread/drive/bound.sh 300 .agents/factory/bin/temp_site.sh
    uv run sh "$PWD/spec/config-set-and-unread/drive/csu_drive.sh" $m || exit 1; done
- id: P4
  name: -R takes a number, and hs cluster reads client.ratelimit
  status: done
  satisfies:
  - R8
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/config-set-and-unread/drive/bound.sh 1200 uv run pytest -q -p no:cacheprovider
    tests/test_config_unread.py tests/test_duration_options.py tests/test_cluster_launch.py
    tests/test_client.py tests/test_relay_launch.py && for m in rate; do sh spec/config-set-and-unread/drive/bound.sh
    300 .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/config-set-and-unread/drive/csu_drive.sh"
    $m || exit 1; done
- id: P5
  name: Docs, help snippets and markers; AGENTS.md and invariants in lockstep
  status: in_progress
  satisfies:
  - R9
  depends_on:
  - P2
  - P3
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'grep -q ''^.. _config_value_types:'' docs/_include/config_intro.rst &&
    grep -q ''config_value_types>'' docs/_include/config_set_desc.rst && grep -q "written
    by the key''s type" src/hypershell/config.py && grep -q "written by the key''s
    type" docs/_include/config_set_help.rst && test "$(grep -c ''versionchanged::
    2.9.0'' docs/cli/config_set.rst)" -ge 1 && test "$(grep -c ''versionchanged::
    2.9.0'' docs/_include/config_param_ref.rst)" -ge 25 && test "$(grep -c ''versionchanged::
    2.9.0'' docs/cli/client.rst)" -ge 4 && test "$(grep -c ''versionchanged:: 2.9.0''
    docs/cli/server.rst)" -ge 8 && test "$(grep -c ''versionchanged:: 2.9.0'' docs/cli/cluster.rst)"
    -ge 11 && for f in src/hypershell/client.py src/hypershell/cluster/__init__.py
    docs/_include/client_help.rst docs/_include/cluster_help.rst; do grep -q ''e.g.
    0.5'' $f || exit 1; done && for f in AGENTS.md .agents/factory/invariants.md;
    do grep -q match_path $f && grep -q plain_rate $f || exit 1; done && ! grep -rn
    ''hs task '' docs/_include/config_set_desc.rst docs/cli/config_set.rst && grep
    -A10 ''^    ``.eager``'' docs/_include/config_param_ref.rst | grep -q ''``1``/``0``
    as a number or as'' && grep -q ''For an optional text key'' docs/_include/config_set_desc.rst
    && ! grep -q ''where an empty variable would not\.$'' docs/_include/config_set_desc.rst
    && test "$(grep -c ''``1``/``0`` included'' docs/cli/server.rst docs/cli/cluster.rst
    | grep -c '':1$'')" -eq 2 && sh spec/config-set-and-unread/drive/bound.sh 600
    uv run pytest -q -p no:cacheprovider tests/test_config_schema.py tests/test_config_set_types.py
    && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-csu sh spec/config-set-and-unread/drive/bound.sh
    900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html
    docs /tmp/claude-501/docs-html-csu'
- id: P6
  name: 'Gate: the whole suite serially, the red list, the drive, the docs'
  status: in_progress
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
  - P5
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/config-set-and-unread/drive/bound.sh 5400 uv run --python 3.14 pytest
    -p no:cacheprovider -q && sh spec/config-set-and-unread/drive/bound.sh 900 uv
    run pytest -q -p no:cacheprovider $(sed -e '/^#/d' -e '/^$/d' spec/config-set-and-unread/red.txt)
    && RED_BASE_REV=d9901cf0 RED_LIST="$PWD/spec/config-set-and-unread/red.txt" sh
    spec/config-set-and-unread/drive/bound.sh 900 sh spec/relay-hardening/drive/red_check.sh
    && for m in set eager evict rate; do sh spec/config-set-and-unread/drive/bound.sh
    300 .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/config-set-and-unread/drive/csu_drive.sh"
    $m || exit 1; done && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-csu sh
    spec/config-set-and-unread/drive/bound.sh 900 uv run --python 3.12 --group docs
    sphinx-build -W --keep-going -q -E -b html docs /tmp/claude-501/docs-html-csu
review:
  last_reviewed_commit: d6d83afa2bf604de9c8c411303f59abde0889451
  verdict: changes-requested
  blocked_reason: 'Review cycle 2: F7 (R1/R4) non-UTF-8 locale truncates or mis-encodes
    the config file; F8 (R7/F3) integer evict past float range crashes; F9 (R8) rate
    under ~1e-14/s kills the executor and hangs hs cluster; F10 PLAUSIBLE spec ids
    in tests. Human gate: F8 server.py, F9 client.py'
  cycle: 2
---
# TECH.md — `hs config set` writes each key by its type, and `hs server` and `hs cluster` read the keys they document

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/config-set-and-unread/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its `verify:`
command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md); research digest
  [`research/00-digest.md`](research/00-digest.md) and briefs `01`–`05`.
- **Test prototypes:** [`research/prototypes/`](research/prototypes/) —
  `config_set_types_proto.py`, `config_unread_proto.py`, `cluster_evict_proto.py`, named so the suite
  never collects them. P1 copies each to `tests/test_config_set_types.py`, `tests/test_config_unread.py`,
  `tests/test_cluster_evict.py`, dropping the "Prototype (plan time)" sentence from the module
  docstring.
- **Red list:** [`red.txt`](red.txt), 99 node ids, checked by the shared
  `spec/relay-hardening/drive/red_check.sh` (each must fail on `d9901cf0` in its call by an assertion)
  and `spec/contended-writes/drive/rest_green.sh` (everything else in the named files passes).
- **Drive:** [`drive/csu_drive.sh`](drive/csu_drive.sh), modes `set`, `eager`, `evict`, `rate`, run in a
  throwaway site. The builder may amend it; a changed check is noted in the commit.
  `tests/test_key_option.py` sweeps `spec/*/drive/*` for a key option, so the drive never writes one,
  even in a comment.
- **Bound:** [`drive/bound.sh`](drive/bound.sh) `SECONDS COMMAND...` bounds every verify step (macOS has
  no `timeout`): SIGINT to the step's own process group at the bound, SIGKILL 15 s later, status 124.

## Running the verify commands here

- Run every `verify:` from the worktree root with these exported (the drive's `uv run`, the tests'
  `hs` subprocesses and child interpreters inherit them):
  `UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-csu`, `UV_CACHE_DIR=/tmp/claude-501/uv-cache-csu`,
  `PATH=/tmp/claude-501/venv314-csu/bin:$PATH`. The worktree's own `.venv` is not used.
- The docs build (P5, P6) runs with the sandbox off, in this cycle's own docs venv
  `/tmp/claude-501/docs-venv-csu` (the verify sets it on the command). Everything else ran in the
  sandbox at plan time.
- `pytest -n auto` is broken on this suite: everything runs serially.
- Every server binds `127.0.0.1`; `hs cluster` makes its own key; `hs server` takes its key from
  `HYPERSHELL_SERVER_AUTH`, never a key option. The tests stop every process they start (their
  `run_bounded` kills the process group); the drive's commands all end on their own.
- Another session reviews in the `relay-shutdown-tail` worktree: never stop its processes, never use
  its venvs.
- Every `verify:` was dry-run verbatim at plan time (under zsh, the machine shared with other
  sessions): P1 on `d9901cf0` with the prototypes copied in, 349 s (`red_check` 99 of 99 red,
  `rest_green` 1100 passed, 1 skipped, 99 deselected); P2–P4 and P6's steps after the suite on a scratch
  copy of `src` with the design applied (`PYTHONPATH` ahead of the editable install): P2 80 s (327
  passed), P3 407 s (16 passed, then 502 passed, 1 skipped), P4 184 s (663 passed, 1 skipped), P6's red list 99 passed, `red_check`
  99 of 99 red, every drive mode ok (2–8 s each). P5's greps fail on `d9901cf0` at the first, as they
  must, and pass on a scratch `docs/` with the label, `:ref:` and markers applied, which built with
  `-W`. The docs build fails inside the sandbox (its sitemap extension binds a socket): run P5's and
  P6's verify with the sandbox off. The whole suite: 3684 passed, 138 skipped in 27 min with the design emulated and the three new files; 3522 passed, 138 skipped in 27 min on d9901cf0 without them (bounded at 90 min).

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md); the checklist is
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §3, §4, §10, §11, §12.
- One phase per `hs-build` invocation; one atomic commit containing **both** the code and the `TECH.md`
  state change, subject `[fix] Build config-set-and-unread P<n>: …`, signed; **no `Co-Authored-By`
  trailer**. Squashed into the single PR-title commit at `hs-publish`.
- Help snippets, markers and prose for every code phase land in P5 (PLAN §3 deviation row 2;
  squashed at publish). No `share/` change (PLAN §2.6).
- Keep off the lines #115 changes or touches (PLAN §5): `client.py` 1312–1314, `cluster/__init__.py`
  86–88, `server.py` 1213–1215, `config_param_ref.rst` 300–302, `cluster_help.rst` 127,
  `server_help.rst` 62; new `test_config_schema.py` tests go at the end of the file. Line numbers are
  `d9901cf0`'s.
- Docs name top-level commands only; no internal or cluster names (public repository).

---

## Phase P1 — Tests first: the red list on d9901cf0 (tests only)
**Satisfies:** R1–R8 · **Depends on:** —
**Goal:** every new test is in the tree and fails on `d9901cf0` for what it checks; everything else in
the touched and neighbouring test files passes.

- [x] `tests/test_config_set_types.py`, `tests/test_config_unread.py`, `tests/test_cluster_evict.py` from
      the prototypes (see above), otherwise unchanged.
- [x] Nothing outside `tests/` and `spec/` changes. A renamed or split test is renamed in `red.txt` too
      (never dropped without a PLAN note).
- **Verify:** see frontmatter: the fence; `red.txt` holds 99 ids; `red_check` 99 of 99 red;
  `rest_green` over the three files and their neighbours. Dry-run at plan time: as stated above.
- **Touches:** `tests/test_config_set_types.py`, `tests/test_config_unread.py`, `tests/test_cluster_evict.py`.

## Phase P2 — `hs config set` writes each value by its key's type (R1–R4)
**Satisfies:** R1, R2, R3, R4 · **Depends on:** P1
**Goal:** `hs config set` reads VALUE by the key's kind through `match_path` + `read_value`; a value
that does not convert exits 2, naming key, form and value, with nothing written.

- [x] `core/config_schema.py`: `match_path(path) -> Kind` after `match_name` (PLAN §2.1 order), in
      `__all__`; `read_value`'s `LOGGING_FILE` path branch refuses `''` (`text != ''`). Do not touch
      `KEYS`/`EMPTY_UNSET` (#116 edited them).
- [x] `config.py` `ConfigSetApp`: drop `type=smart_coerce` and its import; in `run()`, after the
      one-word check, `value = read_value(match_path(self.varpath), self.value)`, a `ValueError` raised
      as `ArgumentError(f'{self.varpath}: expected {error}, given {self.value!r}') from None`; write
      `value`. Anchor in `ConfigSetApp.run`, not `ConfigGetApp.run` (same `split` line).
- [x] `tests/test_config_schema.py`, **appended at the end**: a `@mark.unit` `match_path` table covering
      every PLAN §2.1 case (documented keys of each kind, both variants, `export.RUN_ID`,
      `ssh.nodelist.gpu`, zone keys known and unknown, `database.connect_args.x`, `database.pragmas.x`,
      `database.sslmode`, `server.nothing`, `foo.bar`, `Server.eager`, `server.port.x`); `logging.file`
      `''` among the `read_value` refusals. Not red-listed (they import the new name).
- **Verify:** `tests/test_config_set_types.py`, `test_config_schema.py`, `test_env_config.py`,
  `test_main.py`; drive mode `set`.
- [x] Review cycle 1, F1: a path or VALUE that cannot be written as UTF-8 is refused (exit 2) before
      anything is written (see the remediation section).
- [x] Review cycle 2, F7: `update()` reads and writes the file as UTF-8 whatever the locale; and the
      message ruling: a refusal names the key (see the cycle-2 remediation section).
- **Touches:** `src/hypershell/core/config_schema.py`, `src/hypershell/config.py`, `tests/test_config_schema.py`;
  cycle 2: `src/hypershell/core/config.py`, `tests/test_config_set_types.py`.

## Phase P3 — `server.eager`, `server.attempts` and `server.evict` reach `hs server` and `hs cluster` (R5–R7)
**Satisfies:** R5, R6, R7 · **Depends on:** P2
**Goal:** both apps keep the key when `--eager` is absent and read it by the boolean words at their
start check; `hs cluster` takes `-r` from `server.attempts`; `hs cluster` reads `server.evict` at its
start check, refuses it as `hs server` does before any database, port or server, and hands the period
to the server in every mode.

- [x] `server.py`: `configured_eager(value) -> bool` beside `DEFAULT_EAGER_MODE` (a `bool` as is, else
      `read_value(Kind.BOOL, str(value))`, else `ConfigurationError` naming `server.eager`, the form,
      the value and `blame(config, 'server', 'eager')`); `EVICT_MINIMUM = 10` and
      `check_evict(seconds)` beside `DEFAULT_EVICT` (PLAN §2.4 messages; non-finite refused);
      `HeartMonitor.__init__` calls `check_evict` in place of its inline check (same `wait_check` and
      `evict_after`); `ServerApp`: `--eager` gets `default=eager_mode`, `check_args` reads
      `self.evict_after = check_evict(config_seconds('server', 'evict'))` and
      `self.eager_mode = configured_eager(self.eager_mode)`. Imports: `math`, `ConfigurationError`,
      `blame`, `Kind`/`FORMS`/`read_value`. Do not edit 1213–1215.
- [x] `cluster/__init__.py` `ClusterApp`: `max_retries = config.server.attempts - 1`; `--eager`
      `default=eager_mode`; class attribute `evict_after: int | float = DEFAULT_EVICT`;
      `check_arguments`, right after the `autoscale.period` block:
      `self.evict_after = check_evict(config_seconds('server', 'evict'))` and
      `self.eager_mode = configured_eager(self.eager_mode)`; `run` passes `evict_after=self.evict_after`.
      Do not edit 86–88.
- [x] `cluster/local.py`, `cluster/remote.py` (`RemoteCluster`), `cluster/ssh.py`: a last keyword
      `evict_after: int = DEFAULT_EVICT` passed to `ServerThread(...)`, a docstring Args entry, the
      import where missing. `AutoScalingCluster` unchanged.
- **Verify:** `tests/test_config_unread.py -k 'eager or attempts'`, `test_cluster_evict.py` and the
  cluster/server neighbours; drive modes `eager`, `evict`.
- [x] Review cycle 1, F3: `check_evict` refuses a period over `EVICT_MAXIMUM`; and the `server.eager`
      refusal names the key and its source, the value only for the direct form (see the remediation
      section).
- [x] Review cycle 2, F8: `check_evict` compares against `EVICT_MAXIMUM` before anything that converts
      to a float (see the cycle-2 remediation section).
- **Touches:** `src/hypershell/server.py`, `src/hypershell/cluster/__init__.py`,
  `src/hypershell/cluster/local.py`, `src/hypershell/cluster/remote.py`, `src/hypershell/cluster/ssh.py`;
  cycle 2: `tests/test_cluster_evict.py`.

## Phase P4 — `-R` takes a number, and `hs cluster` reads `client.ratelimit` (R8)
**Satisfies:** R8 · **Depends on:** P3
**Goal:** `-R` takes a finite number on both commands; `hs cluster` defaults it from
`client.ratelimit` (not `task.ratelimit`) in every mode; a launched client gets a whole rate as an
integer word and a fraction as a plain decimal.

- [x] `core/types.py`: `parse_rate(value) -> int | float` beside `parse_period`, over
      `config_schema.read_number` (top-level import), raising `UnitError` (`Rate {value!r} is not
      valid: expected a number`); in `__all__`.
- [x] `client.py`: `-R` `type=parse_rate`; the `ratelimit` annotations/docstrings say a number
      (`Optional[float]`). **Do not edit 1312–1314** (they already divide as floats).
- [x] `cluster/__init__.py`: `ratelimit: Optional[float] = config.client.ratelimit or None`,
      `type=parse_rate`.
- [x] `cluster/launch.py`: `plain_rate(value)` beside `plain_seconds` (doctest example), used for `-R`
      under the existing `if ratelimit is not None`; annotations in `client_launch`/`client_options`;
      `local.py`, `remote.py`, `ssh.py` rate annotations and docstrings.
- [x] `tests/test_cluster_launch.py`: one comment at `CONTRACT_CLIENT_OPTIONS`'s `-R` entry naming the
      sanctioned bend (a fractional rate needs a client of this release; PLAN §3).
- **Verify:** `tests/test_config_unread.py` (all of it now), `test_duration_options.py`,
  `test_cluster_launch.py`, `test_client.py`, `test_relay_launch.py`; drive mode `rate`.
- [x] Review cycle 1, F2: `hs cluster` refuses a `client.ratelimit` that is a number but not finite at
      its start check, exit 2, as `-R` refuses one (see the remediation section).
- [x] Review cycle 2, F9: `-R` and `hs cluster`'s `client.ratelimit` start check refuse a rate other
      than 0 under `RATE_MINIMUM` (1e-6 per second), exit 2 (see the cycle-2 remediation section).
- **Touches:** `src/hypershell/core/types.py`, `src/hypershell/client.py`, `src/hypershell/cluster/__init__.py`,
  `src/hypershell/cluster/launch.py`, `src/hypershell/cluster/local.py`, `src/hypershell/cluster/remote.py`,
  `src/hypershell/cluster/ssh.py`, `tests/test_cluster_launch.py`; cycle 2: `tests/test_config_unread.py`.

## Phase P5 — Docs, help snippets, markers; AGENTS.md and invariants in lockstep (R9)
**Satisfies:** R9 · **Depends on:** P2, P3, P4
**Goal:** every page and help line says what R1–R8 changed, marked `versionchanged:: 2.9.0`; the agent
docs state the two new rules.

- [x] `hs config set`: `SET_HELP` `VALUE` line and `docs/_include/config_set_help.rst` ("Value to be
      set, written by the key's type."); `docs/_include/config_set_desc.rst` paragraph with
      `:ref:`by the key's type <config_value_types>`` (refusal exits 2, nothing written; a text key's
      `""` is written); label `.. _config_value_types:` before `config_intro.rst`'s "The value is read
      by the key's type:" (not lines 146–153); marker in `docs/cli/config_set.rst` between `|` and the
      help include.
- [x] `docs/_include/config_param_ref.rst`: `.attempts`, `.eager` (boolean words for file text),
      `.evict` (the "stop before they start" sentence, PLAN §2.6; third marker in its block), `[client]
      .ratelimit` (a number; `hs client` and `hs cluster`), each with the house `.. only:: html` marker.
      No body line holding only a ``` ``.name`` ``` term.
- [x] `-R` help in `client.py` and `cluster/__init__.py` `HELP`, `docs/_include/client_help.rst`
      (155–157) and `cluster_help.rst` (358–360): "a number, e.g. 0.5".
- [x] Markers in `docs/cli/client.rst` (`-R`), `docs/cli/server.rst` (`server.eager`),
      `docs/cli/cluster.rst` (`server.eager`, `server.attempts`, `server.evict`, `client.ratelimit`,
      `-R`), placed above the existing 2.9.0 markers (PLAN §2.6).
- [x] AGENTS.md § Configuration and invariants §10: `hs config set` writes by the key's kind
      (`match_path` + `read_value`; exit 2; nothing written). AGENTS.md's `client_launch` paragraph and
      invariants §11: a whole `-R` is an integer word; a fractional one needs a client of this release.
- [x] Review cycle 1, F5 and F6: the empty-variable sentence names only the optional text keys;
      `.eager` and the two `server.eager` markers list `1`/`0`; one sentence each for F1–F3 (see the
      remediation section).
- **Verify:** greps for each edit, `tests/test_config_schema.py`, the docs build with `-W`.
- **Touches:** `src/hypershell/config.py`, `src/hypershell/client.py`, `src/hypershell/cluster/__init__.py`,
  `docs/_include/*.rst`, `docs/cli/{config_set,client,server,cluster}.rst`, `AGENTS.md`,
  `.agents/factory/invariants.md`.

## Phase P6 — Gate: the whole suite serially, the red list, the drive, the docs
**Satisfies:** R1–R9 · **Depends on:** P5
**Goal:** nothing else broke; every red test is green; the red list still proves the defects on
`d9901cf0`.

- [x] The whole suite under Python 3.14, serially (no `-n`).
- [x] The 99 red ids pass; `red_check` still reports 99 of 99 red on `d9901cf0`.
- [x] Every drive mode; the docs build.
- **Verify:** see frontmatter.
- [x] Review cycle 1: the whole gate re-run after P2–P5's fixes (see the remediation section).
- **Touches:** nothing (fixes found here go back to the phase that owns them, noted in the commit).

## Review-cycle-1 remediation (2026-11-21)

Under the rulings at the end of [`PLAN.md`](PLAN.md) (maintainer sign-off for the human gate F3 raised;
F1, F2, F3, F5 and F6 from [`REVIEW.md`](REVIEW.md), and the ruling on the `server.eager` message).
P2–P6 were reopened together through `set_phase.py` and each closed again with its own commit. P1 was
not reopened: its verify fences `src` to `d9901cf0`, which no longer holds on the branch, and its red
list proves the defects of that base. Each new test's red run is on `f8293303` instead (the reviewed
commit; the branch's `src`, `tests` and `docs` are unchanged since it), shown by putting the source
change aside in the worktree, running the new tests, and restoring it. `red.txt` stays at 99.

- **F1 (P2) — a VALUE that is not UTF-8.** `hs config set` refuses a path or a VALUE that cannot be
  written as UTF-8 (a byte that is not UTF-8 arrives as a lone surrogate) before it reads the value
  by its key's kind, with exit 2: `<path>: expected text that can be written as UTF-8, given <repr>`,
  or `Variable path <repr> cannot be written as UTF-8`. Nothing is written and no backup is made
  (`src/hypershell/config.py`, `is_utf8` and `ConfigSetApp.run`). The path check goes past the
  ruling's VALUE, since a path with such a byte truncated the file the same way.
  - Tests, `tests/test_config_set_types.py`: `test_not_utf8_refused` (a text key, a variant, an
    `[export]` entry and an untyped path, each under `--local` and `--user`: exit 2, the message, no
    traceback, the file byte for byte and its backups as before, no exception log) and
    `test_not_utf8_path_refused`.
  - Red on `f8293303`: 9 of 9 failed, each at exit 5 for 2 (`UnicodeEncodeError`, an exception log,
    a backup made). Green: 9 passed (the file: 72 passed).
  - Drive mode `set` gains the check (`printf 'a\377'` under `database.pragmas.x`: exit 2, the
    message, the file and the backup count unchanged); on the base source it fails there at exit 5.
  - P2's verify: 373 passed; drive `set` ok.

- **F3 (P3) — an eviction period past the monitor's range.** `check_evict` refuses a period over
  `EVICT_MAXIMUM`, `timedelta.max.days * 86_400` seconds (999,999,999 days, 86399999913600 s), with
  `RuntimeError`: `Evict period must be at most 86399999913600 seconds: given <seconds>`. Both
  commands already call it at their start check, so `hs server` and `hs cluster` exit 5 cleanly,
  before a database, a port or a server, as for an infinite period. The floor and finiteness checks
  run first, so their messages are unchanged. `server.py` changes only `check_evict`, its new
  constant beside `EVICT_MINIMUM`, and that constant's name in `__all__`.
  - Tests, `tests/test_cluster_evict.py`: `REFUSALS` gains `over-cap` (`HYPERSHELL_SERVER_EVICT` one
    past the cap) and `file-float-huge` (a file's TOML float `1e300`; `evict_layer` takes a `toml`
    layer that writes the value as given), so `test_cluster_evict_refused` covers them in every
    mode and `test_server_evict_refused` for `hs server`; `test_evict_over_cap_cli` runs a real
    `hs server` and `hs cluster` with that file (exit 5, the message, never listening, no traceback
    file). The tests compute the cap from `timedelta` rather than import it.
- **`server.eager`'s message (P3, the maintainer's ruling).** `configured_eager` finds the form the
  deciding layer gives (`eager`, `eager_env` or `eager_eval`, as `config_seconds` does). The
  message names the key and its source; the value is named only for the direct form:
  `server.eager: expected a boolean (…), given 'maybe' (from: …)` as before, and
  `server.eager_env: expected a boolean (…) (from: …)` for a variant. A `server.eager_env` naming a
  variable that is not set gives `server.eager_env names <VAR>, which is not set (from: …)`. Before,
  either variant ended in a `KeyError` traceback (exit 5), since `blame` was asked for the direct
  form.
  - Tests, `tests/test_config_unread.py`: `test_eager_variant_refused` (file and environment, `_env`
    and `_eval`, `hs server` and `hs cluster`: exit 3, the form and the source named, the resolved
    value absent, no traceback file) and `test_eager_env_unset_refused`.
- Red on `f8293303` (its `server.py`): all 24 new P3 cases failed: the 14 eviction cases at the
  message (an `OverflowError`, or the run going on past the start check), the 10 eager cases at
  exit 5 for 3 (`KeyError: "Not found: ('server', 'eager')"`). Green: 24 passed.
- Drive: mode `eager` gains `eager_env` naming a variable set to `notaword` (exit 3, key and source
  named, `notaword` absent, no exception log); mode `evict` gains a file's `evict = 1e300` (exit 5,
  the message, never listening, no exception log). On the base `server.py` both new checks fail
  (exit 5 with the `KeyError`; the `OverflowError` message).
  - P3's verify: 26 passed (`-k 'eager or attempts'`), then 516 passed, 1 skipped (6 min 14 s); drives
    `eager` and `evict` ok.

- **F2 (P4) — a non-finite `client.ratelimit`.** `ClusterApp.check_arguments`, after the eager
  check, refuses a rate that is a number but not finite, with exit 2 as `-R` refuses one:
  `client.ratelimit: Rate inf is not valid: expected a finite number (from: …)`. Such a number can
  only come from a file's TOML float (`inf`, `nan`, `-inf`); a rate given as text, from a file, a
  variable, or an `_env` or `_eval` form, is already read by `-R`'s own type as its default, and
  refused there with exit 2. So every source is refused at start, before a database, a port, a
  server or a launch, in every mode. `hs client` is unchanged (the ruling names `hs cluster`).
  - Tests, `tests/test_config_unread.py`: `test_cluster_rate_not_finite_refused` (a file's `inf`,
    `nan`, `-inf`, in local and `--launcher` modes: exit 2, the message, the file named, nothing
    run or launched, no traceback file). Guards, green on the base too:
    `test_cluster_rate_text_not_finite_refused` (text `"inf"` in a file, `ratelimit_env` to `inf`,
    `HYPERSHELL_CLIENT_RATELIMIT_EVAL` printing `nan`: exit 2 from the option, nothing launched),
    `test_cluster_rate_option_over_not_finite_key` (`-R` still overrides such a key and the run
    completes) and `test_cluster_rate_file_text` (a file's `"0.5"`/`"5"` reads as `0.5`/`5`).
  - Red on `f8293303` (its `cluster/__init__.py`): the 6 `not_finite_refused` cases failed: local
    `inf` and `nan` ran with no limit (exit 0), local `-inf` exited 5, and each `--launcher` case
    was still running at the 60 s bound (its client refused `-R Infinity`), so `run_bounded` stopped
    it and failed the test. The 9 guards passed. Green: 15 passed.
  - Drive mode `rate` gains a file's `ratelimit = inf` under `--launcher=env` (exit 2, the message,
    never listening, no task output); on the base source it fails there (exit 5,
    `ClusterLaunchError: every launched client has exited with tasks remaining`).
  - P4's verify: 688 passed, 1 skipped (2 min 40 s); drive `rate` ok.

- **F5 and F6 (P5) — docs.**
  - `docs/_include/config_set_desc.rst`: an empty value for a key that takes text is written as
    `""` and overrides the key in a lower file; the page says only of an optional text key (such
    as `database.user` or `server.bind`) that an empty variable would not, being read as not given.
    It also says a path or value that cannot be written as UTF-8 is refused with exit 2 (F1).
  - `docs/_include/config_param_ref.rst`: `.eager` lists every accepted word, `1`/`0` included
    (as a number or as text), for a value other than TOML `true`/`false` (F6); `.evict` adds "or
    over 999,999,999 days" to the refusals of `hs server` and `hs cluster` (F3); `[client]
    .ratelimit` says `hs cluster` stops before it launches a client if the value is not a finite
    number (F2). The existing `versionchanged:: 2.9.0` markers cover these entries.
  - `docs/cli/server.rst` and `docs/cli/cluster.rst`: the `server.eager` markers said "Text in a
    file takes the boolean words, and any other value stops…", F6's omission in a second place;
    each now says "A value in a file … takes the boolean words, `1`/`0` included".
  - Gate retune (`set_phase.py --phase P5 --verify`): four greps before the tests, that `.eager`
    lists `1`/`0` "as a number or as" text, that `config_set_desc.rst` holds "For an optional text
    key" and no longer ends a line on "where an empty variable would not.", and that each of
    `docs/cli/server.rst` and `docs/cli/cluster.rst` says "`1`/`0` included" once. On
    `f8293303`'s four files the greps fail (the `.eager` clause and the F5 sentence absent, the old
    sentence present, neither page saying "included"); here they pass.
  - P5's verify (sandbox off, for the docs build): 232 passed; `sphinx-build -W` under 3.12 exits 0,
    and the new sentences render in `cli/config_set.html`, `cli/cluster.html` and `config.html`.

- **P6 re-run (the whole gate, here, Python 3.14, serial):** the whole suite 3824 passed, 138
  skipped (25 min 53 s); the 99 red ids passed; `red_check` 99 of 99 red on `d9901cf0`; every
  drive mode ok (`set`, `eager`, `evict`, `rate`, each with its new check); the docs build `-W`
  exits 0. The suite and drives ran in the sandbox, the docs build with it off. 28 min 5 s for the
  sandboxed part.
- **Gaps left as they were:** `hs client` reading a non-finite `client.ratelimit` from a file still
  runs with no limit (the ruling names `hs cluster` only). A `client.ratelimit` given as text from
  an `_env` or `_eval` form that is no number is refused by `-R`'s own type, whose message names
  the option and repeats the text; the `server.eager` message ruling does not reach it.

## Review-cycle-2 remediation (2026-11-22)

Under the review-cycle-2 rulings at the end of [`PLAN.md`](PLAN.md) (maintainer sign-off for the human
gate F8 and F9 raised; F7, F8, F9 and F10 from [`REVIEW.md`](REVIEW.md), and the ruling on
`hs config set`'s refusal messages). P2–P6 were reopened together through `set_phase.py` and each
closed again with its own commit; P1 stays closed, as in cycle 1. Each new test's red run is on
`d6d83afa` (the reviewed commit), shown by putting the source change aside in the worktree (the
file as of `d6d83afa`), running the new tests, and restoring it. `red.txt` stays at 99.

- **F7 (P2) — a locale whose encoding is not UTF-8.** `update()` (`src/hypershell/core/config.py`)
  opened the file with no `encoding=`, so it read and wrote in the locale's encoding while every
  reader decodes UTF-8. It now reads and writes the file as UTF-8 whatever the locale (the three
  `open()` calls of the write path; a backup is a byte-for-byte `shutil.copy` of the file, so it is
  UTF-8 as the file is), and makes the new text before it opens the file for writing.
  `ConfigSetApp.run` and `update()` are the whole write path; no other `open()` is in it.
  - Tests, `tests/test_config_set_types.py`: `test_latin1_locale_written_as_utf8` (under a Latin-1
    locale, `logging.datefmt` set to a euro sign: exit 0; the file decodes as UTF-8 and holds the
    seed's `server.port` and the value; the one backup holds the seed; no exception log) and
    `test_latin1_locale_read_back` (an `é` set under a Latin-1 locale, then a second set there, then
    `hs config get -r` of both keys under the test's own locale gives them back). The child runs
    `hs` with its arguments as JSON, so the value reaches it as text whatever the platform's argv
    decoding, with `LC_ALL` set to the first Latin-1 locale the host has (`PYTHONUTF8=0`); a host
    with none skips the two tests.
  - Red on `d6d83afa`: both failed: the euro sign at exit 5 for 0 (`UnicodeEncodeError`, an exception
    log, the file truncated), the `é` at the second set, exit 3 for 0 (`UnicodeDecodeError` reading
    the Latin-1 byte back).
- **Refusal messages (P2, the maintainer's ruling).** A refusal in `hs config set` names the key
  (`src/hypershell/config.py`, `ConfigSetApp.run`, for the UTF-8 refusal and the key's-kind
  refusal; a key form is recognised by `is_key_variable` on the key's variable).
  - Tests, `tests/test_config_set_types.py`: `test_key_refusal_names_key` (`server.auth`,
    `server.auth_env`, `relay.upstream.auth`, `relay.upstream.auth_eval`: exit 2, the key named, the
    file as before) and the guard `test_other_refusal_names_value` (`logging.datefmt`).
  - Red on `d6d83afa`: the 4 `names_key` cases failed at the message; the guard passed. Green: 7
    passed.
- Drive mode `set` gains both checks (`server.auth_env` refused, exit 2, the key named; under
  `en_US.ISO8859-1`, when the host has it, a euro sign is written as UTF-8, the rest of the file
  kept, and a later `hs config get` reads it). On `d6d83afa`'s `config.py` the first fails at the
  message; on its `core/config.py` the second fails at the set (exit 5).
- P2's verify: 380 passed (1 min 29 s); drive `set` ok.

- **F8 (P3) — an integer eviction period wider than a float.** `check_evict`
  (`src/hypershell/server.py`) called `math.isfinite` before its cap check, and `math.isfinite`
  raises `OverflowError` for an integer wider than a float. It now compares against `EVICT_MAXIMUM`
  right after the floor check, exactly for an integer of any width; a period over the cap is refused
  with the cap's message, or with the finite-number message when it is infinite (a comparison with
  `math.inf`, which converts nothing). So `hs server` and `hs cluster` exit 5 cleanly, before a
  database, a port or a server. The floor's message and the messages for `inf`, `nan` and `-inf` are
  unchanged. Only `check_evict` changes.
  - Tests, `tests/test_cluster_evict.py`: `REFUSALS` gains `int-huge` (`HYPERSHELL_SERVER_EVICT` a
    401-digit integer), `file-int-huge` (the same as a file's TOML integer) and `days-huge` (the same
    number of days as a duration), so `test_cluster_evict_refused` covers them in every mode and
    `test_server_evict_refused` for `hs server`.
  - Red on `d6d83afa` (its `server.py`): 18 of 18 failed at the message (`OverflowError: int too
    large to convert to float`, an exception log). Green: 18 passed.
  - Drive mode `evict` gains the 401-digit integer under `hs cluster` (exit 5, the message, never
    listening, no exception log); on `d6d83afa`'s `server.py` it fails at the message.
  - P3's verify: 26 passed (`-k 'eager or attempts'`), then 534 passed, 1 skipped (6 min 20 s); drives
    `eager` and `evict` ok.
- **F9 (P4) — a rate so small the executor's wait overflows.** The floor is `RATE_MINIMUM = 1e-6`
  tasks per second (`src/hypershell/core/types.py`), one task in about 11.6 days.
  - **Why this value.** A client's executor waits `timedelta(seconds=1/rate)` between tasks on each
    thread, where `rate` is the client's rate divided by its thread count (`client.py`, unchanged).
    A `timedelta` holds at most 86,399,999,913,600 seconds, so at the floor the wait overflows only
    with more than 86,399,999 threads in one client, twenty times the most thread ids a Linux host
    gives a process (4,194,304, its `pid_max` ceiling); other platforms allow fewer. Under the floor,
    `1e-7` would leave a margin of two, and the reviewer's `1e-14` none. No realistic limit is
    affected: the slowest rate a user would set is far above one task in 11.6 days.
  - **What is refused.** A rate other than 0 whose size is under the floor: `0 < abs(rate) <
    RATE_MINIMUM`. `0` stays no limit (R8), and a negative rate is read as before; a tiny negative
    one is refused too, since its wait overflows the same way.
  - `parse_rate` refuses it, so `-R` on `hs client` and `hs cluster`, and any `client.ratelimit`
    given as text (which `-R`'s type reads as its default), exit 2 naming `-R/--ratelimit`:
    `Rate '1e-300' is not valid: expected 0 (no limit) or at least 0.000001 tasks per second in size`.
    `ClusterApp.check_arguments` refuses a file's TOML float under the floor after the finite check,
    exit 2, naming `client.ratelimit` and its source, before a database, a port, a server or a launch.
    `client.py` is not changed.
  - Tests, `tests/test_config_unread.py`: `test_rate_option_under_floor_refused` (`1e-300`, `9e-7`,
    `-1e-300` on both commands), `test_cluster_rate_under_floor_refused` (a file's `1e-300` in local
    and `--launcher` modes: exit 2, the message, the file named, nothing run or launched, no
    traceback file). Guards, green on the base too: `test_rate_option_takes_number` gains `1e-6`,
    and `test_cluster_rate_at_floor` (a file's `1e-6` passes the start check as given).
  - Red on `d6d83afa` (its `core/types.py` and `cluster/__init__.py`): 8 of 8 failed (the option
    took each rate; the cluster ran into the executor's `OverflowError`). The guards passed. Green:
    23 passed.
  - Drive mode `rate` gains a file's `ratelimit = 1e-300` under `--launcher=env` (exit 2, the
    message, never listening, no task output) and `-R 1e-300` (exit 2, naming the option). On
    `d6d83afa`'s two files the drive hangs at the first, as the review found, until its bound.
  - P4's verify: 699 passed, 1 skipped (2 min 39 s); drive `rate` ok.
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
