# PLAN — A relay starts its site clients with its own `hs`, not the legacy `hyper-shell`

> **Status:** Draft for review · **Last updated:** 2026-11-23
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

`RelayApp` resolves the program the user ran once, in the process the user started:
`sys.argv[0]` made absolute when the process's command line is this relay's and it names an
executable file, else the `hs` in the interpreter's scripts directory, else the bare `hs`. A relay
that re-executes to bind its database hands that value to the new process in a private environment
variable beside the existing re-execution marker, so `python -c …` never has to guess. `hs relay
--remote-exe PATH` and `relay.remote_exe` override it, option over key, and the chosen value is
threaded through `RelayThread` → `site_cluster` → `zone_launch` into every launch of every zone,
fixed or topology-driven. Separately, both `DEFAULT_REMOTE_EXE` constants become `'hs'`. Four
phases: tests first, the relay (with its docs and completions), the API default (with the release
note), the gate. The appetite stays small: one resolver, one option and key, one parameter threaded
through three functions, two constants.

## 2. Design

### 2.1 Threading the program to every launch (`src/hypershell/relay/site.py`, `relay/__init__.py`) — R1, R2

- `zone_launch(cluster, zone, …, tls=None, remote_exe: str = DEFAULT_REMOTE_EXE)` builds
  `client_launch(remote_exe, …)` in place of the hard-coded `DEFAULT_REMOTE_EXE` (`site.py:181`).
  Its docstring's "every other value must be the one `site_cluster` was given" now covers it.
- `site_cluster(…, advertise=None, remote_exe: str = DEFAULT_REMOTE_EXE)` passes `remote_exe` to
  the `AutoScalingCluster` it builds (so not even its discarded own launch names another program)
  and to the `zone_launch` that becomes `cluster.launch`. Docstring: "launched as `remote_exe`".
- `RelayThread.__init__(…, advertise=None, remote_exe: Optional[str] = None)`:
  `remote_exe = remote_exe or DEFAULT_REMOTE_EXE`, passed to `site_cluster` and to every further
  zone's `zone_launch` (`relay/__init__.py:551`). Its `Args` docstring gains `remote_exe`.
  `DEFAULT_REMOTE_EXE` is imported from `hypershell.cluster.remote`.
- Topology-driven launches need nothing more: `SiteScaler` launches with the `ClientLaunch` it was
  given (`site_scaler(…, cluster.launch, view)` for the first zone, `zone_fleet(zone_launch(…))`
  for the others; [`research/02`](research/02-launch-paths.md)).

The library default (a `RelayThread` built without `remote_exe`) is `DEFAULT_REMOTE_EXE`, so it
follows §2.4: an embedding program is not `hs relay`, and the Q1 rationale (no computed default for
a library) applies. Only `RelayApp` resolves a path.

### 2.2 The relay's own program (`relay/__init__.py`) — R1, R2, Q2, Q3

**Resolver.** A module function beside `REEXEC_CODE`:

```python
def relay_program(argv0: Optional[str]) -> str:
    """The program a relay started as `argv0` launches its site clients as (see `RelayApp.program`).

    `argv0` made absolute (not resolved, so a symlinked `bin/hs` stays the link) when it names an
    executable file; else the `hs` in this interpreter's scripts directory; else `hs`, from `PATH`.
    """
```

- First candidate: `argv0` when `os.path.isfile(argv0) and os.access(argv0, os.X_OK)` →
  `os.path.abspath(argv0)`. `abspath`, never `realpath`: the link the user ran is what they put on
  `PATH` (GOAL Q2, and what `hs cluster` passes).
- Second: `shutil.which('hs', path=sysconfig.get_path('scripts'))`, made absolute. `which` applies the
  executable check (and `PATHEXT` on Windows).
- Last: `'hs'`.

**Which `argv0`** (`RelayApp.program`, a `functools.cached_property`):

1. `carried = os.environ.pop(PROGRAM_MARKER, None)`; when `carried` and `REEXEC_MARKER` is still in
   `os.environ` (the re-executed relay, before `bind_database` pops it), return `carried` as is. A
   stray `PROGRAM_MARKER` without the re-execution marker is dropped and ignored.
