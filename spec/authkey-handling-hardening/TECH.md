---
slug: authkey-handling-hardening
title: "The queue authentication key is never exposed to other users on client nodes"
kind: fix
appetite: small
status: in_progress
branch: fix/authkey-handling-hardening
base: develop
current_phase: P1
last_updated: "2026-11-05"
phases:
  - id: P1
    name: "One client-launch function; --launcher and --mpi hand the key over in the environment"
    status: pending
    satisfies: [R1, R2, R3, R6, R7]
    depends_on: []
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py -k 'launcher or mpi' && uv run pytest -p no:cacheprovider -q tests/test_cluster.py"
  - id: P2
    name: "Autoscaling (fixed and dynamic) launches through the same function"
    status: pending
    satisfies: [R1, R2, R3, R6, R7]
    depends_on: [P1]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py -k autoscaling && uv run pytest -p no:cacheprovider -q tests/test_autoscale.py"
  - id: P3
    name: "SSH launches through the same function, key on the ssh channel's standard input"
    status: pending
    satisfies: [R1, R2, R3, R6, R7]
    depends_on: [P1]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py -k ssh && uv run pytest -p no:cacheprovider -q tests/test_cluster.py"
  - id: P4
    name: "Compatibility contract, cross-version check, and the docs revision"
    status: pending
    satisfies: [R4, R5, R8]
    depends_on: [P2, P3]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py && uv run pytest -p no:cacheprovider -q -n auto && .agents/factory/bin/temp_site.sh sh -c \"seq 20 | uv run hsx -t 'echo {}' --launcher=env -N2\""
review:
  last_reviewed_commit: ""
  verdict: none
  blocked_reason: ""
  cycle: 0
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
  back to sign-off (GOAL non-goals).
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

- [ ] Create `tests/test_cluster_launch.py` (marked `integration`) with the shared harness from
      PLAN §6:
  - [ ] the stand-in `launcher`, installed as `mpirun` too on a test-local `PATH`;
  - [ ] the `--remote-exe` wrapper that runs this tree's `hypershell.main` with `sys.executable`;
  - [ ] a helper that runs `hs cluster` in its own session with a timeout and kills the group on
        expiry;
  - [ ] a helper that reads the stand-in's records, plus the key-property assertions: the key the
        client received is non-empty and is in no recorded argv;
  - [ ] a helper that reads task results through the data model in the temp site;
  - [ ] a log-capture helper: the in-process construction path, with records at every level.
- [ ] Add the tests, parametrised over TLS on / `--no-tls`, plus one `--zone` case each:
      `test_launcher_mode_keeps_key_off_command_lines`,
      `test_mpi_mode_keeps_key_off_command_lines`, and `test_launcher_mode_logs_never_hold_key`
      (R6). Run them on the unchanged code and record red.
- [ ] Create `src/hypershell/cluster/launch.py` as PLAN §2.1 describes, with launcher-mode
      handoff only. Leave the `ssh_host` branch for P3 (raise `NotImplementedError` until then):
  - [ ] `AUTH_ENV`, `ClientLaunch` (`env`/`stdin` are `repr=False`), `client_launch()`;
  - [ ] the `OMPI_MCA_mca_base_env_list` append;
  - [ ] `start()` merges `{**os.environ, **load_task_env(), **env}` and never mutates
        `os.environ`;
  - [ ] `describe()` returns the redacted argv.
- [ ] Move `redact_secrets` into `launch.py`. Re-export it from `cluster/remote.py`, keeping it in
      `__all__` and keeping its doctest.
- [ ] `RemoteCluster.__init__`: replace the inline `client_args`/`client_argv` block with
      `self.launch = client_launch(…)`. Keep `client_argv` as a read-only alias of
      `self.launch.argv`. In `run_with_exceptions`, log `describe()` at DEBUG as today, then call
      `self.launch.start(stdout=sys.stdout, stderr=sys.stderr)`.
