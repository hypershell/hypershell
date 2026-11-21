---
slug: config-set-and-unread
title: hs config set writes each key by its type, and hs server and hs cluster read
  the keys they document
kind: fix
appetite: small
status: in_progress
branch: fix/config-set-and-unread
base: develop
current_phase: P1
last_updated: '2026-11-21'
phases:
- id: P1
  name: 'Tests first: the red list on d9901cf0 (tests only)'
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
  status: pending
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
  status: pending
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
  status: pending
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
  status: pending
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
    ''hs task '' docs/_include/config_set_desc.rst docs/cli/config_set.rst && sh spec/config-set-and-unread/drive/bound.sh
    600 uv run pytest -q -p no:cacheprovider tests/test_config_schema.py tests/test_config_set_types.py
    && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-csu sh spec/config-set-and-unread/drive/bound.sh
    900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html
    docs /tmp/claude-501/docs-html-csu'
- id: P6
  name: 'Gate: the whole suite serially, the red list, the drive, the docs'
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
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
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

- [ ] `tests/test_config_set_types.py`, `tests/test_config_unread.py`, `tests/test_cluster_evict.py` from
      the prototypes (see above), otherwise unchanged.
- [ ] Nothing outside `tests/` and `spec/` changes. A renamed or split test is renamed in `red.txt` too
      (never dropped without a PLAN note).
- **Verify:** see frontmatter: the fence; `red.txt` holds 99 ids; `red_check` 99 of 99 red;
  `rest_green` over the three files and their neighbours. Dry-run at plan time: as stated above.
- **Touches:** `tests/test_config_set_types.py`, `tests/test_config_unread.py`, `tests/test_cluster_evict.py`.

## Phase P2 — `hs config set` writes each value by its key's type (R1–R4)
**Satisfies:** R1, R2, R3, R4 · **Depends on:** P1
**Goal:** `hs config set` reads VALUE by the key's kind through `match_path` + `read_value`; a value
that does not convert exits 2, naming key, form and value, with nothing written.

