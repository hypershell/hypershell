---
slug: authkey-handling-hardening
title: The queue authentication key is never exposed to other users on client nodes
kind: fix
appetite: small
status: blocked
branch: fix/authkey-handling-hardening
base: develop
current_phase: done
last_updated: '2026-11-05'
phases:
- id: P1
  name: One client-launch function; --launcher and --mpi hand the key over in the
    environment
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R6
  - R7
  depends_on: []
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py -k 'launcher
    or mpi' && uv run pytest -p no:cacheprovider -q tests/test_cluster.py
- id: P2
  name: Autoscaling (fixed and dynamic) launches through the same function
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R6
  - R7
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py -k autoscaling
    && uv run pytest -p no:cacheprovider -q tests/test_autoscale.py
- id: P3
  name: SSH launches through the same function, key on the ssh channel's standard
    input
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R6
  - R7
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py -k ssh
    && uv run pytest -p no:cacheprovider -q tests/test_cluster.py
- id: P4
  name: Compatibility contract, cross-version check, and the docs revision
  status: done
  satisfies:
  - R4
  - R5
  - R8
  depends_on:
  - P2
  - P3
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py && uv
    run pytest -p no:cacheprovider -q -n auto && .agents/factory/bin/temp_site.sh
    sh -c "seq 20 | uv run hsx -t 'echo {}' --launcher=env -N2 -H 127.0.0.1"
review:
  last_reviewed_commit: 20d2509cb824039e490620719c16765aad3cc731
  verdict: changes-requested
  blocked_reason: 'Review cycle 1: residual key reach in one SSH option combination
    (hidden lane), Q1 hint false positive, docs scope of the Open MPI parameter; human
    gate'
  cycle: 1
---
# TECH.md — The queue authentication key is never exposed to other users on client nodes

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/authkey-handling-hardening/TECH.md`); the
per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `.agents/factory/bin/set_phase.py`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md) (§2 design, §6 verification, §7 backport).
- **Security lane:** this is a hidden-lane cycle. Code, tests, docstrings, commit messages and
  this file describe the hardened behaviour only. Test names assert the fixed property (for
  example `test_launcher_mode_keeps_key_off_command_lines`). They never narrate the behaviour
  before the fix. Nothing from `.security/` is copied into a tracked file.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done`.
- phase `status`: `pending | in_progress | done | blocked`.
- `satisfies`: GOAL R-IDs this phase delivers. `depends_on`: phases that must be `done` first.
- `parallel`: `false` throughout — every phase touches `cluster/remote.py` or `cluster/ssh.py`, or
  the shared test file.
- `hammerable`: `false` throughout — this is a security fix; no phase may be scope-hammered.
- `hill`: `uphill` → `crest` → `downhill`. `attempts`: durable failed-verify counter.
- `verify`: the exact command that proves the phase.

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; [`invariants.md`](../../.agents/factory/invariants.md)
  §9–§12 are the ones this change touches.
- One phase per `hs-build` invocation; one atomic commit with the code and the `TECH.md` state
  change, subject `[fix] Build authkey-handling-hardening P<n>: …`. **No trailer.**
- Do not touch `core/queue.py`, `client.py` or `server.py`. If a phase finds it must, STOP: that goes
  back to sign-off (GOAL non-goals). One exception, by maintainer ruling Q1 (PLAN §5, 2026-11-05):
  `client.py` carries the smallest change that makes a client which reaches the server with no key
  of its own name the likely cause (P1).
- No CLI option or help text changes in this cycle. If one becomes necessary, the affected
  `docs/_include/*.rst` and `share/` completions change in the same commit (R8).
- Docs prose uses top-level commands only (`hs list`, `hs info`, `hs wait`, `hs run`, `hs update`).
- **Red-first (R7).**
  - Each phase writes its mode's tests before it changes the call site.
  - It runs them on the unchanged code and confirms that they fail on the key-property
    assertion, not on an error in the harness.
  - It records that in the commit body as "red on <sha>: N failed", then makes the change.
- Every test that runs `hs cluster` does so in a subprocess in its own session, with a hard
  timeout. On expiry it kills the process group, so a client that cannot authenticate can never
  hang the suite.

---

