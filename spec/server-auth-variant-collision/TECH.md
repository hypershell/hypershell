---
slug: server-auth-variant-collision
title: auth_env and auth_eval in a config file work, and fail cleanly when they cannot resolve
kind: fix
appetite: small
status: in_progress
branch: fix/server-auth-variant-collision
base: develop
current_phase: P1
last_updated: '2026-11-11'
phases:
- id: P1
  name: 'The guard: cmdkit 2.8.0, resolve_auth, the import-time two-forms scan, hs config get'
  status: pending
  satisfies:
  - R2
  - R3
  - R6
  - R7
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'uv lock --check && uv run pytest -v tests/test_auth_variants.py && .agents/factory/bin/temp_site.sh
    sh -c ''printf "[server]\nauth_env = \"AUTHVAR_KEY\"\n" > c.toml && HYPERSHELL_CONFIG_FILE=c.toml
    AUTHVAR_KEY=abcdefghijklmnop0123 uv run hs config get server.auth -r | grep -qx abcdefghijklmnop0123
    && ! HYPERSHELL_CONFIG_FILE=c.toml uv run hs config get server.auth -r'''
- id: P2
  name: 'Commands resolve the key at run (server, submit -q, client), whats_new, 2.8.6 pick gate'
  status: pending
  satisfies:
  - R1
  - R5
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'uv run pytest -v tests/test_auth_variants.py tests/test_server.py tests/test_submit.py
    tests/test_client.py && .agents/factory/bin/temp_site.sh sh -c ''printf "[server]\nauth_eval
    = \"exit 4\"\n" > c.toml; HYPERSHELL_CONFIG_FILE=c.toml uv run hs server --forever 2> e.txt; test
    $? -eq 3 && grep -q server.auth_eval e.txt && ! grep -q Traceback e.txt'' && uv run --python 3.12
    --group docs sphinx-build -W --keep-going -q -E -b html docs docs/_build/html && W=$(git stash
    create) && ! git merge-tree --write-tree --name-only --no-messages --merge-base="$(git log -1 --format=%H
    --grep=''Plan server-auth-variant-collision'')" 2.8.6 "${W:-HEAD}" | tail -n +2 | grep -v -e ''^$''
    -e ''^docs/whats_new.rst$'' -e ''^spec/'''
