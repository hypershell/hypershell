# PLAN — Launch-path follow-ups: accurate no-key hints, template docs, an export-name test

> **Status:** Draft for review · **Last updated:** 2026-11-05
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). No `research/` (small appetite; the few
> targeted reads and probes are recorded in §4).

## 1. Summary

Three independent slices. (1) The two no-key hints in `ClientApp.run` get one predicate that is
true only when no key reached the client from any source: no `-k`, no `HYPERSHELL_SERVER_AUTH`, and
a resolved `config.server.auth` that is still the placeholder `DEFAULT_AUTH`. (2) One parametrized
end-to-end test pins the shell-name check in `ssh_env_exports()`. (3) Two docs edits:
`templates.rst` is rewritten to match what probes of the running code show, and the approved
sentence goes into `security.rst`.

## 2. Design

### 2.1 No-key hints (R1–R3) — `src/hypershell/client.py`, `ClientApp.run`

Today both handlers test `not auth_given and AUTH_ENV not in os.environ`. A key from a configuration
file passes that test, because it arrives through `config.server.auth`, not through `-k` or the
environment variable. `run` already resolves the key (`self.auth = config.server.auth` when no `-k`
was given). So the placeholder test on the resolved value covers every other source: a local, user
or system configuration file, including the `_env`/`_eval` forms, which cmdkit resolves into the
same value.

Compute the predicate once, before the `try`, and use it in both handlers:

```python
no_key = not auth_given and AUTH_ENV not in os.environ and self.auth == DEFAULT_AUTH
```

Each clause matches a claim in the hint text: no `-k`, the variable is not set, and nothing else gave
a key. The environment clause is redundant except when the variable holds the placeholder itself. In
that case the hint would say "is not set in its environment", which is false, so the clause stays.

**Alternative considered:** `config.which('server', 'auth') == 'default'`, a source label rather
than a value. It was rejected for three reasons. It counts a file that sets the placeholder as a key.
It depends on how cmdkit labels the `_env`/`_eval` variants. And a placeholder key can never pass a
real server's check anyway, since the server refuses to start with it. The value test is simpler and
needs no new API. It matches the existing `check_args` warning, which keys on the same comparison.
The reviewer's scratch condition (`not auth_given and self.auth == DEFAULT_AUTH`) is this predicate
without the environment clause.

No change to the hint texts, the exception types, or the exit status.

### 2.2 Export-name test (R5) — `tests/test_cluster_launch.py`

A new parametrized integration test drives `hs cluster --ssh HOST --env` through the existing stand-in
`ssh` (which runs the remote words under `/bin/sh`, as a real remote shell would). It puts one
`HYPERSHELL_*` variable with a name a shell cannot assign into the launching environment, with three
cases: a dot, a dash, and a space. It asserts four things:

- the name is on no recorded command line;
- the warning naming it is logged;
- the other `--env` exports still arrive (`record_level_exported`);
- the run completes with every task at exit 0 (`assert_run_complete`).

With the check removed, the export word goes onto ssh's command line, the remote `sh` reads it as a
command (exit 127), no client starts, and the run fails.

### 2.3 Docs (R4, R6)

**`docs/_include/templates.rst`**, *Shell Expansion* and *Lambda Expressions*. The wording follows
the probes in §4, not the reviewer's suggestion verbatim:

- A `{% %}` command runs with the client's own environment without `HYPERSHELL_SERVER_AUTH`. That is
  what the tasks inherit before the client adds the `[export]` values and the `TASK_*` variables.
  A probe shows that a client `hs cluster` starts through a launcher already holds the `[export]`
  values in its own environment (`ClientLaunch.start` adds `load_task_env()`). So the docs say a
  `{% %}` command sees an `[export]` value only where the client's own environment holds it, rather
  than "never".
- `os.environ` in a `{= =}` lambda includes `HYPERSHELL_SERVER_AUTH` under a client that `hs cluster`
  starts as a process of its own, through a launcher or over SSH. It does not under the default
  local mode, whose client runs inside the `hs cluster` process. A client started by hand has it
  only if its own environment does. The reviewer's suggested "under a client that `hs cluster`
  started" is wrong for local mode; the probe shows `False` there.

**`docs/security.rst`.** The old sentence's subject ("Keeping the key out of what tasks inherit")
named its own antecedent. The approved replacement opens with "This", and in the old position "This"
would follow "that run mints its own key for its own server and clients", the nested-run sentence. It
would read as referring to the nested run. Grammar therefore needs the approved two sentences to
follow the statement they refer to. Three edits do that:

- The approved two sentences go at the end of the first paragraph, after "… unless the configuration
  gives it explicitly." The paragraph's last sentence, "This applies to clients from this release on;
  …", moves with them, because it also referred to that statement and would otherwise follow the
  nested-run sentence.
- "There are two explicit ways:" no longer follows "explicitly". It becomes "A task can be given
  the key in two explicit ways:". (Amended at build: the first draft, "The configuration can give a
  task the key …", fitted `[export]` but not the `{= =}` bullet, which is a template.)
- The nested-run sentence stays after the bullets, unchanged.