## Phase P1 — One client-launch function; `--launcher` and `--mpi` hand the key over in the environment
**Satisfies:** R1, R2, R3, R6, R7 · **Depends on:** —
**Goal:** `client_launch()` exists. `RemoteCluster` (used by `--launcher` and `--mpi`) builds its
one client launch through it. The key reaches the clients through their environment only, and runs
complete with TLS on and off, with and without `--zone`.

- [x] Create `tests/test_cluster_launch.py` (marked `integration`) with the shared harness from
      PLAN §6:
  - [x] the stand-in `launcher`, installed as `mpirun` too on a test-local `PATH`;
  - [x] the `--remote-exe` wrapper that runs this tree's `hypershell.main` with `sys.executable`;
  - [x] a helper that runs `hs cluster` in its own session with a timeout and kills the group on
        expiry;
  - [x] a helper that reads the stand-in's records, plus the key-property assertions: the key the
        client received is non-empty and is in no recorded argv;
  - [x] a helper that reads task results through the data model in the temp site;
  - [x] a log-capture helper: the in-process construction path, with records at every level.
- [x] Add the tests, parametrised over TLS on / `--no-tls`, plus one `--zone` case each:
      `test_launcher_mode_keeps_key_off_command_lines`,
      `test_mpi_mode_keeps_key_off_command_lines`, and `test_launcher_mode_logs_never_hold_key`
      (R6). Run them on the unchanged code and record red.
- [x] Create `src/hypershell/cluster/launch.py` as PLAN §2.1 describes, with launcher-mode
      handoff only. Leave the `ssh_host` branch for P3 (raise `NotImplementedError` until then):
  - [x] `AUTH_ENV`, `ClientLaunch` (`env`/`stdin` are `repr=False`), `client_launch()`;
  - [x] the `OMPI_MCA_mca_base_env_list` append;
  - [x] `start()` merges `{**os.environ, **load_task_env(), **env}` and never mutates
        `os.environ`;
  - [x] `describe()` returns the redacted argv.
- [x] Move `redact_secrets` into `launch.py`. Re-export it from `cluster/remote.py`, keeping it in
      `__all__` and keeping its doctest.
- [x] `RemoteCluster.__init__`: replace the inline `client_args`/`client_argv` block with
      `self.launch = client_launch(…)`. Keep `client_argv` as a read-only alias of
      `self.launch.argv`. In `run_with_exceptions`, log `describe()` at DEBUG as today, then call
      `self.launch.start(stdout=sys.stdout, stderr=sys.stderr)`.
- [x] Unit tests in the same file: `client_launch()` argv carries every option it carried before
      (for the same inputs, minus `-k`); `os.environ` is unchanged after building and starting a
      launch; `repr(launch)` holds no key.