2. Else `relay_program(sys.argv[0] if sys.argv[1:] == ['relay', *self.argv] else None)`: `argv[0]`
   counts only when the process's own command line is this relay's, which holds for `hs relay`,
   `hyper-shell relay` (Q3: the program the user ran, whatever its name), a wrapper that `exec`s the
   real `hs`, and the test harness's `main(sys.argv[1:])`; it fails for `main(['relay', …])` inside
   another program (`pytest`, a user script), whose `argv[0]` is not HyperShell
   ([`research/01`](research/01-reexec-carry.md), trap 1).

`PROGRAM_MARKER: Final[str] = '_HYPERSHELL_RELAY_PROGRAM'`, documented as the private variable a
re-executed relay is handed its program in.

**Across the re-execution** (the GOAL's open mechanism). `RelayApp.run()` reads `self.program`
right after `check_args()` and before `bind_database()` (a comment says why: after `execve`,
`sys.argv[0]` is `-c`). `bind_database` adds `PROGRAM_MARKER: self.program` to the `execve`
environment beside `RELAY_MARKER` and `REEXEC_MARKER`. The re-executed process's `run()` reads
`self.program` before its own `bind_database` pops `REEXEC_MARKER`, so step 1 takes the carried
value; the pop in step 1 keeps the variable out of `os.environ`, and so out of every site client's
and task's environment (the fence's `HYPERSHELL_RELAY_` prefix would not match the leading
underscore). Rejected alternatives, appending `--remote-exe` to the re-executed argv and rewriting
`argv[0]` in `REEXEC_CODE`, are in research/01.

### 2.3 The option and the key — R3, Q6

- `RelayApp`: `remote_exe: Optional[str] = None`, `interface.add_argument('--remote-exe',
  default=remote_exe)`, declared after `--launcher-args`. `None` when not given, so it can be told
  from the key (as `advertise`).
- `RelayApp.client_exe` (cached property): the option when not `None`; else `config.relay.remote_exe`
  unless it is `None`, `''` or `PARAM_UNSET`; else `self.program`. `run()` passes
  `remote_exe=self.client_exe` to `RelayThread`. The value is passed on as given: never resolved,
  never checked for existence (non-goals). An empty `--remote-exe ''` is out of scope (Q6).
- `core/config.py` `default['relay']`: `'remote_exe': PARAM_UNSET` after `'launcher_args'`, comment
  "Program site clients are launched as (unset: the relay's own hs)". The `launcher` comment becomes
  "Command prefix to launch each site client (empty means '<relay's hs> client ...')" — R6 names it.
- `core/config_schema.py`: `'relay.remote_exe': T` in `KEYS` after `'relay.launcher_args'`;
  `'relay.remote_exe'` in `EMPTY_UNSET`, so an empty `HYPERSHELL_RELAY_REMOTE_EXE` is not given.
- **Before the fence.** `config` is read at import, and `client_exe` is computed in `run()` before
  `RelayThread` exists, so `fence_site`'s scrub of `HYPERSHELL_RELAY_*` (in
  `RelayThread.run_with_exceptions`) comes after every read. The re-executed relay reads the key
  from the same inherited environment. `fence_site` already passes every launch through
  `check_invocation`, so a program path holding the upstream key is refused like any other leak.

### 2.4 The Python API default (`cluster/ssh.py`, `cluster/remote.py`) — R4, Q1

`DEFAULT_REMOTE_EXE: Final[str] = 'hs'` in both modules; the "retain old name … (for now)" comment
goes. Each docstring: "Default program remote clients are launched as (the `hs` on their
`PATH`)." plus `.. versionchanged:: 2.9.0` "``hs``; formerly ``hyper-shell``." (rendered by
`docs/api/cluster.rst`'s `autodata`). The exported name and every signature stay. `hs cluster`
always passes `--remote-exe`, so what it launches does not change (non-goal).

