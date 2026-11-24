---
slug: client-host-default-from-bind
title: hs client dials server.host when no -H is given
kind: fix
appetite: small
status: in_review
branch: fix/client-host-default-from-bind
base: develop
current_phase: done
last_updated: '2026-11-23'
phases:
- id: P1
  name: Tests first, strict xfail where the fix is missing (tests only)
  status: done
  satisfies:
  - R7
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: l="$TMPDIR/chd-p1.log"; test -z "$(git diff --name-only b88a09ae -- . ':!spec'
    ':!tests')" && UV_CACHE_DIR=/tmp/claude-501/uv-cache-host UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-host
    sh spec/relay-advertise/drive/bound.sh 600 uv run --frozen --python 3.14 pytest
    -q -p no:cacheprovider -rxX tests/test_client_host_default.py > "$l" 2>&1; r=$?;
    tail -n 30 "$l"; test $r -eq 0 && grep -q 'XFAIL.*seed' "$l" && grep -q 'XFAIL.*advertise'
    "$l" && ! grep -q XPASS "$l"
- id: P2
  name: 'One rule for every configured client dial: client_host, its three readers,
    help, docs, completion and agent docs'
  status: done
  satisfies: &id001
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'l="$TMPDIR/chd-p2.log"; export UV_CACHE_DIR=/tmp/claude-501/uv-cache-host;
    UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-host sh spec/relay-advertise/drive/bound.sh
    1800 uv run --frozen --python 3.14 pytest -q -p no:cacheprovider -rxX tests/test_client_host_default.py
    tests/test_config_schema.py tests/test_env_config.py tests/test_env_blame.py tests/test_config_unread.py
    tests/test_cluster_advertise.py tests/test_client_bundle_keys.py tests/test_client_request.py
    tests/test_submit.py tests/test_main.py tests/test_relay_advertise.py > "$l" 2>&1;
    r=$?; tail -n 30 "$l"; test $r -eq 0 && ! grep -q XPASS "$l" && ! grep -rq ''client-host-default
    P2'' tests && ! grep -q ''host: str = config.server.bind'' src/hypershell/client.py
    && grep -q ''default.server.host'' src/hypershell/core/queue.py && HYPERSHELL_CONFIG_FILE=
    UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-host sh spec/relay-advertise/drive/bound.sh
    120 .agents/factory/bin/temp_site.sh sh -c ''uv run --frozen hs client --help;
    uv run --frozen hs submit --help'' | grep -A2 -- ''-H, --host'' | grep -c ''server.advertise''
    | grep -qx 2 && zsh -n share/zsh/site-functions/_hs && grep -q client_host AGENTS.md
    && grep -q client_host .agents/factory/invariants.md && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-host
    sh spec/relay-advertise/drive/bound.sh 900 uv run --python 3.12 --group docs sphinx-build
    -W --keep-going -q -E -b html docs /tmp/claude-501/docs-html-host'
- id: P3
  name: 'The release note; gate: the whole suite serially under 3.14 and 3.11, the
    docs'
  status: done
  satisfies: *id001
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: n=spec/client-host-default-from-bind/RELEASE-NOTE.md; grep -q 'Behaviour
    change' "$n" && grep -q 'server.advertise' "$n" && grep -q 'submit --queue' "$n"
    && ! grep -Eq 'hs task|-k ' "$n" && export UV_CACHE_DIR=/tmp/claude-501/uv-cache-host
    && PATH=/tmp/claude-501/venv314-host/bin:$PATH UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-host
    sh spec/relay-advertise/drive/bound.sh 5400 uv run --frozen --python 3.14 pytest
    -p no:cacheprovider -q && PATH=/tmp/claude-501/venv311-host/bin:$PATH UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv311-host
    sh spec/relay-advertise/drive/bound.sh 5400 uv run --frozen --python 3.11 pytest
    -p no:cacheprovider -q && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-host
    sh spec/relay-advertise/drive/bound.sh 900 uv run --python 3.12 --group docs sphinx-build
    -W --keep-going -q -E -b html docs /tmp/claude-501/docs-html-host
review:
  last_reviewed_commit: 7ec82256
  verdict: changes-requested
  blocked_reason: 'F1 (LOW): .bind reference sentence misstates the fallback'
  cycle: 1
---
# TECH.md — `hs client` dials `server.host` when no `-H` is given

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/client-host-default-from-bind/TECH.md`).
The per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs
its `verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and
makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md). The R-IDs are the contract. Q1–Q4
  were ruled at the GOAL gate (2026-11-23), Q1 and Q3 by the maintainer.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) and its briefs.