- [x] Ruling Q1 (PLAN §5): a launched client that reaches the server with no key of its own exits
      with an error naming the likely cause, not a bare authentication failure.
  - [x] `test_launcher_mode_without_environment_names_the_cause`: a stand-in launcher that drops
        the environment (keeping only `PATH`, `HOME` and the test's site/config isolation). The
        client must exit `runtime_error` with the cause in its CRITICAL line. Red first.
  - [x] `client.py`: `AUTH_ENV` and `LAUNCH_ENV_HINT`. `ClientApp.run` re-raises an
        `AuthenticationError` with the hint appended when the key came from neither `-k` nor
        `AUTH_ENV` (the key in use is `config.server.auth` and `AUTH_ENV` is unset). No option or
        help text changes. `launch.py` imports `AUTH_ENV` from `client.py`; the reverse import
        would cycle through `hypershell.cluster`.
- **Build notes (P1).**
  - The end-to-end runs bind `-H 127.0.0.1`, so clients never resolve the host's own name; a host
    whose name does not resolve (some sandboxes and laptops) would otherwise hang the run.
  - `client_options()` holds the shared option list; `client_launch()` adds the launcher words and
    the handoff. P3 reuses it for the ssh branch.
  - `ClientLaunch.start()` handles the environment only; P3 adds the `stdin` write.
  - `test_launcher_mode_logs_never_hold_key` passes before and after (R6 held already: the
    launchers redacted `-k`); it is the regression guard, not a red-first test.
  - A run whose client fails to authenticate still leaves `hs cluster` waiting for clients, as any
    failed client launch does today; the tests stop it when the client's exit is recorded.
    **Ruling (maintainer, 2026-11-05): accepted for this cycle**, since it applies to any failed
    client launch and the client now prints the ruling-Q1 diagnostic; a follow-up public seed covers it.
- **Verify:** `uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py -k 'launcher or mpi' && uv run pytest -p no:cacheprovider -q tests/test_cluster.py`.
- **Touches:** `src/hypershell/cluster/launch.py` (new), `src/hypershell/cluster/remote.py`,
  `src/hypershell/client.py` (ruling Q1 only), `tests/test_cluster_launch.py` (new).

## Phase P2 — Autoscaling (fixed and dynamic) launches through the same function
**Satisfies:** R1, R2, R3, R6, R7 · **Depends on:** P1
**Goal:** Every autoscaler scale-up, under both policies, starts its client through the
`ClientLaunch` that `client_launch()` built. The key never reaches a command line, and runs
complete.

- [x] Add `test_autoscaling_mode_keeps_key_off_command_lines` and
      `test_autoscaling_mode_logs_never_hold_key`, parametrised over policy `fixed`/`dynamic` and
      TLS on/off, plus one `--zone` case. Autoscaling clusters do not exit on their own: the test
      stops the process group once the data model shows no remaining tasks, then asserts results.
      Record red.
- [x] `AutoScalingCluster.__init__`: build one `ClientLaunch` with `client_launch(…)` (same option
      set as today: no `--no-confirm`, as now). Pass it to `AutoScalerThread`.
- [x] `AutoScaler`: hold `launch: ClientLaunch` instead of `launcher: List[str]`. `start()` logs
      `launch.describe()`. `scale()` calls `self.launch.start(stdout=sys.stdout, stderr=sys.stderr,
      bufsize=0, universal_newlines=True)`.
- [x] `tests/test_autoscale.py` `make_scaler`: build the scaler with a `client_launch(…)` value in
      place of `['hs', 'client']`. The policy tests are otherwise unchanged.
- **Build notes (P2).**
  - `AutoScalingCluster` keeps its launch as `self.launch` (as `RemoteCluster` does) and hands that
    same object to `AutoScalerThread`. Every scale-up calls `launch.start()`, so the next change to
    autoscaled client launches is a change to `client_launch()` only.
  - The autoscaler still advertises the host's own name, as before. The end-to-end runs bind
    `-H 127.0.0.1`, so the `autoscale-launcher` stand-in records the command line it was given and
    then points the client's `-H` at the loopback address; the recorded command line is what the
    key assertions read. P3 meets the same thing for `--ssh`.
  - `test_autoscaling_mode_logs_never_hold_key` is parametrised over policy, in-process with
    `--no-tls`, as P1's launcher-mode log test is. It passes before and after (the autoscaler
    redacted `-k` already); it is the regression guard, not a red-first test.
  - Two further tests: `test_autoscaling_mode_keeps_every_client_option` (the call site builds the
    same client options as before, still without `--no-confirm`, and the autoscaler holds the
    cluster's one launch) and `test_autoscaling_mode_starts_every_client_through_one_launch` (two
    scale-ups from one launch; each client gets the key in its environment only; `os.environ` is
    unchanged).
- **Verify:** `uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py -k autoscaling && uv run pytest -p no:cacheprovider -q tests/test_autoscale.py`.
- **Touches:** `src/hypershell/cluster/remote.py`, `tests/test_cluster_launch.py`,
  `tests/test_autoscale.py`.

## Phase P3 — SSH launches through the same function, key on the ssh channel's standard input
**Satisfies:** R1, R2, R3, R6, R7 · **Depends on:** P1
**Goal:** `SSHCluster` builds one `ClientLaunch` per host. The key travels only on ssh's
standard input and is read into the client's environment by the remote `sh` preamble. Runs
complete with TLS on and off, with `--zone`, and with `--env`.

- [x] Add the stand-in `ssh` to the harness. It records its argv and the remote command, saves its
      standard input to a `0600` file under `tmp_path`, and runs the remote command with `sh -c`,
      feeding that input back (PLAN §6).
