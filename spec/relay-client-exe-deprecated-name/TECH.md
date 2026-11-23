---
slug: relay-client-exe-deprecated-name
title: A relay starts its site clients with its own hs, not the legacy hyper-shell
kind: fix
appetite: small
status: in_progress
branch: fix/relay-client-exe-deprecated-name
base: develop
current_phase: P4
last_updated: '2026-11-23'
phases:
- id: P1
  name: Tests first, strict xfail where the fix is missing (tests only)
  status: done
  satisfies:
  - R5
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: l="$TMPDIR/rexe-p1.log"; git diff --quiet 11faef5e -- src docs share AGENTS.md
    .agents && test -z "$(git diff --name-only 11faef5e -- . ':!spec' ':!tests')"
    && sh spec/relay-advertise/drive/bound.sh 900 uv run pytest -q -p no:cacheprovider
    -rxX tests/test_relay_remote_exe.py tests/test_cluster_exit.py > "$l" 2>&1; r=$?;
    tail -n 25 "$l"; test $r -eq 0 && grep -q 'XFAIL.*test_relay_from_a_symlinked_hs_runs_its_clients\[database\]'
    "$l" && grep -q 'XFAIL.*test_launcher_cluster_exits_when_every_client_fails' "$l"
    && ! grep -q XPASS "$l"
- id: P2
  name: 'The relay launches as its own program: --remote-exe, relay.remote_exe, docs
    and completions'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R5
  - R6
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: l="$TMPDIR/rexe-p2.log"; sh spec/relay-advertise/drive/bound.sh 1800 uv
    run pytest -q -p no:cacheprovider -rxX tests/test_relay_remote_exe.py tests/test_relay_launch.py
    tests/test_relay_advertise.py tests/test_relay_db.py tests/test_relay_topology.py
    tests/test_config_schema.py tests/test_env_config.py tests/test_completions.py
    tests/test_period_floor.py tests/test_cluster_advertise.py tests/test_autoscale_failed_launches.py
    > "$l" 2>&1; r=$?; tail -n 25 "$l"; test $r -eq 0 && ! grep -rq 'relay-remote-exe
    P2' tests && sh spec/relay-advertise/drive/bound.sh 120 .agents/factory/bin/temp_site.sh
    sh -c 'uv run hs relay --help' | awk '/^ +--launcher-args /{a=NR} /^ +--remote-exe
    /{b=NR} END{exit !(a && b && a < b)}' && ! grep -q 'hyper-shell client' docs/_include/relay_help.rst
    && bash -n share/bash_completion.d/hs && zsh -n share/zsh/site-functions/_hs &&
    grep -q 'relay\.remote_exe' AGENTS.md && grep -q 'relay\.remote_exe' .agents/factory/invariants.md
    && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-rexe sh spec/relay-advertise/drive/bound.sh
    900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html
    docs /tmp/claude-501/docs-html-rexe
- id: P3
  name: The Python API launches hs; the release note
  status: done
  satisfies:
  - R4
  - R6
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'l="$TMPDIR/rexe-p3.log"; sh spec/relay-advertise/drive/bound.sh 1800 uv
    run pytest -q -p no:cacheprovider -rxX tests/test_relay_remote_exe.py tests/test_cluster_exit.py
    tests/test_cluster_launch.py tests/test_relay_workload.py > "$l" 2>&1; r=$?; tail
    -n 25 "$l"; test $r -eq 0 && ! grep -rq ''relay-remote-exe P[23]'' tests && !
    grep -rn "''hyper-shell''" src/hypershell/cluster src/hypershell/relay && grep
    -A4 ''^DEFAULT_REMOTE_EXE'' src/hypershell/cluster/ssh.py | grep -q ''versionchanged::
    2.9.0'' && grep -A4 ''^DEFAULT_REMOTE_EXE'' src/hypershell/cluster/remote.py |
    grep -q ''versionchanged:: 2.9.0'' && grep -q "remote_exe=''hyper-shell''" spec/relay-client-exe-deprecated-name/RELEASE-NOTE.md
    && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-rexe sh spec/relay-advertise/drive/bound.sh
    900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html
    docs /tmp/claude-501/docs-html-rexe'