## Frontmatter field reference

See [`.agents/factory/templates/TECH.md`](../../.agents/factory/templates/TECH.md).
- **Status values:** `planned | in_progress | blocked | in_review | done`.
- **Phase flags:** `parallel` is `false` throughout, because P2 touches the coupled core.
  `hammerable` is `false` throughout, because every phase carries correctness.
- **Circuit breaker:** `attempts`, recorded with `set_phase.py --record-attempt`.

## Conventions (apply to every phase)

- **The constitution.** [`AGENTS.md`](../../AGENTS.md), and
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §9–§12 for this change.
- **Commits.**
  - One phase per `hs-build` invocation, with one atomic commit holding both the code and the
    `TECH.md` state change.
  - Subjects follow `[fix] Build client-host-default-from-bind P<n>: …`.
  - Signed, with no `Co-Authored-By` trailer.
- **Docs.** A CLI change updates `docs/_include/*_help.rst` and `share/` completions **in the same
  commit**. Prose entries are marked `.. versionchanged:: 2.9.0` with no `:pr:`, which `hs-release`
  adds. The user-facing note is the squash body (`RELEASE-NOTE.md`).
- **Naming.** Name top-level commands only. Never show a key on a command line.
- **Tests.**
  - Run pytest serially, never with `-n`.
  - Use addresses that never resolve (`*.example.edu`, `adv.example`, `10.0.0.5`); nothing dials.
  - Set configuration through layers: environment, `file_layer`, or a constructed `Configuration`.
    Never mutate `config` and never patch it with a `Namespace`, or `which()` cannot see the layer.
- **Environments.**
  - `/tmp/claude-501/venv314-host` is this worktree's editable environment (Python 3.14, `uv sync`).
  - Create `/tmp/claude-501/venv311-host` and `/tmp/claude-501/docs-venv-host` on first use. The
    venvs need network (pypi.org, files.pythonhosted.org). The docs build needs the sandbox off
    (sitemap socket).

---

## Phase P1 — Tests first, strict xfail where the fix is missing (tests only)
**Satisfies:** R7 (the tests R1–R8 are graded by) · **Depends on:** —
**Goal:** every test this cycle needs is in the tree and collected. Each test that needs the fix is
a strict xfail with reason `client-host-default P2`; the rest pass. Nothing changes outside
`tests/` and `spec/`.