- id: P3
  name: 'Relay: upstream_key delegates to resolve_auth; hs config get relay.upstream.auth (2.9 only)'
  status: pending
  satisfies:
  - R4
  depends_on:
  - P1
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'uv run pytest -v tests/test_relay_launch.py -k "upstream_key or auth" && uv run pytest
    -v tests/test_auth_variants.py && W=$(git stash create) && ! git merge-tree --write-tree --name-only
    --no-messages --merge-base="$(git log -1 --format=%H --grep=''Plan server-auth-variant-collision'')"
    2.8.6 "${W:-HEAD}" | tail -n +2 | grep
    -v -e ''^$'' -e ''^docs/whats_new.rst$'' -e ''^spec/'' -e ''^src/hypershell/relay/upstream.py$''
    -e ''^tests/test_relay_launch.py$'''
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — `auth_env` and `auth_eval` in a config file work, and fail cleanly

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run --no-project --with pyyaml python .agents/factory/bin/next_phase.py spec/server-auth-variant-collision/TECH.md`);
the per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `set_phase.py`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md) (§2 has the guard, the call sites and the 2.8.6
  drift and pick plan).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–02.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done`.
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`).
- `depends_on`: phase ids that must be `done` first.
- `parallel`: `false` here — every phase touches the coupled core (`core/config.py`, `server.py`,
  `client.py`) or builds on it.
- `hammerable`: `false` — correctness/security phases; scope-hammering must never cut them.
- `hill`: `uphill` → `crest` → `downhill`.
- `attempts`: durable failed-verify counter, bumped by `set_phase.py --phase P<n> --record-attempt`.
- `verify`: the exact command that proves the phase.
- `review.cycle`: completed review passes.

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; footguns in
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §9, §10, §12.
- One phase per `hs-build` invocation; one atomic commit with the code and the `TECH.md` state change,
  subject `[fix] Build server-auth-variant-collision P<n>: …`. **No `Co-Authored-By` trailer.**
- **Cherry-pick discipline (2.8.7).** The squash commit is picked onto `2.8.6` after `15587cf`.
  Edit only lines PLAN §2 lists as identical on `2.8.6`: in `core/config.py` leave the last
  `__all__` line, the defaults and everything after the import-time `try` byte-identical (new
  `__all__` names go on a new line after `'config', 'update', 'default', …`; new definitions go
  between `get_logging_style` and the `try`). Do not touch `core/queue.py`. Add `resolve_auth` to the
  existing `core.config` import line in `server.py`, `submit.py`, `client.py`. Relay code and tests
  stay in `src/hypershell/relay/upstream.py` and `tests/test_relay_launch.py` (P3 only).
- Tests use only helpers both lines have (`tests.main`, `tests.main_lines`, `temp_site`,
  `monkeypatch`, `tmp_path`, `cmdkit.config`). CLI tests run `hs` as a subprocess with
  `HYPERSHELL_CONFIG_FILE` pointing at a `tmp_path` TOML (config is built once per process). Tests
  and docs never pass a key with `-k`; docs name top-level commands only.
- Exit codes from `cmdkit.app.exit_status` (`bad_config`); no integer literals in code.
- No error message or log line contains a resolved key.
- `uv run` / `uv lock` need the sandbox disabled.

---

## Phase P1 — The guard
**Satisfies:** R2, R3, R6, R7 · **Depends on:** —
**Goal:** cmdkit 2.8.0 is required and locked; `resolve_auth` and `check_auth_forms` exist; a file with
two forms of a key stops every command with one line and `bad_config`; `hs config get server.auth`
prints the resolved value, or fails naming the key.

- [ ] `pyproject.toml`: `cmdkit[toml]>=2.7.7` → `cmdkit[toml]>=2.8.0`. Then
      `uv lock --upgrade-package cmdkit` (sandbox off). Check `git diff -- uv.lock` touches only the
      `cmdkit` package block (version, sdist, wheel) and the `cmdkit` `requires-dist` specifier; if
      `uv` rewrote anything else, restore `uv.lock` and redo with that command only. Never hand-edit.
- [ ] `core/config.py`: `import subprocess`; add `display_critical` to the `core.exceptions` import;
      define `AUTH_FORMS`, `AUTH_SECTIONS`, `check_auth_forms(base)` and
      `resolve_auth(*section, base=None)` after `get_logging_style` (messages exactly as PLAN §2);
      add the four names on a new `__all__` line after the first one.
- [ ] `core/config.py` import-time `try`: call `check_auth_forms(config)` after the `if/else`; add
      `except ConfigurationError as error: display_critical(error, module=__name__);
      sys.exit(exit_status.bad_config)` before `except Exception`.
- [ ] `config.py` `ConfigGetApp.run`: merged view, section in `AUTH_SECTIONS`, variable `auth` →
      `self.print_output(resolve_auth(*section))` and return (with or without `-x`).
- [ ] `tests/test_auth_variants.py` (new): `@mark.unit` `test_resolve_*` over `Configuration` layers —
      literal, `auth_env` set, `auth_eval` ok; R3 both directions (file `auth` under env-layer
      `auth_env`, and the reverse); unset → names `server.auth_env` and the variable; `exit 4` →
      names `server.auth_eval` and `4`; two forms in the deciding layer and in a lower layer → names
      `server.auth` and `auth, auth_env`; the key never appears in a message; an absent section → not
      found. `@mark.integration` `test_config_get_*`: `hs config get server.auth -r` prints the
      `auth_env` and `auth_eval` values (exit 0); variable unset → exit `bad_config`, stderr names
      `server.auth_env`, no `Traceback`. `@mark.integration` `test_two_forms_*`: `hs config get
      logging.level` and `hs list` with `auth` + `auth_env` in one file → exit `bad_config`, stderr
      names `server.auth` and the file, no `Traceback`, no "traceback written".
- **Verify:** `uv lock --check`, the new test file, and a `temp_site` drive (`hs config get
  server.auth -r` prints the `auth_env` value when set, fails when unset).
- **Touches:** `pyproject.toml`, `uv.lock`, `src/hypershell/core/config.py`,
  `src/hypershell/config.py`, `tests/test_auth_variants.py`.

## Phase P2 — Commands resolve the key at run
**Satisfies:** R1, R5 · **Depends on:** P1
**Goal:** no command reads the key at import; `hs server`, `hs submit -q` and `hs client` use the
resolved key, and fail with `bad_config` and the key named when it cannot resolve; the change still
picks onto `2.8.6`.

- [ ] `server.py`: `auth: Optional[str] = None` (comment: without `-k`, `resolve_auth('server')`, see
      run); in `ServerApp.run` after `special_options()` and before `check_args()`, resolve when
      `None`. Leave `check_args` unchanged.
- [ ] `submit.py`: `queue_auth: Optional[str] = None`; in `run` inside `if self.queue_mode:` before
      `QueueConfig(...)`, resolve when `None`.
- [ ] `client.py`: `self.auth = config.server.auth` → `self.auth = resolve_auth('server')`.
- [ ] Add `resolve_auth` to the `core.config` import line of each of the three modules.
- [ ] `tests/test_auth_variants.py`: `@mark.integration` `test_command_*` — `hs server` (task file)
      and `hs client` as subprocesses with `[server] auth_env` set, tasks complete (R1); `hs server
      --forever`, `hs client`, `hs submit -q` with the variable unset, and with `auth_eval = "exit
      4"` → exit `bad_config`, stderr names the key, no `Traceback` (R5); variable unset but
      `HYPERSHELL_SERVER_AUTH=tooshort` → `hs server` fails on key length, not on configuration
      (R3). `test_config_get_*`: the `auth_eval` failure case for `hs config get` (exit
      `bad_config`, names `server.auth_eval`, no `Traceback`).
- [ ] `docs/whats_new.rst`: *Not yet released → Fixes* entry below the units entry: a configuration
      file can give `server.auth` as `auth_env` or `auth_eval`, as the security guide says; before,
      every command stopped with a traceback; an unset variable, a failing command, or two forms of
      the key in one file is now a configuration error naming the key. Ends "This also ships in
      2.8.7."
- [ ] Build-time pick check (no ref moved): `T=$(git merge-tree --write-tree --merge-base=15587cf^
      2.8.6 15587cf | head -n 1)`; `S=$(git commit-tree "$T" -p 2.8.6 -m sim)`; run the verify's
      merge-tree step with `$S` in place of `2.8.6`; same exclusions. Note the result in the commit
      body.
- **Verify:** the new test file plus `tests/test_server.py`, `tests/test_submit.py`,
  `tests/test_client.py`; a `temp_site` drive (`hs server --forever` with a failing `auth_eval`
  exits 3, names `server.auth_eval`, no traceback); the Sphinx `-W` build; the merge-tree gate against
  `2.8.6` (conflicts only in `docs/whats_new.rst` and `spec/`).
- **Verify note:** the merge-tree step reads the working tree, not `HEAD`: `git stash create` writes
  a commit of the tracked changes without touching the stash stack or any ref, and prints nothing on
  a clean tree, so `"${W:-HEAD}"` is the uncommitted phase before the commit and `HEAD` after it.
  New untracked files are left out, which is safe: a file absent on both the merge base and `2.8.6`
  cannot conflict. (This replaces the units fix's manual `commit-tree` step; same for P3.)
- **Touches:** `src/hypershell/{server,submit,client}.py`, `docs/whats_new.rst`,
  `tests/test_auth_variants.py`.

## Phase P3 — Relay (2.9 only)
**Satisfies:** R4 · **Depends on:** P1, P2
**Goal:** `relay.upstream.auth` behaves as `server.auth` does for R1, R2, R5, through the same guard;
every edit is in a path absent on `2.8.6` except the `whats_new` clause.

- [ ] `relay/upstream.py`: `upstream_key(base=None)` → `return resolve_auth('relay', 'upstream',
      base=base)`; import `resolve_auth`; drop `AUTH_VARIANTS` and the layer walk; keep the docstring's
      precedence sentence accurate.
- [ ] `tests/test_relay_launch.py`: keep `test_upstream_key_forms` and
      `test_upstream_key_refuses_two_forms_in_one_place` passing; add `@mark.unit` cases for an
      `auth_eval` failure (names `relay.upstream.auth_eval`), the unset message (names the variable),
      and a lower-layer duplicate; add `@mark.integration` `test_config_get_relay_upstream_auth`
      (subprocess, `[relay.upstream] auth_env`: resolved value printed; unset → `bad_config`, key
      named, no `Traceback`).
- [ ] `docs/whats_new.rst`: extend the P2 entry with "`relay.upstream.auth` in `[relay.upstream]`
      likewise".
- **Verify:** the relay key tests, the shared test file, and the merge-tree gate against `2.8.6` with
  the two relay paths added to the dropped set.
- **Touches:** `src/hypershell/relay/upstream.py`, `tests/test_relay_launch.py`, `docs/whats_new.rst`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file if reality diverges (note it in the commit body). STOP and escalate only on a
   **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.

At publish, the squash commit body carries the release note (master has no `whats_new.rst`). The 2.8
pick (after `15587cf`) drops `docs/whats_new.rst`, `spec/`, `src/hypershell/relay/upstream.py` and
`tests/test_relay_launch.py`, then runs `uv lock --check` and `tests/test_auth_variants.py` on the
2.8 branch.