### 2.5 Docs, completions, agent docs — R6

Same commit as the code they describe (§12):

- **P2 (the relay).** `APP_USAGE` (`[--launcher CMD] [--launcher-args ARGS] [--remote-exe PATH]`,
  rewrapped) and `APP_HELP`: the option line after `--launcher-args`,
  `--remote-exe PATH  Program site clients are launched as (default: this hs).`, and in the
  description, after the launcher sentence, "Site clients are launched as the hs that runs the
  relay, by its full path, unless --remote-exe ([relay] remote_exe) names another program: the path
  assumes the site's nodes see this installation where this host does." `docs/_include/relay_usage.rst`
  likewise. `docs/_include/relay_help.rst`: the `--launcher` entry's
  ``CMD [ARGS...] hyper-shell client ...`` becomes ``CMD [ARGS...] HS client ...`` with "where *HS*
  is the relay's own program (see ``--remote-exe``)"; a new ``--remote-exe`` *PATH* entry after
  ``--launcher-args``: the default (the `hs` that runs the relay, by its full path, as the user ran it,
  kept across ``--database``), precedence over ``relay.remote_exe``, one program not a command line
  (a wrapper belongs in ``--launcher``), Q4's shared-installation assumption with this option as the
  escape hatch, "See also ``relay.remote_exe``". `docs/_include/config_param_ref.rst` `[relay]`:
  ``.remote_exe`` after ``.launcher_args`` (same content; environment form
  ``HYPERSHELL_RELAY_REMOTE_EXE``; empty is not given; `versionadded:: 2.9.0` inside the html-only
  block as the neighbours carry). Its default is given in words, "(default: the relay's own
  program)", never as a literal: `tests/test_config_schema.py::test_empty_unset_keys` requires an
  `EMPTY_UNSET` key's documented default to be no literal value (`documented_default`). `docs/cli/relay.rst`: `.. versionadded:: 2.9.0` ``--remote-exe``
  beside the ``--advertise`` one (no `:pr:`; `hs-release` adds it). `share/bash_completion.d/hs`
  (relay option list; `--remote-exe` completes files) and `share/zsh/site-functions/_hs` `_hs_relay`
  (`'--remote-exe[Program site clients are launched as (default: this hs)]:program:_files'`).
  `AGENTS.md` and `.agents/factory/invariants.md` §11, in lockstep: the relay-fleet bullet says the
  launches name `RelayApp.client_exe` (option, key, or the relay's own program, carried across the
  re-execution in `_HYPERSHELL_RELAY_PROGRAM`); the `RELAY_PROCESS` bullet (§10, AGENTS.md
  `:703`) names the carried variable.
- **P3 (the API).** The constant docstrings (§2.4). `spec/relay-client-exe-deprecated-name/RELEASE-NOTE.md`:
  the squash-body draft `hs-publish` uses — the relay fix in one paragraph, and a "Behaviour change"
  paragraph for the Python API (`SSHCluster`, `RemoteCluster`, `AutoScalingCluster` and
  `hypershell.cluster.DEFAULT_REMOTE_EXE` now launch `hs client`; pass `remote_exe='hyper-shell'` for
  the old behaviour). That is R6's "the 2.9.0 release notes SHALL say so": `hs-release` drafts the
  notes from the squash bodies.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `relay_program` + `RelayApp.program` (§2.2) → `client_exe` → `RelayThread`/`site_cluster`/`zone_launch(remote_exe=…)` (§2.1) |
| R2 | `PROGRAM_MARKER` across `bind_database`'s `execve` (§2.2); one value to `site_cluster` and every further `zone_launch`, which `SiteScaler` launches with (§2.1) |
| R3 | `--remote-exe`, `relay.remote_exe` (`KEYS`, default), `client_exe` precedence (§2.3) |
| R4 | both `DEFAULT_REMOTE_EXE = 'hs'` (§2.4); the relay never falls back to `hyper-shell` (§2.1–2.2) |
| R5 | `tests/test_relay_remote_exe.py` (§6), the changed `tests/test_cluster_exit.py:332` |
| R6 | §2.5: source help, `relay_usage.rst`, `relay_help.rst`, `config_param_ref.rst`, completions, `core/config.py` comment, release note |
| Q6 | `relay.remote_exe` in `EMPTY_UNSET` (§2.3) |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before
research and again after this design. Touched: §8 (relay carve-out, process lifetime), §9, §10,
§11, §12. Untouched: §1–§7 (no task, retry, server-mode, FSM or shutdown change).