- [x] New `tests/test_client_host_default.py`. Its module docstring states the rule in the order of
  GOAL R3.
  - **The rule** (`@mark.unit`, xfail P2). Import it as
    `client_host = getattr(config_module, 'client_host', None)` so collection survives.
    Build each `Configuration` with a local `layered(**layers)` over `default`, as
    `tests/test_client_bundle_keys.py:31` does. Rows:
    - [x] `seed`: `local` has `bind="0.0.0.0"` and `host="login00.example.edu"` → `login00.example.edu`.
    - [x] `host` over `advertise`: `env` has `advertise`, `local` has `host` → the host.
    - [x] `advertise` over a specific `bind` → `adv.example`.
    - [x] Specific `bind` `10.0.0.5` → `10.0.0.5`.
    - [x] `bind` `0.0.0.0`, and `""` → `localhost`.
    - [x] Nothing given → `localhost`.
    - [x] `host="localhost"` with `advertise` and `bind` set → `localhost`.
    - [x] `advertise=""` and `"<none>"` from a file, with `bind` `10.0.0.5` → `10.0.0.5`.
    - [x] `host_env` naming a variable that is set (`monkeypatch.setenv`) → its value.
    - [x] `host_eval="echo login00.example.edu"` → `login00.example.edu`.
    - [x] `advertise_env` (set), with `bind` `10.0.0.5` → its value.
    - [x] `host_env` naming an unset variable raises `ConfigurationError`, and the message names
      `server.host_env`.
    - [x] `host` given only in the `_` layer (`Configuration.update`) → that host.
    - [x] Across all the rows above, the result is never `'<none>'`.
  - **`WILDCARD_BINDS`** (`@mark.unit`, xfail P2):
    - [x] `hypershell.core.config.WILDCARD_BINDS is hypershell.cluster.launch.WILDCARD_BINDS`.
  - **The readers** (`@mark.integration`). One child interpreter per case, cached for the module,
    via `config_repr` (`tests/test_env_config.py:84`) under the `temp_site` fixture, with
    `file_layer` (`tests/test_config_unread.py:36`) for file cases. The child evaluates a tuple:
    - `hs client`: build `ClientApp.from_cmdline([])`, call `.check_args()`, read `.host`;
    - `hs submit --queue`: build `SubmitApp.from_cmdline(['-q', 'echo', 'one'])`, call
      `.check_arguments()`, read `.queue_server`;
    - `QueueConfig.load().host`.

    Parametrize by reader × case. Mark a (reader, case) row xfail P2 only where HEAD differs (see
    [`research/02-tests.md`](research/02-tests.md), matrix). Case ids include `seed` and
    `advertise`. Cases:
    - [x] `seed` by file, and `seed-env` by environment.
    - [x] `host` over `advertise`.
    - [x] `advertise` over a specific `bind`.
    - [x] `advertise` by environment, with a wildcard `bind`.
    - [x] A specific `bind`.
    - [x] A wildcard `bind`.
    - [x] An empty `bind` from a file, and one from the environment.
    - [x] Nothing given.
    - [x] `host` given as `localhost`.
    - [x] A file's empty or `<none>` `advertise`.
    - [x] `HYPERSHELL_SERVER_HOST_ENV`.
  - [x] **`-H` wins** (R2, `@mark.integration`, passes on HEAD). The file gives `host`, `advertise`
    and `bind`. `-H x.example` reaches `ClientApp.host` and, with `-q`, `SubmitApp.queue_server`.
  - [x] **API defaults** (R5, `@mark.integration`, passes on HEAD). One child, with the seed file and
    `HYPERSHELL_SERVER_ADVERTISE=adv.example`. These are all `'localhost'`:
    - `hypershell.client.DEFAULT_HOST` and `hypershell.submit.DEFAULT_HOST`;
    - `QueueConfig().host` and `hypershell.server.DEFAULT_BIND`;
    - the host in the `address` default (`inspect.signature`) of `ClientThread` (its `__init__`,
      which `ClientThread.new` passes its arguments to) and `run_client`.
  - [x] **The Python API source** (R5, `@mark.unit`, xfail P2). `QueueConfig`'s `host` field default
    is the built-in `default.server.host`. Check it with `dataclasses.fields`, by value and by
    asserting that `core/queue.py`'s source has no `default.server.bind`.
  - [x] **Server side** (R6, `@mark.integration`, passes on HEAD). The seed file is given. The `-H`
    defaults of `ServerApp` and `ClusterApp`, built with the least arguments each needs, are
    `0.0.0.0`.
  - **Help and docs** (`@mark.unit`, xfail P2):
    - [x] The `-H` entry in `ClientApp.interface` help text and in `SubmitApp`'s names
      `server.host` and `server.advertise`.
    - [x] So do the `-H` entries of `docs/_include/client_help.rst` and `submit_help.rst`.
    - [x] `parse_reference` (`tests/test_config_schema.py`) finds `.host`, `.advertise` and `.bind`
      in `[server]`. `.host`'s text names `.advertise` and `.bind`, and `.advertise`'s and
      `.bind`'s name `client`.
    - [x] `docs/cli/client.rst` and `docs/cli/submit.rst` each have a `versionchanged:: 2.9.0`
      whose body names `-H`, above the `.. include::`.
- [x] Nothing changes outside `tests/` and `spec/`.
- **Verify:** see the frontmatter. It fences against `b88a09ae`, and the new file passes with strict
  xfails, `seed` and `advertise` rows among them and no XPASS.
- **Touches:** `tests/test_client_host_default.py`.

## Phase P2 — One rule for every configured client dial: `client_host`, its three readers, help, docs, completion and agent docs
**Satisfies:** R1–R8 · **Depends on:** P1
**Goal:** `hs client`, `hs submit --queue` and `QueueConfig.load()` dial by the one rule. The
Python API keeps `localhost` without naming `bind`. The docs, help, completion and agent docs state
the rule in this same commit (invariants §12).

- [x] `src/hypershell/core/config.py`:
  - [x] Move `WILDCARD_BINDS` here.
  - [x] Add `client_host(base=None)` and the private form-then-layer helper (PLAN §2.1).
  - [x] Add both to `__all__`.
  - [x] Keep `default['server']['host'] = 'localhost'` as is.
- [x] `src/hypershell/cluster/launch.py`: import `WILDCARD_BINDS` from `core.config`. Keep it in
  `__all__`. `advertised_host` is unchanged.
