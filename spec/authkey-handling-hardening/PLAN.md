# PLAN — The queue authentication key is never exposed to other users on client nodes

> **Status:** Draft for review · **Last updated:** 2026-11-05
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Every design element traces to a GOAL R-ID.
> Security lane: this file describes the hardened behaviour only. The limitation it removes is the
> one `docs/security.rst` § *Authentication Key* already states; the detail stays in the hidden lane.

## 1. Summary

Every client-launching mode builds its client command line, environment and key handoff through one
new function, `client_launch()` in a new module `src/hypershell/cluster/launch.py`. The key never
appears on any command line it builds. Launcher modes (`--launcher`, `--mpi`, `--autoscaling` with
either policy) hand the key to the client in the launched process's environment, as
`HYPERSHELL_SERVER_AUTH`. `--ssh` writes it to the ssh channel's standard input, where a small POSIX
`sh` preamble on the remote side reads it into that same variable and `exec`s the client. Every
client since 2.8.0 already takes its key from `HYPERSHELL_SERVER_AUTH` when no `-k` is given, so the
client, the server, the handshake and the wire do not change. It fits `small`: one new ~150-line
module, three call-site replacements, one new test file, and a docs revision.

## 2. Design

Base: `fix/authkey-handling-hardening` at `865031c`, rebased on `develop` `02b292a` (2.9.0a4), so
the launchers already carry `--zone` (#78) and the in-process queue (#76). `core/queue.py`,
`client.py` and `server.py` are **not** touched.

### 2.1 The single launch function (`src/hypershell/cluster/launch.py`, new)

```python
AUTH_ENV: Final[str] = 'HYPERSHELL_SERVER_AUTH'

@dataclass(frozen=True)
class ClientLaunch:
    argv: List[str]                                   # what Popen runs; never holds the key
    env: Dict[str, str] = field(repr=False)           # additions merged over the inherited env
    stdin: Optional[str] = field(default=None, repr=False)  # SSH only: the key line
    inherit_env: bool = False                         # SSH: Popen gets no env= (as today)
    def start(self, **popen_options) -> Popen: ...    # one client process per call
    def describe(self) -> str: ...                    # redacted argv for logs; never env/stdin

def client_launch(remote_exe: str, address: Tuple[str, int], auth: str, *,
                  launcher: Sequence[str] = (), launcher_args: Sequence[str] = (),
                  ssh_host: Optional[str] = None, ssh_exports: Sequence[str] = (),
                  num_threads: int, bundlesize: int, bundlewait: int, template: str,
                  delay_start: float, task_signalwait: int,
                  no_confirm: bool = False, capture: bool = False, monitor: bool = False,
                  client_cores: int = None, client_memory: int = None, client_timeout: int = None,
                  task_timeout: int = None, ratelimit: int = None,
                  tls_enabled: bool = True, zone: Optional[str] = None) -> ClientLaunch
```

- **One place for the client invocation (planning requirement 3; Z3 changes only this).** The
  function owns the shared client options (`-H -p -N -b -w -t -d -S`, the optional `--no-confirm`,
  `--capture`, `--monitor`, `-C -M -T -W -R`, `--no-tls`, `--zone`) that each launcher builds
  separately today. It owns the key handoff too. Callers keep only what is theirs: the key
  (`secrets.token_hex(64)`, unchanged), the template choice (`DEFAULT_TEMPLATE` in JSON mode,
  §11), the advertised host, and the `launcher` / `launcher_args` / nodelist.
- **Launcher modes** (`ssh_host is None`): `argv = [*launcher, *launcher_args, remote_exe, 'client',
  …options]`. `env = {AUTH_ENV: auth}`, plus `OMPI_MCA_mca_base_env_list` with `AUTH_ENV` appended
  to any value already set (`;`-separated). That entry asks Open MPI's `mpirun` to forward the
  variable to remote ranks. Other launchers ignore it, and it carries a name, not a value.
  `start()` passes `env={**os.environ, **load_task_env(), **self.env}`, the same merge the
  launchers make today with the key added last so that it wins.
- **SSH** (`ssh_host` set): `argv = [*launcher, *launcher_args, '-T', ssh_host, *ssh_exports, 'sh',
  '-c', shlex.quote(script)]`, where `script` is
  `IFS= read -r HYPERSHELL_SERVER_AUTH || { echo '…no authentication key on standard input' >&2;
  exit 1; }; export HYPERSHELL_SERVER_AUTH; exec <remote_exe> client <shlex.join(options)>`.
  - `remote_exe` stays unquoted, as today, so `~` and `$VAR` in it still expand.
  - `ssh_exports` are the existing `--env` tokens (`compile_env()`), unchanged.
  - The explicit `sh -c` makes the preamble independent of the user's login shell.
  - Quoting is now one uniform `shlex` layer. Today `-t` gets hand-made single quotes and `--zone`
    gets `shlex.quote`. A template with spaces or `$` expands exactly as before; one that contains
    a `'` now works.
  - `-T` goes after the user's ssh arguments, so no remote terminal is allocated whatever those
    arguments or `ssh_config` say. Standard input is then a plain channel to the remote shell.
  - `start()` opens ssh with `stdin=PIPE`, writes the key line, and closes the pipe. A
    `BrokenPipeError` from a host that is already gone is ignored, and ssh's exit status reports
    the failure as it does today. `env=` is not passed, so ssh inherits the environment as today.
- **The launching process's own `os.environ` is never modified.** The key exists only in the
  `ClientLaunch` and in each child's environment or pipe.
- **Logging (R6).** `describe()` is `redact_secrets(argv, ['-k', '--auth'])`. The redaction stays
  as a guard even though the argv no longer carries the key. `env` and `stdin` are `repr=False`
  and are never logged. `redact_secrets` moves to `launch.py` and is re-exported from
  `cluster/remote.py` (its `__all__` and the `ssh.py` import keep working).

### 2.2 Call sites

- `cluster/remote.py` `RemoteCluster.__init__`: build one `ClientLaunch` (`self.launch`).
  `run_with_exceptions` logs `self.launch.describe()` and calls `self.launch.start(stdout=…,
  stderr=…)`. `client_argv` stays as a read-only alias of `self.launch.argv` for introspection.
- `cluster/remote.py` `AutoScalingCluster.__init__` → `AutoScalerThread(launch, …)`. `AutoScaler`
  holds a `ClientLaunch` instead of an argv list. `scale()` calls `self.launch.start(stdout=…,
  stderr=…, bufsize=0, universal_newlines=True)`, so every scale-up in both `fixed` and `dynamic`
  policies goes through the same function. `start()` logs `describe()`.
  - `tests/test_autoscale.py` `make_scaler` builds its scaler with
    `client_launch('hs', ('localhost', 0), 'x'*32, …)` in place of `['hs', 'client']`.
- `cluster/ssh.py` `SSHCluster.__init__`: one `ClientLaunch` per host (`self.launches`).
  `run_with_exceptions` starts each one. `client_argv` stays as the list of their argvs.
- `cluster/local.py`: unchanged (in-process clients; R2 only confirms it, by the existing suite).
- No CLI option, help text, config key or default changes, so `docs/_include/*.rst` and `share/`
  are untouched (R8's conditional clause does not fire).

### 2.3 Docs (R8)

- `docs/security.rst` § *Authentication Key*: replace the admonition *"The key is visible on the
  client command line in cluster launches"* with one that says how launched clients receive the
  key:
  - Launcher modes use the client's environment. Name the requirement that the launcher pass the
    environment through: `srun`, MPICH, Intel MPI and Flux do by default; Open MPI is told to by
    the launch itself; a launcher set to drop the environment (e.g. `srun --export=NONE`) makes
    clients fail to authenticate.
  - SSH uses the ssh channel's standard input, with no remote terminal. `-t`/`-tt` in the ssh
    arguments have no effect on that launch (the forced `-T` follows them), and a bare `-n` is
    dropped with a warning.
    - **Correction (2026-11-05, P3 finding and foreman ruling at P4).** The earlier wording,
      "`-n` and `-tt` ... have no effect", did not hold: `-T` overrides `-t`/`-tt`, but nothing
      overrode `-n`, which made ssh read `/dev/null`, so the key never arrived. Ruling: for
      client launches, `client_launch()` drops a bare `-n` from the ssh arguments (from
      `--ssh-args` or `config.ssh.args`) and logs one WARNING naming it.
  - The key is on no command line.
  - Keep, unchanged, the sentence about a directly configured `server.auth` in plaintext (a
    separate item).
- `AGENTS.md`: rewrite § *Queue transport & security* "Auth-token exposure" to the hardened
  behaviour. In § *Cluster orchestration* and § *High-risk files & footguns*, replace "There is
  no shared client-argv builder" with "`cluster/launch.py` `client_launch()` is the one
  client-invocation builder".
- `.agents/factory/invariants.md` §11: the same bullet change.
- Top-level commands only in any prose (`hs list`, `hs info`, `hs wait`, `hs run`, `hs update`).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `client_launch()` never puts the key in `argv`. Launcher modes: key only in the child's environment. SSH: key only on ssh's stdin, read by the remote `sh` builtin. The launcher's own environment is never modified (§2.1). |
| R2   | All four subprocess modes (`--launcher`, `--mpi`, both `--autoscaling` policies, `--ssh`) build through `client_launch()`, each call site changed in its own phase and tested on its own. `local` is unchanged and confirmed by the existing suite (§2.2). |
| R3   | Same client options, same template rule, same host/port. The key reaches the client's `config.server.auth`. Tests run each mode end to end with TLS on and with `--no-tls`, and with `--zone` (§6). |
| R4   | `core/queue.py`, `client.py`, `server.py` untouched. Handshake, wire and key format unchanged. Manual `hs server`/`hs client` key options unchanged. |
| R5   | 2.9 line: the launched command uses only options a 2.9.0a4 client accepts, and the handoff variable is the one every client since 2.8.0 reads. A contract test pins both, and a tag-gated test runs a 2.9.0a4 client under the new launcher (§6). 2.8 line: §7. |
| R6   | `describe()` = `redact_secrets(argv, ['-k','--auth'])`. Secret fields `repr=False`. A test captures all log records at every level during each mode's launch and finds no key. |
| R7   | `tests/test_cluster_launch.py`: one end-to-end test per mode with a stand-in `ssh`/`mpirun`/launcher script in `tmp_path` that records what it ran. Red on `865031c`, green after. No special setup, no second account (§6). |
| R8   | `docs/security.rst`, `AGENTS.md` (two sections), `invariants.md` §11 revised in P4. No option/help change, so no `_include/`/`share/` change. |

## 3. Invariant gate (AGENTS.md constitution check)

Gate #1 (pre-design) and #2 (post-design) both run; touched sections §9, §10, §11, §12.

- **§9 Auth is always required; key policy.** Unchanged. The key is still `secrets.token_hex(64)`
  (128 chars of `[0-9a-f]`, passes `AUTH_MINIMUM_LENGTH`/`AUTH_ALLOWED_CHARS`). No path drops the
  key or falls back to the placeholder. A client that receives no key fails authentication loudly
  (SSH: the preamble exits 1 before the client starts).
- **§9 TLS / cert material not forwarded.** Unchanged: only `--no-tls` is propagated, now from
  `client_launch()`.
- **§10 Config built once at import.** The client reads `HYPERSHELL_SERVER_AUTH` at import into
  `config.server.auth`, the `-k` default. Env outranks every config file (CLI > env > files), so a
  stray `server.auth` in a client node's config cannot override the run's key. The launching
  process's `config` and `os.environ` are not mutated.
- **§11 Cluster orchestration.** "No shared client-argv builder" becomes "one builder". JSON mode
  still sends `DEFAULT_TEMPLATE` (the caller passes the template it passes today). The advertised
  host is unchanged. The autoscaler still never terminates clients and still ignores
  `no_confirm`/`in_memory`/`forever`/`restart`, because the call site passes the same options as
  today. SSH still shells out to the plain `ssh` binary.
- **§12 Same-commit rules.** No CLI/help change, so no `_include/`/`share/` change. Python 3.11–3.14
  only (dataclass `field(repr=False)`, `shlex.join`).
- **High-blast-radius files:** `cluster/remote.py` and `cluster/ssh.py` are touched, so this cycle
  takes the human gate (expected; `hammerable: false` on every phase). `client.py` and `core/*` are
  not touched.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| New module `cluster/launch.py` | Planning requirement 3 (one function for Z3). It also lands on `2.8.4` as a new file with no merge conflict (§7). | Putting `client_launch()` inside `remote.py` works on `develop`, but the 2.8.5 port would then conflict inside `remote.py`, the most-edited launcher file. |
| SSH appends `-T` after user ssh args | The key travels on standard input, which must be a plain channel. | Honouring a user's `-tt` puts a terminal between the launcher and the remote shell, which the stdin handoff cannot rely on; documenting "don't pass `-tt`" leaves a footgun. |

## 4. Rabbit holes (resolved)

The appetite is `small`, but `remote.py` and `ssh.py` are high-blast-radius, so the high-blast-radius
exception applied. The research was done inline and in sequence; see
[`research/00-digest.md`](research/00-digest.md),
[`01-client-key-intake.md`](research/01-client-key-intake.md) and
[`02-launch-paths.md`](research/02-launch-paths.md).

- **Do older clients accept the key this way?** Yes. `git show <tag>:src/hypershell/client.py` and
  `core/config.py` for 2.8.0, 2.8.1, 2.8.2, 2.8.3, 2.8.4, 2.9.0a1 and 2.9.0a4 all have
  `auth: str = config.server.auth` as the `-k` default and `Environ(prefix='HYPERSHELL').expand()`
  in the config merge. A client given no `-k` and `HYPERSHELL_SERVER_AUTH` in its environment uses
  that key on every tag.
- **Does any launcher today depend on argv-only delivery?** No. `RemoteCluster` and `AutoScaler`
  already pass an explicit `env=` to `Popen`. `SSHCluster` has no stdin use today; its ssh children
  inherit the parent's stdin, so moving them onto a closed pipe also stops ssh competing for a task
  stream on `hs cluster`'s own stdin.
- **Remote login shells that are not POSIX (csh/tcsh/fish).** The preamble runs under an explicit
  `sh -c`, so only the outer single-quoted word is parsed by the login shell. That parse works in
  all of them. `--env` exports still assume a Bourne-like login shell, as they do today.
- **`server.auth_env` / `auth_eval` on client nodes.** cmdkit refuses an attribute that has more
  than one variant. A config-file `auth_env` already collides with the built-in default `auth`
  today, so this change adds no new failure (see §5 for the pre-existing item).

## 5. Risks & open questions

- **Q1 (maintainer): launcher modes rest on environment pass-through.**
  - `srun` (default `--export=ALL`), MPICH/Hydra, Intel MPI and Flux pass the environment.
  - Open MPI is asked to through `OMPI_MCA_mca_base_env_list`. That parameter is documented for
    Open MPI 4; it is not yet checked on Open MPI 5.
  - A launcher configured to drop the environment now fails to authenticate where it ran before.
    That is the price of taking the key off the command line, and the docs say so.
  - There is no fallback to the command line.
  - Proposed: accept and document, and have the maintainer check `--mpi` once on a real Open MPI 4
    and 5 install at review. The factory has no cluster access.
  - **Ruling (maintainer, 2026-11-05): accept and fail clearly.**
    - The docs state the requirement.
    - A client started by `--launcher`/`--mpi` that reaches the server without the launch's key
      exits with an error naming the likely cause: the launcher did not pass the environment. It
      does not exit with a bare authentication failure.
    - The smallest change that achieves this is in scope, including in `client.py` if needed.
    - Before merge, the foreman checks `--launcher srun` on the test cluster, with the default
      environment and with `--export=NONE`, and checks `--mpi` with the Open MPI it offers.
- **Q2 (maintainer): SSH options.**
  - The launch now owns ssh's standard input and forces `-T`, so a user's `-n`/`-t`/`-tt` in
    `--ssh-args` or `config.ssh.args` no longer applies to client launches.
  - Clients still exit on idle and on server shutdown, as today.
  - Proposed: accept and document.
  - **Ruling (maintainer, 2026-11-05): accept and document**, in the docs and the release notes.
- **Q3 (maintainer, optional): task environment.** Tasks inherit `HYPERSHELL_SERVER_AUTH` from
  their client, as they inherit any `HYPERSHELL_*` variable a user sets. That makes it visible to
  the same user only. Dropping it from task environments would touch `client.py`, and only 2.9
  clients would do it. Proposed: no change in this cycle; the maintainer decides.
  - **Ruling (maintainer, 2026-11-05): no change in this cycle;** recorded as a follow-up seed.
- **Risk: test runtime and hangs.** A client that fails to authenticate leaves a cluster waiting.
  Every new test runs `hs cluster` in its own session with a hard timeout and kills the process
  group on expiry.
- **Pre-existing, out of scope:** a config-file `server.auth_env`/`auth_eval` appears to collide
  with the built-in default `auth` (cmdkit "more than one variant"), although `docs/security.rst`
  recommends those suffixes. Worth its own public issue; not changed here.

## 6. Verification strategy

New file `tests/test_cluster_launch.py` (marked `integration`). The test names state the fixed
property: the launch keeps the key off every command line, and the run completes.

- **Stand-ins, written to `tmp_path` per test:**
  - a `launcher` script (also installed as `mpirun` on a test-local `PATH` for `--mpi`). It records
    its argv and whether `HYPERSHELL_SERVER_AUTH` is set, then `exec`s the rest of its argv.
  - an `ssh` script. It records its argv and the remote command, saves its standard input to a
    `0600` file under `tmp_path`, and runs the remote command with `sh -c`, the way `sshd` runs a
    login shell, feeding that input back.
  - `--remote-exe` points at a wrapper that runs this tree's `hypershell.main` with
    `sys.executable`.
- **Per mode** (`launcher`, `mpi`, `autoscaling-fixed`, `autoscaling-dynamic`, `ssh`): run
  `hs cluster` on a small task file in a temp site.
  - The run completes, and every task has a recorded exit status of 0 (read through the data
    model).
  - The key the client received (from the child's environment, or from the stdin the `ssh` stand-in
    saved) is non-empty and appears in no recorded argv and no remote command string.
  - Parametrised over TLS on and `--no-tls`, plus one `--zone` case per mode.
  - Autoscaling runs are stopped once the data model shows no remaining tasks.
- **R6:** each mode's test also captures log records at every level (TRACE through CRITICAL) for
  the launch and asserts that the key is in none.
- **Red-first (R7):** each phase writes its mode's tests first, runs them against the unchanged
  call site and records the failure in the phase notes, then makes the change and goes green.
- **R5 contract (no network):**
  - Every option in `client_launch()`'s argv must belong to a frozen set: the `hs client` options
    2.9.0a4 accepts.
  - `AUTH_ENV == 'HYPERSHELL_SERVER_AUTH'`.
  - `os.environ` is unchanged after building and starting a launch.
- **R5 cross-version (tag-gated):** if tag `2.9.0a4` is reachable, `git archive` its `src/` into
  `tmp_path` and run the `launcher`-mode test with a `--remote-exe` wrapper that imports that tree.
  The test is skipped with a reason otherwise (shallow CI clones).
- **Regression:** `tests/test_cluster.py` (local mode, R2/R3), `tests/test_autoscale.py`, and the
  full suite at P4.
- **CLI drive:**
  `.agents/factory/bin/temp_site.sh sh -c "seq 20 | uv run hsx -t 'echo {}' --launcher=env -N2"`
  — `env` as a trivial launcher runs the client locally through the launcher path, end to end.

## 7. Backport to 2.8.5

Base: tag `2.8.4` (read with `git show 2.8.4:path`). Done on the maintainer's `hotfix/2.8.5`
branch after this cycle lands on `develop`, alongside the TCP_NODELAY backport. Not part of this
branch's TECH (no `master`/hotfix writes from this cycle).

- **What differs at 2.8.4:** the launchers have no `zone` parameter and no `from_json`/
  `client_template` (they pass `template` directly). The structure is otherwise identical: the
  same three client-argv blocks, the same `env={**os.environ, **load_task_env()}` in `RemoteCluster`
  and `AutoScaler.scale`, the same SSH `Popen` without `env=`, and the same `redact_secrets`.
- **How it adapts:**
  - Copy `cluster/launch.py` verbatim. Its `zone` argument defaults to `None` and is never passed
    on 2.8, so `--zone` is never emitted to a 2.8 client.
  - Hand-port the three call sites, dropping the `zone=`/`from_json` lines.
  - Adapt `tests/test_autoscale.py` `make_scaler` the same way.
  - Port `docs/security.rst` and `AGENTS.md` with the same text.
- **R5-on-2.8 testing:**
  - The same `tests/test_cluster_launch.py`, without the `--zone` cases.
  - The contract test's frozen option set becomes the `hs client` options 2.8.0 accepts. Every
    2.8.x client must take every option the launchers emit; the port checks this from the tags.
  - The tag-gated cross-version test runs clients from tags `2.8.0` and `2.8.4` under the 2.8.5
    launcher, in both launcher and SSH modes.
  - For 2.8.5 server ↔ any 2.8.x peer interop: the server, client and queue code are untouched, so
    the 2.8.x handshake is identical by construction. A maintainer drive with
    `tests/cluster/build_archive.sh` (`HSA_REF=2.8.0`, `2.8.4`) and `drive.sh` client archives
    confirms it on real nodes.
- **Estimate:** about half a day (port plus tests), plus the maintainer's drive.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