- [ ] Unit tests in the same file: `client_launch()` argv carries every option it carried before
      (for the same inputs, minus `-k`); `os.environ` is unchanged after building and starting a
      launch; `repr(launch)` holds no key.
- **Verify:** `uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py -k 'launcher or mpi' && uv run pytest -p no:cacheprovider -q tests/test_cluster.py`.
- **Touches:** `src/hypershell/cluster/launch.py` (new), `src/hypershell/cluster/remote.py`,
  `tests/test_cluster_launch.py` (new).

## Phase P2 — Autoscaling (fixed and dynamic) launches through the same function
**Satisfies:** R1, R2, R3, R6, R7 · **Depends on:** P1
**Goal:** Every autoscaler scale-up, under both policies, starts its client through the
`ClientLaunch` that `client_launch()` built. The key never reaches a command line, and runs
complete.

- [ ] Add `test_autoscaling_mode_keeps_key_off_command_lines` and
      `test_autoscaling_mode_logs_never_hold_key`, parametrised over policy `fixed`/`dynamic` and
      TLS on/off, plus one `--zone` case. Autoscaling clusters do not exit on their own: the test
      stops the process group once the data model shows no remaining tasks, then asserts results.
      Record red.
- [ ] `AutoScalingCluster.__init__`: build one `ClientLaunch` with `client_launch(…)` (same option
      set as today: no `--no-confirm`, as now). Pass it to `AutoScalerThread`.
- [ ] `AutoScaler`: hold `launch: ClientLaunch` instead of `launcher: List[str]`. `start()` logs
      `launch.describe()`. `scale()` calls `self.launch.start(stdout=sys.stdout, stderr=sys.stderr,
      bufsize=0, universal_newlines=True)`.
- [ ] `tests/test_autoscale.py` `make_scaler`: build the scaler with a `client_launch(…)` value in
      place of `['hs', 'client']`. The policy tests are otherwise unchanged.
- **Verify:** `uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py -k autoscaling && uv run pytest -p no:cacheprovider -q tests/test_autoscale.py`.
- **Touches:** `src/hypershell/cluster/remote.py`, `tests/test_cluster_launch.py`,
  `tests/test_autoscale.py`.

## Phase P3 — SSH launches through the same function, key on the ssh channel's standard input
**Satisfies:** R1, R2, R3, R6, R7 · **Depends on:** P1
**Goal:** `SSHCluster` builds one `ClientLaunch` per host. The key travels only on ssh's
standard input and is read into the client's environment by the remote `sh` preamble. Runs
complete with TLS on and off, with `--zone`, and with `--env`.

- [ ] Add the stand-in `ssh` to the harness. It records its argv and the remote command, saves its
      standard input to a `0600` file under `tmp_path`, and runs the remote command with `sh -c`,
      feeding that input back (PLAN §6).
- [ ] Add `test_ssh_mode_keeps_key_off_command_lines`, parametrised over TLS on/off, plus a
      `--zone` case, an `--env` (`export_env`) case, and a template that contains a space and a
      single quote. Assert that the saved input carries the key, which appears in no argv and no
      remote command string. Add `test_ssh_mode_logs_never_hold_key`. Record red.
- [ ] Implement the `ssh_host` branch of `client_launch()` as PLAN §2.1 describes:
  - [ ] `-T` after the user's ssh args, then `ssh_exports`, then `sh -c <quoted preamble>`;
  - [ ] `remote_exe` stays unquoted, and the options are `shlex.join`ed;
  - [ ] the preamble exits 1 with a message if no key line arrives;
  - [ ] `inherit_env=True`, and `stdin` is the key line;
  - [ ] `start()` uses `stdin=PIPE`, writes, closes, and ignores `BrokenPipeError`.
- [ ] `SSHCluster.__init__`: replace the per-host argv block with
      `self.launches = [client_launch(…, ssh_host=host, ssh_exports=launcher_env) for host in nodelist]`.
      Keep `client_argv` as `[l.argv for l in self.launches]`. `run_with_exceptions` logs
      `describe()` per host and calls `start(stdout=…, stderr=…)`.