- **§10 Configuration.** `relay.remote_exe` gets its kind in `KEYS` and its reference entry in the
  same commit (P2; `tests/test_config_schema.py` enforces it); `EMPTY_UNSET` gains it; the relay
  reads it from the import-time `config`, never mutates it. `RELAY_PROCESS`/the re-execution: one
  more private marker crosses the `execve` beside the two that already do; the database rebind is
  unchanged.
- **§11 Cluster orchestration.** Every site launch is still built by `client_launch()` through
  `zone_launch` and passes `fence_site`; only its first argument changes. No key in any argv: the
  program path is checked by `check_invocation` like every other word. `hs cluster`'s launches are
  untouched (it always passes `--remote-exe`). `client_launch` itself is unchanged, so the 2.9.0a4
  contract test is unaffected.
- **§9 Security.** The new variable carries a path, not a secret, and is popped before any launch,
  so it never reaches a site client or task (tested). No change to auth, TLS or the key handling.
- **§8 Signals.** No handler changes; the re-execution still happens before the handlers are
  installed (`install_drain_handlers` follows `bind_database` in `run()`).
- **§12 Conventions.** CLI change, help snippets and completions in one commit (P2); prose markers
  (`versionadded` for `--remote-exe`, `versionchanged` for the constants); new tests tagged
  `@mark.unit`/`@mark.integration`; exit statuses unchanged.
- **High-blast-radius files.** `cluster/remote.py` and `cluster/ssh.py` change one constant and its
  docstring each (P3); no logic in them changes.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `argv[0]` counts only when `sys.argv[1:] == ['relay', *RelayApp.argv]`, and only when executable | The ruled rule (Q2 (c)) would otherwise launch an embedding program (`pytest`, a user script) or a non-executable file as the site client | Literal "names an existing file": wrong program for `main(['relay', …])` and the suite's own relay harness (research/01). Flagged for the plan gate (§5) |

## 4. Rabbit holes (resolved)

- **How the program survives `--database`'s re-execution** → a private environment variable beside
  `REEXEC_MARKER`, resolved once in the process the user started
  ([`research/01`](research/01-reexec-carry.md)). Proven in a scratch prototype: plain and
  `--database` relays from a symlinked `bin/hs` with no `hyper-shell` on `PATH` launch `…/bin/hs
  client` and complete every task; the variable never reaches the site environment.
- **Reading `HYPERSHELL_RELAY_REMOTE_EXE` before the fence** → `config` is an import-time snapshot
  and `client_exe` is resolved in `run()` before `RelayThread` exists
  ([`research/02`](research/02-launch-paths.md)).
- **Every launch path** → one `ClientLaunch` per zone, all built by `zone_launch`; `SiteScaler` uses
  the one it is given (research/02).
- **A test that really runs a client from a symlinked `bin/hs`** → a `bin/` of symlinks to the test
  interpreter's own `hs`/`hsx`, `PATH=bin:/usr/bin:/bin`, a real upstream `hs server`, no launcher;
  prototype in [`research/prototypes/relay_remote_exe_proto.py`](research/prototypes/relay_remote_exe_proto.py)
  ([`research/03`](research/03-symlinked-hs-test.md)).

## 5. Risks & open questions

- **For the plan gate: the `argv[0]` refinement** (§3 deviation). The maintainer ruled "`sys.argv[0]`
  made absolute when it names an existing file"; the plan adds "and the process's command line is
  this relay's, and the file is executable". Without it `main(['relay', …])` from Python names the
  embedding program. Both narrowings fall through to the next ruled candidate, never to
  `hyper-shell`.