- id: P4
  name: 'Gate: the whole suite serially under 3.14 and 3.11, the docs'
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: PATH=/tmp/claude-501/venv314-rexe/bin:$PATH UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-rexe
    sh spec/relay-advertise/drive/bound.sh 5400 uv run --python 3.14 pytest -p no:cacheprovider
    -q && PATH=/tmp/claude-501/venv311-rexe/bin:$PATH UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv311-rexe
    sh spec/relay-advertise/drive/bound.sh 5400 uv run --python 3.11 pytest -p no:cacheprovider
    -q && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-rexe sh spec/relay-advertise/drive/bound.sh
    900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html
    docs /tmp/claude-501/docs-html-rexe
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — A relay starts its site clients with its own `hs`, not the legacy `hyper-shell`

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/relay-client-exe-deprecated-name/TECH.md`);
the per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes
one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md). The R-IDs are the contract. Q1–Q6 were
  ruled at the GOAL gate (2026-11-23), Q1 and Q2 by the maintainer.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) and its briefs.
- **Test prototype:** [`research/prototypes/relay_remote_exe_proto.py`](research/prototypes/relay_remote_exe_proto.py)
  (R5's end-to-end case), named so the suite never collects it. P1 copies it into
  `tests/test_relay_remote_exe.py`. Run the prototype itself with
  `PYTHONPATH=$PWD uv run pytest -p tests.conftest spec/relay-client-exe-deprecated-name/research/prototypes/relay_remote_exe_proto.py`.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done`
- `appetite`: `small | big`. It caps the phase count and the build-iteration budget.
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: the GOAL R-IDs this phase delivers (the traceability anchor for `hs-review`).
- `depends_on`: the phase ids that must be `done` first.
- `parallel`: `false` for all four. P1 writes the tests P2 and P3 turn green; P2 and P3 both edit
  `relay/` and `config` code paths and share test modules.
- `hammerable`: `false` for all four. Which program every site client runs is the whole fix.
- `hill`: `uphill` → `crest` → `downhill`.
- `attempts`: the durable failed-verify counter, bumped by `set_phase.py --phase P<n> --record-attempt`.
- `verify`: the exact command that proves the phase.
- `review.cycle`: completed review passes.

## Running the verify commands here

- Run every `verify:` from the worktree root with these exported (every `uv run` and every
  subprocess test inherits them): `UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-rexe`,
  `UV_CACHE_DIR=/tmp/claude-501/uv-cache-rexe`, `TMPDIR=/tmp/claude-501`, and
  `PATH=/tmp/claude-501/venv314-rexe/bin:$PATH`. The worktree's own `.venv` is not used, and the
  shared uv cache is not used (it is corrupt). `venv314-rexe` was synced at plan time (Python 3.14).
  P4 sets its own environment per Python (3.11 in `/tmp/claude-501/venv311-rexe`, created on first
  use, which needs PyPI).
- The R5 test sets its own `PATH` (`inst/bin:/usr/bin:/bin`) for the processes it starts; nothing
  else in the environment may put a `hyper-shell` in `/usr/bin` or `/bin` (the test asserts it).
- The docs build runs in **`/tmp/claude-501/docs-venv-rexe`**, a docs venv of this cycle's own (synced
  at plan time), and **needs the sandbox off**: in the sandbox `sphinx_sitemap` fails at
  `builder-inited` on a refused socket bind. Dry-run on `11faef5e` at plan time: passes, sandbox off.
  Never another worktree's venv. Every other verify step ran in the sandbox at plan time.
- Every bound uses `sh spec/relay-advertise/drive/bound.sh SECONDS COMMAND…` (macOS has no
  `timeout`; status 124 at the bound).
- `pytest -n auto` is broken on this suite: everything runs serially. Every relay, server and
  client binds `127.0.0.1`. Keys go only through `HYPERSHELL_SERVER_AUTH` and
  `HYPERSHELL_RELAY_UPSTREAM_AUTH`, never as a command-line option. `tests/test_key_option.py`
  sweeps `spec/*/drive/*` and the tests for a key option: never write the short key flag, even in a
  comment.
- `relay.remote_exe`'s reference entry gives its default in words, never as a literal (PLAN §2.5;
  `test_empty_unset_keys`).