- [ ] Unit test: with `ssh_host` set, the argv ends in `['sh', '-c', <script>]`, where `-T` follows
      the user args, and the script holds no key.
- **Verify:** `uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py -k ssh && uv run pytest -p no:cacheprovider -q tests/test_cluster.py`.
- **Touches:** `src/hypershell/cluster/launch.py`, `src/hypershell/cluster/ssh.py`,
  `tests/test_cluster_launch.py`.

## Phase P4 — Compatibility contract, cross-version check, and the docs revision
**Satisfies:** R4, R5, R8 · **Depends on:** P2, P3
**Goal:** The 2.9-line compatibility promise is pinned by tests, and the public docs and agent
guidance describe the hardened behaviour.

- [ ] `test_launch_contract_uses_only_known_client_options`: freeze the set of `hs client` options
      2.9.0a4 accepts (from `git show 2.9.0a4:src/hypershell/client.py`, written into the test as a
      literal). Assert that every option `client_launch()` emits, across all modes and optional
      flags, is in it, and that `AUTH_ENV == 'HYPERSHELL_SERVER_AUTH'`.
- [ ] `test_old_client_runs_under_new_launcher`:
  - [ ] Skip with a reason unless tag `2.9.0a4` resolves and its `src/` imports under this
        interpreter.
  - [ ] Otherwise `git archive` that tag's `src/` into `tmp_path` and run the `launcher`-mode and
        `ssh`-mode runs with a `--remote-exe` wrapper that imports that tree.
  - [ ] Assert that the runs complete and that the key property holds.
- [ ] `docs/security.rst` § *Authentication Key*: replace the admonition titled *"The key is visible
      on the client command line in cluster launches"* with the hardened description in PLAN §2.3
      (environment for launcher modes and the pass-through requirement, ssh standard input with no
      remote terminal, the key on no command line). Keep the `server.auth` plaintext sentence. Fix
      the § *Operational hardening* bullet, which says the key "is redacted from launch logs", if
      its wording now misleads.
- [ ] `AGENTS.md`:
  - [ ] § *Queue transport & security*: rewrite the "Auth-token exposure" bullet to the hardened
        behaviour.
  - [ ] § *Cluster orchestration*: replace "There is no shared client-argv builder…" with the
        `cluster/launch.py` `client_launch()` rule.
  - [ ] § *High-risk files & footguns*: amend the matching `remote.py`/`ssh.py` note.
- [ ] `.agents/factory/invariants.md` §11: the same bullet change.
- [ ] Confirm that `docs/_include/` and `share/` are unchanged (no option/help change).
- [ ] Full suite with `-n auto`, then the CLI drive: `--launcher=env`, which runs a local client
      through the launcher path.
- **Verify:** `uv run pytest -p no:cacheprovider -rs tests/test_cluster_launch.py && uv run pytest -p no:cacheprovider -q -n auto && .agents/factory/bin/temp_site.sh sh -c "seq 20 | uv run hsx -t 'echo {}' --launcher=env -N2"`.
- **Touches:** `tests/test_cluster_launch.py`, `docs/security.rst`, `AGENTS.md`,
  `.agents/factory/invariants.md`.

---

## Out of this FSM: the 2.8.5 backport

PLAN §7 is the plan. It runs on the maintainer's `hotfix/2.8.5` branch (base tag `2.8.4`) after
this cycle lands on `develop`. `cluster/launch.py` is copied verbatim and the three call sites
are hand-ported without `zone`/`from_json`. The same tests run, with the contract set frozen from
2.8.0 and the tag-gated cross-version runs against 2.8.0 and 2.8.4 clients, plus a maintainer
`build_archive.sh`/`drive.sh` check. Estimate: about half a day.

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` for detail), red-first.
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file if reality diverges (through `set_phase.py`; note the amendment in the commit
   body). STOP and escalate on a `GOAL.md` contradiction or any need to touch `core/queue.py`,
   `client.py` or `server.py`.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