- [x] `src/hypershell/client.py`:
  - [x] `host: Optional[str] = None`.
  - [x] In `check_args`: `if self.host is None: self.host = client_host()`.
  - [x] Replace `{DEFAULT_HOST}` in the `-H` help line with the rule, wrapped to the description
    column.
- [x] `src/hypershell/submit.py`:
  - [x] `queue_server: Optional[str] = None`.
  - [x] In `check_arguments`: resolve it with `client_host()` when `queue_mode` and `None`.
  - [x] Update the `-H` help line in the same way.
- [x] `src/hypershell/core/queue.py`:
  - [x] `QueueConfig.host: str = default.server.host`.
  - [x] `load()` sets `'host': client_host(_config)`.
- [x] `src/hypershell/server.py`: `DEFAULT_BIND: Final[str] = default.server.bind`.
- [x] `docs/_include/client_help.rst` and `submit_help.rst`: update the `-H` entries (PLAN §2.4).
- [x] `docs/_include/config_param_ref.rst` `[server]`:
  - [x] `.host`: worded default and the rule.
  - [x] `.advertise` and `.bind`: the client sentence.
  - [x] `.. versionchanged:: 2.9.0` in `.. only:: html` blocks.
- [x] `docs/cli/client.rst` and `docs/cli/submit.rst`: a `.. versionchanged:: 2.9.0` above each
  `.. include::`.
- [x] `share/zsh/site-functions/_hs`: the `hs client -H` description becomes `(default: by
  server.host)`.
- [x] `AGENTS.md` (the `advertised_host` paragraph) and `.agents/factory/invariants.md` §11: one
  sentence each for the client rule (`core/config.py::client_host`). Amend "only
  `ClusterApp`/`RelayApp` read" `advertise` to say clients given no `-H` read it too, through
  `client_host`.
- [x] Remove every `client-host-default P2` xfail marker.
- **Verify:** see the frontmatter. It runs the new file and the neighbouring config, client, submit,
  cluster-advertise and relay-advertise suites with no XPASS and no marker left. It also covers the
  source greps, the `--help` drive in a throwaway site with `HYPERSHELL_CONFIG_FILE` empty,
  `zsh -n`, the agent-doc greps and the Sphinx `-W` build (sandbox off).
- **Touches:** `src/hypershell/core/config.py`, `cluster/launch.py`, `client.py`, `submit.py`,
  `core/queue.py`, `server.py`, `docs/_include/{client_help,submit_help,config_param_ref}.rst`,
  `docs/cli/{client,submit}.rst`, `share/zsh/site-functions/_hs`, `AGENTS.md`,
  `.agents/factory/invariants.md`, `tests/test_client_host_default.py`.

## Phase P3 — The release note; gate: the whole suite serially under 3.14 and 3.11, the docs
**Satisfies:** R1–R8 · **Depends on:** P2
**Goal:** the squash body says what changes for whom. The change holds together on both Pythons,
with the docs.

- [x] Write `spec/client-host-default-from-bind/RELEASE-NOTE.md` (PLAN §2.5;
  [`research/03-docs.md`](research/03-docs.md) draft). The format is a heading, one context
  paragraph, `---`, then the plain-text body:
  - [x] One paragraph on the fix and the rule.
  - [x] A "Behaviour change:" paragraph covering the four configurations (`host` given;
    `advertise` without `host`; only a specific `bind`; a wildcard `bind` with nothing else).
  - [x] That `-H`, the Python API defaults and `server.advertise`'s meaning for launched clients are
    unchanged.
  - [x] That a `host_eval` no longer runs at every command's start.
  - [x] That an unset `_env` form is refused, naming the key.
- [x] Commit body: the suite totals per Python. Re-run a flaky failure once and record its node id
  and both outcomes.
- **Verify:** see the frontmatter (the release-note greps, the whole suite serially on 3.14 and
  3.11, and the Sphinx `-W` build).
- **Touches:** `spec/client-host-default-from-bind/RELEASE-NOTE.md`, this file.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `fix/client-host-default-from-bind`, `develop` reachable.
3. Execute every `[ ]` in the phase (PLAN.md has the detail).
4. Run the phase's `verify:`. Never advance on a checkbox alone.
5. Amend this file if reality diverges: update state through `set_phase.py`, and note the change in
   the commit body. STOP and escalate only on a `GOAL.md` contradiction.
6. Mark the phase `done` and advance `current_phase`. Make one signed `[fix]` commit, then stop and
   report.