Every other word is unchanged.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | §2.1 `no_key` predicate in the `AuthenticationError` handler; test case "configuration file, wrong key" |
| R2   | §2.1 `no_key` predicate in the `SSLCertVerificationError` handler; test case "launcher drops the environment, key in a configuration file" |
| R3   | §2.1, same predicate; the existing hint cases (no key, `-k`, environment) stay in the parametrizations and pass unchanged |
| R4   | §2.3 `templates.rst`, wording from the §4 probes |
| R5   | §2.2 parametrized test; the mutant run (check removed) recorded in TECH.md |
| R6   | §2.3 `security.rst` |

## 3. Invariant gate (AGENTS.md constitution check)

Gate #1 (pre-research) and gate #2 (post-design) touch the same sections:

- **§9 Queue transport & security.** Auth is still required, and the placeholder refusal and key
  rules are untouched. Only the advisory text on an error path changes.
- **§10 Configuration.** The predicate reads the already-resolved, immutable `config.server.auth`,
  with no reload. The `_env`/`_eval` suffixes resolve before the comparison.
- **§11 Cluster orchestration.** No change to `client_launch()` or the key handoff. The new test only
  observes it. The templates docs describe the existing strip of the key variable from `{% %}`
  environments without changing it.
- **§12 Conventions.** New tests are tagged (the module is `pytestmark = mark.integration`) and use
  `@mark.parametrize`. No CLI surface change, so no `_include` help snippet or `share/` completion
  change. The docs name only top-level commands.
- **High-blast-radius file:** `client.py`. The edit is confined to `ClientApp.run`'s exception
  handlers (the CLI layer). It touches none of the FSM, resource-accounting, signal or shutdown
  contracts. Any CONFIRMED review finding here still goes to the human gate.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| Lean plan despite `client.py` being on the high-blast-radius list (hs-plan Step 3 asks for a fan-out) | The change is one boolean on an error path, in `ClientApp.run`, after the client has already failed. It touches none of the coupled-core contracts the fan-out exists to pin. The contracts it does depend on (config precedence, how `run` resolves the key) were read directly and probed (§4). | A research fan-out would cost more than the whole build and pin nothing the targeted reads did not. |

## 4. Rabbit holes (resolved)

All of these were settled by targeted reads and by probes run in throwaway sites (2026-11-05).

- **Where the key resolves from.** `core/config.py` builds `config` from default, preload, system,
  user, local (`HYPERSHELL_CONFIG_FILE`) and env, in that order. Probes of `config.server.auth` and
  `config.which('server', 'auth')`:
  - nothing set → `'<not-secure>'`, `default`;
  - a file with `[server] auth = "…"` → the key, `local`;
  - `HYPERSHELL_SERVER_AUTH=` (empty) → `None`, `env`. That is not the placeholder, and the old
    clause already suppresses it.
- **What a `{% %}` command and a `{= =}` lambda see.** Each probe used `HYPERSHELL_EXPORT_MARKER_X`
  set in the launching environment, and a template whose `{% %}` command and task each dump their
  environment as JSON. The task's argument is a `{= 'HYPERSHELL_SERVER_AUTH' in os.environ =}`
  lambda.

  | Mode | `{% %}` command: `MARKER_X` | `{% %}` command: `TASK_*` | `{% %}` command: key variable | task: `MARKER_X` | task: `TASK_*` | lambda: key variable |
  |------|------|------|------|------|------|------|
  | local `hs cluster` | absent | none | absent | `exported` | 36 | `False` |
  | `hs cluster --launcher=env` | `exported` | none | absent | `exported` | 36 | `True` |
  | `hs server -k` + `hs client -k` by hand (no export set) | — | — | — | — | 36 | `False` |
  | SSH remote step (`ssh_remote_script` under `/bin/sh`, key on stdin, client stand-in) | — | — | — | — | — | the client process holds the key variable |

  The launcher row shows that a launched client's own environment holds the `[export]` values (from
  `ClientLaunch.start`), so its `{% %}` command sees them too.
- **Does a stand-in `ssh` exercise the remote shell?** Yes. `SSH_BODY` runs the joined remote words
  under `/bin/sh -c`, so an unassignable export name fails the launch as it would on a real host.

## 5. Risks & open questions

- The hint test for R2 needs a launched client that has the right key in a configuration file but
  cannot verify the server. The existing bare-environment launcher stand-in gives it a home of its
  own, so it generates its own self-signed certificate. A third parametrization writes a
  configuration file there holding the run's key and points `HYPERSHELL_CONFIG_FILE` at it. This
  mirrors the reviewer's probe.
- None of this is security-lane material. The docs and tests state only behaviour that is already
  public.

## 6. Verification strategy

- **Red first.** P1's new test cases are written and run before the predicate changes. They must fail
  on the unchanged `client.py`, and the existing cases must pass. Then the predicate goes in and every
  case passes. Both runs are recorded in TECH.md.
- **Mutant.** P2's test runs green, then against `launch.py` with the `SHELL_NAME` skip removed
  (it must fail), then green again after the file is restored. The kill is recorded in TECH.md.
- **Docs.** The probes in §4 are the execution check for R4. The Sphinx build runs with `-W`, and
  only the known pre-existing warnings are allowed.
- **Final gates.** The full suite under `-n auto` on Python 3.14, and the Sphinx build.