- [x] Add `test_ssh_mode_keeps_key_off_command_lines`, parametrised over TLS on/off, plus a
      `--zone` case, an `--env` (`export_env`) case, and a template that contains a space and a
      single quote. Assert that the saved input carries the key, which appears in no argv and no
      remote command string. Add `test_ssh_mode_logs_never_hold_key`. Record red.
- [x] Implement the `ssh_host` branch of `client_launch()` as PLAN §2.1 describes:
  - [x] `-T` after the user's ssh args, then `ssh_exports`, then `sh -c <quoted preamble>`;
  - [x] `remote_exe` stays unquoted, and the options are `shlex.join`ed;
  - [x] the preamble exits 1 with a message if no key line arrives;
  - [x] `inherit_env=True`, and `stdin` is the key line;
  - [x] `start()` uses `stdin=PIPE`, writes, closes, and ignores `BrokenPipeError`.
- [x] `SSHCluster.__init__`: replace the per-host argv block with
      `self.launches = [client_launch(…, ssh_host=host, ssh_exports=launcher_env) for host in nodelist]`.
      Keep `client_argv` as `[l.argv for l in self.launches]`. `run_with_exceptions` logs
      `describe()` per host and calls `start(stdout=…, stderr=…)`.
- [x] Unit test: with `ssh_host` set, the argv ends in `['sh', '-c', <script>]`, where `-T` follows
      the user args, and the script holds no key.
- **Build notes (P3).**
  - **Ruling on P2's open point (maintainer, 2026-11-05).** The autoscaler and `--ssh` hand clients
    the host's own name, not the `-H` bind address. This cycle doesn't change that (the advertised
    host is unchanged: PLAN §3, invariants §11). The stand-in `ssh` uses P2's approach: it records its argv,
    the remote command and its standard input, then points the client's `-H` at the loopback
    address; the key assertions read what it recorded. A follow-up public seed covers "the
    advertised host ignores the bind address".
  - The remote step is `IFS= read -r HYPERSHELL_SERVER_AUTH`, a `case` test for an empty value,
    `export`, then `exec <remote_exe> client <options>`: shell syntax and builtins only, so the key
    is on no command line on the remote side either, and nothing echoes it. It sets only the
    variable every 2.8.x and 2.9 client already reads when no `-k` is given (R5 on 2.8).
  - Login shells: `test_ssh_launch_reads_key_under_any_login_shell` sends the remote command the
    way ssh does (words joined by spaces) to `<shell> -c`, with and without a key, for sh, bash,
    zsh, dash, ksh, csh, tcsh and fish; a shell that is not installed is skipped (fish, on the build
    host). Under fish, a backslash just before a single quote in the template is read as an escape
    inside the outer quotes; that is no different from today, where fish parses the whole remote
    command line.
  - Quoting: one `shlex` layer for every option; a template with a space, a double quote and a
    single quote reaches the client intact, end to end. `remote_exe` stays unquoted as before, so
    `~` and `$VAR` expand and a path with spaces still needs the user's own quoting.
  - Ruling Q1 in ssh mode: a launch whose key does not arrive never starts a client. The remote
    step prints `SSH_NO_KEY_MESSAGE` (it names `HYPERSHELL_SERVER_AUTH`, ssh's standard input and
    `ssh -n`) and exits 1 (`test_ssh_mode_without_key_names_the_cause`). The client's own
    diagnostic cannot fire in this mode, since the variable is always set when the client starts.
    `hs cluster` then waits, as after any failed client launch (P1 ruling).
  - Q2 finding: `-T` after the user's arguments overrides `-t`/`-tt`, but nothing overrides `-n`.
    With `-n` in `--ssh-args` or `config.ssh.args`, ssh reads `/dev/null`, so the key never arrives
    and the launch fails with the message above. PLAN §2.3's "`-n` ... no effect" does not hold;
    the P4 docs wording waits on the maintainer's ruling.
  - The launch now owns ssh's standard input (a pipe it closes after the key line), so ssh no
    longer shares `hs cluster`'s own standard input.
  - `describe()` shows an ssh launch's last word as ssh sends it (quoted once, for the remote
    shell), not quoted again for a local shell.
  - `test_ssh_mode_logs_never_hold_key` passes before and after (the ssh launcher redacted `-k`
    already); it is the regression guard, not a red-first test.
  - Further tests: `test_ssh_launch_runs_client_under_sh_with_no_terminal` (the TECH unit test,
    over the shared option sets) and `test_ssh_mode_launches_each_host_with_every_client_option`
    (one launch per host, one key, `client_argv` is the list of their argvs).
