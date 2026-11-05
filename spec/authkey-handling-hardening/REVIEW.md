# REVIEW — The queue authentication key is never exposed to other users on client nodes

> Adversarial QA by `hs-review`. **Cycle 1 deviation (foreman's instruction):** the blind
> correctness pass ran inline in the orchestrator, not in a spawned subagent. Its inputs were
> [`GOAL.md`](GOAL.md), `git diff origin/develop...20d2509 -- . ':(exclude)spec/'`, the AGENTS.md
> invariants and the runnable repo. `PLAN.md`/`TECH.md` were read only after the findings below
> were formed, to check the code against the dated rulings. Delegated blindness is lost, so every
> finding rests on an executed command. Security-lane rule applies: findings are stated as
> hardening defects in terms of the fixed property; one is recorded only by kind (detail in the
> hidden lane).

- **Reviewed commit:** 20d2509  ·  **Base:** develop (`02b292a`, 2.9.0a4)  ·  **Date:** 2026-11-05
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 (escalate to human on non-convergence)
- **Contract drift:** one post-shape `GOAL.md` commit, `865031c` (the maintainer's R4/R5
  split-by-line sign-off). Legitimate; graded against it.

## Verification run

All on macOS (Linux `/proc` semantics reasoned explicitly below); throwaway sites and scratch
copies only; every spawned process run under a process-group kill timer and confirmed gone.

- `uv run --frozen pytest -rs tests/test_cluster_launch.py tests/test_autoscale.py` → **75 passed,
  1 skipped** (fish not installed), 86s.
- `uv run --frozen pytest -rs -x --ignore=tests/test_cluster_launch.py` (rest of the suite) →
  **893 passed, 84 skipped** (Postgres/opt-in), 621s.
- `uv run --frozen sphinx-build -E docs <scratch>` → exit 0; only the two pre-existing "not in any
  toctree" warnings.
- **R4 by hand** (own script; not `bench_queue.py compat`): `git archive 2.9.0a4 src` into scratch;
  `hs server FILE -k K --no-db` and `hs client -k K -T 5` across {2.9.0a4 server ↔ branch client,
  branch server ↔ 2.9.0a4 client, branch ↔ branch} × {TLS, `--no-tls`} → all six: client exit 0,
  server exit 0, 5/5 task results, key in neither log.
- **R5 by hand:** branch `hs cluster --launcher=<pass-through recorder>` and `--ssh` (stand-in
  `ssh` running the remote words under `bash -c`) with `--remote-exe` → a 2.9.0a4 tree, TLS and
  `--no-tls` → all four exit 0 with 3/3 results.
- **SSH remote step, hostile quoting:** `client_launch(..., ssh_host=...)`, remote words joined as
  ssh does and run under `sh`, `bash`, `zsh`, `dash`, `ksh`, `csh`, `tcsh` with the key on stdin;
  8 templates (`$(…)`/backticks, mixed quotes, `;&&|<>*?[]~#`, backslashes, `!! !$ %1 ^x`,
  `$HOME ${X:-y} $$`, unicode/tab, newline) → 52/56 deliver the exact template and the key; no
  injected command ran. The 4 misses are `csh`/`tcsh` with `!` (history) or a newline. The
  previous form also handed the template to the login shell inside single quotes, where csh still
  applies history substitution and rejects a newline, so these read as pre-existing (by reading;
  the old-form re-run was refused by a command safety check and not retried).
- **ssh stdin:** 300 × `ClientLaunch.start()` against a child that closes stdin and exits at once
  → 0 exceptions (`BrokenPipeError` path); a child that sleeps 2s before `read` → `start()`
  returns in 0.00s (129-byte key line is below `PIPE_BUF`) and the child reads 128 chars.
- **Q1 diagnostic** (branch `hs server -k J`, clients in an isolated home): no key → hint, exit 5;
  wrong `-k` → no hint, exit 5; wrong `HYPERSHELL_SERVER_AUTH` → no hint, exit 5; right `-k` →
  exit 0. Two false positives, see F2.
- **Single-function goal:** `grep` over `src/` → the only client argv builder is
  `cluster/launch.py:167` (ssh: the script at `:221`); no `-k` emitted anywhere; `Popen` sites are
  `launch.py:82-83` and the client's task runner only.
- **Test discrimination (mutation, scratch `git archive 20d2509`, PYTHONPATH-swapped `src`):**
  see the table below.

### Mutation results

| Mutation | Caught by |
|----------|-----------|
| pre-fix hybrid (develop's `remote.py`/`ssh.py`, branch `launch.py`/`client.py`) | `test_launcher_mode_keeps_key_off_command_lines` (first failure) |
| `-k <key>` appended in the launcher branch | `test_launcher_mode_keeps_key_off_command_lines` |
| `-k <key>` only on the `--mpi` launch | `test_mpi_mode_keeps_key_off_command_lines` |
| `-k <key>` only on the autoscaler's launch | `test_autoscaling_mode_keeps_key_off_command_lines` |
| key in the remote `sh` script | `test_ssh_mode_keeps_key_off_command_lines` |
| key in ssh's own environment | `test_ssh_mode_keeps_key_off_command_lines` |
| key as an export word after the host | `test_ssh_mode_keeps_key_off_command_lines` |
| no forced `-T` | `test_ssh_mode_keeps_key_off_command_lines` |
| bare `-n` kept | `test_ssh_mode_ignores_no_input_option` |
| value of a value-taking ssh option not kept (`-l -n`) | `test_ssh_launch_drops_no_input_option[option-value]` |
| `describe()` appends `env` | `test_launcher_mode_keeps_key_off_command_lines` (DEBUG launch line) |
| `describe()` appends `stdin` | `test_ssh_mode_keeps_key_off_command_lines` |
| `env` shown in `repr()` | `test_client_launch_never_shows_key` |
| `start()` writes `env` into `os.environ` | `test_client_launch_leaves_caller_environment_unchanged` |
| no Open MPI parameter | `test_mpi_mode_keeps_key_off_command_lines` |
| no empty-key check in the remote step | `test_ssh_mode_without_key_names_the_cause` |
| Q1 hint never fires | `test_launcher_mode_without_environment_names_the_cause`, once the harness copy's env-dropping stand-in keeps `PYTHONPATH` (it drops it, so a swapped `client.py` is invisible to the plain harness run; harness artifact, not a test gap) |
| Q1 hint always fires (also for a wrong `-k`) | **survives** (F4) |
| `BrokenPipeError` not caught in `start()` | **survives** (F4) |
| `--zone` not emitted | `test_launcher_mode_keeps_key_off_command_lines[tls-zone]` |

Pre-fix hybrid, run without `-x` over the key-property tests: all 17 per-mode
`*_keeps_key_off_command_lines` cases (launcher, mpi, autoscaling fixed/dynamic, ssh; TLS,
`--no-tls`, `--zone`, `--env`, quoted template) and both no-key diagnostics **fail**; the 4
`*_logs_never_hold_key` cases pass, as expected (R6 regression guards, redaction held before).
The tagless scratch copy also shows the tag-gated tests skipping (4 skipped vs 1 in the worktree).

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `cluster/launch.py` `client_launch()`/`ClientLaunch` (env for launcher modes; ssh stdin + remote `read`); call sites `remote.py`, `ssh.py` | e2e per mode with recorded argv/env/stdin (tests); end-to-end drive (H1) | ❌ one residual (H1) |
| R2 | all four process modes build through `client_launch()` | grep; per-mode e2e and mutation rows | ✅ |
| R3 | unchanged client options via `client_options()`; old/new parity tests | e2e per mode × TLS/`--no-tls` (+`--zone`); full suite | ✅ (Open MPI ≤4 `-x` case: F1, plausible) |
| R4 | `core/queue.py`, `server.py` untouched | by-hand cross-version run, both directions, TLS on/off | ✅ |
| R5 | key via the variable every 2.9.0a4+ client reads; contract test | tag-gated test + by-hand run (launcher, ssh) | ✅ (tag tests skip in CI: F4) |
| R6 | `describe()` = redacted argv; `env`/`stdin` `repr=False` | log-capture tests at level 1; mutation rows | ❌ one residual (H1) |
| R7 | `tests/test_cluster_launch.py` | mutation table; pre-fix hybrid | ✅ for R1 per mode (gaps: F4) |
| R8 | `docs/security.rst`, `AGENTS.md`, invariants §11; no CLI/help change | docs build; read against code | ⚠️ one inaccuracy (F3) |

Unmapped changes: the Q1 diagnostic in `client.py` (maps to maintainer ruling Q1, not an R-ID;
in scope by that ruling).

## Linux key-reach reasoning (what held)

- Launcher modes: the key is only in the launched process's environment
  (`/proc/<pid>/environ`, mode 0400 plus the ptrace access check) — never in `cmdline`, which any
  user can read. `hs cluster`'s own `os.environ` is never modified (test + mutation). srun steps
  and MPICH/Intel MPI/Open MPI forward the environment over their control channels, not argv; the
  Open MPI parameter carries the variable's name only.
- `--ssh`: the key is only in the pipe to ssh, the ssh channel, and the remote `sh`'s variable
  before `exec`. Local `ssh` argv, remote login-shell argv and remote client argv hold no key.
  Forced `-T` means no remote tty can echo it.
- No temp file holds the key (site/home grep after runs); `describe()`/`repr()` hold none;
  `write_traceback` uses `format_exc()` (no locals); the Q1 hint text holds none.

## Findings

### [H1 · CRITICAL · CONFIRMED] Residual key reach in one SSH launch option combination (R1, R6)
- **Severity note:** impact is MEDIUM (an edge path that needs a specific option combination), but
  it contradicts the invariant this diff adds to `invariants.md` §11 ("the key is never in a
  launched argv"), which the rubric makes auto-CRITICAL.
- **Where:** the SSH launch path (`cluster/ssh.py`, `cluster/launch.py`).
- **Summary:** in one combination of `--ssh` options, a run's key still reaches a command line and
  a DEBUG log line. Detail, reproduction and a small fix are in the hidden security lane, as the
  `AGENTS.md` security-lane rule requires; the maintainer has the reference.
- **Evidence:** executed end to end with stand-in launchers (scratch only), and by a direct
  `SSHCluster(...)` construction.
- **Gate:** touches `cluster/ssh.py` and a security invariant → human sign-off.

### [F1 · MEDIUM · PLAUSIBLE] With Open MPI ≤4.1, `mpirun -x` aborts when `OMPI_MCA_mca_base_env_list` is set (R3)
- **Where:** `src/hypershell/cluster/launch.py:168` sets `OMPI_MCA_mca_base_env_list` for every
  launcher-mode launch.
- **Failure scenario:** (a) `hs cluster --launcher='mpirun -x LD_LIBRARY_PATH …'` on Open MPI
  4.x: `mpirun` sees `-x` on its command line and the parameter in its environment and aborts
  ("You have attempted to pass environment variables to Open MPI with both the "-x" method and by
  setting the MCA parameter "mca_base_env_list"") — no client starts, where it ran before.
  (b) Under any launcher mode, clients pass the variable on to tasks, so a task that runs Open MPI
  4.x `mpirun -x …` aborts the same way.
- **Evidence:** Open MPI v4.1.x source `orte/mca/schizo/ompi/schizo_ompi.c:733-738` and
  `help-orterun.txt:665-672` (fetched and read). Task inheritance executed: a launcher-mode run with
  task `printenv OMPI_MCA_mca_base_env_list` printed `HYPERSHELL_SERVER_AUTH`.
  `RemoteCluster(launcher='srun …')` and `AutoScalingCluster(launcher='')` both carry the variable
  in `launch.env`. Open MPI 5.0.11's `libprrte` has no such check (string search of the PyPI
  wheel), so this looks 4.x-only. **Not reproduced:** running the downloaded `mpirun` locally was
  denied by a safety check; the foreman's planned real-cluster `--mpi` check should include a
  `-x` case on Open MPI 4.
- **Touches:** R3 ("as it does today").

### [F2 · LOW · CONFIRMED] Q1 hint fires for an explicit `-k` whose value equals `config.server.auth`
- **Where:** `src/hypershell/client.py:1658` decides "no key of its own" by value
  (`self.auth == config.server.auth`), not by where the key came from.
- **Failure scenario:** a user config with `server.auth = K` and `hs client -k K` against a server
  with another key (or `-k '<not-secure>'` with no config) → the CRITICAL line says "No key reached
  this client: … and no -k was given". TECH's own condition is "the key came from neither `-k` nor
  `HYPERSHELL_SERVER_AUTH`". Also, a config-file key with no `-k` gets the "No key reached this
  client" wording although a key did arrive (matches the ruling's intent; wording only).
- **Evidence:** scripted server/client probe, cases `-k` = config key and `-k '<not-secure>'` →
  hint present, exit 5; wrong `-k` not equal to config → no hint, exit 5. Exit codes unchanged in
  every case.
- **Gate:** `client.py` is high-blast-radius → human sign-off (low severity).

### [F3 · LOW · CONFIRMED] `docs/security.rst` scopes the Open MPI parameter to `--mpi`
- **Where:** `docs/security.rst:183-185` ("With ``--mpi``, the launch also asks Open MPI's
  ``mpirun`` to forward…").
- **Failure scenario:** the parameter is set for `--launcher`, `--mpi` and `--autoscaling` alike
  (`launch.py:168`), and it reaches clients and their tasks; the docs imply only `--mpi`, and do not
  say tasks see it (relevant to F1(b)).
- **Evidence:** the `RemoteCluster`/`AutoScalingCluster` construction and the task `printenv` run
  above.
- **Touches:** R8 (accuracy).

### [F4 · LOW] Test gaps — (a) CONFIRMED, (b) PLAUSIBLE
- (a) No test asserts the Q1 hint stays silent for a wrong `-k` or a wrong `HYPERSHELL_SERVER_AUTH`
  (mutation "hint always" survives), and none exercises `ClientLaunch.start()`'s
  `BrokenPipeError` path (mutation survives). See the mutation table.
- (b) The tag-gated tests (`test_launch_contract_matches_tagged_client`,
  `test_old_client_runs_under_new_launcher`) skip without tag `2.9.0a4`; `.github/workflows/tests.yml:27`
  checks out with the default shallow, tagless clone, so R5's cross-version check would not run in
  CI. Shown locally in a tagless scratch copy (both skip); CI itself not run.

## Human-gate triggers

- **H1** (CONFIRMED): `cluster/ssh.py`, security invariant §9/§11 → human sign-off required.
- **F2** (CONFIRMED, LOW): `client.py` → human sign-off required.
- F4(a) (CONFIRMED, LOW): tests only; no gate of its own.
- F1 and F4(b) are PLAUSIBLE: for triage, not auto-loop. F1 should ride on the foreman's real Open MPI check.

## Rulings check (after the findings)

- Q1 (accept and fail clearly; smallest change in `client.py`): implemented; the value-based test
  is the F2 edge. Q1 in ssh mode (remote message stands in): holds (`test_ssh_mode_without_key_names_the_cause`).
- Q2 / P4 ruling 1 (force `-T`; drop a bare `-n` with one WARNING; leave `-qn`): holds, values of
  value-taking options kept (`-l -n`, `-o -n`).
- Q3 (tasks keep the variable; follow-up seed): code matches. F1(b) bears on that ruling; the
  maintainer may want to revisit it.
- P1 ruling (a failed launch leaves `hs cluster` waiting): unchanged behaviour, as ruled.
- PLAN §2.1 `read … || exit` became an empty-value `case` test: stricter, fine.
- Pre-existing, out of scope, already recorded in PLAN §5: config-file `server.auth_eval`/`auth_env`
  crash at import ("more than one variant") — reproduced, unchanged by this diff.
- Pre-existing, out of scope: `ClusterApp.run_ssh` always passes `launcher_args=shlex.split(--ssh-args)`
  (a list, never `None`), so `ssh.args` from configuration reaches only library callers of
  `SSHCluster`; the new docs sentence about `-n`/`-t` "in `--ssh-args` or `ssh.args`" is accurate for
  both paths that exist.

## Triage (human gate) — cycle 1

Cycle 1 (`826449a`, reviewing `20d2509`) requested changes, with one CRITICAL (H1) and a
`client.py` finding (F2), which trigger the human gate. The maintainer and the foreman ruled on
2026-11-05; the rulings are recorded in PLAN §5 and TECH § *Review-1 remediation*.

| Finding | Ruling | Remediation |
|---|---|---|
| H1 · CRITICAL · CONFIRMED | fix | No `--ssh --env` launch puts the key, or any value the launch hands over off the command line, on a command line or in a log line. The ssh branch of `client_launch()` drops any export that assigns `HYPERSHELL_SERVER_AUTH`; `describe()` shows such a word as `HYPERSHELL_SERVER_AUTH=***`. Tests: `test_ssh_env_export_never_carries_key`, `test_ssh_launch_exports_never_carry_key`, `test_client_launch_description_redacts_key_exports`. |
| F1 · MEDIUM · PLAUSIBLE | fix | The Open MPI parameter is set only for `--mpi` or an `mpirun`/`mpiexec` launcher; beside a user's own `-x`, the launch appends `-x HYPERSHELL_SERVER_AUTH` instead. The client takes the launch's entry out of the parameter in its tasks' environment (other names kept; dropped if empty). Tests: `test_client_launch_sets_no_open_mpi_parameter_for_other_launchers`, `test_client_launch_forwards_key_by_name_beside_mpirun_x`, `test_launched_tasks_never_hold_key`, `test_task_environment_never_holds_key`. The real Open MPI 4 `-x` check stays with the foreman. |
| F2 · LOW · CONFIRMED | fix | The hint keys on whether `-k` was given. Test: `test_client_names_missing_key_only_when_none_given[explicit-default-key]`. |
| F3 · LOW · CONFIRMED | fix the docs | `docs/security.rst` names the launchers that set the parameter, the `-x` case, and that tasks see neither it nor the key. |
| F4 · LOW | fix | (a) `test_client_names_missing_key_only_when_none_given` (wrong `-k`, wrong environment key) and `test_client_launch_start_survives_closed_input` pin the two surviving mutants. (b) CI fetches tag `2.9.0a4`. |
| Q3 ruling | reversed (maintainer) | Strip it now: the client removes `HYPERSHELL_SERVER_AUTH` from every task's environment; a task that needs it is given it explicitly through `[export]`. Documented in `docs/security.rst`. |

Next: a scoped cycle 2 of the remediation diff only.

## Review cycle 2 — changes-requested (2026-11-05)

> **Mode: scoped** to the cycle-1 remediation (foreman's instruction):
> `git diff 826449a..a18f672 -- . ':(exclude)spec/'` (`bdc87b7` code, tests, docs, CI; `a18f672`
> gates and FSM), graded against [`GOAL.md`](GOAL.md), the AGENTS.md invariants and the runnable
> repo. The blind pass ran inline in the orchestrator (no subagent, foreman's instruction).
> TECH § *Review-1 remediation*, PLAN §5 and the cycle-1 section were read only after the findings
> below were formed. Hidden-lane items are named by kind only.

- **Reviewed commit:** a18f672  ·  **Base:** develop (`02b292a`, 2.9.0a4)  ·  **Date:** 2026-11-05
- **Verdict:** changes-requested
- **Cycle:** 2 of ≤3 (one more build↔review round before escalation)
- **Contract drift:** none since cycle 1 (`git log 826449a..a18f672 -- spec/authkey-handling-hardening/GOAL.md` is empty).

### Verification run (cycle 2)

Scratch copies only (`git archive a18f672`, `git archive origin/develop`), Python 3.14.6 venvs from
`uv sync --frozen`, every run under a process-group kill timer; no process left behind.

- The remediation's 26 new test cases pass. With tag `2.9.0a4` fetched into the scratch copy,
  `tests/test_cluster_launch.py tests/test_autoscale.py tests/test_client.py tests/test_cluster.py`
  → 161 passed, 1 skipped (fish not installed), so the contract and cross-version tests run and pass.
- Full suite, branch: `-n auto` (10 workers) → 974 passed, 91 skipped; `-n 16` → the same.
  Develop: `-n auto` → 890 passed, 87 skipped; `-n 16` → 1 failed
  (`test_queue_start.py::test_busy_port_raises_from_start[no-tls]`, a develop-side timing flake).
  The 4 extra branch skips are the tag-gated tests in a tagless archive.
- Sphinx (`-W --keep-going`): only the two pre-existing "not in any toctree" warnings, as on develop.
- **Task environments, every mode.** Scratch tests in which each task writes its environment to a
  `0600` file: `--ssh`, `--ssh --env`, `--autoscaling` fixed and dynamic, and a hand-started
  `hs client` with the key in its environment → no task saw `HYPERSHELL_SERVER_AUTH` or the key, and a
  user's `OMPI_MCA_mca_base_env_list=USER_A` reached tasks unchanged (launcher and `--mpi` are the
  branch's own `test_launched_tasks_never_hold_key`). The client's own `os.environ` is untouched
  (unit test; mutant M4 below). `hs run`/`hs submit` carry only arguments and tags into a task
  (`TASK_*`/`TASK_TAG_*` names only); `load_task_env()` is `config.export` only. One other
  client-side path is N2.
- **`[export]`.** It is safe as the explicit route: values are literal (no `_env`/`_eval`
  expansion), so it can only hand over a key the operator already holds. Probe: `[export]
  hypershell_server_auth_env = "MY_KEY_VAR"` → the task gets `HYPERSHELL_SERVER_AUTH_ENV=MY_KEY_VAR`;
  `HYPERSHELL_EXPORT_HYPERSHELL_SERVER_AUTH=…` → the task gets `HYPERSHELL_SERVER_AUTH`. See N5.
- **F2 against a real `hs server -k K`:** config-file key right → exit 0; config-file key wrong →
  `AuthenticationError` with the hint (the wording the cycle-1 ruling accepted); environment right
  over config wrong → 0; `-k` right over environment wrong → 0; `-k` wrong over environment right →
  `AuthenticationError`, no hint. Precedence (config file < `HYPERSHELL_SERVER_AUTH` < `-k`) is
  unchanged.
- **F1, the builder (`client_launch()`):** `/opt/x/bin/mpirun` → parameter set; `mpiexec -x FOO` →
  `-x HYPERSHELL_SERVER_AUTH` appended, no parameter; `srun mpirun` and `mpiexec.hydra` → neither;
  `mpirun.openmpi`, `mpiexec.openmpi`, `orterun`, `prterun` → neither (N3); `--x FOO`, `-xFOO`,
  `-x=FOO` → not read as `-x`, so the parameter is set (Open MPI's acceptance of these forms not
  checked here). The `-x` branch is added only when the user's words already hold `-x`; MPICH's
  `mpiexec` has no `-x`, so that command was already rejected before the launch touched it, and
  without `-x` MPICH ignores the Open MPI parameter and passes the environment as before.
- **CI tag fetch:** the exact step run in a `--depth=1 --no-tags` clone of a local bare origin.
  See N4.

#### Mutation results (cycle 2)

Scratch copy, one mutant at a time, run over the remediation's tests:

| Mutant | Caught by |
|--------|-----------|
| M1 no strip of the key variable | `test_launched_tasks_never_hold_key[launcher,mpi]`, `test_task_environment_never_holds_key` (4) |
| M2 strip the variable, leave the Open MPI list | `…never_hold_key[mpi]`, `…never_holds_key[launch-only,user-names-kept]` |
| M3 drop the whole Open MPI list | `…never_hold_key[launcher,mpi]`, `…never_holds_key[user-names-kept,user-only]` |
| M4 strip by mutating `os.environ` | `test_task_environment_never_holds_key` (4) |
| M5 no `basename` on the launcher | `…sets_open_mpi_parameter_for_mpi_launchers[mpirun-path]` |
| M6 every launcher treated as MPI | `…sets_no_open_mpi_parameter_for_other_launchers` (4) |
| M7 never add `-x` | `test_client_launch_forwards_key_by_name_beside_mpirun_x` (2) |
| M8 no ssh export filter | `test_ssh_env_export_never_carries_key`, `test_ssh_launch_exports_never_carry_key` |
| M9 no `describe()` redaction | `test_client_launch_description_redacts_key_exports` (2) |
| M10 hint ignores `-k` | `test_client_names_missing_key_only_when_none_given[explicit-default-key,explicit-wrong-key]` |
| M11 hint by value (the cycle-1 form) | `…[explicit-default-key]` |
| M12 hint never fires | `…[no-key]` |

All 12 killed. No test covers N2's path.

### Findings (cycle 2)

#### [N1 · CRITICAL by the rubric, impact LOW · CONFIRMED] Residual key reach in an SSH export edge case (R1, R6)
- **Severity note:** the preconditions are contrived and the run's own key is never involved, but
  the case breaks the absolute wording this remediation adds to `invariants.md` §11, AGENTS.md and
  `docs/security.rst` about `--env` exports, which the rubric makes auto-CRITICAL. Same treatment
  as H1 in cycle 1; the human gate decides.
- **Where:** the SSH launch path (`cluster/launch.py`, `cluster/ssh.py`).
- **Summary:** the H1 fix holds for the ordinary case, but in one edge case a key value can still
  reach a command line and a DEBUG log line. Detail, reproduction and a small
  fix are in the hidden security lane; the maintainer has the reference.
- **Evidence:** a direct `SSHCluster(...)` construction in a scratch copy.

#### [N2 · MEDIUM · CONFIRMED] Residual task-side key reach in one client path (Q3 ruling)
- **Where:** the client's per-task path (`client.py`).
- **Summary:** task processes no longer see the key in any mode, but one other client-side path
  still exposes it to code the run executes for each task, so the Q3 ruling ("the client removes
  `HYPERSHELL_SERVER_AUTH` from every task's environment") and the new `docs/security.rst` sentence
  ("Tasks therefore see neither the key…") are not fully met. Detail, reproduction and a small fix
  are in the hidden security lane.
- **Evidence:** an end-to-end `hs cluster --launcher` run in a scratch copy reproduced it for 4 of
  4 tasks; the same run's task processes held no key.
- **Gate:** `client.py` and a security property → human sign-off.

#### [N3 · LOW · PLAUSIBLE] Open MPI under another program name gets no forwarding (R3)
- **Where:** `src/hypershell/cluster/launch.py:181` matches the launcher's base name against
  `mpirun`/`mpiexec` only.
- **Failure scenario:** `--launcher='mpirun.openmpi …'` (the Debian/Ubuntu name when MPICH is also
  installed), `mpiexec.openmpi`, `orterun` or `prterun` gets neither the parameter nor `-x`. Where Open
  MPI starts remote ranks without passing the launcher's environment (its ssh/rsh launch), those
  clients would have no key and exit with the hint, where develop (key on the command line) and the
  cycle-1 build (parameter for every launcher) ran. The docs do scope forwarding to
  `mpirun`/`mpiexec`, so this is a reach question, not a docs error.
- **Evidence:** builder probe above. Not reproduced (no Open MPI here); worth one name variant in
  the foreman's real Open MPI check.

#### [N4 · LOW · CONFIRMED] The CI tag fetch fails, rather than skips, when the tag is missing (F4(b))
- **Where:** `.github/workflows/tests.yml:31-34`.
- **Failure scenario:** a push to a fork's `develop`/`master` (the workflow's push trigger) whose
  tags predate `2.9.0a4`: `origin` is the fork, the fetch exits 128 and fails every matrix job,
  where the tag-gated tests would otherwise just skip. Pull requests from forks are fine:
  `actions/checkout` points `origin` at the base repository, which has the tag. The step is
  otherwise correct and minimal (one tag, depth 1).
- **Evidence:** the exact command in a shallow tagless clone of a bare origin without the tag →
  `fatal: couldn't find remote ref refs/tags/2.9.0a4`, exit 128; with the tag → exit 0, the tag
  resolves and `git show 2.9.0a4:src/hypershell/client.py` reads.
- **Fix shape:** let the step tolerate a missing tag (for example `|| echo "::notice::…"`), so the
  tests' own skip applies.

#### [N5 · LOW · CONFIRMED] `docs/security.rst` offers `[export]` for a key it cannot carry (R8)
- **Where:** `docs/security.rst:212-214` ("A task that needs the key, for example one that starts
  its own client of the same server, has to be given it explicitly, through the ``[export]``
  section").
- **Failure scenario:** under `hs cluster` the key is minted per run and never known to the
  operator, and `[export]` takes literal values only, so the example cannot be done for a run's
  key. It holds for an operator-held key (a hand-started server and client), where
  `HYPERSHELL_EXPORT_HYPERSHELL_SERVER_AUTH` avoids a plaintext key in a config file.
- **Evidence:** the `[export]` probe above; `secrets.token_hex(64)` per launch in `remote.py`/`ssh.py`.

### Cycle-1 closure

| Item | Status | Evidence |
|------|--------|----------|
| H1 | closed for the reported combination; residual N1 | M8, M9; `test_ssh_env_export_never_carries_key` |
| F1 (launcher side) | closed in the builder (MPI launchers only, `-x` beside a user's `-x`, full paths); real Open MPI 4/5 runs stay with the foreman; reach question N3 | M5–M7; builder probe |
| F1(b) / Q3 reversal | closed for task processes in every mode and for a hand-started client; residual N2 | task-environment probes; M1–M4 |
| F2 | closed: explicit `-k` never hints; key precedence unchanged | M10–M12; F2 probes |
| F3 | closed: the launchers, the `-x` case and the task behaviour are described accurately; wording issue N5 | read against the code and probes |
| F4(a) | closed: both cycle-1 survivors are now killed | TECH re-runs; M10–M12; `test_client_launch_start_survives_closed_input` passes |
| F4(b) | works when the tag exists; fails instead of skipping without it (N4) | CI step simulation |

### Timing failures in the builder's full run

`test_server_keeps_its_files_at_the_open_file_limit` and `test_waits_for_slow_peer_upgrade` are
pre-existing, load-sensitive tests, not caused by this diff. Each passed 3 of 3 serial runs on the
branch and on develop, and in both full parallel runs per tree above (10 and 16 workers); develop
showed a different timing flake under 16 workers. Neither test reaches the changed code in a
timing-relevant way: the upgrade test starts no client or cluster (a SQLite lock and a 2 s window
that a slow subprocess start under load can miss), and the open-file-limit test passes `-k`
explicitly, so its only changed per-task work is one pass over the client's environment. Not
reproduced on 3.12.13, where the builder saw them.

### Human-gate triggers (cycle 2)

- **N1** (CONFIRMED, rubric-CRITICAL): SSH launch path and the §11 wording → human sign-off.
- **N2** (CONFIRMED, MEDIUM): `client.py`, a security property → human sign-off.
- N4, N5 (CONFIRMED, LOW): CI and docs only; no gate of their own.
- N3 is PLAUSIBLE: for triage; it should ride on the foreman's real Open MPI check.

Next: `/hs-build` for N1, N2, N4 and N5 after the human gate; then a scoped cycle 3, the last before
escalation.