- **The relay names the link, not its target.** A `bin/hs` symlink into `libexec/bin/` is what the
  clients run, as the user ran it. Where nodes see the installation at another path, `--remote-exe`
  is the escape hatch (Q4); the docs say so.
- **A missing program still ends the relay** when there is no launcher: `Popen` raises
  `FileNotFoundError` inside `SiteScaler`, the relay logs CRITICAL and exits (reproduced on `develop` with the
  old default, and on the prototype with `relay.remote_exe=/nonexistent/hs`). The GOAL's non-goal
  assumes "the relay's existing relaunch warnings cover that"; they cover a launcher that exits
  non-zero, not a direct launch. This cycle's default removes the common trigger. Proposed: a
  follow-up seed, `relay-direct-launch-missing-program`, filed by the foreman.
- **Python API behaviour change** (Q1, ruled). An API user whose nodes have only `hyper-shell` on
  `PATH` (impossible for a normal install, which has both) would break; the release note gives
  `remote_exe='hyper-shell'`.
- **Windows.** `relay_program` uses `shutil.which` for the scripts-directory candidate, which honours
  `PATHEXT`; the relay is otherwise POSIX-first (`fcntl`). Not tested here.

## 6. Verification strategy

Tests first (P1), all strict xfail where the fix is missing, then removed by the phase that fixes
them. New `tests/test_relay_remote_exe.py`:

- **R5 end to end** (`@mark.integration`, from the prototype): a relay started as a symlinked
  `inst/bin/hs` (only `hs` and `hsx` there, `PATH=inst/bin:/usr/bin:/bin`, `hyper-shell` asserted
  absent), against a real upstream `hs server --forever`, runs three tasks to completion; its DEBUG
  log names `<inst/bin/hs> client`, never `hyper-shell`; cases `plain` and `database` (the latter
  asserts the re-execution happened). xfail P2.
- **R3/R5 precedence** (`@mark.integration`, `relays` fixture + `recording_launcher`): the recorded
  program word is `HYPERSHELL_RELAY_REMOTE_EXE`'s value; `--remote-exe` wins over it; an empty key
  leaves the relay's own program; with `--database` the recorded environment has no
  `_HYPERSHELL_RELAY_PROGRAM`. xfail P2.
- **Units** (`@mark.unit`): `relay_program` (absolute, relative made absolute, symlink kept,
  non-executable file, `-c`, `None`, empty scripts directory → `'hs'`); `RelayApp.program` (own
  command line; embedded `main` → not `argv[0]`; carried with and without `REEXEC_MARKER`, popped
  either way); `client_exe` precedence table (option, key, option over key, empty key, `None` key);
  `bind_database` hands `PROGRAM_MARKER` to `os.execve` (patched); `RelayThread(zones=['cpu', 'gpu',
  None], remote_exe=…)` → every fleet's `machine.launch` names it, with and without a launcher;
  `relay.remote_exe` is a declared text key in `EMPTY_UNSET` and is fenced by `scrub_environment`;
  `hs relay --help` lists `--remote-exe` after `--launcher-args` (xfail P2). The R4 table: both
  constants, `hypershell.cluster.DEFAULT_REMOTE_EXE`, and the `remote_exe` defaults of `SSHCluster`,
  `RemoteCluster`, `AutoScalingCluster`, `site_cluster`, `zone_launch` and `RelayThread` are `'hs'`
  (xfail P3). Docs/completions checks (help pages, config reference, completions name
  `--remote-exe`/`relay.remote_exe`, `relay_help.rst` no longer says `hyper-shell client`; xfail P2).
- **Changed:** `tests/test_cluster_exit.py:332` expects `f'{launcher} hs client '` (xfail P3).
  `tests/test_config_schema.py::MATCHES` and `tests/test_env_config.py::EMPTY_NOT_GIVEN` gain the key
  in P2, with it (dict-driven parametrization cannot carry a per-row mark).
- **Gate (P4):** the whole suite serially under 3.14 and 3.11, the Sphinx `-W` build.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