- **Verify:** `uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py -k ssh && uv run pytest -p no:cacheprovider -q tests/test_cluster.py`.
- **Touches:** `src/hypershell/cluster/launch.py`, `src/hypershell/cluster/ssh.py`,
  `tests/test_cluster_launch.py`.

## Phase P4 — Compatibility contract, cross-version check, and the docs revision
**Satisfies:** R4, R5, R8 · **Depends on:** P2, P3
**Goal:** The 2.9-line compatibility promise is pinned by tests, and the public docs and agent
guidance describe the hardened behaviour.

- [x] `test_launch_contract_uses_only_known_client_options`: freeze the set of `hs client` options
      2.9.0a4 accepts (from `git show 2.9.0a4:src/hypershell/client.py`, written into the test as a
      literal). Assert that every option `client_launch()` emits, across all modes and optional
      flags, is in it, and that `AUTH_ENV == 'HYPERSHELL_SERVER_AUTH'`.
- [x] `test_old_client_runs_under_new_launcher`:
  - [x] Skip with a reason unless tag `2.9.0a4` resolves and its `src/` imports under this
        interpreter.
  - [x] Otherwise `git archive` that tag's `src/` into `tmp_path` and run the `launcher`-mode and
        `ssh`-mode runs with a `--remote-exe` wrapper that imports that tree.
  - [x] Assert that the runs complete and that the key property holds.
