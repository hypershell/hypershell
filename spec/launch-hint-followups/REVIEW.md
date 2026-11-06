# REVIEW — Launch-path follow-ups: accurate no-key hints, template docs, an export-name test

> Adversarial QA by `hs-review`. The correctness pass grades the branch diff against
> [`GOAL.md`](GOAL.md) and the AGENTS.md invariants only. Every finding cites an **executed**
> command, not an assertion.

## Review cycle 1 — changes-requested (2026-11-05)

> **Mode:** full blind pass over `git diff origin/develop...HEAD -- . ':(exclude)spec/'`
> (`client.py`, `tests/test_cluster_launch.py`, `docs/_include/templates.rst`, `docs/security.rst`),
> graded against `GOAL.md`, the invariants, the runnable repo and the foreman's named checks. The
> blind pass ran inline in the orchestrator (no subagent, foreman's instruction). `PLAN.md` and
> `TECH.md` were read only after the findings below were formed. `origin/develop` stands in for
> `{base}`, since the local `develop` is stale.

- **Reviewed commit:** b80c6b2  ·  **Base:** develop (`origin/develop` at `d2a460f`)  ·  **Date:** 2026-11-05
- **Verdict:** changes-requested. There are two CONFIRMED findings, both LOW. F1 is a docs
  sentence that R4 covers. F2 is an R3 test gap. Neither is a code defect.
- **Cycle:** 1 of ≤3.
- **Contract drift:** none. `git log origin/develop..HEAD -- spec/launch-hint-followups/GOAL.md`
  shows only `ea56e37`, the shaping commit.

### Verification run

All runs used scratch copies only (`git archive b80c6b2` and `git archive d2a460f`), with venvs from
`uv sync --frozen --python 3.14` (Python 3.14.6). Every run was under a perl process-group kill
timer. A command-line check at the end shows that none of this review's processes remain.

- **Full suite**, `pytest -n auto`, in a copy without the contract tag: **1002 passed, 91 skipped,
  0 failed**. That is the builder's 1005 + 88, with the three tag-dependent tests skipped.
- **Sphinx** `-W --keep-going -E`: exit 1 with exactly the two known warnings, the same as on
  `d2a460f`. Both are "document isn't included in any toctree", for `cli/task_submit.rst` and
  `manual.rst`. Both changed passages render as written.
- **Docs gates.** The diff names no `hs task …` command; it names `hs cluster`, `hs client` and
  `hs server` only. There are no internal names. Apart from the sentence the maintainer approved,
  nothing describes how a weakness works.

#### No-key hint matrix (real `hs server -k` and `hs client` processes, head against base)

Two failure kinds were exercised. Without TLS, the client fails to authenticate. With TLS, the
client fails to verify the server, because its home holds no CA for the server's certificate. In
each case a fresh server was started, and the client was isolated with `HYPERSHELL_CONFIG_FILE`
or a local site of its own.

| Client's key source | auth failure: head / base | TLS failure: head / base |
|---|---|---|
| none (a client started by hand) | hint / hint | hint / hint |
| config file (`HYPERSHELL_CONFIG_FILE`), wrong key | **no hint** / hint | **no hint** / hint |
| config file in the local site (`config.toml`), wrong key | **no hint** / hint | **no hint** / hint |
| config file, the server's key | runs, exit 0 / same | **no hint** / hint |
| config file, `auth = "<not-secure>"` | hint / hint | hint / hint |
| config file, `auth_env` or `auth_eval` | exits at import, both (O1) | same |
| `HYPERSHELL_SERVER_AUTH`, wrong key | no hint / no hint | no hint / no hint |
| `HYPERSHELL_SERVER_AUTH`, the server's key | runs, exit 0 / same | no hint / no hint |
| `HYPERSHELL_SERVER_AUTH="<not-secure>"` | no hint / no hint | no hint / no hint |
| `-k` with a wrong key, or with `<not-secure>` | no hint / no hint | no hint / no hint |
| key in the environment, nothing listening on the port | `ConnectionRefusedError`, no hint, both | same |

Exit status and error class are identical on head and base in every row. Only the hint differs,
and only in the rows where the key came from a configuration file. So nothing else in `hs client`
changed.

#### Template environment probes (`hs cluster` with stand-ins; the remote step runs under `/bin/sh`)

Each probe ran a template that recorded, per task, four things:

- what a `{% %}` command saw of the key variable, of an `[export]` value and of `TASK_ID`;
- what the task saw of the same `[export]` value and of `TASK_ID`;
- whether a `{= =}` lambda found the key variable in `os.environ`.

Each probe ran twice, with `[export]` given by a config file and by `HYPERSHELL_EXPORT_*`. Both
gave the same results.

| Run | `{% %}`: key var | `{% %}`: `[export]` | `{% %}`: `TASK_ID` | task: `[export]`, `TASK_ID` | `{= =}`: key var |
|---|---|---|---|---|---|
| local `hs cluster` | absent | absent | absent | present, set | absent |
| `--launcher` | absent | **present** | absent | present, set | present |
| `--ssh` | absent | absent | absent | present, set | present |
| `--ssh --env` | absent | absent | absent | present, set | present |
| `hs client -k` by hand, own env without the variable | absent | absent | absent | present, set | absent |
| `hs client -k` by hand, own env with the variable | absent | absent | absent | present, set | present |
| local `hs cluster`, launching shell exports the variable | absent | — | — | — | **present (the shell's value)**: F1 |
| `--launcher`, launching shell exports the variable | absent | — | — | — | present (the run's own key) |

Every claim in the new `templates.rst` text holds, except the local-mode sentence (F1).

#### Mutation results (one at a time, over `tests/test_cluster_launch.py`, `-n auto`)

| Mutant | Result |
|---|---|
| H1 no-key predicate as on base (no `DEFAULT_AUTH` clause) | killed (`…none_given[config-wrong-key]`, `…under_tls[key-in-config]`) |
| H2 environment clause dropped | **survived**: 112 passed (F2) |
| H3 `-k` clause dropped | killed (`…none_given[explicit-default-key]`) |
| H4 `DEFAULT_AUTH` clause negated | killed (5 tests) |
| H5 hint never shown | killed (4 tests) |
| H6 TLS handler keeps the old condition | killed (`…under_tls[key-in-config]`) |
| H7 auth handler keeps the old condition | killed (`…none_given[config-wrong-key]`) |
| E1 export-name check removed | killed (`…shell_cannot_assign[dot,dash,space]`) |
| E2 warning kept, `continue` dropped | killed (same 3) |
| E3 `match` instead of `fullmatch` | killed (same 3) |
| E4 name pattern admits `.` and `-` | killed (`[dot]`, `[dash]`) |
| E5 name pattern admits a space | killed (`[space]`) |

11 of 12 mutants were killed. The scratch sources were restored and checked with `cmp` after each run.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `client.py:1668`, `:1696` | `…none_given[config-wrong-key]`; hint matrix, config rows; H1, H7 killed | ✅ |
| R2 | `client.py:1668`, `:1700` | `…under_tls[key-in-config]`; hint matrix, TLS column; H1, H6 killed | ✅ |
| R3 | `client.py:1668` | no-key cases (auth, TLS, launcher, ssh) keep the hint; `-k` and environment suppress it; H3–H5 killed; H2 survives | ✅ code · test gap F2 |
| R4 | `templates.rst:178-183`, `:197-203` | template probes above | ❌ one sentence (F1) |
| R5 | `test_ssh_env_skips_names_a_shell_cannot_assign` | E1–E5 killed; asserts the name is on no command line and the run completes | ✅ |
| R6 | `security.rst:226-232` | approved text verbatim; see the judgement below | ✅ |

Unmapped changes: none. The `LAUNCHER_BARE_ENV_BODY` change from `keep_key` to `key_in` serves the
R2 test.

### The `docs/security.rst` move

The two approved sentences are verbatim. They now follow "… neither a task's environment nor a
`{% %}` command's holds the key …, unless the configuration gives it explicitly." So "This" refers
to keeping the key out of those environments, which is the meaning the maintainer approved. In the
old position, "This" would have followed the sentence about a nested run minting its own key, and
pointed at the wrong thing. Three companion edits were grammar-driven:

- "This applies to clients from this release on; …" moved with the approved sentences. Left behind,
  it too would have followed the nested-run sentence. It now sits two sentences after its
  antecedent, but its second clause ("an older client … still passes its environment to its tasks
  unchanged") makes clear what it refers to.
- "There are two explicit ways:" had to change, because it no longer follows "explicitly". The new
  lead-in, "A task can be given the key in two explicit ways:", fits both bullets. It is also more
  exact than before: an `[export]` entry never reaches a `{% %}` command.
- The nested-run sentence is unchanged.

The passage reads correctly, and the move keeps the approved meaning.

### Findings

#### [F1 · LOW · CONFIRMED] `templates.rst` says local mode's lambdas never see the key variable
- **Where:** `docs/_include/templates.rst:200-202`
- **Text:** "It does not under the default local mode, whose client runs inside the `hs cluster`
  process: the key that run mints is never put in that process's environment."
- **Failure scenario:**
  - The shell that starts a local `hs cluster` already exports `HYPERSHELL_SERVER_AUTH`, for
    example for a server the operator started by hand. A `{= =}` lambda then finds the variable in
    `os.environ`, holding the shell's value.
  - The clause after the colon holds: the run's own key is never there. "It does not" goes too far.
  - The next sentence makes this exception for a client started by hand. The local-mode sentence
    needs the same one.
- **Evidence:** scratch probe `test_probe_launching_env_holds_variable`, with the template's
  `{= os.environ.get('HYPERSHELL_SERVER_AUTH', 'absent')[:6] =}`:
  - `[local]`: the lambda reads `userow`, the shell's value.
  - `[launcher]`: it reads the run's own key, which the launch sets over the inherited value.
- **Requirement:** R4, which asks for an accurate statement of which clients have the variable.
- **Possible fix (the wording is the builder's choice):** "Under the default local mode, whose
  client runs inside the `hs cluster` process, it has it only if the environment that starts
  `hs cluster` sets it: the key that run mints is never put in that process's environment."

#### [F2 · LOW · CONFIRMED] Under R3, no test covers the environment clause of the no-key predicate
- **Where:** `src/hypershell/client.py:1668` (behaviour); `tests/test_cluster_launch.py:1302-1308`
  (the parametrization).
- **Failure scenario:**
  - Mutant H2 drops `AUTH_ENV not in os.environ` from `no_key`, and the whole module still passes.
  - The two differ on one input only: `HYPERSHELL_SERVER_AUTH="<not-secure>"` with no `-k`.
  - Head and base give no hint there. That is right, because the hint would say the variable "is
    not set in its environment". H2 gives the hint.
  - The `-k` arm has its placeholder case (`explicit-default-key`). The environment arm, which R3
    also covers ("today's behaviour, unchanged"), has none.
- **Evidence:** the hint probe was run on head and on H2. Of 16 cases, only
  `env-placeholder` differs (head: no hint; H2: hint).
- **Fix:** add one case to `test_client_names_missing_key_only_when_none_given`:
  `pytest.param([], {AUTH_ENV: DEFAULT_AUTH}, None, False, id='environment-default-key')`.

### Observations (not findings; outside this cycle's scope)

- **O1 (pre-existing on `d2a460f`).** A configuration file with `[server] auth_eval = …` or
  `auth_env = …` makes every `hs` command exit at import with
  `AttributeError: 'auth' has more than one variant`. cmdkit sees the default `auth` and the file's
  variant in one `server` namespace.
  - `docs/security.rst` recommends exactly this form (lines 246 and 570–605).
  - For now, then, the `_env`/`_eval` forms cannot deliver a key, and the R1/R2 hint question does
    not arise for them. Once they do resolve, the new predicate handles the value.
  - This needs a separate seed.
- **O2 (pre-existing, outside R6 by design).** The `{= =}` bullet in `docs/security.rst` says
  `os.environ` there is "the client's own environment, key included". Under local mode the run's
  key is not there; `templates.rst` now says so. Bringing the two pages into line needs wording the
  maintainer approves.
- **O3.** A configuration file that sets `auth = "<not-secure>"` gets the hint, on head and on
  base. The placeholder is the value for "no key", and what the hint says still holds. The GOAL
  clarification accepts a test on the value. This is not a finding.

### Human-gate triggers

- F2's subject is the `client.py` predicate. Its fix touches tests only, but `client.py` is on the
  high-blast-radius list, so human sign-off applies.
- F1 is docs only and has no gate of its own.
- No AGENTS.md invariant is touched. Auth rules, TLS posture and the key handoff are unchanged.

### After the findings: PLAN/TECH read

- PLAN §2.1 keeps the environment clause on purpose, for the placeholder case. F2 is that case,
  untested.
- PLAN §2.1 says the `_env`/`_eval` forms "resolve into the same value". O1 shows they do not, on
  base either. The diff's code is not affected.
- PLAN §4's probes did not cover a launching shell that already exports the key variable (F1).
- Nothing in PLAN/TECH changes a finding.

**Next:** `/hs-build` for F1 (R4 wording) and F2 (one test case). Then review cycle 2, scoped to
those two items.
