# REVIEW — Refuse the queue key on a command line

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** fcd3ba759d1f5bc998ae916f27b9d58c41969685  ·  **Base:** origin/develop (e507f5b; merge base 2ffb5cc)  ·  **Date:** 2026-11-13
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

Contract drift: `GOAL.md` changed only in the shaping commit (b6ccee1) and the GOAL-gate lock
(f81842c), both before planning. No drift during the build. The branch does not touch
`.agents/factory/invariants.md` or `AGENTS.md`. Between the merge base and `origin/develop`, no
`src/` file changes; `git merge-tree --write-tree origin/develop HEAD` is clean.

Reviewer inputs: `GOAL.md`, `invariants.md`, `review-rubric.md`, `AGENTS.md`, and
`git diff origin/develop...HEAD -- . ':(exclude)spec/'`. The reviewer also got
`git diff origin/develop...HEAD -- 'spec/*/drive/*'`, because R8 grades the executable drives and the
`spec/` exclusion hides them.

## Verification run

- `uv run pytest tests/test_key_option.py tests/test_completions.py` → 99 passed.
- `uv run pytest -m unit` → 1498 passed, 3 skipped, 743 deselected.
- `uv run pytest tests/test_auth_variants.py` → 46 passed. Every key source (env, `auth`, `auth_env`,
  `auth_eval`) was exercised on server, client and `submit -q`.
- `uv run pytest` on `test_cluster_launch`, `test_relay_launch`, `test_dispatch_staged`,
  `test_dispatch_version`, `test_dispatch_workload`, `test_queue_limits`, `test_queue_start`,
  `test_queue_workload`, `test_server`, `test_topology_server`, `test_upgrade_cli`,
  `test_zone_workload`, `test_cluster` → 390 passed, 1 skipped.
- `uv run pytest tests/test_relay_workload.py tests/test_relay_outage.py` → 18 passed.
- CLI refusal probe in fresh `temp_site`s with a log file at devel level: 116 command lines across
  `hs client|server|submit|cluster|relay`, `hsx` and `hyper-shell`. Forms covered: `-k KEY`,
  `-kKEY`, `-k=KEY`, `--auth KEY`, `--auth=KEY`, bare, duplicated, unambiguous prefixes, `-qkKEY`,
  `-Ek KEY`, before and after positionals, `-k -KEY`, `-k -- KEY`.
  → Every case exits 2, names `HYPERSHELL_SERVER_AUTH` and `server.auth`, creates no database, and
  leaves no key on stdout, stderr or in any site file. The exception is F4's ambiguous `=` forms,
  which predate this branch.
- Key-policy comparison against `origin/develop`'s source: short key, bad characters, placeholder
  key, and no key on server, client and `submit`. → Identical outcomes.
- `temp_site` drives, all rc=0:
  - `hs server` + `hs client` with the key in the environment → 3/3 tasks.
  - `hs submit -q` with `server.auth` in a config file → submitted.
  - A server task file holding `echo file -k task-value` → the client printed `file -k task-value`.
  - `hsx -t 'echo tmpl -k {}'` → `tmpl -k a`.
  - `hsx --launcher env` → 2/2.
- The F1 and F3 inputs, re-run by the orchestrator:
  `.agents/factory/bin/temp_site.sh sh -c "uv run hs relay -N 1 --launcher srun --launcher-args '-k --exclusive'; uv run hs cluster tasks.in --ssh host1 --ssh-args '-k -i id'; uv run hsx tasks.in --aut fixed --help"`
  → rc=2 three times. The same three argv on `origin/develop`'s source (`Interface.parse_args` with
  `PYTHONPATH` at a `git archive` of `origin/develop`) → all parse.
- `UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs $TMPDIR/docs-html`
  → rc=0.
