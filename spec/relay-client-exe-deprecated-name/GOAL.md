# GOAL — A relay starts its site clients with its own `hs`, not the legacy `hyper-shell`

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** relay-client-exe-deprecated-name
- **kind:** fix
- **appetite:** small  ·  *Taken from the seed. One launch default, one option and key, their
  docs and completions, and one test. The Python API default (Q1) is the only part that reaches
  beyond the relay.*
- **target:** 2.9.0 (for 2.9.0a10)

## Problem

A relay launches every site client as the literal command `hyper-shell client …`. Both of its
launch builders go through `zone_launch` (`src/hypershell/relay/site.py:162`, the
`client_launch(DEFAULT_REMOTE_EXE, …)` call at `:181`; used by `site_cluster` at `:150` and by
`RelayThread` at `src/hypershell/relay/__init__.py:551`), and `DEFAULT_REMOTE_EXE` is
`'hyper-shell'` (`src/hypershell/cluster/remote.py:54`). `hs relay` has no `--remote-exe` option
and `[relay]` has no key to change it (`src/hypershell/core/config_schema.py:98-108`). The
defect is **pre-existing** since the relay landed (2.9.0a6); all line numbers are checked against
`develop` at `5897e5ab`.

`hyper-shell` is the legacy entry point (`AGENTS.md`: "legacy `hyper-shell` … keep all three").
An installation that puts only `hs` and `hsx` on `PATH` has no `hyper-shell` there, so every relay
launch fails on the node with command-not-found. RCAC's modules do exactly that: they link
`bin/hs` and `bin/hsx` to `libexec/bin/` and put only `bin/` on `PATH` (checked 2026-11-23 on
Gautschi, per the seed). The relay-local fleet then relaunches a client that can never start.

`hs cluster` does not have this problem: its `--remote-exe` defaults to
`sys.argv[0].rstrip('x')` (`src/hypershell/cluster/__init__.py:212`), the path of the `hs` the
user ran. The relay cannot simply copy that: when it is given `--database`, or is started through
`main(['relay', …])`, it re-executes itself as `python -c '…' relay …`
(`src/hypershell/relay/__init__.py:1376`, `REEXEC_CODE` at `:212`), after which `sys.argv[0]` is
`-c`.

The Python API has the same default twice over: `cluster/ssh.py:42` defines a second
`DEFAULT_REMOTE_EXE = 'hyper-shell'`, exported from `hypershell.cluster`
(`cluster/__init__.py:41`, `:52`), and `SSHCluster` (`ssh.py:248`), `RemoteCluster`
(`remote.py:259`) and `AutoScalingCluster` (`remote.py:964`) default `remote_exe` to them.

What it costs: tier 4 of the RCAC Workflow Automation guide (a server on one cluster, relays on
each cluster's login node) cannot run on RCAC's module layout, and the guide's merge is held on
it.

## Outcome / vision

A relay starts its clients the way `hs cluster` does: with the `hs` of the installation that runs
the relay, named by its full path, so a relay works wherever `hs relay` itself was found. A site
whose clients must run a different installation says so with `hs relay --remote-exe` or
`relay.remote_exe`. No built-in launch default anywhere in HyperShell names `hyper-shell`; the
`hyper-shell` entry point itself stays.

## Acceptance criteria (the contract)

- **R1** — WHEN `hs relay` runs with no remote executable given, the relay SHALL launch each site
  client with the HyperShell program that runs the relay, named by an absolute path, so that a
  relay started from a `bin/` that holds `hs` and `hsx` but no `hyper-shell` (and with no
  `hyper-shell` on `PATH`) launches clients that start, connect and run tasks. *(Which file R1
  names is Q2 and Q3.)*
- **R2** — R1 SHALL hold however the relay was started: from a symlinked `bin/hs`, with
  `--database PATH` (which re-executes the relay), and through each zone's fleet and each
  topology-driven launch alike.
- **R3** — `hs relay` SHALL accept `--remote-exe PATH`, and `[relay]` SHALL accept a
  `remote_exe` key (kind TEXT in `config_schema.KEYS`), each naming the program a site client is
  launched as. WHEN either is given, every site client of every zone SHALL be launched with it;
  WHEN both are given, the command-line option SHALL win. An unset key SHALL mean the R1 default.
  *(An empty key: Q6.)*
- **R4** — No built-in default for the program a client is launched as SHALL be the bare name
  `hyper-shell`, in the relay or in the Python API's cluster classes. *(What the Python API
  default becomes is Q1.)*