- [ ] `core/config_schema.py`: `match_path(path) -> Kind` after `match_name` (PLAN §2.1 order), in
      `__all__`; `read_value`'s `LOGGING_FILE` path branch refuses `''` (`text != ''`). Do not touch
      `KEYS`/`EMPTY_UNSET` (#116 edited them).
- [ ] `config.py` `ConfigSetApp`: drop `type=smart_coerce` and its import; in `run()`, after the
      one-word check, `value = read_value(match_path(self.varpath), self.value)`, a `ValueError` raised
      as `ArgumentError(f'{self.varpath}: expected {error}, given {self.value!r}') from None`; write
      `value`. Anchor in `ConfigSetApp.run`, not `ConfigGetApp.run` (same `split` line).
- [ ] `tests/test_config_schema.py`, **appended at the end**: a `@mark.unit` `match_path` table covering
      every PLAN §2.1 case (documented keys of each kind, both variants, `export.RUN_ID`,
      `ssh.nodelist.gpu`, zone keys known and unknown, `database.connect_args.x`, `database.pragmas.x`,
      `database.sslmode`, `server.nothing`, `foo.bar`, `Server.eager`, `server.port.x`); `logging.file`
      `''` among the `read_value` refusals. Not red-listed (they import the new name).
- **Verify:** `tests/test_config_set_types.py`, `test_config_schema.py`, `test_env_config.py`,
  `test_main.py`; drive mode `set`.
- **Touches:** `src/hypershell/core/config_schema.py`, `src/hypershell/config.py`, `tests/test_config_schema.py`.

## Phase P3 — `server.eager`, `server.attempts` and `server.evict` reach `hs server` and `hs cluster` (R5–R7)
**Satisfies:** R5, R6, R7 · **Depends on:** P2
**Goal:** both apps keep the key when `--eager` is absent and read it by the boolean words at their
start check; `hs cluster` takes `-r` from `server.attempts`; `hs cluster` reads `server.evict` at its
start check, refuses it as `hs server` does before any database, port or server, and hands the period
to the server in every mode.

- [ ] `server.py`: `configured_eager(value) -> bool` beside `DEFAULT_EAGER_MODE` (a `bool` as is, else
      `read_value(Kind.BOOL, str(value))`, else `ConfigurationError` naming `server.eager`, the form,
      the value and `blame(config, 'server', 'eager')`); `EVICT_MINIMUM = 10` and
      `check_evict(seconds)` beside `DEFAULT_EVICT` (PLAN §2.4 messages; non-finite refused);
      `HeartMonitor.__init__` calls `check_evict` in place of its inline check (same `wait_check` and
      `evict_after`); `ServerApp`: `--eager` gets `default=eager_mode`, `check_args` reads
      `self.evict_after = check_evict(config_seconds('server', 'evict'))` and
      `self.eager_mode = configured_eager(self.eager_mode)`. Imports: `math`, `ConfigurationError`,
      `blame`, `Kind`/`FORMS`/`read_value`. Do not edit 1213–1215.
- [ ] `cluster/__init__.py` `ClusterApp`: `max_retries = config.server.attempts - 1`; `--eager`
      `default=eager_mode`; class attribute `evict_after: int | float = DEFAULT_EVICT`;
      `check_arguments`, right after the `autoscale.period` block:
      `self.evict_after = check_evict(config_seconds('server', 'evict'))` and
      `self.eager_mode = configured_eager(self.eager_mode)`; `run` passes `evict_after=self.evict_after`.
      Do not edit 86–88.
- [ ] `cluster/local.py`, `cluster/remote.py` (`RemoteCluster`), `cluster/ssh.py`: a last keyword
      `evict_after: int = DEFAULT_EVICT` passed to `ServerThread(...)`, a docstring Args entry, the
      import where missing. `AutoScalingCluster` unchanged.
- **Verify:** `tests/test_config_unread.py -k 'eager or attempts'`, `test_cluster_evict.py` and the
  cluster/server neighbours; drive modes `eager`, `evict`.
- **Touches:** `src/hypershell/server.py`, `src/hypershell/cluster/__init__.py`,
  `src/hypershell/cluster/local.py`, `src/hypershell/cluster/remote.py`, `src/hypershell/cluster/ssh.py`.

## Phase P4 — `-R` takes a number, and `hs cluster` reads `client.ratelimit` (R8)
**Satisfies:** R8 · **Depends on:** P3
**Goal:** `-R` takes a finite number on both commands; `hs cluster` defaults it from
`client.ratelimit` (not `task.ratelimit`) in every mode; a launched client gets a whole rate as an
integer word and a fraction as a plain decimal.

- [ ] `core/types.py`: `parse_rate(value) -> int | float` beside `parse_period`, over
      `config_schema.read_number` (top-level import), raising `UnitError` (`Rate {value!r} is not
      valid: expected a number`); in `__all__`.
- [ ] `client.py`: `-R` `type=parse_rate`; the `ratelimit` annotations/docstrings say a number
      (`Optional[float]`). **Do not edit 1312–1314** (they already divide as floats).
- [ ] `cluster/__init__.py`: `ratelimit: Optional[float] = config.client.ratelimit or None`,
      `type=parse_rate`.
- [ ] `cluster/launch.py`: `plain_rate(value)` beside `plain_seconds` (doctest example), used for `-R`
      under the existing `if ratelimit is not None`; annotations in `client_launch`/`client_options`;
      `local.py`, `remote.py`, `ssh.py` rate annotations and docstrings.
- [ ] `tests/test_cluster_launch.py`: one comment at `CONTRACT_CLIENT_OPTIONS`'s `-R` entry naming the
      sanctioned bend (a fractional rate needs a client of this release; PLAN §3).
- **Verify:** `tests/test_config_unread.py` (all of it now), `test_duration_options.py`,
  `test_cluster_launch.py`, `test_client.py`, `test_relay_launch.py`; drive mode `rate`.
- **Touches:** `src/hypershell/core/types.py`, `src/hypershell/client.py`, `src/hypershell/cluster/__init__.py`,
  `src/hypershell/cluster/launch.py`, `src/hypershell/cluster/local.py`, `src/hypershell/cluster/remote.py`,
  `src/hypershell/cluster/ssh.py`, `tests/test_cluster_launch.py`.

## Phase P5 — Docs, help snippets, markers; AGENTS.md and invariants in lockstep (R9)
**Satisfies:** R9 · **Depends on:** P2, P3, P4
**Goal:** every page and help line says what R1–R8 changed, marked `versionchanged:: 2.9.0`; the agent
docs state the two new rules.

- [ ] `hs config set`: `SET_HELP` `VALUE` line and `docs/_include/config_set_help.rst` ("Value to be
      set, written by the key's type."); `docs/_include/config_set_desc.rst` paragraph with
      `:ref:`by the key's type <config_value_types>`` (refusal exits 2, nothing written; a text key's
      `""` is written); label `.. _config_value_types:` before `config_intro.rst`'s "The value is read
      by the key's type:" (not lines 146–153); marker in `docs/cli/config_set.rst` between `|` and the
      help include.
- [ ] `docs/_include/config_param_ref.rst`: `.attempts`, `.eager` (boolean words for file text),
      `.evict` (the "stop before they start" sentence, PLAN §2.6; third marker in its block), `[client]
      .ratelimit` (a number; `hs client` and `hs cluster`), each with the house `.. only:: html` marker.
      No body line holding only a ``` ``.name`` ``` term.
- [ ] `-R` help in `client.py` and `cluster/__init__.py` `HELP`, `docs/_include/client_help.rst`
      (155–157) and `cluster_help.rst` (358–360): "a number, e.g. 0.5".
- [ ] Markers in `docs/cli/client.rst` (`-R`), `docs/cli/server.rst` (`server.eager`),
      `docs/cli/cluster.rst` (`server.eager`, `server.attempts`, `server.evict`, `client.ratelimit`,
      `-R`), placed above the existing 2.9.0 markers (PLAN §2.6).
- [ ] AGENTS.md § Configuration and invariants §10: `hs config set` writes by the key's kind
      (`match_path` + `read_value`; exit 2; nothing written). AGENTS.md's `client_launch` paragraph and
      invariants §11: a whole `-R` is an integer word; a fractional one needs a client of this release.
- **Verify:** greps for each edit, `tests/test_config_schema.py`, the docs build with `-W`.
- **Touches:** `src/hypershell/config.py`, `src/hypershell/client.py`, `src/hypershell/cluster/__init__.py`,
  `docs/_include/*.rst`, `docs/cli/{config_set,client,server,cluster}.rst`, `AGENTS.md`,
  `.agents/factory/invariants.md`.

## Phase P6 — Gate: the whole suite serially, the red list, the drive, the docs
**Satisfies:** R1–R9 · **Depends on:** P5
**Goal:** nothing else broke; every red test is green; the red list still proves the defects on
`d9901cf0`.

- [ ] The whole suite under Python 3.14, serially (no `-n`).
- [ ] The 99 red ids pass; `red_check` still reports 99 of 99 red on `d9901cf0`.
- [ ] Every drive mode; the docs build.
- **Verify:** see frontmatter.
- **Touches:** nothing (fixes found here go back to the phase that owns them, noted in the commit).

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