- Expected durations (plan-time dry-run): the prototype's two cases ≈ 10 s together (both fail on
  `11faef5e` by assertion); the P2 test set ≈ 8–10 min (with the design emulated in a scratch copy,
  787 passed in 8 min 19 s over most of it, the only failures the reference entry not yet written and
  the `test_cluster_exit` assertion P3 changes); the whole suite ≈ 25 min per Python (bounded at 90 min each).

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution. The footgun checklist is
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) (§10, §11 and §12 here).
- One phase per `hs-build` invocation. Each phase is one atomic commit holding the code and the
  `TECH.md` state change, subject `[fix] Build relay-client-exe-deprecated-name P<n>: …`, signed.
  **No `Co-Authored-By` trailer.** Squashed into the single PR-title commit at `hs-publish`.
- The CLI change, its help snippets, the config reference and both completions land in **one
  commit** (P2, §12); the constant change and its docstrings and release note in P3.
- Version markers carry no `:pr:` (`hs-release` adds it). Docs, tests and notes use top-level
  commands only (`hs relay`, `hs server`, `hs submit`, `hs list`, `hs cluster`).
- **Strict-xfail convention (P1).** Each test that needs the fix carries
  `pytest.mark.xfail(strict=True, reason='relay-remote-exe P2')` or `'relay-remote-exe P3'`
  (`pytest.param(..., marks=…)` for single rows). The phase that makes it pass removes the marker;
  P2's and P3's verify lines grep that none of theirs remain.

---

## Phase P1 — Tests first, strict xfail where the fix is missing (tests only)
**Satisfies:** R5 (the tests R1–R6 are graded by) · **Depends on:** —
**Goal:** every test this cycle needs is in the tree and collected; each that needs the fix is a
strict xfail naming the phase that fixes it; the rest pass. No change outside `tests/` and `spec/`.