- [x] `docs/security.rst` § *Authentication Key*: replace the admonition titled *"The key is visible
      on the client command line in cluster launches"* with the hardened description in PLAN §2.3
      (environment for launcher modes and the pass-through requirement, ssh standard input with no
      remote terminal, the key on no command line). Ruling Q1: also say what a client prints when
      the launcher drops the environment (the CRITICAL line names `HYPERSHELL_SERVER_AUTH` and the
      launcher's environment as the likely cause), and that `--mpi` asks Open MPI to forward the
      variable by name through `OMPI_MCA_mca_base_env_list`. For `--ssh`, say what the remote step
      prints when no key arrives, that `-t`/`-tt` no longer apply, and what `-n` does (P3 build
      notes: it stops the key arriving), worded to the maintainer's ruling on `-n`. Keep the `server.auth` plaintext sentence. Fix
      the § *Operational hardening* bullet, which says the key "is redacted from launch logs", if
      its wording now misleads.
- [x] `AGENTS.md`:
  - [x] § *Queue transport & security*: rewrite the "Auth-token exposure" bullet to the hardened
        behaviour.
  - [x] § *Cluster orchestration*: replace "There is no shared client-argv builder…" with the
        `cluster/launch.py` `client_launch()` rule.
  - [x] § *High-risk files & footguns*: amend the matching `remote.py`/`ssh.py` note.
- [x] `.agents/factory/invariants.md` §11: the same bullet change.
- [x] Confirm that `docs/_include/` and `share/` are unchanged (no option/help change).
- [x] Full suite with `-n auto`, then the CLI drive: `--launcher=env -H 127.0.0.1`, which runs a
      local client through the launcher path.
- [x] Ruling 1 below: an SSH client launch drops a bare `-n` from the ssh arguments, with one
      WARNING. Red first: `test_ssh_mode_ignores_no_input_option` (end to end, `--ssh-args='-n -o
      BatchMode=yes'`), `test_ssh_mode_drops_no_input_option_once` (from `--ssh-args` and from
      `ssh.args`, two hosts, one warning) and `test_ssh_launch_drops_no_input_option` (unit cases).
- **Build notes (P4).**
  - **Rulings on P3's open points (foreman, 2026-11-05).**
    1. *`-n` in the ssh arguments.* Ruling Q2 says the launch owns ssh's standard input, so a
       user's `-n` must not break it. For client launches, drop a bare `-n` from the ssh
       arguments, whether it comes from `--ssh-args` or `config.ssh.args`, and log one WARNING
       naming it. A P4 code item in `cluster/launch.py`'s ssh branch, red first. PLAN §2.3's
       statement is corrected with a dated note. The docs describe it with `-t`/`-tt`: the
       launch forces `-T`, owns standard input, and ignores `-n`.
    2. *Q1 in ssh mode.* Confirmed: the remote step's message (it names the variable and ssh's
       standard input) stands in for the client's Q1 hint in this mode. `-n` in the ssh
       arguments no longer triggers it, so the message now names the remaining causes: a
       wrapper that closes ssh's input, or `-n` inside combined flags such as `-qn`.
  - `ssh_launch_args()` in `launch.py` drops each bare `-n` word and keeps the value of every
    ssh option that takes one (so `-l -n` keeps its value). The ssh branch of `client_launch()`
    applies it to the launcher's words after the program name and to `launcher_args`.
    `SSHCluster` applies it once before building its per-host launches, so a run with many
    hosts logs the warning once. Combined flags (`-qn`) are left alone, as the ruling names a
    bare `-n`; the remote step's message covers them.
  - `test_ssh_mode_without_key_names_the_cause` now gets its no-input ssh from the stand-in's
    `STAND_IN_SSH_NO_INPUT=1` (a wrapper that closes ssh's input), since `-n` no longer causes
    it.
  - Contract: `CONTRACT_CLIENT_OPTIONS` freezes the 2.9.0a4 `hs client` options with whether
    each takes a value. `test_launch_contract_uses_only_known_client_options` walks the client
    words of launcher and ssh launches over the shared option sets. A tag-gated
    `test_launch_contract_matches_tagged_client` checks the literal against
    `git show 2.9.0a4:src/hypershell/client.py`. Both pass before and after; they pin the
    contract and are not red-first tests.
  - Cross-version: `test_old_client_runs_under_new_launcher[launcher|ssh]` skips unless tag
    `2.9.0a4` resolves and its `src/` imports under the running interpreter. It extracts that
    tree with `git archive` and runs the real `hs cluster` with a remote executable that imports
    it. It asserts the key property, that every client imported the tagged tree, and that every
    task completed. It runs (not skipped) on 3.12 and 3.14 here.
  - Docs: `docs/security.rst` replaces the admonition with *"How cluster launches hand the key to
    their clients"*. The § *Operational Recommendations* logging bullet now says the key never
    appears in launch logs. `AGENTS.md` changes the module map row, § *Queue transport &
    security* (now "Auth-token handoff"), § *Cluster orchestration* and § *High-risk files*.
    `invariants.md` §11 has the matching bullet. `docs/_include/` and `share/` are unchanged.
- **Verify:** `uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py && uv run pytest -p no:cacheprovider -q -n auto && .agents/factory/bin/temp_site.sh sh -c "seq 20 | uv run hsx -t 'echo {}' --launcher=env -N2 -H 127.0.0.1"`.
  **Ruling (maintainer, 2026-11-05):** the CLI drive binds `-H 127.0.0.1`, so it does not depend on
  the host's own name resolving.
- **Touches:** `tests/test_cluster_launch.py`, `docs/security.rst`, `AGENTS.md`,
  `.agents/factory/invariants.md`; by ruling 1, `src/hypershell/cluster/launch.py`,
  `src/hypershell/cluster/ssh.py` and the PLAN §2.3 correction.

---

## Out of this FSM: the 2.8.5 backport

PLAN §7 is the plan. It runs on the maintainer's `hotfix/2.8.5` branch (base tag `2.8.4`) after
this cycle lands on `develop`. `cluster/launch.py` is copied verbatim and the three call sites
are hand-ported without `zone`/`from_json`. `launch.py` imports `AUTH_ENV` from `client.py`, so
the P1 `client.py` change (`AUTH_ENV`, `LAUNCH_ENV_HINT` and the `ClientApp.run` re-raise) ports
with it. The same tests run, with the contract set frozen from
2.8.0 and the tag-gated cross-version runs against 2.8.0 and 2.8.4 clients, plus a maintainer
`build_archive.sh`/`drive.sh` check. Estimate: about half a day.

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` for detail), red-first.
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file if reality diverges (through `set_phase.py`; note the amendment in the commit
   body). STOP and escalate on a `GOAL.md` contradiction or any need to touch `core/queue.py`,
   `client.py` (beyond the ruling-Q1 diagnostic) or `server.py`.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