- The same with `-b man` → rc=0, and no `\-k` or `\-\-auth` in `hs.1` or `hyper-shell.1`.
- Tree left clean (`git status --porcelain` empty). The reviewer's one hung probe (`hsx --mpi` with no
  `mpirun` installed, which is environmental) was stopped by its recorded PID. No hs, hsx or pytest
  process from this review is left running.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `core/config.py:409-436` (`RefuseKeyOption`, `refuse_key_option`); registered in `client.py`, `server.py`, `submit.py` (fcd3ba7) | 116-case CLI probe; `test_key_option.py` | ✅ |
| R2 | same | probe scanned stdout, stderr and every site file at devel log level | ✅ (F4 predates, outside R1's forms) |
| R3 | refusal advice on `submit` points a task's `-k` past `--`; the parser leaves `-k` after `--` alone | `test_submit_task_option_after_separator`, `test_submit_task_option_reaches_task`; server task-file and `hsx -t` drives | ✅ (test gap: F5) |
| R4 | key resolution unchanged (`resolve_auth`); `client.py`, `server.py`, `submit.py` call sites | `test_auth_variants.py` 46 passed; key-policy comparison against develop; cluster/relay suites 390 + 18 passed; drives | ❌ partial — F1: `hs cluster`/`hs relay` runs whose option value starts with `-k ` no longer start |
| R5 | usage and help of client, server and submit; `client.py:1147-1159` hints | `hs {client,server,submit,cluster,relay} --help` greps clean; `submit` `-q` line names both sources | ✅ |
| R6 | `docs/_include/*`, `getting_started.rst`, `security.rst`, `security_alt.rst`, `templates.rst`, `install.rst`, `manual.rst` | grep of `docs/` and `README.rst`; man build grep; html `-W` build | ❌ partial — F2: changed passages on `cli/client`, `cli/server`, `cli/submit` and `templates.rst` carry no `versionchanged` |
| R7 | `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs` | grep clean (`_hs_authkey` remains only for `hs config set server.auth`, a Non-goal); `test_completions.py` passed | ✅ |
| R8 | `tests/*`, `tests/bench/bench_queue.py`, `spec/relay/drive/relay_throughput.py` (5a06832, fcd3ba7) | `test_sweep_finds_no_key_option`; reviewer grep over `tests/` and `spec/*/drive/` finds only the Non-goal negative assertions | ❌ partial — F5: R3 tested on `hs submit` only |

Unmapped changes:
- **The refusal on `hs cluster`/`hsx` and `hs relay`** (`CLUSTER_KEY_ADVICE`, `RELAY_KEY_ADVICE`).
  R1 names only three commands, but the Outcome says "any command", so this is in scope. It is also
  the source of F1 and F3.
- **`AUTH_ENV` moved from `client.py` to `core/config.py`.** `client.py` still re-exports it, so this
  is a supporting refactor.
- **Status bookkeeping** in `ROADMAP.md` and `issues/key-option-on-command-line.md`
  (`adopted:key-option-refused`).

## Findings

### [HIGH/CONFIRMED] F2 — Changed key passages on the per-command pages and in templates.rst carry no `versionchanged`
- **Where:**
  - `docs/cli/client.rst:14-25`, `docs/cli/server.rst:14-22`, `docs/cli/submit.rst:14-26`, which
    include the changed `client_desc`, `server_desc` and `submit_help` snippets.
  - `docs/_include/templates.rst:213-214`.
  - `docs/install.rst:470`, where the `--auth <tab>` completion bullet was removed.
- **Failure scenario:**
  - The per-command reference pages show the new "never from the command line" paragraph and the
    rewritten `-q` key passage, but no `.. versionchanged:: 2.9.0` for this change. Each of these
    pages already holds a 2.9.0 marker beside the same `.. include::` for another change (:pr:`78`).
  - The rewritten templates.rst sentence about a hand-started client's key is unmarked.
  - The three new markers went only into `docs/manual.rst`. `getting_started.rst` and `security.rst`
    carry their own.
- **Evidence:**
  - HTML build, then a grep for the marker text "A key on the command line is refused": 0 hits in
    `cli/client.html`, `cli/server.html`, `cli/submit.html` and `templates.html`; 4 in `manual.html`,
    1 in `security.html`, 1 in `getting_started.html`.
  - `grep -n versionchanged docs/cli/{client,server,submit}.rst` shows only the :pr:`78`, :pr:`101`
    and :pr:`50` markers.
- **Touches invariant / requirement:** R6 ("each changed passage that describes behaviour SHALL
  carry `.. versionchanged:: 2.9.0`"); `invariants.md` §12 and `AGENTS.md` § Docs ("the marker goes
  beside the `.. include::`"). The reviewer graded this MEDIUM. It is regraded HIGH here because the
  rubric grades a §12 marker gap HIGH, and R6 is unmet.

### [MEDIUM/CONFIRMED] F1 — Registering `-k` on hs cluster and hs relay breaks option values that begin with `-k `
- **Where:** `src/hypershell/cluster/__init__.py:218`, `src/hypershell/relay/__init__.py:1097`
  (`refuse_key_option(...)`).
- **Failure scenario:** Once `-k` is a registered short option, argparse reads a separate argument
  that starts with `-k` (even one with a space in it) as that option, not as the value of the option
  before it.
  - `hs relay -N 1 --launcher srun --launcher-args '-k --exclusive'` (srun's `-k` is `--no-kill`)
    → rc=2 "argument --launcher-args: expected one argument".
  - `hs cluster tasks.in --ssh host1 --ssh-args '-k -i id'` (ssh's `-k`) → rc=2 "argument
    --ssh-args: expected one argument".
  - `hsx tasks.in -t '-k {}'`, `--launcher '-k srun'` and `--remote-exe '-k x'` fail the same way.
  - All of these parse on `origin/develop`. Only the `=` form (`--ssh-args=-k …`) still works, and
    it is the only form `test_option_value_holding_k_parses` checks.
  - On `hs client` and `hs submit`, `-t '-k {}'` already failed on develop, so those are not
    regressions.
- **Evidence:** the orchestrator's `temp_site` re-run above (rc=2 ×2 with the messages quoted),
  and the reviewer's `Interface.parse_args` comparison: develop → `OK … ssh_args '-k -i id'`, `OK
  relay … '-k --exclusive'`; branch → `ERR … expected one argument`.
- **Touches invariant / requirement:** R4 ("the clients that `hs cluster` launches in every mode,
  and the site clients `hs relay` launches, SHALL join as before"). These runs no longer start.

### [LOW/CONFIRMED] F3 — `--a`, `--au` and `--aut` on hs cluster are now ambiguous
- **Where:** `src/hypershell/cluster/__init__.py:218`.
- **Failure scenario:** `hsx tasks.in --aut fixed` → rc=2 "ambiguous option: --aut could match
  --auth, --autoscaling". On `origin/develop` this parses as `--autoscaling fixed`. `--auto` still
  works.
- **Evidence:** the orchestrator's `temp_site` re-run (rc=2 with that message), and the develop
  `parse_args` comparison (OK).
- **Touches invariant / requirement:** R4 (an `hs cluster` invocation that worked no longer starts).

### [LOW/CONFIRMED] F5 — R3 is tested on hs submit only
- **Where:** `tests/test_key_option.py:124-145`, `:214`.
- **Failure scenario:** R8 asks for R1–R3 tests on each of the three commands. Nothing tests a `-k`
  in an `hs server` task file reaching the task.
  - The behaviour itself is correct: a reviewer drive put `echo file -k task-value` in a server task
  file, and the client printed `file -k task-value`, rc=0.
  - `hs client` has no task input of its own, so it needs no R3 test.
- **Evidence:** `grep -n "def test_" tests/test_key_option.py`, which shows only the submit
  separator and task-file tests for R3; the drive above.
- **Touches invariant / requirement:** R8.

### [LOW/CONFIRMED · predates the branch · not counted] F4 — An ambiguous abbreviation joined with `=` echoes its value
- **Failure scenario:**
  - `hs server --forever --au=KEY` → "ambiguous option: --au=KEY could match --autoscaling,
    --auth", on stderr and in the log file.
  - `hs client --a=KEY` (ambiguous with `--available-cores` and `--available-ssh-groups`) leaks the
    same way.
  - `hs initdb -k KEY` and `hs config get … -k KEY` → "unrecognized arguments: -k KEY".
  - Develop's output is identical for server and client. On develop `hs cluster --a=KEY` leaked
    through "Unknown AutoScalerPolicy"; it now leaks through the ambiguity message instead.
  - The space-separated forms (`--au KEY`) do not echo.
- **Disposition:** R1 lists only the forms the parser accepted before, and these were never
  accepted, so this falls outside R1 and R2. It predates the diff. `origin/develop` already tracks
  it publicly in the seed `issues/parser-echoes-option-values.md` (e507f5b), so it is recorded here
  for reference and does not affect the verdict.

## Human-gate triggers

- **Not triggered.** No CONFIRMED finding touches the high-blast-radius core: F1 and F3 are in
  `cluster/__init__.py` and `relay/__init__.py`, F2 is in `docs/`, and F5 is in `tests/`.
- None touches a security or DB-lifecycle invariant: the key-policy checks (§9) were compared
  against develop and are unchanged.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).

## Review cycle 2 — approved (2026-11-13)

- **Reviewed commit:** 27668a1b978132f6258897b5f407f25788e03357  ·  **Base:** origin/develop (e507f5b; merge base 2ffb5cc)  ·  **Date:** 2026-11-13
- **Verdict:** approved
- **Cycle:** 2 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** a fresh blind pass over the full spec-excluded diff, plus a re-run of F1, F2, F3 and F5
  against this head. No `debate`, no `completeness` sub-pass. The correctness pass was a fresh
  subagent given `GOAL.md`, `git diff origin/develop...HEAD -- . ':(exclude)spec/'`,
  `git diff origin/develop...HEAD -- 'spec/*/drive/*'` (R8 grades the drives), `AGENTS.md`,
  `invariants.md`, the rubric, and the failure scenarios of this file's cycle-1 findings. It was told
  not to read `PLAN.md`, `TECH.md`, `META.md`, this file, or `research/`. It was also given the
  review-cycle-1 ruling, stated as a reading of the contract: R1's list of commands (`hs client`,
  `hs server`, `hs submit`) is exhaustive, and `hs cluster`/`hsx` and `hs relay` keep
  `origin/develop`'s parsers. That ruling is recorded in `PLAN.md` (a981df1), not in `GOAL.md`.

### Pre-flight

- `TECH.md`: `status: in_review`, P1 and P2 `done`, `review.cycle: 1`.
- Contract drift: `GOAL.md` is unchanged since the GOAL-gate lock (f81842c). The cycle-1 ruling is in
  `PLAN.md` only and was graded as binding.
- The branch does not touch `AGENTS.md` or `.agents/`. `git merge-tree --write-tree origin/develop
  HEAD` is clean. No `src/` file changes between the merge base and `origin/develop`.
- `src/hypershell/cluster/__init__.py` and `src/hypershell/relay/__init__.py` have no diff against
  `origin/develop`.

### Verification run

- `uv run pytest tests/test_key_option.py tests/test_completions.py tests/test_auth_variants.py
  tests/test_server.py` → 151 passed.
- `uv run pytest tests/test_cluster_launch.py tests/test_relay_launch.py` → 200 passed, 1 skipped
  (fish not installed).
- `uv run pytest` over the converted files (`test_dispatch_staged`, `test_dispatch_version`,
  `test_dispatch_workload`, `test_queue_limits`, `test_queue_start`, `test_queue_workload`,
  `test_relay_outage`, `test_relay_workload`, `test_topology_server`, `test_upgrade_cli`,
  `test_zone_workload`) and `test_client`, `test_submit`, `test_logging`, `test_main` → 257 passed.
- `uv run pytest -m unit` → 1482 passed, 3 skipped.
- `pytest -m unit tests/test_key_option.py` on CPython 3.11, 3.12 and 3.13 (temporary environments)
  → 54 passed on each.
- Orchestrator re-run: `uv run pytest -q tests/test_key_option.py` → 69 passed.
- Parser comparison against a `git archive` of `origin/develop` (`PYTHONPATH`): every action on
  `ClusterApp` and `RelayApp` is identical (option strings, dest, nargs, default, type, action class,
  const, choices). On `ClientApp`, `ServerApp` and `SubmitApp` the only difference is the key action
  (`RefuseKeyOption`, `nargs='?'`, `dest=key_option`, default `SUPPRESS`).
- CLI refusal probe in a `temp_site`, log file set, console and file levels DEVEL: `hs client -N 1
  -T 5`, `hs server --forever` and `hs submit -q echo hi` × `-k KEY`, `-kKEY`, `--auth KEY`,
  `--auth=KEY` → 12 of 12 exit 2.
  - Each refusal names `HYPERSHELL_SERVER_AUTH` and `server.auth`; `submit` adds that a task's own
    `-k` goes after `--`.
  - The key appears on neither stdout nor stderr, and in no file under the site. No database file is
    created.
  - `hyper-shell client -k …` also exits 2.
- R3 and R4 drives in `temp_site`s, all exit 0:
  - R3: a server task file `echo -k …`, `hs submit -q … -- echo -k …`, `hs submit … -- echo -k …`
    into the database, and a client template `echo -k {}`. Each task printed its `-k` unchanged.
  - R4: the key from the environment, `server.auth`, `auth_env` and `auth_eval`, across server,
    client, `submit -q` and database submit. Every task ran.
  - Key policy, `hs server --print` on HEAD and on `origin/develop`, with no key, a short key, the
    placeholder, bad characters and a good key → identical messages.
  - `hsx tasks.in -t 'echo -k {}' -N 2` → printed `-k one`, `-k two`.
  - The reviewer's `hsx --launcher env` drive could not resolve the laptop's own hostname under the
    Bash sandbox. This is environmental. The launcher, ssh and autoscale joins rest on
    `test_cluster_launch` and `test_relay_launch` above.
- Docs:
  - `UV_PROJECT_ENVIRONMENT=$TMPDIR/rv2-docs-venv uv run --python 3.12 --group docs sphinx-build -W
    --keep-going -q -E -b html docs $TMPDIR/rv2-docs-html` (sandbox off) → exit 0.
  - The same with `-b man` → exit 0. With roff backslashes stripped, `hs.1` and `hyper-shell.1` have no
    `-k` or `--auth`. The committed `share/man/man1/hs.1` still has them, and the GOAL leaves it to
    `hs-release`.
  - Orchestrator grep of the built HTML: the 2.9.0 key marker is present in `cli/client.html`,
    `cli/server.html`, `cli/submit.html`, `templates.html`, `install.html` and `manual.html`.
- Completions: `bash -n` and `zsh -n` pass. Bash `_hs_client`, `_hs_server` and `_hs_submit` offer
  40, 39 and 35 options, none of them `-k` or `--auth`.
- Tree left clean (`git status --porcelain` empty). The worktree `.venv` is still CPython 3.14.
  - The reviewer's one stuck drive (`hsx --launcher env`) was stopped by its task handle, and no
    hs, hsx or hyper-shell process from this worktree remained.
  - The orchestrator's probes were parse-only (`-h`) or pytest runs that exited.

### Cycle-1 findings

| ID | Status | Evidence |
|----|--------|----------|
| F1 | ✅ fixed | Cluster and relay parsers are byte-for-byte `origin/develop`'s. Each value below parses as that option's value, and the same CLI line with `-h` exits 0 on HEAD and on develop: relay `--launcher-args '-k --exclusive'`, cluster `--ssh-args '-k -i id'`, hsx `-t '-k {}'`, `--launcher '-k srun'` and `--remote-exe '-k x'`. Control: `hs client -t '-k {}' -h` exits 2, as on develop. Pinned by `test_cluster_and_relay_parse_as_before`. |
| F2 | ✅ fixed | `.. versionchanged:: 2.9.0` sits beside the changed includes in `docs/cli/client.rst`, `server.rst` and `submit.rst`, after the hand-started client sentence in `docs/_include/templates.rst`, and after the completion examples in `docs/install.rst`. It is already in `manual.rst`, `security.rst`, `getting_started.rst` and `security_alt.rst`. The HTML `-W` build passes. |
| F3 | ✅ fixed | `ClusterApp.interface.parse_args(['tasks.in', '--aut', 'fixed'])` gives `autoscaling_policy='fixed'` on HEAD and on develop. `hs cluster tasks.in --aut fixed -h` exits 0. Pinned by the `cluster-autoscaling-prefix` case. |
| F5 | ✅ fixed | `test_server_and_client_task_option_reaches_task` covers a server task file and a client template, beside the `hs submit` `--` and task-file tests. All pass, and the reviewer's drives agree. |
| F4 | unchanged (predates; not counted) | `hs server --forever --au=KEY` and `--aut=KEY`, and `hs client --a=KEY`, echo the same as on develop. The space-separated forms echo nothing. Still tracked in `issues/parser-echoes-option-values.md`. |

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `core/config.py:394-436` (`RefuseKeyOption`, `refuse_key_option`). Registered at `client.py:1595`, `server.py:1703` and `submit.py:1313` (`SUBMIT_KEY_ADVICE`, `submit.py:1203`). | 12-case CLI probe; `test_key_option.py`; parser comparison | ✅ |
| R2 | same | probe: no key on stdout, stderr or any site file at DEVEL | ✅ |
| R3 | the parser leaves `-k` after `--` and in task files alone | drives A–E; `test_submit_task_option_*`, `test_server_and_client_task_option_reaches_task` | ✅ |
| R4 | `resolve_auth('server')` unchanged, called at `client.py:1665`, `server.py:1742` and `submit.py:1361`; cluster and relay parsers are develop's | per-source drives; key-policy parity; `test_auth_variants`; cluster and relay launch suites; the converted workload suites | ✅ |
| R5 | usage and help of the three commands; hints at `client.py:1147-1159` | `--help` greps; `submit -q` line names both sources | ✅ |
| R6 | `docs/_include/*`, `docs/cli/*`, `getting_started.rst`, `security.rst`, `install.rst`, `manual.rst`, `templates.rst`, `security_alt.rst` | docs and README grep; markers as under F2; HTML `-W` and man builds | ✅ |
| R7 | `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs` | completion run, syntax checks, `test_completions` | ✅ |
| R8 | `tests/*`, `tests/bench/bench_queue.py`, `spec/relay/drive/relay_throughput.py`; `test_key_option.py` | `test_sweep_finds_no_key_option`; reviewer grep (only the refusal tests and the Non-goal negative assertions); every new test tagged | ✅ |

Unmapped changes: none beyond those cycle 1 recorded. The `AUTH_ENV` move to `core/config.py`
(still importable from `hypershell.client`) supports R1 and R5. The status bookkeeping in
`ROADMAP.md` and `issues/key-option-on-command-line.md` follows the usual convention. The cluster and
relay refusal that cycle 1 recorded here is gone.

### Findings

None. One candidate was dropped: the `hsx --launcher env` drive failure was the sandbox's
hostname resolution, not the branch.

### Human-gate triggers

- **Not triggered.** There is no CONFIRMED finding.
- The key-policy checks (§9) match develop's, and the key stays out of every launched argv (§11);
  `test_cluster_launch` and `test_relay_launch` pass.