- [x] New `tests/test_relay_remote_exe.py` (module docstring: the program a relay's site clients are
      launched as — the option, else the key, else the relay's own `hs` — and the Python API default):
  - [x] **R5 end to end** (`@mark.integration`, xfail P2): the prototype's
        `test_relay_from_a_symlinked_hs_runs_its_clients[plain|database]`, unchanged but for the
        docstring sentence about being a prototype.
  - [x] **R3/R5 precedence** (`@mark.integration`, xfail P2), with `relays` and `recording_launcher`
        (imported as `tests/test_relay_advertise.py` does): the word after the launcher in every
        recorded argv is `/opt/key/bin/hs` with `HYPERSHELL_RELAY_REMOTE_EXE=/opt/key/bin/hs`;
        `/opt/opt/bin/hs` with `--remote-exe /opt/opt/bin/hs` as well (option over key); with
        `HYPERSHELL_RELAY_REMOTE_EXE=` (empty) it is not `''` and not `hyper-shell` (the relay's own
        program: the harness's `tmp/hs` is not executable, so the interpreter's scripts `hs`); and
        with `--database tmp/relay.db` plus the key, the program is the key's and no recorded
        environment holds `_HYPERSHELL_RELAY_PROGRAM`.
  - [x] **`relay_program`** (`@mark.unit`, xfail P2; the import guarded so collection survives:
        `relay_program = getattr(relay_module, 'relay_program', None)`): an executable file by
        absolute path → itself; by relative path (`monkeypatch.chdir`) → its absolute path; a symlink
        → the link's absolute path, not the target; a non-executable file, `'-c'`, `None`, a missing
        path → `os.path.join(sysconfig.get_path('scripts'), 'hs')` when that exists; with
        `sysconfig.get_path` patched to an empty directory → `'hs'`.
  - [x] **`RelayApp.program`** (`@mark.unit`, xfail P2): `sys.argv` patched to
        `[exe, 'relay', '--port', '1']` and `RelayApp.argv` to `['--port', '1']` → `exe` (absolute);
        `sys.argv` an embedding program's (`[exe, 'tests/x.py']`) → the scripts `hs`, not `exe`;
        `_HYPERSHELL_RELAY_PROGRAM=/carried/hs` with `_HYPERSHELL_RELAY_REEXEC=1` → `/carried/hs`;
        without the re-execution marker → ignored; the variable is gone from `os.environ` after
        either read.
  - [x] **`RelayApp.client_exe`** (`@mark.unit`, xfail P2), as `test_relay_app_advertise_host`:
        option; key; option over key; empty key → `program`; `None` key → `program`
        (`RelayApp.program` patched to a sentinel).
  - [x] **The re-execution carries it** (`@mark.unit`, xfail P2): `os.execve` patched to record and
        raise; `RelayApp.from_cmdline(['--database', str(tmp_path / 'other.db')])` with `program`
        patched → the recorded environment's `_HYPERSHELL_RELAY_PROGRAM` is that value, beside the
        two existing markers.
  - [x] **Every fleet** (`@mark.unit`, xfail P2): `RelayThread(address=('127.0.0.1', free_port()),
        zones=['cpu', 'gpu', None], remote_exe='/opt/x/bin/hs')` → every `machine.launch.argv[0]`
        (cluster autoscaler and each fleet) is `/opt/x/bin/hs`; with `launcher='srun --exact'` the
        word after `srun --exact` is; `site_cluster(..., remote_exe=…)` and
        `zone_launch(cluster, 'gpu', remote_exe=…)` likewise.
  - [x] **The key** (`@mark.unit`, xfail P2): `KEYS['relay.remote_exe'] is Kind.TEXT`, it is in
        `EMPTY_UNSET`, `match_name('HYPERSHELL_RELAY_REMOTE_EXE')`, `default['relay']['remote_exe']
        is PARAM_UNSET`, and `scrub_environment` removes `HYPERSHELL_RELAY_REMOTE_EXE`.
  - [x] **Help and docs** (`@mark.unit`, xfail P2): `APP_USAGE`/`APP_HELP` list `--remote-exe`
        after `--launcher-args`; `relay_usage.rst` and `relay_help.rst` have it, and `relay_help.rst`
        no longer says `hyper-shell client`; `config_param_ref.rst`'s `[relay]` has `.remote_exe`
        (`parse_reference` from `tests/test_config_schema.py`); both completions offer it in their
        relay sections; `core/config.py`'s `launcher` comment does not say `'hs client` literally
        unqualified (assert the new wording, PLAN §2.3).
  - [x] **R4** (`@mark.unit`, xfail P3): `hypershell.cluster.ssh.DEFAULT_REMOTE_EXE`,
        `hypershell.cluster.remote.DEFAULT_REMOTE_EXE` and `hypershell.cluster.DEFAULT_REMOTE_EXE`
        are `'hs'`; the `remote_exe` default (`inspect.signature`) of `SSHCluster`, `RemoteCluster`,
        `AutoScalingCluster`, `site_cluster`, `zone_launch` is `'hs'`; `RelayThread(...)` built
        without `remote_exe` launches `hs`.
- [x] `tests/test_cluster_exit.py::test_launcher_cluster_exits_when_every_client_fails`: assert
      `f'{launcher} hs client '` (`:332`), the test marked xfail P3.
- [x] Nothing outside `tests/` and `spec/` changes.
- **Verify:** see frontmatter (the fence against `11faef5e`; the new file and `test_cluster_exit.py`
  pass with strict xfails; the R5 `database` case and the `test_cluster_exit` case are among the
  XFAILs).
- **Touches:** `tests/test_relay_remote_exe.py`, `tests/test_cluster_exit.py`.

## Phase P2 — The relay launches as its own program; `--remote-exe`, `relay.remote_exe`, docs and completions
**Satisfies:** R1, R2, R3, R5, R6 · **Depends on:** P1
**Goal:** a relay from an `hs`-only `bin/` runs its site clients as that `hs`, with or without
`--database`, in every zone; `--remote-exe` and `relay.remote_exe` override it; every help page,
reference entry and completion says so — one commit (§12).

- [x] `relay/site.py`: `zone_launch(..., remote_exe: str = DEFAULT_REMOTE_EXE)` →
      `client_launch(remote_exe, …)`; `site_cluster(..., remote_exe: str = DEFAULT_REMOTE_EXE)` →
      `AutoScalingCluster(..., remote_exe=remote_exe)` and `zone_launch(..., remote_exe=remote_exe)`;
      docstrings (PLAN §2.1).
- [x] `relay/__init__.py`: `PROGRAM_MARKER`, `relay_program` (PLAN §2.2); `RelayThread.__init__(...,
      remote_exe: Optional[str] = None)` → `site_cluster` and every further `zone_launch`, with its
      `Args` entry; `RelayApp`: `--remote-exe` after `--launcher-args`, `program` and `client_exe`
      cached properties, `run()` reads `self.program` before `bind_database()` (with the comment) and
      passes `remote_exe=self.client_exe`; `bind_database` adds `PROGRAM_MARKER: self.program` to the
      `execve` environment; `APP_USAGE`/`APP_HELP` (PLAN §2.5).
- [x] `core/config.py` (`'remote_exe': PARAM_UNSET` after `launcher_args`; the `launcher` comment) and
      `core/config_schema.py` (`KEYS`, `EMPTY_UNSET`) (PLAN §2.3).
- [x] `tests/test_config_schema.py::MATCHES` gains `'HYPERSHELL_RELAY_REMOTE_EXE': (('relay',
      'remote_exe'), Kind.TEXT)`; `tests/test_env_config.py::EMPTY_NOT_GIVEN` gains `'relay.remote_exe':
      ('relay.remote_exe', 'HYPERSHELL_RELAY_REMOTE_EXE')`.
- [x] Docs and completions (PLAN §2.5, P2 list): `docs/_include/relay_usage.rst`, `relay_help.rst`,
      `config_param_ref.rst` (`[relay]` `.remote_exe`), `docs/cli/relay.rst` (`versionadded`),
      `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs`; `AGENTS.md` and
      `.agents/factory/invariants.md` §10/§11 in lockstep.
- [x] Remove every `relay-remote-exe P2` marker.
- **Verify:** see frontmatter (the new file and every relay, config and completion suite the change
  touches, no P2 marker left; `hs relay --help` lists `--remote-exe` after `--launcher-args`;
  `relay_help.rst` no longer says `hyper-shell client`; both completions parse; both agent docs name
  `relay.remote_exe`; the Sphinx `-W` build). Dry-run at plan time with the design emulated in a
  scratch copy: see the plan commit body.
- **Touches:** `src/hypershell/relay/__init__.py`, `src/hypershell/relay/site.py`,
  `src/hypershell/core/config.py`, `src/hypershell/core/config_schema.py`, the docs and completions
  above, `AGENTS.md`, `.agents/factory/invariants.md`, `tests/test_relay_remote_exe.py`,
  `tests/test_config_schema.py`, `tests/test_env_config.py`.

## Phase P3 — The Python API launches `hs`; the release note
**Satisfies:** R4, R6 · **Depends on:** P2
**Goal:** no built-in default anywhere names `hyper-shell`; the change is in the docs and the
release note.

- [x] `cluster/ssh.py:42`, `cluster/remote.py:54`: `DEFAULT_REMOTE_EXE: Final[str] = 'hs'`, the
      "retain old name" comment removed, docstrings with `.. versionchanged:: 2.9.0` (PLAN §2.4).
- [x] `spec/relay-client-exe-deprecated-name/RELEASE-NOTE.md`: the squash-body draft (PLAN §2.5, P3).
- [x] Remove every `relay-remote-exe P3` marker (the R4 tests, `test_cluster_exit.py`).
- **Verify:** see frontmatter (the new file, `test_cluster_exit.py`, `test_cluster_launch.py`,
  `test_relay_workload.py`; no marker left; no `'hyper-shell'` literal in `src/hypershell/cluster` or
  `src/hypershell/relay`; both docstrings carry the marker; the release note names
  `remote_exe='hyper-shell'`; the Sphinx `-W` build).
- **Touches:** `src/hypershell/cluster/ssh.py`, `src/hypershell/cluster/remote.py`,
  `tests/test_relay_remote_exe.py`, `tests/test_cluster_exit.py`,
  `spec/relay-client-exe-deprecated-name/RELEASE-NOTE.md`.

## Phase P4 — Gate: the whole suite serially under 3.14 and 3.11, the docs
**Satisfies:** R1–R6 · **Depends on:** P3
**Goal:** the change holds together on both Pythons, with the docs.

- [ ] Commit body: the suite totals per Python. A flaky failure is re-run once and recorded with its
      node id and both outcomes.
- **Verify:** see frontmatter.
- **Touches:** `spec/relay-client-exe-deprecated-name/TECH.md` only.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `fix/relay-client-exe-deprecated-name`, `develop` reachable.
3. Execute every `[ ]` in the phase (PLAN.md has the detail).
4. Run the phase's `verify:` — never advance on a checkbox alone.
5. Amend this file if reality diverges (state through `set_phase.py`; note it in the commit body).
   STOP and escalate only on a `GOAL.md` contradiction.
6. Mark the phase `done`, advance `current_phase`; one signed `[fix]` commit; stop and report.