- **R5** — A test SHALL start a relay from a symlinked `bin/hs` whose directory holds no
  `hyper-shell`, with no `hyper-shell` on `PATH`, and assert that the launched client's command
  names that installation's `hs` and that a client starts and completes a task; a second case SHALL
  do the same with `--database` given. A test SHALL show `--remote-exe` and `relay.remote_exe`
  taking effect, with the option over the key.
- **R6** — The change SHALL be documented in the same commit as the code: `hs relay`'s usage and
  help text in source, `docs/_include/relay_usage.rst` and `relay_help.rst` (the `--launcher`
  entry's `CMD [ARGS...] hyper-shell client ...` and a new `--remote-exe` entry),
  `docs/_include/config_param_ref.rst`'s `[relay]` section, the bash and zsh completions under
  `share/`, and the `[relay] launcher` comment in `core/config.py`. IF the Python API default
  changes (Q1), THEN the 2.9.0 release notes SHALL say so.

## Non-goals (no-gos)

- **Removing or renaming the `hyper-shell` entry point.** It stays (`pyproject.toml:105`,
  `AGENTS.md`); a relay started as `hyper-shell relay` must keep working.
- **Changing `hs cluster`'s `--remote-exe` default.** It already works; the plan may share code
  with it but SHALL NOT change what `hs cluster` launches.
- **A per-zone remote executable** (`remote_exe` in `[relay.zone.NAME]`). All zones of one relay
  run one installation (pending Q5).
- **A command line as the remote executable.** The value names one program; a wrapper
  (`apptainer exec …`, `env …`) belongs in `--launcher`.
- **Checking that the remote executable exists on the client's node**, or new diagnostics for a
  client that cannot start: the relay's existing relaunch warnings cover that.
- **Installations where the client's node sees the relay's installation at a different path**:
  `--remote-exe` is the escape hatch, as it is for `hs cluster`.
- **`python -m hypershell`.** The package has no `__main__.py`, so there is no such start form to
  support.

## Clarifications

- **Seed promoted** (2026-11-23). `status: unshaped`; its draft R1–R4 were taken as input. The
  seed's R1 ("resolved the way `hs cluster` resolves `--remote-exe`") is restated as an outcome,
  because `sys.argv[0]` is `-c` after the relay's re-execution (Problem); its R3 became R4 here.

- **Q1** `[NEEDS CLARIFICATION: Python API default.]` `SSHCluster`, `RemoteCluster` and
  `AutoScalingCluster` default `remote_exe` to `DEFAULT_REMOTE_EXE = 'hyper-shell'` (two
  constants, `ssh.py:42` and `remote.py:54`; the first is exported from `hypershell.cluster`).
  Options:
  (a) keep `'hyper-shell'` for the API and fix only the relay (R4 narrows to the relay);
  (b) change both constants to `'hs'`, keeping the exported name;
  (c) make the API default the running installation's `hs` by path, as for the relay.
  **Recommendation: (b).** Every installation that has `hyper-shell` also has `hs` (same package,
  same scripts), so `'hs'` cannot break a working setup that relies on `PATH`, and it fixes the
  `hs`-only layout. (c) is wrong for a library: in an embedding program `sys.argv[0]` is the
  user's script, and a computed module-level default surprises API users. Changing the value is a
  public default change for 2.9.0 and goes in the release notes (R6). `tests/test_cluster_exit.py:332`
  asserts `hyper-shell client` in an `hs cluster`-style launch message built from this default and
  will change with it.

- **Q2** `[NEEDS CLARIFICATION: how the relay's own hs is resolved.]` Start forms: `hs relay` via
  `PATH` (`sys.argv[0]` is then the full path the shell found, symlink unresolved); `./bin/hs relay`
  (a relative path); `hyper-shell relay`; a site wrapper script that execs the real `hs`;
  `main(['relay', …])` from Python; and any of these with `--database`, which re-executes as
  `python -c …` so `sys.argv[0]` is `-c`. Options:
  (a) `sys.argv[0]` as `hs cluster` does — fails after re-execution and embeds a relative path;
  (b) the `hs` script in the running interpreter's scripts directory (`sys.executable`'s
  directory, or `sysconfig`'s `scripts` path) — survives re-execution and wrappers, but names
  `libexec/bin/hs` rather than the `bin/hs` link, and misses a `pip --user` layout;
  (c) `sys.argv[0]` made absolute when it names an existing file, captured before re-execution and
  carried across it; otherwise (b); otherwise the bare `hs`.
  **Recommendation: (c).** It names what the user ran whenever that is knowable (so a symlinked
  `bin/hs` stays the symlink, matching `hs cluster`), survives `--database`, and degrades to a
  path that still exists. The mechanism for carrying it across re-execution is `hs-plan`'s.

- **Q3** `[NEEDS CLARIFICATION: a relay started as hyper-shell.]` When the user runs
  `hyper-shell relay`, should clients be launched as that `hyper-shell` path, or as the `hs`
  beside it? **Recommendation: the program the user ran, whatever its name.** It exists by
  construction, and the seed's "never by the deprecated name" targets the bare-name default,
  not an absolute path the user chose. R4 is worded to allow this.

- **Q4** `[NEEDS CLARIFICATION: launchers that leave the host.]` The relay has no `--ssh` mode,
  but `--launcher` may be `srun`, `mpirun` or `ssh HOST`, and a client may land on a node that does
  not see the relay's installation at the same path. Options: (a) no special case: the absolute
  path assumes a shared installation, and `--remote-exe`/`relay.remote_exe` is the escape hatch,
  stated in the docs; (b) fall back to the bare `hs` whenever a launcher is set. **Recommendation:
  (a).** It is what `hs cluster` does under `--ssh` and `--launcher`, and (b) reintroduces the
  `PATH` dependency that caused this defect.

- **Q5** `[NEEDS CLARIFICATION: per-zone remote executable.]` Should `[relay.zone.NAME]` accept
  `remote_exe` beside `launcher` and `launcher_args`? **Recommendation: no, not in this cycle**
  (Non-goals). The zones of one relay share its host and filesystem; a zone that needs another
  installation can wrap it in its own launcher. A later seed can add it.

- **Q6** `[NEEDS CLARIFICATION: an empty relay.remote_exe.]` Should an empty value (including an
  empty `HYPERSHELL_RELAY_REMOTE_EXE`) mean "not given" (add the key to `EMPTY_UNSET`, like
  `relay.advertise`), or be refused at start? **Recommendation: not given**, matching the other
  optional text keys of `[relay]`; an empty `--remote-exe ''` on the command line is absurd input
  and out of scope.

## Related materials

- Seed: `issues/relay-client-exe-deprecated-name.md` (ROADMAP entry "A relay starts its clients
  as the deprecated `hyper-shell`").
- Related seed: `issues/autoscaling-cluster-ignores-usr1.md` (found in the same work).
- Found while drafting the RCAC Workflow Automation guide's tier 4 (RCAC-Docs PR #338),
  2026-11-23.
- Source: `src/hypershell/relay/site.py` (`zone_launch`, `site_cluster`, `fence_site`),
  `src/hypershell/relay/__init__.py` (option parsing, `bind_database` re-execution),
  `src/hypershell/cluster/launch.py` (`client_launch`), `src/hypershell/cluster/remote.py`,
  `src/hypershell/cluster/ssh.py`, `src/hypershell/cluster/__init__.py` (`--remote-exe`),
  `src/hypershell/core/config.py` (`[relay]` defaults), `src/hypershell/core/config_schema.py`
  (`KEYS`, `EMPTY_UNSET`).
- Docs: `docs/_include/relay_usage.rst`, `relay_help.rst`, `config_param_ref.rst` (`[relay]`),
  `cluster_help.rst` (`--remote-exe`); completions `share/bash_completion.d/hs`,
  `share/zsh/site-functions/_hs`.
